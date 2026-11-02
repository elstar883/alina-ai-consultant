import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alina_bot.alina import AlinaBot, HistoryStore, sanitize_for_vk  # noqa: E402
from alina_bot.bot import split_message  # noqa: E402
from alina_bot.knowledge_base import KnowledgeBase  # noqa: E402


class FakeGigaChat:
    def __init__(self, answer: str = "Стоимость уточнит менеджер.") -> None:
        self.answer = answer
        self.last_messages: list[dict[str, str]] = []

    def chat(self, messages, **kwargs):
        self.last_messages = messages
        return self.answer


def make_settings() -> SimpleNamespace:
    return SimpleNamespace(
        kb_path=ROOT / "knowledge" / "zerocoder.md",
        kb_top_k=4,
        kb_max_chars=8000,
        sales_form_url="https://example.test/form",
        history_turns=4,
        history_ttl_minutes=60,
        prompt_path=ROOT / "prompts" / "alina_system_prompt.md",
    )


class AlinaTest(unittest.TestCase):
    def setUp(self) -> None:
        self.fake = FakeGigaChat()
        self.alina = AlinaBot(
            settings=make_settings(),
            client=self.fake,
            knowledge_base=KnowledgeBase(path=ROOT / "knowledge" / "zerocoder.md"),
        )

    def test_system_prompt_contains_knowledge_and_form_link(self) -> None:
        self.alina.reply(1, "расскажите про Zerocoder")
        system = self.fake.last_messages[0]
        self.assertEqual(system["role"], "system")
        self.assertIn("База знаний Zerocoder", system["content"])
        self.assertIn("https://example.test/form", system["content"])
        self.assertNotIn("{{SALES_FORM_URL}}", system["content"])

    def test_lead_intent_adds_form_link(self) -> None:
        answer = self.alina.reply(2, "Сколько стоит обучение?")
        self.assertIn("https://example.test/form", answer)

    def test_history_is_used_and_reset(self) -> None:
        self.alina.reply(3, "Привет, хочу учиться")
        self.alina.reply(3, "А сколько стоит?")
        user_messages = [
            message for message in self.fake.last_messages if message["role"] == "user"
        ]
        self.assertGreaterEqual(len(user_messages), 2)

        self.alina.reset(3)
        self.alina.reply(3, "Новый вопрос")
        user_messages = [
            message for message in self.fake.last_messages if message["role"] == "user"
        ]
        self.assertEqual(len(user_messages), 1)

    def test_empty_message_gets_hint(self) -> None:
        answer = self.alina.reply(4, "   ")
        self.assertIn("только текст", answer)


class SanitizeTest(unittest.TestCase):
    def test_markdown_is_removed(self) -> None:
        raw = "**Важно**\n## Заголовок\n- пункт\n[форма](https://example.test)"
        clean = sanitize_for_vk(raw)
        self.assertNotIn("**", clean)
        self.assertNotIn("##", clean)
        self.assertIn("— пункт", clean)
        self.assertIn("форма: https://example.test", clean)


class HistoryStoreTest(unittest.TestCase):
    def test_history_is_limited(self) -> None:
        store = HistoryStore(max_turns=2, ttl_minutes=60)
        for index in range(10):
            store.append(1, "user", f"сообщение {index}")
            store.append(1, "assistant", f"ответ {index}")
        self.assertLessEqual(len(store.get(1).messages), 4)


class SplitMessageTest(unittest.TestCase):
    def test_short_message_is_not_split(self) -> None:
        self.assertEqual(split_message("привет"), ["привет"])

    def test_long_message_is_split(self) -> None:
        text = "\n\n".join(["Абзац текста. " * 40 for _ in range(10)])
        parts = split_message(text, limit=500)
        self.assertGreater(len(parts), 1)
        self.assertTrue(all(len(part) <= 500 for part in parts))
        self.assertEqual(" ".join(" ".join(parts).split()), " ".join(text.split()))


if __name__ == "__main__":
    unittest.main()
