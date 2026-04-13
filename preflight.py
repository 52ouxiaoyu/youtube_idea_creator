from __future__ import annotations

import json
import os
import socket
import subprocess
import shutil
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

from .config import AppConfig
from .logging_utils import get_logger


logger = get_logger(__name__)


@dataclass
class EndpointCheck:
    name: str
    url: str
    host: str
    port: int


def _endpoint_from_url(name: str, url: str, default_port: int) -> EndpointCheck:
    parsed = urlparse(url)
    if parsed.scheme:
        host = parsed.hostname or ""
        port = parsed.port or default_port
    else:
        host = url
        port = default_port
        url = f"https://{url}" if default_port == 443 else f"http://{url}"
    if not host:
        raise ValueError(f"{name} endpoint is invalid: {url}")
    return EndpointCheck(name=name, url=url, host=host, port=port)


def _check_dns(host: str) -> None:
    socket.getaddrinfo(host, None)


def _check_tcp(host: str, port: int, timeout: float = 5.0) -> None:
    with socket.create_connection((host, port), timeout=timeout):
        pass


def _check_endpoint(check: EndpointCheck) -> None:
    logger.info("preflight %s: resolving %s", check.name, check.host)
    _check_dns(check.host)
    logger.info("preflight %s: connecting %s:%s", check.name, check.host, check.port)
    _check_tcp(check.host, check.port)
    logger.info("preflight %s: ok", check.name)


def _normalize_root_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.scheme:
        parsed = urlparse(f"http://{url}")
    host = parsed.hostname or "localhost"
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return f"{parsed.scheme or 'http'}://{host}:{port}"


def _parse_ollama_model_path(model: str) -> tuple[str, str]:
    if ":" in model:
        repo, tag = model.split(":", 1)
    else:
        repo, tag = model, "latest"
    return repo.replace("/", os.sep), tag


def _local_ollama_model_exists(model: str) -> bool:
    model_root = os.getenv("OLLAMA_MODELS")
    candidates = []
    if model_root:
        candidates.append(Path(model_root))
    candidates.append(Path("/Volumes/ORICO/Ollama/models"))
    candidates.append(Path.home() / ".ollama" / "models")

    repo_path, tag = _parse_ollama_model_path(model)
    for root in candidates:
        manifest = root / "manifests" / "registry.ollama.ai" / "library" / repo_path / tag
        if manifest.exists():
            logger.info("preflight ollama_api: found local manifest %s", manifest)
            return True
    return False


def _check_ollama_model(root_url: str, model: str) -> None:
    if _local_ollama_model_exists(model):
        logger.info("preflight ollama_api: model=%s verified from local model store", model)
        return

    tags_url = f"{root_url}/api/tags"
    logger.info("preflight ollama_api: checking model=%s via %s", model, tags_url)
    with urlopen(tags_url, timeout=5) as response:  # nosec B310 - local/controlled URL
        payload = json.loads(response.read().decode("utf-8"))
    models = payload.get("models", [])
    model_names = {item.get("name") for item in models if isinstance(item, dict)}

    if model in model_names:
        logger.info("preflight ollama_api: model=%s found in API tags", model)
        return

    cli_names: set[str] = set()
    ollama_bin = shutil.which("ollama")
    if ollama_bin:
        try:
            proc = subprocess.run(
                [ollama_bin, "list"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    if not line or line.lower().startswith("name "):
                        continue
                    parts = line.split()
                    if parts:
                        cli_names.add(parts[0])
        except OSError:
            pass

    if model in cli_names:
        logger.warning(
            "preflight ollama_api: API tags do not list model=%s, but ollama list does. "
            "Continuing because the local model store appears to contain the model.",
            model,
        )
        return

    available_api = ", ".join(sorted(name for name in model_names if name)) or "none"
    available_cli = ", ".join(sorted(cli_names)) or "none"
    if cli_names:
        raise RuntimeError(
            f"Ollama is running, but model '{model}' is not available from the API. "
            f"API tags: {available_api}. "
            f"ollama list: {available_cli}. "
            "Please restart Ollama or update AI_MODEL if needed."
        )
    if model not in model_names:
        raise RuntimeError(
            f"Ollama is running, but model '{model}' is not available. "
            f"Available models: {available_api}. "
            "Please run `ollama list` and update AI_MODEL if needed."
        )


def run_preflight_checks(config: AppConfig) -> None:
    """Fail fast if the runtime cannot reach the required endpoints."""

    checks = [
        _endpoint_from_url("youtube_api", config.youtube_api_endpoint, 443),
    ]

    if config.ai_provider == "ollama":
        ollama_endpoint = config.ai_base_url or "http://localhost:11434/v1"
        checks.append(_endpoint_from_url("ollama_api", ollama_endpoint, 11434))
    elif config.ai_provider == "openai" and config.ai_base_url:
        checks.append(_endpoint_from_url("openai_api", config.ai_base_url, 443))
    elif config.ai_provider == "gemini":
        gemini_url = config.ai_base_url or "https://generativelanguage.googleapis.com/v1beta"
        checks.append(_endpoint_from_url("gemini_api", gemini_url, 443))

    logger.info("starting preflight checks")
    for check in checks:
        try:
            _check_endpoint(check)
        except ConnectionRefusedError as exc:
            if check.name == "ollama_api":
                raise RuntimeError(
                    "Ollama is not accepting connections at "
                    f"{check.host}:{check.port}. "
                    "Please start Ollama first (for example: `ollama serve`) "
                    "or update AI_BASE_URL to the correct local endpoint."
                ) from exc
            raise RuntimeError(
                f"{check.name} is reachable by DNS but refused the TCP connection at "
                f"{check.host}:{check.port}. "
                "Please verify the service is running."
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"Preflight failed for {check.name} ({check.host}:{check.port}): {exc}"
            ) from exc

    if config.ai_provider == "ollama":
        _check_ollama_model(_normalize_root_url(config.ai_base_url or "http://localhost:11434/v1"), config.ai_model)
    logger.info("preflight checks passed")
