import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from alina_bot.knowledge_base import KnowledgeBase, stem  # noqa: E402


class KnowledgeBaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kb = KnowledgeBase(path=ROOT / "knowledge" / "zerocoder.md", top_k=6)

    def test_knowledge_base_loaded(self) -> None:
        stats = self.kb.stats()
        self.assertGreaterEqual(stats["sections"], 10)
        self.assertGreater(stats["characters"], 5000)

    def test_stemming(self) -> None:
        self.assertEqual(stem("курсы"), stem("курс"))
        self.assertEqual(stem("обучения"), stem("обучение"))
        self.assertEqual(stem("цены"), stem("цена"))

    def test_price_question_finds_price_and_programs(self) -> None:
        titles = [result.chunk.title for result in self.kb.search("сколько стоит вайб-кодер")]
        self.assertTrue(any("Стоимость" in title for title in titles), titles)
        self.assertTrue(any("профессии" in title.lower() for title in titles), titles)

    def test_kids_question_finds_zero_kids(self) -> None:
        titles = [result.chunk.title for result in self.kb.search("есть ли обучение для детей")]
        self.assertTrue(any("Zero Kids" in title for title in titles), titles)

    def test_career_question_finds_career_center(self) -> None:
        titles = [result.chunk.title for result in self.kb.search("поможете с трудоустройством")]
        self.assertTrue(any("Карьерный" in title for title in titles), titles)

    def test_context_contains_sources_and_disclaimer(self) -> None:
        context = self.kb.build_context("расскажите про университет")
        self.assertIn("База знаний Zerocoder", context)
        self.assertIn("https://zerocoder.ru", context)
        self.assertIn("01.11.2026", context)

    def test_unknown_question_returns_general_context(self) -> None:
        results = self.kb.search("квантовая хромодинамика")
        self.assertTrue(results)
        self.assertLessEqual(len(results), self.kb.top_k + 2)


if __name__ == "__main__":
    unittest.main()
