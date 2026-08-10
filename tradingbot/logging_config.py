"""Structured logging configuration."""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

import structlog


def configure_logging(level: str = "INFO", path: str | Path = "logs/tradingbot.log") -> None:
    log_path = Path(path)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    # Plain FileHandler grew tradingbot.log unbounded (9.2 MB after one day
    # of live trading) - rotate at 20 MB, keep 5 backups (~100 MB max) so it
    # never needs manual cleanup.
    file_handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=20 * 1024 * 1024, backupCount=5
    )
    logging.basicConfig(
        format="%(message)s",
        level=getattr(logging, level.upper(), logging.INFO),
        handlers=[logging.StreamHandler(), file_handler],
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
