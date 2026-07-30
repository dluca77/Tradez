"""Structured logging configuration."""
from __future__ import annotations

import logging
from pathlib import Path

import structlog


def configure_logging(level: str = "INFO", path: str | Path = "logs/tradingbot.log") -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[logging.StreamHandler(), logging.FileHandler(log_path)],
    )

    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.stdlib.LoggerFactory(),
    )
