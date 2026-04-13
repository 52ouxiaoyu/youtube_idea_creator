from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def extract_video_id(video_url: str) -> str:
    """Extract a YouTube video id from common URL forms."""

    parsed = urlparse(video_url)
    host = parsed.netloc.lower()
    path = parsed.path.strip("/")

    if "youtu.be" in host and path:
        return path.split("/")[0]

    if "youtube.com" in host:
        if path == "watch":
            query = parse_qs(parsed.query)
            if "v" in query and query["v"]:
                return query["v"][0]
        if path.startswith("shorts/"):
            return path.split("/")[1]
        if path.startswith("embed/"):
            return path.split("/")[1]
        if path.startswith("live/"):
            return path.split("/")[1]

    raise ValueError(f"Unable to extract video id from URL: {video_url}")


def build_comment_url(video_id: str, comment_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"


def safe_truncate(text: str, max_chars: int) -> str:
    text = " ".join(text.split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + "…"

