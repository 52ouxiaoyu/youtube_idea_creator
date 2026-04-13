from __future__ import annotations

import time
from urllib.parse import urlparse
from typing import Any, Callable, Dict, List, Optional

import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from .models import CommentRecord, PopularVideo
from .logging_utils import get_logger
from .rate_limiter import RateLimiter
from .utils import build_comment_url, extract_video_id


logger = get_logger(__name__)


class YouTubeCommentScraper:
    """Fetch a video's top-level comments and all replies."""

    def __init__(
        self,
        api_key: str,
        api_endpoint: str = "https://www.googleapis.com/youtube/v3",
        min_request_interval_seconds: float = 1.0,
        max_retries: int = 4,
        backoff_base_seconds: float = 1.5,
        http_timeout_seconds: float = 20.0,
    ):
        if not api_key:
            raise ValueError("YOUTUBE_API_KEY is missing.")
        self.api_key = api_key
        self.api_endpoint = self._normalize_api_endpoint(api_endpoint)
        self._service = None
        self.rate_limiter = RateLimiter(min_request_interval_seconds)
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.http_timeout_seconds = http_timeout_seconds

    @property
    def service(self):
        if self._service is None:
            # Static discovery avoids a network fetch during client construction.
            logger.info("building YouTube API client endpoint=%s", self.api_endpoint)
            self._service = build(
                "youtube",
                "v3",
                http=httplib2.Http(timeout=self.http_timeout_seconds),
                developerKey=self.api_key,
                client_options={"api_endpoint": self.api_endpoint},
                cache_discovery=False,
                static_discovery=True,
            )
        return self._service

    def _normalize_api_endpoint(self, api_endpoint: str) -> str:
        """googleapiclient expects the root API host, not the service path."""

        parsed = urlparse(api_endpoint)
        if not parsed.scheme:
            return "https://www.googleapis.com"
        return f"{parsed.scheme}://{parsed.netloc}"

    def fetch_all_comment_threads(
        self,
        video_url: str,
        max_comment_pages_per_video: int | None = None,
        max_reply_pages_per_thread: int | None = None,
        signal_scoring_fn: Callable[[str], int] | None = None,
        signal_threshold: int = 0,
        high_signal_target: int | None = None,
    ) -> List[CommentRecord]:
        """Public helper requested by the task: accepts a video URL and returns all comments."""

        video_id = extract_video_id(video_url)
        video_title = self._fetch_video_title(video_id)
        logger.info("fetching comments for video_id=%s title=%s", video_id, video_title)
        records: List[CommentRecord] = []
        seen_comment_ids: set[str] = set()
        high_signal_count = 0

        def record_signal(text: str) -> None:
            nonlocal high_signal_count
            if signal_scoring_fn is None or high_signal_target is None:
                return
            if signal_scoring_fn(text) >= signal_threshold:
                high_signal_count += 1

        def target_reached() -> bool:
            return high_signal_target is not None and high_signal_count >= high_signal_target

        page_token: Optional[str] = None
        page_index = 0
        while True:
            page_index += 1
            logger.info("requesting commentThreads page=%s video_id=%s", page_index, video_id)
            response = self._execute_with_retry(
                self.service.commentThreads()
                .list(
                    part="snippet,replies",
                    videoId=video_id,
                    maxResults=100,
                    pageToken=page_token,
                    textFormat="plainText",
                )
            )
            items = response.get("items", [])
            logger.info(
                "received commentThreads page=%s items=%s next_page=%s",
                page_index,
                len(items),
                bool(response.get("nextPageToken")),
            )

            for thread in items:
                top_comment = thread["snippet"]["topLevelComment"]
                top_record = self._thread_item_to_record(
                    video_id,
                    video_url,
                    video_title,
                    top_comment,
                    thread,
                    is_reply=False,
                )
                if top_record.comment_id not in seen_comment_ids:
                    records.append(top_record)
                    seen_comment_ids.add(top_record.comment_id)
                    record_signal(top_record.comment_text)

                thread_comment_count = int(thread["snippet"].get("totalReplyCount", 0) or 0)
                if thread_comment_count > 0 and not target_reached():
                    records.extend(
                        self._fetch_all_replies_for_thread(
                            video_id=video_id,
                            video_url=video_url,
                            video_title=video_title,
                            parent_comment_id=top_record.comment_id,
                            seen_comment_ids=seen_comment_ids,
                            signal_scoring_fn=signal_scoring_fn,
                            signal_threshold=signal_threshold,
                            high_signal_target=high_signal_target,
                            record_signal=record_signal,
                            target_reached=target_reached,
                            )
                    )

                if target_reached():
                    logger.info(
                        "adaptive high-signal target reached for video_id=%s high_signal_count=%s target=%s",
                        video_id,
                        high_signal_count,
                        high_signal_target,
                    )
                    break

            page_token = response.get("nextPageToken")
            if not page_token:
                logger.info("commentThreads complete for video_id=%s total_records=%s", video_id, len(records))
                break

            if max_comment_pages_per_video and page_index >= max_comment_pages_per_video:
                logger.warning(
                    "commentThreads page cap reached for video_id=%s max_comment_pages_per_video=%s total_records=%s",
                    video_id,
                    max_comment_pages_per_video,
                    len(records),
                )
                break

            if target_reached():
                break

        return records

    def _fetch_all_replies_for_thread(
        self,
        video_id: str,
        video_url: str,
        video_title: str,
        parent_comment_id: str,
        seen_comment_ids: set[str],
        max_reply_pages_per_thread: int | None = None,
        signal_scoring_fn: Callable[[str], int] | None = None,
        signal_threshold: int = 0,
        high_signal_target: int | None = None,
        record_signal: Callable[[str], None] | None = None,
        target_reached: Callable[[], bool] | None = None,
    ) -> List[CommentRecord]:
        records: List[CommentRecord] = []
        page_token: Optional[str] = None
        page_index = 0

        while True:
            page_index += 1
            logger.info(
                "requesting replies page=%s parent_comment_id=%s video_id=%s",
                page_index,
                parent_comment_id,
                video_id,
            )
            response = self._execute_with_retry(
                self.service.comments()
                .list(
                    part="snippet",
                    parentId=parent_comment_id,
                    maxResults=100,
                    pageToken=page_token,
                    textFormat="plainText",
                )
            )
            items = response.get("items", [])
            logger.info(
                "received replies page=%s items=%s next_page=%s",
                page_index,
                len(items),
                bool(response.get("nextPageToken")),
            )

            for reply in items:
                record = self._reply_item_to_record(video_id, video_url, video_title, reply, parent_comment_id)
                if record.comment_id in seen_comment_ids:
                    continue
                records.append(record)
                seen_comment_ids.add(record.comment_id)
                if signal_scoring_fn is not None and record_signal is not None:
                    if signal_scoring_fn(record.comment_text) >= signal_threshold:
                        record_signal(record.comment_text)
                if target_reached is not None and target_reached():
                    logger.info(
                        "adaptive high-signal target reached while fetching replies for parent_comment_id=%s target=%s",
                        parent_comment_id,
                        high_signal_target,
                    )
                    break

            page_token = response.get("nextPageToken")
            if not page_token:
                logger.info(
                    "replies complete for parent_comment_id=%s total_reply_records=%s",
                    parent_comment_id,
                    len(records),
                )
                break

            if max_reply_pages_per_thread and page_index >= max_reply_pages_per_thread:
                logger.warning(
                    "reply page cap reached for parent_comment_id=%s max_reply_pages_per_thread=%s total_reply_records=%s",
                    parent_comment_id,
                    max_reply_pages_per_thread,
                    len(records),
                )
                break

            if target_reached is not None and target_reached():
                break

        return records

    def _thread_item_to_record(
        self,
        video_id: str,
        video_url: str,
        video_title: str,
        top_comment: Dict[str, Any],
        thread: Dict[str, Any],
        is_reply: bool,
    ) -> CommentRecord:
        snippet = top_comment["snippet"]
        comment_id = top_comment["id"]
        return CommentRecord(
            video_id=video_id,
            video_url=video_url,
            video_title=video_title,
            comment_id=comment_id,
            comment_url=build_comment_url(video_id, comment_id),
            comment_text=(snippet.get("textDisplay") or "").strip(),
            author=(snippet.get("authorDisplayName") or "").strip(),
            like_count=int(snippet.get("likeCount", 0) or 0),
            published_at=(snippet.get("publishedAt") or "").strip(),
            is_reply=is_reply,
            parent_comment_id="",
            thread_id=thread.get("id", ""),
        )

    def _reply_item_to_record(
        self,
        video_id: str,
        video_url: str,
        video_title: str,
        reply: Dict[str, Any],
        parent_comment_id: str,
    ) -> CommentRecord:
        snippet = reply["snippet"]
        comment_id = reply["id"]
        return CommentRecord(
            video_id=video_id,
            video_url=video_url,
            video_title=video_title,
            comment_id=comment_id,
            comment_url=build_comment_url(video_id, comment_id),
            comment_text=(snippet.get("textDisplay") or "").strip(),
            author=(snippet.get("authorDisplayName") or "").strip(),
            like_count=int(snippet.get("likeCount", 0) or 0),
            published_at=(snippet.get("publishedAt") or "").strip(),
            is_reply=True,
            parent_comment_id=parent_comment_id,
            thread_id=parent_comment_id,
        )

    def _execute_with_retry(self, request: Any) -> Dict[str, Any]:
        """Execute an API request with rate limiting and exponential backoff."""

        last_error: Optional[Exception] = None
        for attempt in range(self.max_retries + 1):
            self.rate_limiter.wait()
            try:
                logger.debug("executing request attempt=%s", attempt + 1)
                return request.execute()
            except HttpError as exc:
                last_error = exc
                status = getattr(exc.resp, "status", None)
                logger.warning(
                    "http error status=%s attempt=%s/%s: %s",
                    status,
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                if status not in {403, 429, 500, 503} or attempt == self.max_retries:
                    raise
                time.sleep(self.backoff_base_seconds * (2**attempt))
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "request failed attempt=%s/%s: %s",
                    attempt + 1,
                    self.max_retries + 1,
                    exc,
                )
                if attempt == self.max_retries:
                    raise
                time.sleep(self.backoff_base_seconds * (2**attempt))

        if last_error:
            raise last_error
        raise RuntimeError("Unexpected API retry state.")

    def fetch_most_popular_videos(
        self,
        region_code: str = "US",
        max_results: int = 10,
        category_id: Optional[str] = None,
    ) -> List[PopularVideo]:
        """Fetch the most popular videos for a region.

        YouTube's "most popular" feed is region-dependent, so region_code matters.
        """

        max_results = max(1, min(50, int(max_results)))
        logger.info(
            "requesting mostPopular videos region=%s max_results=%s category=%s",
            region_code,
            max_results,
            category_id or "all",
        )
        request = self.service.videos().list(
            part="snippet,statistics",
            chart="mostPopular",
            regionCode=region_code,
            maxResults=max_results,
            pageToken=None,
            videoCategoryId=category_id,
        )
        response = self._execute_with_retry(request)

        results: List[PopularVideo] = []
        for item in response.get("items", []):
            video_id = item.get("id", "")
            snippet = item.get("snippet", {})
            statistics = item.get("statistics", {})
            if not video_id:
                continue

            results.append(
                PopularVideo(
                    video_id=video_id,
                    video_url=f"https://www.youtube.com/watch?v={video_id}",
                    title=(snippet.get("title") or "").strip(),
                    category_id=(snippet.get("categoryId") or "").strip(),
                    channel_title=(snippet.get("channelTitle") or "").strip(),
                    view_count=int(statistics.get("viewCount", 0) or 0),
                )
            )

        logger.info("received %s popular videos for region=%s", len(results), region_code)
        return results

    def _fetch_video_title(self, video_id: str) -> str:
        logger.info("requesting video metadata for video_id=%s", video_id)
        request = self.service.videos().list(
            part="snippet",
            id=video_id,
            maxResults=1,
        )
        response = self._execute_with_retry(request)
        items = response.get("items", [])
        if not items:
            logger.warning("no metadata found for video_id=%s", video_id)
            return video_id
        snippet = items[0].get("snippet", {})
        title = (snippet.get("title") or video_id).strip()
        logger.info("video metadata resolved video_id=%s title=%s", video_id, title)
        return title
