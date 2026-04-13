from __future__ import annotations

import argparse
import asyncio
from datetime import datetime
from pathlib import Path
import os

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional dependency
    def load_dotenv() -> bool:  # type: ignore[return-type]
        return False

from .analyzer import AIAnalyzer
from .config import AppConfig
from .exporter import ResultExporter
from .logging_utils import configure_logging, get_logger, timed_step
from .preflight import run_preflight_checks
from .pain_filter import PainPointFilter
from .dedupe_store import DeduplicationStore
from .youtube_client import YouTubeCommentScraper


logger = get_logger(__name__)


class IdeaCreatorYouTubeEdition:
    """Orchestrates the end-to-end workflow."""

    def __init__(self, config: AppConfig):
        self.config = config
        self.scraper = YouTubeCommentScraper(
            api_key=config.youtube_api_key,
            api_endpoint=config.youtube_api_endpoint,
            min_request_interval_seconds=config.min_request_interval_seconds,
            max_retries=config.max_retries,
            backoff_base_seconds=config.backoff_base_seconds,
            http_timeout_seconds=config.youtube_http_timeout_seconds,
        )
        self.filter = PainPointFilter(min_signal_score=config.filter_min_score)
        self.analyzer = AIAnalyzer(
            provider=config.ai_provider,
            api_key=config.ai_api_key,
            model=config.ai_model,
            base_url=config.ai_base_url,
        )
        self.exporter = ResultExporter()
        self.dedupe_store = DeduplicationStore.load(config.dedupe_state_path)
        if config.reset_dedupe:
            self.dedupe_store.clear()

    async def run_single(self, video_url: str) -> Path:
        with timed_step(logger, "single-video pipeline"):
            comments = self.scraper.fetch_all_comment_threads(
                video_url,
                max_comment_pages_per_video=self.config.max_comment_pages_per_video,
                max_reply_pages_per_thread=self.config.max_reply_pages_per_thread,
                signal_scoring_fn=self.filter.score_comment_text,
                signal_threshold=self.config.filter_min_score,
                high_signal_target=self.config.target_high_signal_comments_per_video,
            )
            logger.info("scraped %s comments and replies", len(comments))

            filtered = self.filter.filter(comments)
            logger.info("keyword filter kept %s comments", len(filtered))

            analysis = await self._analyze_and_export(filtered, suffix="single")

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            md_path = self.config.output_dir / f"idea_creator_youtube_{timestamp}.md"
            xlsx_path = self.config.output_dir / f"idea_creator_youtube_{timestamp}.xlsx"
            self.exporter.save_markdown(analysis, md_path)
            self.exporter.save_xlsx(analysis, xlsx_path)
            logger.info("saved markdown: %s", md_path)
            logger.info("saved xlsx: %s", xlsx_path)
            return md_path

    async def run_popular(
        self,
        region_code: str = "US",
        popular_count: int = 10,
        category_id: str | None = None,
        max_comments_per_video: int = 25,
    ) -> Path:
        analysis_target_count = max(popular_count, self.config.popular_analysis_floor)
        with timed_step(logger, f"popular-videos pipeline region={region_code} count={analysis_target_count}"):
            logger.info(
                "fetching top %s popular videos for region=%s category=%s (requested=%s, analysis_floor=%s)",
                analysis_target_count,
                region_code,
                category_id or "all",
                popular_count,
                self.config.popular_analysis_floor,
            )
            fetch_count = min(
                30,
                max(
                    analysis_target_count,
                    analysis_target_count * self.config.popular_fetch_multiplier,
                ),
            )
            popular_videos = self.scraper.fetch_most_popular_videos(
                region_code=region_code,
                max_results=fetch_count,
                category_id=category_id,
            )
            if category_id:
                selected_videos = popular_videos[:analysis_target_count]
            else:
                selected_videos = self._select_popular_videos_for_analysis(
                    popular_videos,
                    analysis_target_count,
                    self.config.preferred_category_ids,
                    self.config.deprioritized_category_ids,
                    self.dedupe_store.seen_video_ids,
                )
            skipped_seen_videos = sum(1 for video in popular_videos if self.dedupe_store.is_seen_video(video.video_id))
            logger.info(
                "fetched %s popular videos, selected %s for analysis (preferred categories=%s, deprioritized categories=%s, skipped_seen=%s)",
                len(popular_videos),
                len(selected_videos),
                ",".join(self.config.preferred_category_ids),
                ",".join(self.config.deprioritized_category_ids),
                skipped_seen_videos,
            )

            all_comments = []
            for idx, video in enumerate(selected_videos, start=1):
                logger.info(
                    "(%s/%s) scraping video: %s | channel=%s | views=%s | id=%s",
                    idx,
                    len(selected_videos),
                    video.title,
                    video.channel_title or "unknown",
                    video.view_count,
                    video.video_id,
                )
                video_comments = self.scraper.fetch_all_comment_threads(
                    video.video_url,
                    max_comment_pages_per_video=self.config.max_comment_pages_per_video,
                    max_reply_pages_per_thread=self.config.max_reply_pages_per_thread,
                    signal_scoring_fn=self.filter.score_comment_text,
                    signal_threshold=self.config.filter_min_score,
                    high_signal_target=self.config.target_high_signal_comments_per_video,
                )
                logger.info(
                    "video done: %s comments/replies collected for %s",
                    len(video_comments),
                    video.title,
                )
                new_comments = []
                for comment in video_comments:
                    if self.dedupe_store.is_seen_comment(comment.comment_id):
                        continue
                    new_comments.append(comment)
                    self.dedupe_store.mark_comment(comment.comment_id)

                self.dedupe_store.mark_video(video.video_id)
                all_comments.extend(new_comments)
                self.dedupe_store.save()
                logger.info(
                    "dedupe filtered video=%s kept_new_comments=%s seen_comments=%s",
                    video.video_id,
                    len(new_comments),
                    len(video_comments) - len(new_comments),
                )

            logger.info("total comments collected before filter: %s", len(all_comments))
            filtered = self.filter.filter(all_comments)
            logger.info("keyword filter kept %s comments", len(filtered))

            if max_comments_per_video > 0:
                filtered = self._limit_comments_per_video(filtered, max_comments_per_video)
                logger.info(
                    "per-video cap applied: %s comments remain",
                    len(filtered),
                )

            analysis = await self._analyze_and_export(
                filtered,
                suffix=f"popular_{region_code}_{len(selected_videos)}",
            )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            md_path = self.config.output_dir / f"idea_creator_youtube_popular_{region_code}_{timestamp}.md"
            xlsx_path = self.config.output_dir / f"idea_creator_youtube_popular_{region_code}_{timestamp}.xlsx"
            self.exporter.save_markdown(analysis, md_path)
            self.exporter.save_xlsx(analysis, xlsx_path)
            logger.info("saved markdown: %s", md_path)
            logger.info("saved xlsx: %s", xlsx_path)
            return md_path

    async def _analyze_and_export(self, filtered, suffix: str):
        logger.info(
            "starting AI analysis for %s comments in batches of %s",
            len(filtered),
            self.config.batch_size,
        )
        analysis = await self.analyzer.analyze_batches(
            filtered,
            batch_size=self.config.batch_size,
            max_comment_chars=self.config.max_comment_chars,
        )
        logger.info("generated %s structured items (%s)", len(analysis), suffix)
        return analysis

    def _limit_comments_per_video(self, filtered, max_comments_per_video: int):
        grouped = {}
        for item in filtered:
            grouped.setdefault(item.record.video_id, []).append(item)

        limited = []
        for video_id, items in grouped.items():
            # Keep the highest-signal comments per video first.
            items.sort(key=lambda item: (item.signal_score, item.record.like_count, len(item.matched_keywords)), reverse=True)
            limited.extend(items[:max_comments_per_video])

        return limited

    def _select_popular_videos_for_analysis(
        self,
        videos,
        target_count: int,
        preferred_category_ids,
        deprioritized_category_ids,
        seen_video_ids,
    ):
        preferred_ids = {str(category_id) for category_id in preferred_category_ids if str(category_id)}
        deprioritized_ids = {str(category_id) for category_id in deprioritized_category_ids if str(category_id)}
        seen_ids = {str(video_id) for video_id in seen_video_ids if str(video_id)}
        if not preferred_ids:
            preferred_ids = set()

        def category_rank(video):
            category_id = str(video.category_id or "")
            if category_id in preferred_ids:
                return 0
            if category_id in deprioritized_ids:
                return 2
            return 1

        ranked_videos = sorted(
            videos,
            key=lambda video: (
                category_rank(video),
                -int(getattr(video, "view_count", 0) or 0),
            ),
        )
        unseen_videos = [video for video in ranked_videos if video.video_id not in seen_ids]
        return unseen_videos[:target_count]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Idea Creator - YouTube Edition")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--video-url", help="单个 YouTube 视频 URL")
    group.add_argument("--popular-mode", action="store_true", help="自动抓取热门视频评论")
    parser.add_argument("--output-dir", default=None, help="输出目录")
    parser.add_argument("--batch-size", type=int, default=None, help="AI 批次大小")
    parser.add_argument("--model", default=None, help="AI 模型名")
    parser.add_argument("--provider", default=None, choices=["openai", "ollama", "gemini"], help="AI 提供商")
    parser.add_argument("--region-code", default="US", help="热门视频地区代码，默认 US")
    parser.add_argument("--popular-count", type=int, default=10, help="热门视频数量，默认 10")
    parser.add_argument("--category-id", default=None, help="可选的视频分类 ID")
    parser.add_argument("--max-comments-per-video", type=int, default=25, help="每个视频最多保留多少条评论用于分析")
    parser.add_argument("--skip-preflight", action="store_true", help="跳过启动前网络检查")
    parser.add_argument("--reset-dedupe", action="store_true", help="重置已处理视频/评论缓存")
    return parser


