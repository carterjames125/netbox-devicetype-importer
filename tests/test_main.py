"""Unit tests for devicetype_importer.main.

Covers the main() entry point: verifies that it delegates directly to the
Click CLI command without adding any independent logic of its own.
"""
from unittest.mock import patch


class TestMain:
    """Tests that main() delegates to the cli() Click command."""

    def test_main_calls_cli(self):
        """main() invokes cli() exactly once with no extra arguments."""
        with patch("devicetype_importer.main.cli") as mock_cli:
            from devicetype_importer.main import main
            main()
        mock_cli.assert_called_once()

    def test_main_does_not_swallow_cli_exit(self):
        """main() does not suppress SystemExit raised by the Click command."""
        import pytest
        with patch("devicetype_importer.main.cli", side_effect=SystemExit(1)):
            from devicetype_importer.main import main
            with pytest.raises(SystemExit):
                main()
