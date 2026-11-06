"""Small, reviewed meteor knowledge catalogue. No embeddings or network required."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

CATALOGUE = Path(__file__).with_name("meteor_knowledge.json")
STOP = {"ve", "ile", "bir", "bu", "ne", "neden", "icin", "nasıl", "nasil",
        "benim", "bizim", "olan", "olanlar", "is", "the", "and", "what"}


def tokens(value: str) -> set[str]:
    folded = unicodedata.normalize("NFKD", value.replace("ı", "i").replace("İ", "I"))
    folded = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return {s for s in re.findall(r"[a-z0-9]+", folded) if len(s) > 2 and s not in STOP}


def matches(one: set[str], other: set[str]) -> int:
    """Handle common Turkish suffixes without a tokenizer dependency."""
    return sum(1 for word in one if any(
        word == candidate or
        (len(word) >= 5 and len(candidate) >= 5 and
         (word.startswith(candidate) or candidate.startswith(word)))
        for candidate in other))


class Knowledge:
    def __init__(self, catalogue: Path = CATALOGUE):
        cards = json.loads(catalogue.read_text(encoding="utf-8"))
        if not isinstance(cards, list):
            raise ValueError("Knowledge catalogue is not a list")
        for card in cards:
            if (not isinstance(card, dict) or
                not all(isinstance(card.get(key), str)
                        for key in ("id", "title", "text", "source_url", "source_name")) or
                not isinstance(card.get("tags"), list)):
                raise ValueError("Invalid knowledge card")
        self.cards = cards

    def search(self, query: str, limit: int = 3) -> list[dict]:
        if not isinstance(query, str) or not 2 <= len(query.strip()) <= 240:
            raise ValueError("Knowledge query must be 2..240 characters")
        q = tokens(query)
        if not q:
            return []
        scored = []
        for card in self.cards:
            title_tags = tokens(card["title"] + " " + " ".join(card["tags"]))
            body = tokens(card["text"])
            # Exact match beats a loose Turkish suffix match: "meteorit"
            # must not rank an unrelated "meteor yörüngesi" card first.
            score = (10 * len(q & title_tags) + 3 * len(q & body)
                     + 3 * matches(q, title_tags) + matches(q, body))
            if score:
                scored.append((score, card["id"], card))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [{key: card[key] for key in
                 ("id", "title", "text", "source_url", "source_name")}
                for _, _, card in scored[:min(limit, 3)]]
