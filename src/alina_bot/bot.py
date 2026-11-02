"""Чат-бот сообщества ВКонтакте: Long Poll + GigaChat + база знаний Zerocoder."""

from __future__ import annotations

import argparse
import logging
import secrets
import signal
import sys
import threading
import time
from collections import deque
from typing import Any

import httpx

from .alina import AlinaBot
from .config import ConfigError, Settings, configure_logging, load_settings
from .gigachat_client import GigaChatClient, GigaChatError
from .knowledge_base import KnowledgeBase

logger = logging.getLogger(__name__)

VK_API_URL = "https://api.vk.com/method/"
MAX_MESSAGE_LENGTH = 3800

RESET_COMMANDS = {
    "начать",
    "начать заново",
    "старт",
    "/start",
    "start",
    "сброс",
    "сбросить",
    "/reset",
    "reset",
    "новый диалог",
    "очистить историю",
}

HELP_COMMANDS = {"помощь", "/help", "help", "что ты умеешь", "команды"}


class VKApiError(RuntimeError):
    def __init__(self, method: str, code: int, message: str) -> None:
        super().__init__(f"VK API {method}: код {code}, {message}")
        self.method = method
        self.code = code
        self.vk_message = message


class VKClient:
    def __init__(self, *, token: str, group_id: int, api_version: str = "5.199") -> None:
        self._token = token
        self.group_id = group_id
        self._api_version = api_version
        self._client = httpx.Client(
            timeout=httpx.Timeout(40.0),
            headers={"User-Agent": "zerocoder-vk-alina/1.0"},
        )

    def api_call(self, method: str, **params: Any) -> Any:
        payload = {
            "access_token": self._token,
            "v": self._api_version,
            **params,
        }
        try:
            response = self._client.post(VK_API_URL + method, data=payload)
        except httpx.HTTPError as exc:
            raise VKApiError(method, -1, f"сетевая ошибка: {exc.__class__.__name__}") from exc

        try:
            data = response.json()
        except ValueError as exc:
            raise VKApiError(method, -1, f"некорректный ответ HTTP {response.status_code}") from exc

        if "error" in data:
            error = data["error"]
            raise VKApiError(
                method,
                int(error.get("error_code", -1)),
                str(error.get("error_msg", "неизвестная ошибка")),
            )
        return data.get("response")

    # ------------------------------------------------------------- Long Poll
    def get_long_poll_server(self) -> tuple[str, str, str]:
        response = self.api_call("groups.getLongPollServer", group_id=self.group_id)
        if not isinstance(response, dict):
            raise VKApiError("groups.getLongPollServer", -1, "пустой ответ")
        server = response.get("server")
        key = response.get("key")
        ts = response.get("ts")
        if not server or not key or ts is None:
            raise VKApiError(
                "groups.getLongPollServer",
                -1,
                "нет server/key/ts. Проверьте, что Long Poll API включён в сообществе.",
            )
        server = str(server)
        if server.startswith("http://"):
            server = "https://" + server[len("http://") :]
        return server, str(key), str(ts)

    def poll(self, server: str, key: str, ts: str, wait: int = 25) -> dict[str, Any]:
        response = self._client.get(
            server,
            params={"act": "a_check", "key": key, "ts": ts, "wait": wait},
            timeout=httpx.Timeout(wait + 15),
        )
        response.raise_for_status()
        return response.json()

    # -------------------------------------------------------------- действия
    def set_typing(self, peer_id: int) -> None:
        try:
            self.api_call(
                "messages.setActivity",
                peer_id=peer_id,
                group_id=self.group_id,
                type="typing",
            )
        except VKApiError as exc:
            logger.debug("Не удалось показать «печатает» для peer_id=%s: %s", peer_id, exc)

    def send_message(self, peer_id: int, text: str) -> None:
        for part in split_message(text):
            self._send_part(peer_id, part)

    def _send_part(self, peer_id: int, text: str) -> None:
        params = {
            "peer_id": peer_id,
            "message": text,
            "random_id": secrets.randbits(63),
            "disable_mentions": 1,
            "dont_parse_links": 1,
            "group_id": self.group_id,
        }
        for attempt in range(1, 3):
            try:
                self.api_call("messages.send", **params)
                return
            except VKApiError as exc:
                if exc.code == 9 and attempt == 1:  # flood control
                    logger.warning("VK flood control для peer_id=%s, пауза.", peer_id)
                    time.sleep(2)
                    continue
                if exc.code == 6 and attempt == 1:  # too many requests
                    logger.warning("VK: слишком много запросов, пауза.")
                    time.sleep(3)
                    continue
                if exc.code == -1 and attempt == 1:  # сетевая ошибка
                    logger.warning("VK: сетевая ошибка при отправке, повторяю.")
                    time.sleep(1)
                    continue
                raise

    def close(self) -> None:
        self._client.close()


