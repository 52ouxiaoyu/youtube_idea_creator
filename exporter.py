from __future__ import annotations

from pathlib import Path
from typing import Iterable, List

from openpyxl import Workbook

from .models import AnalysisItem


class ResultExporter:
    """Export analysis results to Markdown and Excel."""

    def save_markdown(self, items: Iterable[AnalysisItem], output_path: Path) -> Path:
        rows = list(items)

        def esc(text: str) -> str:
            return str(text).replace("|", "\\|").replace("\n", " ").strip()

        lines = [
            "# Idea Creator - YouTube Edition",
            "",
            "| 视频标题 | 原评论内容 | 原评论语言 | 需求分数 | 原评论链接 | 痛点总结 | AI 工具构思 | 预计开发难度 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]

        for item in rows:
            lines.append(
                "| "
                + " | ".join(
                    [
                        esc(item.video_title or item.video_id),
                        esc(item.raw_comment),
                        esc(item.comment_language or "未知"),
                        str(item.signal_score),
                        f"[link]({item.comment_url})",
                        esc(item.pain_point),
                        esc(item.tool_concept),
                        "★" * max(1, min(5, item.difficulty_stars)),
                    ]
                )
                + " |"
            )

        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return output_path

    def save_xlsx(self, items: Iterable[AnalysisItem], output_path: Path) -> Path:
        rows = list(items)

        wb = Workbook()
        ws = wb.active
        ws.title = "Idea Creator"
        headers = [
            "视频标题",
            "原评论内容",
            "原评论语言",
            "需求分数",
            "原评论链接",
            "痛点总结",
            "AI 工具构思",
            "预计开发难度",
        ]
        ws.append(headers)

        for item in rows:
            ws.append(
                [
                    item.video_title or item.video_id,
                    item.raw_comment,
                    item.comment_language or "未知",
                    item.signal_score,
                    item.comment_url,
                    item.pain_point,
                    item.tool_concept,
                    "★" * max(1, min(5, item.difficulty_stars)),
                ]
            )

        # Make the sheet a bit easier to read when opened directly.
        widths = {
            "A": 42,
            "B": 80,
            "C": 55,
            "D": 35,
            "E": 45,
            "F": 14,
        }
        for col, width in widths.items():
            ws.column_dimensions[col].width = width

        wb.save(output_path)
        return output_path
