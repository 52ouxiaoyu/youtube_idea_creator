from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class CommentRecord:
    """One YouTube comment or reply."""

    video_id: str
    video_url: str
    video_title: str
    comment_id: str
    comment_url: str
    comment_text: str
    author: str = ""
    like_count: int = 0
    published_at: str = ""
    is_reply: bool = False
    parent_comment_id: str = ""
    thread_id: str = ""


@dataclass
class FilteredComment:
    """A comment that matched the pain-point keywords."""

    record: CommentRecord
    matched_keywords: List[str] = field(default_factory=list)
    signal_score: int = 0


@dataclass
class AnalysisItem:
    """Final structured result for export."""

    video_title: str
    video_url: str
    comment_url: str
    pain_point: str
    tool_concept: str
    difficulty_stars: int
    signal_score: int = 0
    raw_comment: str = ""
    video_id: str = ""
    comment_id: str = ""
    matched_keywords: str = ""
    comment_language: str = ""


@dataclass
class PopularVideo:
    """A video returned from the 'mostPopular' chart."""

    video_id: str
    video_url: str
    title: str
    category_id: str = ""
    channel_title: str = ""
    view_count: int = 0