class VKBotApp:
    def __init__(
        self,
        *,
        settings: Settings,
        vk: VKClient,
        gigachat: GigaChatClient,
        alina: AlinaBot,
    ) -> None:
        self.settings = settings
        self.vk = vk
        self.gigachat = gigachat
        self.alina = alina
        self._stop = threading.Event()
        self._seen_events: deque[Any] = deque(maxlen=5000)
        self._seen_event_ids: set[Any] = set()

    def stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        server, key, ts = self.vk.get_long_poll_server()
        logger.info(
            "Бот запущен: сообщество %s, модель %s, Long Poll подключён.",
            self.settings.vk_group_id,
            self.settings.gigachat_model,
        )

        while not self._stop.is_set():
            try:
                data = self.vk.poll(server, key, ts)
            except (httpx.HTTPError, VKApiError, ValueError) as exc:
                logger.warning("Ошибка Long Poll: %s. Переподключаюсь через 3 секунды.", exc)
                if self._stop.wait(3):
                    break
                try:
                    server, key, ts = self.vk.get_long_poll_server()
                except VKApiError:
                    logger.exception("Не удалось переподключиться к Long Poll.")
                continue

            if not isinstance(data, dict):
                logger.warning("Long Poll вернул неожиданный ответ, переподключаюсь.")
                if self._stop.wait(3):
                    break
                try:
                    server, key, ts = self.vk.get_long_poll_server()
                except VKApiError:
                    logger.exception("Не удалось переподключиться к Long Poll.")
                continue

            failed = data.get("failed")
            if failed:
                if failed == 1:
                    ts = str(data.get("ts", ts))
                    logger.info("Long Poll: обновлён ts.")
                    continue
                logger.warning("Long Poll вернул failed=%s, получаю новый ключ.", failed)
                try:
                    server, key, ts = self.vk.get_long_poll_server()
                except VKApiError:
                    logger.exception("Не удалось получить новый Long Poll сервер.")
                    if self._stop.wait(3):
                        break
                continue

            ts = str(data.get("ts", ts))
            updates = data.get("updates")
            if not isinstance(updates, list):
                logger.warning("Long Poll вернул пустой или некорректный список updates.")
                continue
            for update in updates:
                if self._stop.is_set():
                    break
                if not isinstance(update, dict):
                    continue
                try:
                    self.handle_update(update)
                except Exception:
                    logger.exception("Ошибка обработки обновления VK.")
                    # Не роняем бота из-за одного сообщения.

        logger.info("Бот остановлен.")

    def handle_update(self, update: dict[str, Any]) -> None:
        if not isinstance(update, dict):
            return
        if update.get("type") != "message_new":
            return

        event_id = update.get("event_id")
        if event_id is not None:
            if event_id in self._seen_event_ids:
                return
            if len(self._seen_events) == self._seen_events.maxlen:
                oldest = self._seen_events[0]
                self._seen_event_ids.discard(oldest)
            self._seen_events.append(event_id)
            self._seen_event_ids.add(event_id)

        obj = update.get("object") or {}
        message = obj.get("message") if isinstance(obj.get("message"), dict) else obj
        if not isinstance(message, dict):
            return

        if message.get("out") == 1:
            return
        from_id = message.get("from_id")
        if not isinstance(from_id, int) or from_id <= 0:
            return

        peer_id = message.get("peer_id") or from_id
        if not isinstance(peer_id, int):
            return

        text = str(message.get("text") or "").strip()
        attachments = message.get("attachments") or []

        if not text:
            if attachments:
                self.vk.send_message(
                    peer_id,
                    "Я пока понимаю только текст. Напишите, пожалуйста, вопрос словами — "
                    "например, какое направление обучения вас интересует.",
                )
            return

        logger.info(
            "Сообщение от peer_id=%s (длина=%s символов, вложений=%s).",
            peer_id,
            len(text),
            len(attachments),
        )

        normalized = text.lower().strip()
        if normalized in RESET_COMMANDS:
            self.vk.send_message(peer_id, self.alina.reset(peer_id))
            return
        if normalized in HELP_COMMANDS:
            self.vk.send_message(peer_id, self.alina.help_message())
            return

        self.vk.set_typing(peer_id)
        answer = self.alina.reply(peer_id, text)
        self.vk.send_message(peer_id, answer)

    def close(self) -> None:
        self.vk.close()
        self.gigachat.close()


