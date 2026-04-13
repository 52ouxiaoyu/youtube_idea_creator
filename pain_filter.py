from __future__ import annotations

from typing import Iterable, List

from .config import (
    PAIN_POINT_KEYWORDS,
    PAIN_SIGNAL_KEYWORDS,
    SOFT_INTENT_KEYWORDS,
    STRONG_INTENT_KEYWORDS,
)
from .models import CommentRecord, FilteredComment


class PainPointFilter:
    """Keyword-based first-pass filter to reduce token spend."""

    def __init__(self, keywords: List[str] | None = None, min_signal_score: int = 4):
        self.keywords = [kw.lower() for kw in (keywords or PAIN_POINT_KEYWORDS)]
        self.strong_intent_keywords = [kw.lower() for kw in STRONG_INTENT_KEYWORDS]
        self.soft_intent_keywords = [kw.lower() for kw in SOFT_INTENT_KEYWORDS]
        self.pain_signal_keywords = [kw.lower() for kw in PAIN_SIGNAL_KEYWORDS]
        self.min_signal_score = min_signal_score

    def filter(self, comments: Iterable[CommentRecord]) -> List[FilteredComment]:
        results: List[FilteredComment] = []
        seen: set[str] = set()

        for comment in comments:
            text = " ".join(comment.comment_text.split()).lower()
            if not text:
                continue

            if text in seen:
                continue
            seen.add(text)

            signal_score = self.score_comment_text(text)

            if signal_score < self._min_signal_score():
                continue

            matched = [kw for kw in self.keywords if kw in text]
            results.append(
                FilteredComment(
                    record=comment,
                    matched_keywords=matched,
                    signal_score=signal_score,
                )
            )

        # Higher-like comments first, but prefer comments with clearer intent signals.
        results.sort(
            key=lambda item: (
                item.signal_score,
                len(item.matched_keywords),
                item.record.like_count,
            ),
            reverse=True,
        )
        return results

    def _score_comment(
        self,
        text: str,
        strong_intent_hits: List[str],
        soft_intent_hits: List[str],
        pain_signal_hits: List[str],
        question_hits: int,
    ) -> int:
        words = text.split()
        score = 0
        score += 4 * len(strong_intent_hits)
        score += 1 * len(soft_intent_hits)
        score += 1 * len(pain_signal_hits)
        score += 1 * question_hits

        if len(words) <= 4:
            score -= 2
        elif len(words) <= 7:
            score -= 1

        if len(strong_intent_hits) == 1 and not pain_signal_hits and len(words) <= 8:
            score -= 1

        if any(token in text for token in ("recommend", "solution", "alternative", "automate", "app", "tool")):
            score += 1

        return score

    def _min_signal_score(self) -> int:
        return self.min_signal_score

    def score_comment_text(self, text: str) -> int:
        normalized = " ".join(text.split()).lower()
        if not normalized:
            return 0

        strong_intent_hits = [kw for kw in self.strong_intent_keywords if kw in normalized]
        soft_intent_hits = [kw for kw in self.soft_intent_keywords if kw in normalized]
        pain_signal_hits = [kw for kw in self.pain_signal_keywords if kw in normalized]
        question_hits = 1 if "?" in normalized or "？" in normalized else 0
        return self._score_comment(
            normalized,
            strong_intent_hits,
            soft_intent_hits,
            pain_signal_hits,
            question_hits,
        )

    def is_high_signal_comment(self, text: str) -> bool:
        return self.score_comment_text(text) >= self.min_signal_score