async def run_cli() -> None:
    load_dotenv()
    args = build_parser().parse_args()
    config = AppConfig()

    if args.output_dir:
        config.output_dir = Path(args.output_dir)
    if args.batch_size is not None:
        config.batch_size = args.batch_size
    if args.model:
        config.ai_model = args.model
    if args.provider:
        config.ai_provider = args.provider
    if args.max_comments_per_video is not None:
        config.max_comments_per_video = args.max_comments_per_video
    if args.skip_preflight:
        config.skip_preflight = True
    if args.reset_dedupe:
        config.reset_dedupe = True
    if not os.getenv("DEDUPE_STATE_PATH"):
        config.dedupe_state_path = config.output_dir / "idea_creator_seen.json"

    configure_logging(config.log_level)
    logger.info(
        "runtime config: provider=%s model=%s batch_size=%s max_comments_per_video=%s output_dir=%s log_level=%s",
        config.ai_provider,
        config.ai_model,
        config.batch_size,
        config.max_comments_per_video,
        config.output_dir,
        config.log_level,
    )
    config.ensure_output_dir()
    logger.info("output directory ready: %s", config.output_dir)

    if not config.skip_preflight:
        run_preflight_checks(config)
    else:
        logger.info("preflight checks skipped")

    app = IdeaCreatorYouTubeEdition(config)
    if args.popular_mode:
        logger.info(
            "mode=popular region=%s popular_count=%s max_comments_per_video=%s category=%s dedupe_state=%s reset_dedupe=%s",
            args.region_code,
            args.popular_count,
            config.max_comments_per_video,
            args.category_id or "all",
            config.dedupe_state_path,
            config.reset_dedupe,
        )
        await app.run_popular(
            region_code=args.region_code,
            popular_count=args.popular_count,
            category_id=args.category_id,
            max_comments_per_video=config.max_comments_per_video,
        )
    else:
        logger.info("mode=single video_url=%s", args.video_url)
        await app.run_single(args.video_url)


def main() -> None:
    asyncio.run(run_cli())
