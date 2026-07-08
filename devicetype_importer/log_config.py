"""Centralized Loguru configuration for the importer project.

Environment variables:
- LOG_LEVEL (default: INFO)
- LOG_JSON (default: false)
- LOG_FILE (default: logs/netbox_importer.log)
- LOG_ROTATION (default: 10 MB)
- LOG_RETENTION (default: 7 days)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from loguru import logger


DEFAULT_LOG_FILE = "logs/netbox_importer.log"

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _env_bool(name: str, default: bool = False) -> bool:
	value = os.getenv(name)
	if value is None:
		return default
	return value.strip().lower() in _TRUE_VALUES


def setup_logging(
	*,
	level: str | None = None,
	json_logs: bool | None = None,
	log_file: str | Path | None = None,
) -> None:
	"""Configure global Loguru logging sinks for console and optional file output.

	Call this once near application startup.
	"""
	resolved_level = (level or os.getenv("LOG_LEVEL", "INFO")).upper()
	resolved_json = _env_bool("LOG_JSON", default=False) if json_logs is None else json_logs
	resolved_log_file = log_file or os.getenv("LOG_FILE")

	logger.remove()

	if resolved_json:
		logger.add(
			sys.stdout,
			level=resolved_level,
			serialize=True,
			backtrace=False,
			diagnose=False,
			enqueue=True,
		)
	else:
		logger.add(
			sys.stdout,
			level=resolved_level,
			colorize=True,
			backtrace=False,
			diagnose=False,
			enqueue=True,
			format=(
				"<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
				"<level>{level: <8}</level> | "
				"<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - "
				"<level>{message}</level>"
			),
		)

	if resolved_log_file:
		path = Path(resolved_log_file)
		path.parent.mkdir(parents=True, exist_ok=True)
		logger.add(
			path,
			level=resolved_level,
			serialize=resolved_json,
			rotation=os.getenv("LOG_ROTATION", "10 MB"),
			retention=os.getenv("LOG_RETENTION", "7 days"),
			backtrace=False,
			diagnose=False,
			enqueue=True,
		)


def get_logger(name: str):
	"""Return a logger bound with module context for better traceability."""
	return logger.bind(module=name)


__all__ = ["DEFAULT_LOG_FILE", "get_logger", "logger", "setup_logging"]
