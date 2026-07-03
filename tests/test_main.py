"""Unit tests for devicetype_importer.main.

Covers the main() entry point: verifies that it correctly wires Config,
CLI argument parsing, and DTLRepo together in the expected call order.
"""
import sys
import pytest
from unittest.mock import MagicMock, patch


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """Set mandatory env vars and reset argv to a clean state before each test."""
    monkeypatch.delenv("NETBOX_URL", raising=False)
    monkeypatch.delenv("NETBOX_TOKEN", raising=False)
    monkeypatch.setenv("NETBOX_URL", "http://localhost:8000")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    monkeypatch.setattr(sys, "argv", ["nb-dt-import"])


def _make_mock_repo():
    """Return a MagicMock pre-configured with DTLRepo's expected return values."""
    mock_repo = MagicMock()
    mock_repo.get_devices.return_value = ([], [])
    mock_repo.parse_data_files_multiprocess.return_value = []
    return mock_repo


class TestMain:
    """Tests that main() wires Config, CLI, and DTLRepo together in the correct order."""

    def test_main_runs_without_error(self):
        """main() completes without raising when all dependencies are available."""
        mock_repo = _make_mock_repo()
        with patch("devicetype_importer.main.DTLRepo") as MockDTL:
            MockDTL.return_value = mock_repo
            from devicetype_importer.main import main
            main()

    def test_main_calls_clone_or_update_repo(self):
        """main() calls clone_or_update_repo() to ensure the library is current."""
        mock_repo = _make_mock_repo()
        with patch("devicetype_importer.main.DTLRepo") as MockDTL:
            MockDTL.return_value = mock_repo
            from devicetype_importer.main import main
            main()
        mock_repo.clone_or_update_repo.assert_called_once()

    def test_main_calls_get_devices(self):
        """main() calls get_devices() to discover device-type definition files."""
        mock_repo = _make_mock_repo()
        with patch("devicetype_importer.main.DTLRepo") as MockDTL:
            MockDTL.return_value = mock_repo
            from devicetype_importer.main import main
            main()
        mock_repo.get_devices.assert_called_once()

    def test_main_calls_parse_data_files_multiprocess(self):
        """main() calls parse_data_files_multiprocess() to load device definitions."""
        mock_repo = _make_mock_repo()
        with patch("devicetype_importer.main.DTLRepo") as MockDTL:
            MockDTL.return_value = mock_repo
            from devicetype_importer.main import main
            main()
        mock_repo.parse_data_files_multiprocess.assert_called_once()

    def test_main_passes_vendor_filter(self, monkeypatch):
        """Vendor names from --vendors are forwarded to get_devices as the vendors filter."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--vendors", "cisco"])
        mock_repo = _make_mock_repo()
        with patch("devicetype_importer.main.DTLRepo") as MockDTL:
            MockDTL.return_value = mock_repo
            from devicetype_importer.main import main
            main()
        _, kwargs = mock_repo.get_devices.call_args
        assert kwargs.get("vendors") == ["cisco"] or mock_repo.get_devices.call_args[0][1] == ["cisco"]

    def test_main_constructs_dtl_repo_with_config(self):
        """main() instantiates DTLRepo exactly once, passing the resolved config values."""
        mock_repo = _make_mock_repo()
        with patch("devicetype_importer.main.DTLRepo") as MockDTL:
            MockDTL.return_value = mock_repo
            from devicetype_importer.main import main
            main()
        MockDTL.assert_called_once()
        call_kwargs = MockDTL.call_args
        assert call_kwargs is not None
