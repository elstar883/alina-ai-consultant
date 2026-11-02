"""База знаний Zerocoder: загрузка Markdown и поиск релевантных фрагментов.

Механизм простой и предсказуемый (без внешних сервисов):
1. Файл knowledge/zerocoder.md разбивается на разделы по заголовкам ##.
2. Разделы индексируются по словам с лёгким стеммингом русских окончаний.
3. На запрос пользователя считается BM25-оценка, добавляются синонимы и
   бонус за совпадение с заголовком.
4. В промпт попадают только самые релевантные фрагменты + «закреплённые»
   разделы (о компании и контакты).

Так база знаний не отправляется целиком на каждый запрос, а ответы остаются
привязанными к фактам с официального сайта.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_KB_PATH = Path(__file__).resolve().parents[2] / "knowledge" / "zerocoder.md"

SECTION_RE = re.compile(r"(?m)^##\s+(.+?)\s*$")
SOURCE_RE = re.compile(r"Источник:\s*(https?://\S+)")
WORD_RE = re.compile(r"[а-яёa-z0-9]+", re.IGNORECASE)

PINNED_TITLE_PARTS = ("о zerocoder", "контакты")

STOPWORDS = {
    "а", "без", "бы", "был", "была", "были", "быть", "в", "вам", "вас", "весь",
    "во", "вот", "все", "всё", "вы", "где", "да", "даже", "для", "до", "его",
    "ее", "её", "ей", "если", "есть", "еще", "ещё", "же", "за", "здесь", "и",
    "из", "или", "им", "их", "к", "как", "какая", "какие", "какой", "когда",
    "кто", "ли", "мне", "меня", "мы", "на", "над", "надо", "наш", "не", "него",
    "нее", "неё", "нет", "ни", "но", "ну", "о", "об", "он", "она", "они", "оно",
    "от", "по", "под", "при", "про", "с", "сам", "свой", "себе", "так", "также",
    "там", "те", "тем", "то", "того", "тоже", "той", "только", "том", "ты", "у",
    "уже", "хорошо", "хоть", "чего", "чей", "чем", "что", "чтобы", "чье", "чья",
    "эта", "эти", "это", "этот", "я",
}

SYNONYMS = {
    "цена": ["стоимость", "оплата", "тариф", "рассрочка", "сколько"],
    "цены": ["стоимость", "оплата", "тариф", "рассрочка", "сколько"],
    "стоимость": ["цена", "оплата", "тариф", "рассрочка"],
    "сколько": ["стоимость", "цена", "оплата"],
    "курс": ["программа", "направление", "обучение"],
    "курсы": ["программа", "направление", "обучение"],
    "программа": ["курс", "направление", "обучение"],
    "программы": ["курс", "направление", "обучение"],
    "обучение": ["курс", "программа", "направление"],
    "учиться": ["обучение", "курс", "программа"],
    "трудоустройство": ["работа", "карьера", "стажировка", "заказы", "карьерный"],
    "работа": ["трудоустройство", "карьера", "стажировка"],
    "зарплата": ["доход", "заработок", "работа"],
    "диплом": ["сертификат", "документ"],
    "сертификат": ["диплом", "документ"],
    "ребенок": ["дети", "школьник", "zero kids"],
    "ребёнок": ["дети", "школьник", "zero kids"],
    "дети": ["ребенок", "школьник", "zero kids"],
    "бот": ["вайб-кодинг", "чат-бот", "разработка"],
    "боты": ["вайб-кодинг", "чат-бот", "разработка"],
    "нейросеть": ["нейросети", "ии", "искусственный интеллект"],
    "нейросети": ["нейросеть", "ии", "искусственный интеллект"],
    "ии": ["нейросети", "искусственный интеллект"],
    "искусственный": ["ии", "нейросети"],
    "недорого": ["стоимость", "цена", "рассрочка"],
    "дешево": ["стоимость", "цена", "рассрочка"],
    "дешёво": ["стоимость", "цена", "рассрочка"],
    "записаться": ["заявка", "форма", "менеджер", "консультация"],
    "консультация": ["менеджер", "заявка", "форма"],
    "менеджер": ["консультация", "заявка", "связаться"],
    "связаться": ["менеджер", "контакты", "форма"],
    "контакты": ["телефон", "почта", "telegram", "менеджер"],
}

SUFFIXES = (
    "иями", "ями", "ами", "ыми", "ими", "ого", "ему", "ому", "его", "ее", "её",
    "ые", "ых", "ая", "ое", "ий", "ия", "ие", "ей", "ой", "ам", "ям", "ом",
    "ем", "ах", "ях", "ую", "юю", "ишь", "ешь", "ов", "ев", "ы", "и", "а", "я",
    "о", "е", "у", "ю", "ь",
)


def normalize_word(word: str) -> str:
    return word.lower().replace("ё", "е")


def stem(word: str) -> str:
    word = normalize_word(word)
    if len(word) <= 3:
        return word
    for suffix in SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in WORD_RE.findall(text):
        raw = normalize_word(raw)
        if raw in STOPWORDS or len(raw) < 2:
            continue
        tokens.append(stem(raw))
    return tokens


@dataclass(frozen=True)
class Chunk:
    id: str
    title: str
    text: str
    source: str
    order: int


@dataclass(frozen=True)
class SearchResult:
    chunk: Chunk
    score: float


class KnowledgeBase:
    def __init__(
        self,
        path: str | Path = DEFAULT_KB_PATH,
        *,
        top_k: int = 6,
        max_chars: int = 14_000,
        max_chunk_chars: int = 2600,
    ) -> None:
        self.path = Path(path)
        self.top_k = top_k
        self.max_chars = max_chars
        self.max_chunk_chars = max_chunk_chars
        self.chunks = self._load_chunks(self.path.read_text(encoding="utf-8"))
        if not self.chunks:
            raise ValueError(f"База знаний пуста или не содержит разделов ##: {self.path}")

        self._tokens = [tokenize(chunk.title + " " + chunk.text) for chunk in self.chunks]
        self._title_tokens = [set(tokenize(chunk.title)) for chunk in self.chunks]
        self._doc_freq: dict[str, int] = {}
        for tokens in self._tokens:
            for token in set(tokens):
                self._doc_freq[token] = self._doc_freq.get(token, 0) + 1

        self._avg_len = sum(len(t) for t in self._tokens) / max(len(self._tokens), 1)
        self._idf = {
            token: math.log(1 + (len(self.chunks) - df + 0.5) / (df + 0.5))
            for token, df in self._doc_freq.items()
        }

    # ------------------------------------------------------------- загрузка
    def _load_chunks(self, markdown: str) -> list[Chunk]:
        matches = list(SECTION_RE.finditer(markdown))
        chunks: list[Chunk] = []
        order = 0

        for index, match in enumerate(matches):
            title = match.group(1).strip()
            body_start = match.end()
            body_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
            body = markdown[body_start:body_end].strip()
            source_match = SOURCE_RE.search(body)
            source = source_match.group(1).rstrip(".,;)") if source_match else "https://zerocoder.ru/"
            body = _strip_blockquote_header(body)

            for part_index, part in enumerate(self._split_body(body), start=1):
                part_title = title if part_index == 1 else f"{title} (часть {part_index})"
                chunks.append(
                    Chunk(
                        id=f"chunk-{order:03d}",
                        title=part_title,
                        text=part,
                        source=source,
                        order=order,
                    )
                )
                order += 1

        return chunks

    def _split_body(self, body: str) -> list[str]:
        if len(body) <= self.max_chunk_chars:
            return [body] if body else []

        # Сначала пробуем делить по подзаголовкам ###.
        subparts = [p.strip() for p in re.split(r"(?m)^###\s+", body) if p.strip()]
        if len(subparts) > 1:
            merged = self._merge_paragraphs(subparts)
        else:
            merged = self._merge_paragraphs([p.strip() for p in body.split("\n\n") if p.strip()])
        return merged

    def _merge_paragraphs(self, paragraphs: list[str]) -> list[str]:
        parts: list[str] = []
        current = ""
        for paragraph in paragraphs:
            candidate = (current + "\n\n" + paragraph).strip() if current else paragraph
            if current and len(candidate) > self.max_chunk_chars:
                parts.append(current.strip())
                current = paragraph
            else:
                current = candidate
        if current.strip():
            parts.append(current.strip())

        # Не оставляем крошечный «хвост» вроде одной строки с источником.
        if len(parts) > 1 and len(parts[-1]) < 200:
            parts[-2] = parts[-2].rstrip() + "\n\n" + parts[-1]
            parts.pop()
        return parts

    # --------------------------------------------------------------- поиск
    def _expand_query(self, query: str) -> list[str]:
        raw_tokens = [normalize_word(w) for w in WORD_RE.findall(query)]
        expanded = list(raw_tokens)
        for token in raw_tokens:
            expanded.extend(SYNONYMS.get(token, []))
        return [stem(w) for w in expanded if w not in STOPWORDS and len(w) >= 2]

    def search(self, query: str, *, top_k: int | None = None) -> list[SearchResult]:
        query = (query or "").strip()
        top_k = top_k or self.top_k
        query_tokens = self._expand_query(query)
        unique_query_tokens = set(query_tokens)

        if not unique_query_tokens:
            return [SearchResult(chunk=c, score=0.0) for c in self.chunks[:top_k]]

        scores: list[float] = []
        k1, b = 1.5, 0.75
        for index, tokens in enumerate(self._tokens):
            counts: dict[str, int] = {}
            for token in tokens:
                counts[token] = counts.get(token, 0) + 1

            score = 0.0
            doc_len = max(len(tokens), 1)
            for token in unique_query_tokens:
                tf = counts.get(token, 0)
                if not tf:
                    continue
                idf = self._idf.get(token, 0.0)
                denominator = tf + k1 * (1 - b + b * doc_len / self._avg_len)
                score += idf * (tf * (k1 + 1)) / denominator

            title_hits = unique_query_tokens & self._title_tokens[index]
            score += sum(self._idf.get(token, 0.0) for token in title_hits) * 1.8

            if len(query) >= 4 and query.lower() in self.chunks[index].text.lower():
                score += 2.5

            scores.append(score)

        selected = self._select(scores, query, top_k)
        return selected

    def _select(self, scores: list[float], query: str, top_k: int) -> list[SearchResult]:
        ranked = sorted(
            (SearchResult(chunk=self.chunks[i], score=scores[i]) for i in range(len(self.chunks))),
            key=lambda item: (item.score, -item.chunk.order),
            reverse=True,
        )

        pinned = [
            SearchResult(chunk=c, score=0.0)
            for c in self.chunks
            if any(part in c.title.lower() for part in PINNED_TITLE_PARTS)
        ]

        chosen: list[SearchResult] = []
        seen: set[str] = set()
        used_chars = 0

        # Сначала закреплённые разделы, но не больше ~35% бюджета на них.
        pinned_budget = int(self.max_chars * 0.35)
        pinned_used = 0
        for item in pinned:
            if item.chunk.id in seen:
                continue
            size = len(item.chunk.text)
            if pinned_used + size > pinned_budget:
                continue
            chosen.append(item)
            seen.add(item.chunk.id)
            pinned_used += size
            used_chars += size

        for item in ranked:
            if len(chosen) >= top_k + len(pinned):
                break
            if item.chunk.id in seen or item.score <= 0:
                continue
            size = len(item.chunk.text)
            if used_chars + size > self.max_chars:
                continue
            chosen.append(item)
            seen.add(item.chunk.id)
            used_chars += size

        # Если совсем ничего не нашли, добавляем общий раздел о Zerocoder.
        if not chosen:
            chosen = [SearchResult(chunk=self.chunks[0], score=0.0)]

        chosen.sort(key=lambda item: item.chunk.order)
        return chosen

    def build_context(self, query: str, *, top_k: int | None = None) -> str:
        results = self.search(query, top_k=top_k)
        parts = [
            "# База знаний Zerocoder",
            "Ниже — официальные сведения с сайта zerocoder.ru. Используй только их. "
            "Если ответа нет, честно скажи об этом и предложи менеджера. "
            "Данные собраны 01.11.2026; цены и условия могут меняться.",
        ]
        for result in results:
            chunk = result.chunk
            parts.append(f"## {chunk.title}\n{chunk.text}\nИсточник: {chunk.source}")
        return "\n\n".join(parts)

    def stats(self) -> dict[str, int]:
        return {
            "sections": len(self.chunks),
            "characters": sum(len(chunk.text) for chunk in self.chunks),
            "unique_terms": len(self._doc_freq),
        }


def _strip_blockquote_header(body: str) -> str:
    lines = [line for line in body.splitlines() if not line.startswith(">")]
    return "\n".join(lines).strip()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Проверка поиска по базе знаний Zerocoder")
    parser.add_argument("query", help="Вопрос пользователя")
    parser.add_argument("--top-k", type=int, default=6)
    parser.add_argument("--max-chars", type=int, default=14_000)
    args = parser.parse_args()

    kb = KnowledgeBase(top_k=args.top_k, max_chars=args.max_chars)
    results = kb.search(args.query)
    print(f"Запрос: {args.query!r}")
    print(f"Разделов в базе: {len(kb.chunks)}")
    for number, result in enumerate(results, start=1):
        preview = " ".join(result.chunk.text.split())[:180]
        print(f"\n{number}. [{result.score:.2f}] {result.chunk.title}")
        print(f"   {preview}...")


if __name__ == "__main__":
    main()
