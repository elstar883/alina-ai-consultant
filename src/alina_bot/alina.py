"""Логика Алины: системный промпт, история диалога, база знаний и GigaChat."""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .config import Settings
from .gigachat_client import GigaChatError
from .knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

LEAD_INTENT_RE = re.compile(
    r"(цен[аыуе]|стоимост|сколько\s+стоит|рассрочк|тариф|оплат|записат|заявк|"
    r"консультац|менеджер|подобрать|подходит|хочу\s+учиться|хочу\s+курс|"
    r"интересует|куп(ить|лю|аю)|оформ|связаться|перезвон|пробн|скидк)",
    re.IGNORECASE,
)

CTA_COOLDOWN_TURNS = 3
MAX_HISTORY_MESSAGE_CHARS = 2000
MAX_INPUT_CHARS = 3000
MAX_REPLY_CHARS = 3800


class ChatClient(Protocol):
    def chat(self, messages: list[dict[str, str]], **kwargs: object) -> str: ...


@dataclass
class PeerState:
    messages: list[dict[str, str]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.time)
    turns: int = 0
    last_cta_turn: int = -100


class HistoryStore:
    """История диалогов в памяти процесса с TTL и ограничением длины."""

    def __init__(self, max_turns: int = 8, ttl_minutes: int = 180) -> None:
        self.max_messages = max_turns * 2
        self.ttl_seconds = ttl_minutes * 60
        self._peers: dict[int, PeerState] = {}

    def get(self, peer_id: int) -> PeerState:
        self._cleanup()
        state = self._peers.get(peer_id)
        if state is None:
            state = PeerState()
            self._peers[peer_id] = state
        return state

    def append(self, peer_id: int, role: str, content: str) -> None:
        state = self.get(peer_id)
        content = (content or "").strip()
        if not content:
            return
        state.messages.append(
            {"role": role, "content": content[:MAX_HISTORY_MESSAGE_CHARS]}
        )
        if len(state.messages) > self.max_messages:
            del state.messages[: len(state.messages) - self.max_messages]
        state.updated_at = time.time()
        if role == "user":
            state.turns += 1

    def reset(self, peer_id: int) -> None:
        self._peers.pop(peer_id, None)

    def turns_since_cta(self, peer_id: int) -> int:
        state = self.get(peer_id)
        return state.turns - state.last_cta_turn

    def mark_cta(self, peer_id: int) -> None:
        state = self.get(peer_id)
        state.last_cta_turn = state.turns

    def _cleanup(self) -> None:
        if not self.ttl_seconds:
            return
        now = time.time()
        expired = [
            peer_id
            for peer_id, state in self._peers.items()
            if now - state.updated_at > self.ttl_seconds
        ]
        for peer_id in expired:
            self._peers.pop(peer_id, None)


