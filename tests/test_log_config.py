"""Unit tests for devicetype_importer.log_config.

Covers setup_logging (console, JSON, and file sinks; env-var configuration)
and the get_logger helper that returns a bound Loguru logger.
"""
import pytest
from loguru import logger as loguru_logger
from devicetype_importer.log_config import setup_logging, get_logger


class TestSetupLogging:
    """Tests that setup_logging configures Loguru sinks without raising for all option combos."""

    def test_default_setup_does_not_raise(self):
        """Calling setup_logging with no arguments completes without error."""
        setup_logging()

    def test_json_mode_does_not_raise(self):
        """json_logs=True switches to a serialised JSON sink without error."""
        setup_logging(json_logs=True)

    def test_human_mode_does_not_raise(self):
        """json_logs=False keeps the coloured human-readable console format."""
        setup_logging(json_logs=False)

    def test_debug_level_does_not_raise(self):
        """level='DEBUG' is accepted without error."""
        setup_logging(level="DEBUG")

    def test_warning_level_does_not_raise(self):
        """level='WARNING' is accepted without error."""
        setup_logging(level="WARNING")

    def test_file_sink_does_not_raise(self, tmp_path):
        """Passing log_file adds a rotating file sink without error."""
        log_file = tmp_path / "app.log"
        setup_logging(log_file=str(log_file))

    def test_file_sink_creates_parent_dirs(self, tmp_path):
        """setup_logging creates missing parent directories for the log file path."""
        log_file = tmp_path / "nested" / "logs" / "app.log"
        setup_logging(log_file=str(log_file))
        assert log_file.parent.exists()

    def test_level_from_env(self, monkeypatch):
        """LOG_LEVEL env var is respected when the level param is not passed."""
        monkeypatch.setenv("LOG_LEVEL", "ERROR")
        setup_logging()

    def test_json_from_env(self, monkeypatch):
        """LOG_JSON='true' activates JSON mode via environment variable."""
        monkeypatch.setenv("LOG_JSON", "true")
        setup_logging()

    def test_file_from_env(self, monkeypatch, tmp_path):
        """LOG_FILE env var adds a file sink when the log_file param is absent."""
        log_file = tmp_path / "env.log"
        monkeypatch.setenv("LOG_FILE", str(log_file))
        setup_logging()

    def test_log_after_setup_does_not_raise(self):
        """Emitting records at various levels after setup completes without error."""
        setup_logging(level="DEBUG")
        loguru_logger.debug("debug message")
        loguru_logger.info("info message")
        loguru_logger.warning("warning message")


class TestGetLogger:
    """Tests that get_logger returns a usable bound Loguru logger."""

    def test_returns_non_none(self):
        """get_logger always returns a non-None object."""
        bound = get_logger("test_module")
        assert bound is not None

    def test_returned_logger_is_callable(self):
        """The object returned by get_logger can emit log records."""
        bound = get_logger("test_module")
        bound.info("test log from bound logger")

    def test_different_names_return_loggers(self):
        """Calling get_logger with different module names returns a valid logger for each."""
        a = get_logger("module_a")
        b = get_logger("module_b")
        assert a is not None
        assert b is not None
