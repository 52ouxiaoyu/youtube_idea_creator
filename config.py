from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os


STRONG_INTENT_KEYWORDS = [
    "how can i",
    "how do i",
    "wish there was",
    "is there a tool",
    "is there an app",
    "any solution",
    "need a way",
    "need a tool",
    "looking for",
    "can someone recommend",
    "does anyone know",
    "where can i",
    "i'm looking for",
    "want to automate",
    "tool for",
    "app for",
]

SOFT_INTENT_KEYWORDS = [
    "how to",
    "i need",
    "need help",
    "need advice",
    "need support",
]

PAIN_SIGNAL_KEYWORDS = [
    "manually",
    "expensive",
    "too expensive",
    "hard to",
    "workaround",
    "slow",
    "bug",
    "broken",
    "doesn't work",
    "does not work",
    "can't",
    "cannot",
    "unable",
    "time consuming",
    "waste of time",
]

PAIN_POINT_KEYWORDS = STRONG_INTENT_KEYWORDS + SOFT_INTENT_KEYWORDS + PAIN_SIGNAL_KEYWORDS

# Backward compatible alias used by older imports.
HIGH_INTENT_KEYWORDS = STRONG_INTENT_KEYWORDS


def _parse_csv_env(value: str | None, default: tuple[str, ...]) -> tuple[str, ...]:
    if not value:
        return default
    items = tuple(part.strip() for part in value.split(",") if part.strip())
    return items or default


@dataclass
class AppConfig:
    """Central configuration for the pipeline."""

    youtube_api_key: str = field(default_factory=lambda: os.getenv("YOUTUBE_API_KEY", ""))
    youtube_api_endpoint: str = field(default_factory=lambda: os.getenv("YOUTUBE_API_ENDPOINT", "https://www.googleapis.com"))
    ai_api_key: str = field(default_factory=lambda: os.getenv("AI_API_KEY", ""))
    ai_provider: str = field(default_factory=lambda: os.getenv("AI_PROVIDER", "openai"))
    ai_base_url: str = field(default_factory=lambda: os.getenv("AI_BASE_URL", ""))
    ai_model: str = field(default_factory=lambda: os.getenv("AI_MODEL", "gpt-4o-mini"))
    output_dir: Path = field(default_factory=lambda: Path(os.getenv("OUTPUT_DIR", "outputs")))
    batch_size: int = int(os.getenv("BATCH_SIZE", "8"))
    max_comment_chars: int = int(os.getenv("MAX_COMMENT_CHARS", "700"))
    max_comments_per_video: int = int(os.getenv("MAX_COMMENTS_PER_VIDEO", "25"))
    max_comment_pages_per_video: int = int(os.getenv("MAX_COMMENT_PAGES_PER_VIDEO", "20"))
    max_reply_pages_per_thread: int = int(os.getenv("MAX_REPLY_PAGES_PER_THREAD", "5"))
    target_high_signal_comments_per_video: int = int(os.getenv("TARGET_HIGH_SIGNAL_COMMENTS_PER_VIDEO", "12"))
    min_request_interval_seconds: float = float(os.getenv("MIN_REQUEST_INTERVAL_SECONDS", "1.0"))
    youtube_http_timeout_seconds: float = float(os.getenv("YOUTUBE_HTTP_TIMEOUT_SECONDS", "20"))
    max_retries: int = int(os.getenv("MAX_RETRIES", "4"))
    backoff_base_seconds: float = float(os.getenv("BACKOFF_BASE_SECONDS", "1.5"))
    filter_min_score: int = int(os.getenv("FILTER_MIN_SCORE", "3"))
    popular_fetch_multiplier: int = int(os.getenv("POPULAR_FETCH_MULTIPLIER", "3"))
    popular_analysis_floor: int = int(os.getenv("POPULAR_ANALYSIS_FLOOR", "3"))
    preferred_category_ids: tuple[str, ...] = field(
        default_factory=lambda: _parse_csv_env(os.getenv("PREFERRED_CATEGORY_IDS"), ("26", "27", "28"))
    )
    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    skip_preflight: bool = field(default_factory=lambda: os.getenv("SKIP_PREFLIGHT", "0") == "1")

    def ensure_output_dir(self) -> Path:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        return self.output_dir
