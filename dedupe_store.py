
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Set, Tuple

from .logging_utils import get_logger

logger = get_logger(__name__)


class DeduplicationStore:
    """Tracks processed videos and comments to avoid duplicate work."""

    def __init__(self, state_path: Path | None = None):
        self.state_path = state_path
        self.seen_video_ids: Set[str] = set()
        self.seen_comment_ids: Set[str] = set()

    @classmethod
    def load(cls, state_path: Path | None = None) -> "DeduplicationStore":
        """Load state from JSON file if it exists."""
        store = cls(state_path)
        if state_path and state_path.exists():
            try:
                with state_path.open("r", encoding="utf-8") as f:
                    data = json.load(f)
                store.seen_video_ids = set(data.get("seen_video_ids", []))
                store.seen_comment_ids = set(data.get("seen_comment_ids", []))
                logger.info(
                    "Loaded dedupe state: %s videos, %s comments",
                    len(store.seen_video_ids),
                    len(store.seen_comment_ids),
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to load dedupe state: %s", exc)
        else:
            logger.info("No dedupe state file found at %s", state_path)
        return store

    def save(self) -> None:
        """Save current state to JSON file."""
        if not self.state_path:
            return
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "seen_video_ids": sorted(self.seen_video_ids),
            "seen_comment_ids": sorted(self.seen_comment_ids),
        }
        try:
            with self.state_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            logger.debug("Saved dedupe state to %s", self.state_path)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to save dedupe state: %s", exc)

    def clear(self) -> None:
        """Clear all tracked IDs."""
        self.seen_video_ids.clear()
        self.seen_comment_ids.clear()
        logger.info("Cleared dedupe state")
        self.save()

    def is_seen_video(self, video_id: str) -> bool:
        return video_id in self.seen_video_ids

    def mark_video(self, video_id: str) -> None:
        self.seen_video_ids.add(video_id)

    def is_seen_comment(self, comment_id: str) -> bool:
        return comment_id in self.seen_comment_ids

    def mark_comment(self, comment_id: str) -> None:
        self.seen_comment_ids.add(comment_id)
