import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alina_bot.bot import VKBotApp  # noqa: E402


class FakeVK:
    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.typing: list[int] = []

    def send_message(self, peer_id: int, text: str) -> None:
        self.sent.append((peer_id, text))

    def set_typing(self, peer_id: int) -> None:
        self.typing.append(peer_id)


class FakeAlina:
    def __init__(self) -> None:
        self.replies: list[tuple[int, str]] = []
        self.resets: list[int] = []
        self.help_calls = 0

    def reply(self, peer_id: int, text: str) -> str:
        self.replies.append((peer_id, text))
        return "Ответ Алины"

    def reset(self, peer_id: int) -> str:
        self.resets.append(peer_id)
        return "Диалог сброшен"

    def help_message(self) -> str:
        self.help_calls += 1
        return "Справка"


def make_app() -> tuple[VKBotApp, FakeVK, FakeAlina]:
    settings = SimpleNamespace(vk_group_id=1, gigachat_model="GigaChat-2")
    vk = FakeVK()
    alina = FakeAlina()
    app = VKBotApp(
        settings=settings,
        vk=vk,
        gigachat=SimpleNamespace(),
        alina=alina,
    )
    return app, vk, alina


class VKUpdateTest(unittest.TestCase):
    def test_message_new_is_answered(self) -> None:
        app, vk, alina = make_app()
        app.handle_update(
            {
                "type": "message_new",
                "event_id": 1,
                "object": {
                    "message": {
                        "from_id": 42,
                        "peer_id": 42,
                        "out": 0,
                        "text": "Сколько стоит обучение?",
                        "attachments": [],
                    }
                },
            }
        )
        self.assertEqual(alina.replies, [(42, "Сколько стоит обучение?")])
        self.assertEqual(vk.sent, [(42, "Ответ Алины")])
        self.assertEqual(vk.typing, [42])

    def test_outgoing_message_is_ignored(self) -> None:
        app, vk, alina = make_app()
        app.handle_update(
            {
                "type": "message_new",
                "event_id": 2,
                "object": {
                    "message": {
                        "from_id": 1,
                        "peer_id": 42,
                        "out": 1,
                        "text": "Ответ сообщества",
                    }
                },
            }
        )
        self.assertEqual(alina.replies, [])
        self.assertEqual(vk.sent, [])

    def test_duplicate_event_is_ignored(self) -> None:
        app, vk, alina = make_app()
        update = {
            "type": "message_new",
            "event_id": 3,
            "object": {
                "message": {
                    "from_id": 42,
                    "peer_id": 42,
                    "out": 0,
                    "text": "Привет",
                }
            },
        }
        app.handle_update(update)
        app.handle_update(update)
        self.assertEqual(len(alina.replies), 1)
        self.assertEqual(len(vk.sent), 1)

    def test_reset_command(self) -> None:
        app, vk, alina = make_app()
        app.handle_update(
            {
                "type": "message_new",
                "event_id": 4,
                "object": {
                    "message": {
                        "from_id": 42,
                        "peer_id": 42,
                        "out": 0,
                        "text": "Сброс",
                    }
                },
            }
        )
        self.assertEqual(alina.resets, [42])
        self.assertEqual(vk.sent, [(42, "Диалог сброшен")])

    def test_attachment_without_text_gets_hint(self) -> None:
        app, vk, alina = make_app()
        app.handle_update(
            {
                "type": "message_new",
                "event_id": 5,
                "object": {
                    "message": {
                        "from_id": 42,
                        "peer_id": 42,
                        "out": 0,
                        "text": "",
                        "attachments": [{"type": "photo"}],
                    }
                },
            }
        )
        self.assertEqual(alina.replies, [])
        self.assertIn("только текст", vk.sent[0][1])


if __name__ == "__main__":
    unittest.main()
