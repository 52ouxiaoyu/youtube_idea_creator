from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import re
from urllib.parse import urlsplit, urlunsplit
from typing import Any, Dict, List, Sequence

import httpx
from openai import AsyncOpenAI

try:
    import google.generativeai as genai
except ImportError:  # pragma: no cover - optional provider
    genai = None  # type: ignore[assignment]

from .models import AnalysisItem, FilteredComment
from .logging_utils import get_logger
from .utils import safe_truncate


logger = get_logger(__name__)

OLLAMA_CLI_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_CLI_TIMEOUT_SECONDS", "900"))
OLLAMA_HTTP_TIMEOUT_SECONDS = float(os.getenv("OLLAMA_HTTP_TIMEOUT_SECONDS", "180"))
OLLAMA_THINK = os.getenv("OLLAMA_THINK", "false").lower() == "true"


class AIAnalyzer:
    """Analyze filtered comments with OpenAI, Ollama, or Gemini."""

    def __init__(self, provider: str, api_key: str, model: str, base_url: str = ""):
        self.provider = provider.lower().strip()
        self.api_key = api_key or ("ollama" if self.provider == "ollama" else "")
        self.model = model
        self.base_url = base_url or ("http://localhost:11434/v1" if self.provider == "ollama" else "")
        self._ollama_api_root = self._normalize_ollama_api_root(self.base_url)
        self._ollama_bin = shutil.which("ollama")
        self._openai_client = (
            AsyncOpenAI(api_key=self.api_key, base_url=self.base_url or None)
            if self.provider == "openai"
            else None
        )
        self._gemini_model = None

        if self.provider == "gemini":
            if not self.api_key:
                raise ValueError("AI_API_KEY is missing for Gemini.")
            if genai is None:
                raise ImportError("google-generativeai is required for provider='gemini'.")
            genai.configure(api_key=self.api_key)
            self._gemini_model = genai.GenerativeModel(model_name=model)
        elif self.provider in {"openai", "ollama"}:
            if self.provider == "openai" and not self.api_key:
                raise ValueError("AI_API_KEY is missing for OpenAI.")
        else:
            raise ValueError("AI_PROVIDER must be one of: openai, ollama, gemini.")

    async def analyze_batches(
        self,
        comments: Sequence[FilteredComment],
        batch_size: int = 8,
        max_comment_chars: int = 700,
        max_in_flight: int = 3,
    ) -> List[AnalysisItem]:
        if not comments:
            logger.info("no filtered comments to analyze")
            return []

        batches = [comments[i : i + batch_size] for i in range(0, len(comments), batch_size)]
        logger.info(
            "preparing AI analysis provider=%s model=%s comments=%s batches=%s batch_size=%s",
            self.provider,
            self.model,
            len(comments),
            len(batches),
            batch_size,
        )
        semaphore = asyncio.Semaphore(max_in_flight)
        tasks = [self._analyze_batch(batch, semaphore, max_comment_chars) for batch in batches]
        nested = await asyncio.gather(*tasks)

        flattened: List[AnalysisItem] = []
        for batch_items in nested:
            flattened.extend(batch_items)
        return flattened

    async def _analyze_batch(
        self,
        batch: Sequence[FilteredComment],
        semaphore: asyncio.Semaphore,
        max_comment_chars: int,
    ) -> List[AnalysisItem]:
        async with semaphore:
            batch_id = batch[0].record.comment_id if batch else "empty"
            logger.info("analyzing batch size=%s sample_comment_id=%s", len(batch), batch_id)
            payload = [
                {
                    "index": idx,
                    "comment_id": item.record.comment_id,
                    "comment_url": item.record.comment_url,
                    "video_title": item.record.video_title,
                    "video_url": item.record.video_url,
                    "comment": safe_truncate(item.record.comment_text, max_comment_chars),
                    "matched_keywords": item.matched_keywords,
                    "like_count": item.record.like_count,
                    "signal_score": item.signal_score,
                }
                for idx, item in enumerate(batch, start=1)
            ]

            prompt = {
                "task": "提炼真实需求，输出中文 JSON。",
                "output_schema": {
                    "items": [
                        {
                            "index": 1,
                            "pain_point": "具体麻烦",
                            "tool_concept": "核心功能",
                            "difficulty_stars": 3,
                        }
                    ]
                },
                "rules": [
                    "只返回 JSON。",
                    "顶层必须只有 items 字段。",
                    "每一项必须包含 index, pain_point, tool_concept, difficulty_stars。",
                    "pain_point 和 tool_concept 必须全部使用中文。",
                    "pain_point 只写具体麻烦，不要泛化。",
                    "tool_concept 只写核心功能，不要展开实现细节。",
                    "difficulty_stars 只能是 1 到 5 的整数星级。",
                    "不要臆造评论里没有的信息。",
                ],
                "comments": payload,
            }

            for attempt in range(3):
                try:
                    if self.provider == "openai":
                        results = await self._analyze_with_openai(batch, prompt)
                    elif self.provider == "ollama":
                        results = await self._analyze_with_ollama(batch, prompt)
                    else:
                        results = await self._analyze_with_gemini(batch, prompt)
                    logger.info("batch analyzed successfully size=%s", len(results))
                    return results
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        logger.exception("batch failed after retries: %s", exc)
                        return self._fallback(batch)
                    logger.warning(
                        "batch attempt=%s failed, retrying: %s",
                        attempt + 1,
                        exc,
                    )
                    await asyncio.sleep(2**attempt)

            return self._fallback(batch)

    async def _analyze_with_openai(
        self,
        batch: Sequence[FilteredComment],
        prompt: Dict[str, Any],
    ) -> List[AnalysisItem]:
        assert self._openai_client is not None
        logger.info("sending batch to provider=%s model=%s items=%s", self.provider, self.model, len(batch))
        request_kwargs: Dict[str, Any] = {
            "model": self.model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": "你是一位产品分析师。只用中文输出，只返回合法 JSON。",
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
        }
        # OpenAI's JSON mode is great when available, but local OpenAI-compatible
        # servers like Ollama may not always support it consistently.
        if self.provider == "openai":
            request_kwargs["response_format"] = {"type": "json_object"}

        response = await self._openai_client.chat.completions.create(**request_kwargs)
        content = response.choices[0].message.content or "{}"
        logger.info("AI response received provider=%s raw_chars=%s", self.provider, len(content))
        parsed = self._safe_json_loads(content)
        return self._map_results(batch, parsed)

    async def _analyze_with_ollama(
        self,
        batch: Sequence[FilteredComment],
        prompt: Dict[str, Any],
    ) -> List[AnalysisItem]:
        if self._ollama_bin:
            try:
                return await self._analyze_with_ollama_cli(batch, prompt)
            except Exception as exc:  # noqa: BLE001
                logger.warning("ollama CLI attempt failed, falling back to HTTP API: %s", exc)

        try:
            return await self._analyze_with_ollama_http(batch, prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ollama HTTP API attempt failed: %s", exc)
            raise

    async def _analyze_with_ollama_cli(
        self,
        batch: Sequence[FilteredComment],
        prompt: Dict[str, Any],
    ) -> List[AnalysisItem]:
        assert self._ollama_bin is not None
        logger.info("sending batch to provider=%s model=%s items=%s via CLI", self.provider, self.model, len(batch))
        prompt_text = json.dumps(prompt, ensure_ascii=False)

        def _run_cli() -> str:
            cmd = [self._ollama_bin, "run", self.model, "--format", "json"]
            if OLLAMA_THINK:
                cmd.append("--think")
            else:
                cmd.extend(["--think=false", "--hidethinking"])
            proc = subprocess.run(
                cmd + [prompt_text],
                capture_output=True,
                text=True,
                check=False,
                timeout=OLLAMA_CLI_TIMEOUT_SECONDS,
            )
            if proc.returncode != 0:
                stderr = (proc.stderr or "").strip()
                raise RuntimeError(stderr or f"ollama CLI exited with code {proc.returncode}")
            return proc.stdout.strip()

        content = await asyncio.to_thread(_run_cli)
        logger.info("AI response received provider=%s raw_chars=%s", self.provider, len(content))
        parsed = self._safe_json_loads(content)
        return self._map_results(batch, parsed)

    async def _analyze_with_ollama_http(
        self,
        batch: Sequence[FilteredComment],
        prompt: Dict[str, Any],
    ) -> List[AnalysisItem]:
        logger.info("sending batch to provider=%s model=%s items=%s", self.provider, self.model, len(batch))
        payload = {
            "model": self.model,
            "system": "你是一位产品分析师。只用中文输出，只返回合法 JSON。",
            "prompt": json.dumps(prompt, ensure_ascii=False),
            "stream": False,
            "format": "json",
            "think": OLLAMA_THINK,
        }

        url = f"{self._ollama_api_root}/api/generate"
        async with httpx.AsyncClient(timeout=OLLAMA_HTTP_TIMEOUT_SECONDS) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()

        body = response.json()
        content = body.get("response") or "{}"
        logger.info("AI response received provider=%s raw_chars=%s", self.provider, len(content))
        parsed = self._safe_json_loads(content)
        return self._map_results(batch, parsed)

    async def _analyze_with_gemini(
        self,
        batch: Sequence[FilteredComment],
        prompt: Dict[str, Any],
    ) -> List[AnalysisItem]:
        assert self._gemini_model is not None
        logger.info("sending batch to provider=%s model=%s items=%s", self.provider, self.model, len(batch))
        response = await asyncio.to_thread(
            self._gemini_model.generate_content,
            json.dumps(prompt, ensure_ascii=False),
        )
        content = getattr(response, "text", "") or "{}"
        logger.info("AI response received provider=%s raw_chars=%s", self.provider, len(content))
        parsed = self._safe_json_loads(content)
        return self._map_results(batch, parsed)

    def _safe_json_loads(self, text: str) -> Dict[str, Any]:
        cleaned = self._clean_response_text(text)
        try:
            parsed = json.loads(cleaned)
            return self._normalize_parsed_payload(parsed)
        except json.JSONDecodeError:
            logger.warning("AI response was not valid JSON, attempting to recover")
            candidates = self._json_candidates(cleaned)
            for candidate in candidates:
                try:
                    parsed = json.loads(candidate)
                    return self._normalize_parsed_payload(parsed)
                except json.JSONDecodeError:
                    continue
            try:
                import ast

                parsed = ast.literal_eval(candidates[0] if candidates else cleaned)
                return self._normalize_parsed_payload(parsed)
            except Exception:
                preview = cleaned[:240].replace("\n", " ")
                logger.warning("AI malformed payload preview: %s", preview)
                return {}

    def _clean_response_text(self, text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = cleaned.replace("\u201c", '"').replace("\u201d", '"').replace("\u2019", "'")
        cleaned = re.sub(r",\s*([}\]])", r"\1", cleaned)
        return cleaned

    def _json_candidates(self, text: str) -> List[str]:
        candidates: List[str] = []
        starts = [idx for idx in (text.find("{"), text.find("[")) if idx != -1]
        if not starts:
            return candidates

        for start in sorted(starts):
            for end_char in ("}", "]"):
                end = text.rfind(end_char)
                if end > start:
                    candidates.append(text[start : end + 1])
        if text.startswith("{") or text.startswith("["):
            candidates.insert(0, text)
        # Deduplicate while keeping order.
        seen = set()
        unique_candidates = []
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                unique_candidates.append(candidate)
        return unique_candidates

    def _normalize_parsed_payload(self, parsed: Any) -> Dict[str, Any]:
        if isinstance(parsed, dict):
            if "items" in parsed and isinstance(parsed["items"], list):
                return parsed
            if isinstance(parsed.get("results"), list):
                return {"items": parsed["results"]}
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
        return {}

    def _map_results(self, batch: Sequence[FilteredComment], parsed: Dict[str, Any]) -> List[AnalysisItem]:
        items = parsed.get("items", [])
        if not isinstance(items, list):
            logger.warning("AI payload missing items array, falling back")
            return self._fallback(batch)

        by_index = {idx: item for idx, item in enumerate(batch, start=1)}
        results: List[AnalysisItem] = []

        for raw in items:
            if not isinstance(raw, dict):
                continue

            index = int(raw.get("index", 0) or 0)
            source = by_index.get(index)
            if source is None:
                continue

            difficulty = raw.get("difficulty_stars", 3)
            try:
                difficulty_int = int(difficulty)
            except (TypeError, ValueError):
                difficulty_int = 3

            results.append(
                AnalysisItem(
                    video_title=source.record.video_title,
                    video_url=source.record.video_url,
                    comment_url=source.record.comment_url,
                    pain_point=str(raw.get("pain_point", "")).strip() or "未能稳定提炼痛点",
                    tool_concept=str(raw.get("tool_concept", "")).strip() or "建议进一步人工复核工具方向",
                    difficulty_stars=max(1, min(5, difficulty_int)),
                    signal_score=source.signal_score,
                    raw_comment=source.record.comment_text,
                    video_id=source.record.video_id,
                    comment_id=source.record.comment_id,
                    matched_keywords=", ".join(source.matched_keywords),
                    comment_language=self._detect_comment_language(source.record.comment_text),
                )
            )

        if not results:
            logger.warning("AI returned no mappable items, falling back")
            return self._fallback(batch)
        return results

    def _fallback(self, batch: Sequence[FilteredComment]) -> List[AnalysisItem]:
        results: List[AnalysisItem] = []
        for item in batch:
            results.append(
                AnalysisItem(
                    video_title=item.record.video_title,
                    video_url=item.record.video_url,
                    comment_url=item.record.comment_url,
                    pain_point=self._fallback_pain_point_cn(item),
                    tool_concept=self._fallback_tool_concept_cn(item),
                    difficulty_stars=min(5, max(1, 2 + len(item.matched_keywords) // 2)),
                    signal_score=item.signal_score,
                    raw_comment=item.record.comment_text,
                    video_id=item.record.video_id,
                    comment_id=item.record.comment_id,
                    matched_keywords=", ".join(item.matched_keywords),
                    comment_language=self._detect_comment_language(item.record.comment_text),
                )
            )
        logger.info("fallback produced %s items", len(results))
        return results

    def _detect_comment_language(self, text: str) -> str:
        if not text:
            return "未知"

        chinese_chars = sum(
            1
            for ch in text
            if "\u4e00" <= ch <= "\u9fff" or "\u3400" <= ch <= "\u4dbf"
        )
        ascii_letters = sum(1 for ch in text if ch.isascii() and ch.isalpha())
        digit_chars = sum(1 for ch in text if ch.isdigit())

        if chinese_chars >= 2 and chinese_chars >= ascii_letters:
            return "中文"
        if ascii_letters > chinese_chars:
            return "英文"
        if digit_chars and chinese_chars == 0 and ascii_letters == 0:
            return "其他"
        if chinese_chars > 0:
            return "中英混合"
        return "其他"

    def _fallback_pain_point_cn(self, item: FilteredComment) -> str:
        matched = "、".join(item.matched_keywords[:3])
        if matched:
            return f"用户在围绕“{matched}”表达需求或痛点，但模型未稳定抽取出更细的中文结论。"
        return "用户表达了某种未被满足的需求，但模型未稳定抽取出更细的中文结论。"

    def _fallback_tool_concept_cn(self, item: FilteredComment) -> str:
        matched = "、".join(item.matched_keywords[:3])
        if matched:
            return f"可以做一个帮助用户解决“{matched}”相关问题的工具，核心能力是自动化处理、简化步骤并降低操作成本。"
        return "可以做一个帮助用户整理、简化并自动化处理相关问题的工具。"

    @staticmethod
    def _normalize_ollama_api_root(base_url: str) -> str:
        parsed = urlsplit(base_url or "http://localhost:11434/v1")
        scheme = parsed.scheme or "http"
        host = parsed.hostname or "localhost"
        port = parsed.port or 11434
        return urlunsplit((scheme, f"{host}:{port}", "", "", ""))