def split_message(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Делит длинный ответ на части по абзацам, не разрывая слова без нужды."""

    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split_at = window.rfind("\n\n")
        if split_at < limit // 2:
            split_at = window.rfind("\n")
        if split_at < limit // 2:
            split_at = window.rfind(" ")
        if split_at <= 0:
            split_at = limit
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        parts.append(remaining)
    return [part for part in parts if part]


def check_connections(settings: Settings) -> int:
    """Проверяет VK Long Poll и GigaChat, не показывая секреты."""

    exit_code = 0
    vk = VKClient(
        token=settings.vk_group_token,
        group_id=settings.vk_group_id,
        api_version=settings.vk_api_version,
    )
    gigachat = GigaChatClient(
        credentials=settings.authorization_key,
        scope=settings.gigachat_scope,
        base_url=settings.gigachat_base_url,
        auth_url=settings.gigachat_auth_url,
        model=settings.gigachat_model,
        verify_ssl=settings.gigachat_verify_ssl,
        ca_bundle_file=settings.gigachat_ca_bundle_file,
        timeout=settings.gigachat_timeout,
        temperature=settings.gigachat_temperature,
        max_tokens=settings.gigachat_max_tokens,
    )

    try:
        vk.get_long_poll_server()
        print("VK: OK — Long Poll сервер доступен, токен сообщества принят.")
    except VKApiError as exc:
        print(f"VK: ОШИБКА — {exc}")
        exit_code = 1

    try:
        models = gigachat.list_models()
        print("GigaChat: OK — авторизация прошла. Доступные модели: " + ", ".join(models))
    except GigaChatError as exc:
        print(f"GigaChat: ОШИБКА — {exc}")
        exit_code = 1
    finally:
        vk.close()
        gigachat.close()

    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description="Чат-бот Zerocoder для сообщества ВКонтакте")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Проверить подключение к VK и GigaChat и выйти",
    )
    args = parser.parse_args()

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Ошибка конфигурации: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)

    if args.check:
        return check_connections(settings)

    try:
        knowledge_base = KnowledgeBase(
            path=settings.kb_path,
            top_k=settings.kb_top_k,
            max_chars=settings.kb_max_chars,
        )
    except Exception as exc:
        logger.error("Не удалось загрузить базу знаний: %s", exc)
        return 2

    stats = knowledge_base.stats()
    logger.info(
        "База знаний: %s разделов, %s символов, %s уникальных терминов.",
        stats["sections"],
        stats["characters"],
        stats["unique_terms"],
    )

    vk = VKClient(
        token=settings.vk_group_token,
        group_id=settings.vk_group_id,
        api_version=settings.vk_api_version,
    )
    gigachat = GigaChatClient(
        credentials=settings.authorization_key,
        scope=settings.gigachat_scope,
        base_url=settings.gigachat_base_url,
        auth_url=settings.gigachat_auth_url,
        model=settings.gigachat_model,
        verify_ssl=settings.gigachat_verify_ssl,
        ca_bundle_file=settings.gigachat_ca_bundle_file,
        timeout=settings.gigachat_timeout,
        temperature=settings.gigachat_temperature,
        max_tokens=settings.gigachat_max_tokens,
    )
    alina = AlinaBot(settings=settings, client=gigachat, knowledge_base=knowledge_base)
    app = VKBotApp(settings=settings, vk=vk, gigachat=gigachat, alina=alina)

    def handle_signal(signum: int, _frame: Any) -> None:
        logger.info("Получен сигнал %s, завершаю работу.", signum)
        app.stop()

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        app.run()
    except KeyboardInterrupt:
        logger.info("Остановка по Ctrl+C.")
    except VKApiError as exc:
        logger.error("Не удалось запустить бота: %s", exc)
        return 1
    finally:
        app.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
