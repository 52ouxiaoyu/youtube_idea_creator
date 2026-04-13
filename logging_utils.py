from __future__ import annotations

import logging
import sys
import time
from contextlib import contextmanager
from typing import Iterator


def configure_logging(level: str = "INFO") -> None:
    """Configure a compact, timestamped console logger."""

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


@contextmanager
def timed_step(logger: logging.Logger, label: str) -> Iterator[None]:
    """Log the start and end of a stage with elapsed time."""

    start = time.perf_counter()
    logger.info("%s - start", label)
    try:
        yield
    except Exception:
        logger.exception("%s - failed", label)
        raise
    finally:
        elapsed = time.perf_counter() - start
        logger.info("%s - done in %.2fs", label, elapsed)