class AlinaBot:
    def __init__(
        self,
        *,
        settings: Settings,
        client: ChatClient,
        knowledge_base: KnowledgeBase | None = None,
        history: HistoryStore | None = None,
    ) -> None:
        self.settings = settings
        self.client = client
        self.kb = knowledge_base or KnowledgeBase(
            path=settings.kb_path,
            top_k=settings.kb_top_k,
            max_chars=settings.kb_max_chars,
        )
        self.history = history or HistoryStore(
            max_turns=settings.history_turns,
            ttl_minutes=settings.history_ttl_minutes,
        )
        self.sales_form_url = settings.sales_form_url
        self.system_prompt = self._load_prompt(Path(settings.prompt_path))

    def _load_prompt(self, path: Path) -> str:
        prompt = path.read_text(encoding="utf-8")
        prompt = prompt.replace("{{SALES_FORM_URL}}", self.sales_form_url)
        if "{{" in prompt:
            logger.warning("В системном промпте остались незаполненные плейсхолдеры.")
        return prompt

    # ------------------------------------------------------------- публичное
    def reply(self, peer_id: int, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return (
                "Я пока понимаю только текст. Напишите, пожалуйста, вопрос словами — "
                "например, какое направление обучения вас интересует."
            )
        if len(text) > MAX_INPUT_CHARS:
            text = text[:MAX_INPUT_CHARS]
            logger.info("Сообщение peer_id=%s обрезано до %s символов.", peer_id, MAX_INPUT_CHARS)

        state = self.history.get(peer_id)
        lead_intent = bool(LEAD_INTENT_RE.search(text))

        system_prompt = self.system_prompt
        try:
            kb_context = self.kb.build_context(text)
        except Exception:
            logger.exception("Не удалось подготовить контекст базы знаний.")
            kb_context = (
                "# База знаний Zerocoder\n"
                "Контекст временно недоступен. Не выдумывай факты: предложи "
                "пользователю связаться с менеджером."
            )

        if lead_intent:
            system_prompt += (
                "\n\n## Дополнительный сигнал\n"
                "Пользователь проявил интерес к стоимости, записи или консультации. "
                "Дай полезный ответ по базе знаний и мягко предложи оставить заявку "
                f"по ссылке {self.sales_form_url}. Не дави и не повторяй ссылку, "
                "если она уже была в предыдущих сообщениях."
            )

        messages: list[dict[str, str]] = [
            {"role": "system", "content": system_prompt + "\n\n" + kb_context}
        ]
        messages.extend(state.messages)
        messages.append({"role": "user", "content": text})

        self.history.append(peer_id, "user", text)

        try:
            raw_reply = self.client.chat(messages)
        except GigaChatError as exc:
            logger.error("Ошибка GigaChat при ответе peer_id=%s: %s", peer_id, exc)
            fallback = self._fallback_message(peer_id)
            self.history.append(peer_id, "assistant", fallback)
            return fallback
        except Exception:
            logger.exception("Неожиданная ошибка при обращении к GigaChat.")
            fallback = self._fallback_message(peer_id)
            self.history.append(peer_id, "assistant", fallback)
            return fallback

        answer = sanitize_for_vk(raw_reply)
        if not answer:
            fallback = self._fallback_message(peer_id)
            self.history.append(peer_id, "assistant", fallback)
            return fallback

        answer = self._maybe_add_form_link(peer_id, answer, lead_intent=lead_intent)
        answer = answer[:MAX_REPLY_CHARS].rstrip()
        self.history.append(peer_id, "assistant", answer)
        return answer

    def reset(self, peer_id: int) -> str:
        self.history.reset(peer_id)
        return (
            "Хорошо, начинаем диалог заново. Я Алина, ИИ-консультант онлайн-университета "
            "Zerocoder. Расскажите, что вас интересует: программы, форматы обучения, "
            "стоимость или выбор направления?"
        )

    def help_message(self) -> str:
        return (
            "Я Алина, ИИ-консультант Zerocoder. Помогаю с выбором программ по нейросетям, "
            "ИИ и вайб-кодингу, рассказываю о форматах, стоимости и карьерной поддержке.\n\n"
            "Команды:\n"
            "— «сброс» или «начать заново» — очистить историю диалога;\n"
            "— «помощь» — показать это сообщение.\n\n"
            f"Если нужен точный ответ от специалиста, оставьте заявку: {self.sales_form_url}"
        )

    # -------------------------------------------------------------- служебное
    def _maybe_add_form_link(self, peer_id: int, answer: str, *, lead_intent: bool) -> str:
        url = self.sales_form_url
        if url and url in answer:
            self.history.mark_cta(peer_id)
            return answer

        # Если модель уже предложила форму/менеджера, но забыла ссылку — добавляем.
        mentions_next_step = any(
            marker in answer.lower()
            for marker in ("заявк", "форм", "менеджер", "консультац", "связаться")
        )
        if (lead_intent or mentions_next_step) and (
            self.history.turns_since_cta(peer_id) >= CTA_COOLDOWN_TURNS
        ):
            answer = (
                answer.rstrip()
                + "\n\nЕсли удобно, оставьте заявку здесь: "
                + url
                + " — менеджер Zerocoder поможет с выбором и уточнит условия."
            )
            self.history.mark_cta(peer_id)
        return answer

    def _fallback_message(self, peer_id: int) -> str:
        self.history.mark_cta(peer_id)
        return (
            "Извините, у меня не получилось получить ответ. Попробуйте, пожалуйста, "
            "написать ещё раз через минуту.\n\n"
            f"Если вопрос срочный, оставьте заявку: {self.sales_form_url} — "
            "менеджер Zerocoder поможет с выбором и уточнит условия."
        )


def sanitize_for_vk(text: str) -> str:
    """Убирает markdown-разметку, которую ВКонтакте показывает как символы."""

    text = text.strip()
    text = re.sub(r"^Алина\s*:\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1: \2", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    text = re.sub(r"__(.+?)__", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"~~(.+?)~~", r"\1", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "— ", text)
    text = re.sub(r"(?m)^\s*>\s?", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
