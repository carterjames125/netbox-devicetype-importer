"""Unit tests for devicetype_importer.cli.

Covers the _normalize helper, Click option parsing (defaults, vendor/slug
normalisation, flag behaviour, env-var overrides), and CLI integration with
a mocked DTLRepo via click.testing.CliRunner.
"""
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from devicetype_importer.cli import _normalize, cli

BASE_ENV = {"NETBOX_URL": "http://localhost:8000", "NETBOX_TOKEN": "test-token"}


@contextmanager
def _mocked_repo():
    """Context manager that patches DTLRepo and yields the configured mock instance."""
    mock_repo = MagicMock()
    mock_repo.get_devices.return_value = ([], [])
    mock_repo.parse_data_files_multiprocess.return_value = []
    with patch("devicetype_importer.cli.DTLRepo", return_value=mock_repo):
        yield mock_repo


def _run(*args, env=None):
    """Invoke the CLI with DTLRepo mocked; returns (Result, mock_repo)."""
    runner = CliRunner()
    with _mocked_repo() as mock_repo:
        result = runner.invoke(
            cli,
            list(args),
            env={**BASE_ENV, **(env or {})},
            catch_exceptions=False,
        )
    return result, mock_repo


# ── _normalize ────────────────────────────────────────────────────────────────

class TestNormalize:
    """Tests for the _normalize helper that cleans vendor/slug token lists."""

    def test_empty_tuple_returns_empty(self):
        """An empty tuple produces an empty list."""
        assert _normalize(()) == []

    def test_single_value(self):
        """A single-element tuple with no commas passes through unchanged."""
        assert _normalize(("cisco",)) == ["cisco"]

    def test_multiple_values(self):
        """Multiple tuple entries are all included in the output."""
        assert sorted(_normalize(("cisco", "juniper"))) == ["cisco", "juniper"]

    def test_comma_separated_string(self):
        """A comma-separated string in one tuple entry is split into separate tokens."""
        result = _normalize(("cisco,juniper",))
        assert "cisco" in result
        assert "juniper" in result

    def test_strips_whitespace(self):
        """Whitespace around tokens after comma-splitting is stripped."""
        result = _normalize(("cisco, juniper",))
        assert "cisco" in result
        assert "juniper" in result

    def test_casefolded(self):
        """All tokens are lower-cased regardless of input casing."""
        assert _normalize(("CISCO", "Juniper")) == ["cisco", "juniper"]

    def test_filters_empty_tokens(self):
        """Empty tokens produced by consecutive commas are dropped."""
        result = _normalize(("cisco,,juniper",))
        assert "" not in result
        assert len(result) == 2


# ── CLI defaults ──────────────────────────────────────────────────────────────

class TestCliDefaults:
    """Tests that the CLI uses correct defaults when optional flags are omitted."""

    def test_exits_zero_with_required_token(self):
        """The command exits 0 when NETBOX_TOKEN is provided."""
        result, _ = _run()
        assert result.exit_code == 0

    def test_missing_token_exits_nonzero(self, monkeypatch):
        """The command exits non-zero when NETBOX_TOKEN is absent from both env and CLI."""
        monkeypatch.delenv("NETBOX_TOKEN", raising=False)
        runner = CliRunner()
        with _mocked_repo():
            result = runner.invoke(cli, [], env={"NETBOX_URL": "http://localhost:8000"})
        assert result.exit_code != 0

    def test_token_from_env_var(self):
        """NETBOX_TOKEN env var is accepted in place of the --netbox-token flag."""
        result, _ = _run(env={"NETBOX_TOKEN": "from-env"})
        assert result.exit_code == 0

    def test_no_vendors_passes_empty_list(self):
        """When --vendors is omitted, get_devices receives an empty vendors list."""
        result, mock_repo = _run()
        assert result.exit_code == 0
        _, kwargs = mock_repo.get_devices.call_args
        assert kwargs.get("vendors") == []

    def test_no_slugs_passes_empty_list(self):
        """When --slugs is omitted, parse_data_files_multiprocess receives an empty slugs list."""
        result, mock_repo = _run()
        assert result.exit_code == 0
        _, kwargs = mock_repo.parse_data_files_multiprocess.call_args
        assert kwargs.get("slugs") == []


# ── CLI vendors ───────────────────────────────────────────────────────────────

class TestCliVendors:
    """Tests that --vendors parses, normalises, and forwards vendor names correctly."""

    def test_single_vendor(self):
        """A single --vendors flag is forwarded as a one-element list."""
        result, mock_repo = _run("--vendors", "cisco")
        assert result.exit_code == 0
        _, kwargs = mock_repo.get_devices.call_args
        assert kwargs["vendors"] == ["cisco"]

    def test_multiple_vendors_repeated_flag(self):
        """Multiple --vendors flags are collected into a single list."""
        result, mock_repo = _run("--vendors", "cisco", "--vendors", "juniper")
        assert result.exit_code == 0
        _, kwargs = mock_repo.get_devices.call_args
        assert sorted(kwargs["vendors"]) == ["cisco", "juniper"]

    def test_comma_separated_vendors(self):
        """A single comma-separated --vendors value is split into individual entries."""
        result, mock_repo = _run("--vendors", "cisco,juniper")
        assert result.exit_code == 0
        _, kwargs = mock_repo.get_devices.call_args
        assert "cisco" in kwargs["vendors"]
        assert "juniper" in kwargs["vendors"]

    def test_vendors_casefolded(self):
        """Vendor names are lower-cased before being forwarded."""
        result, mock_repo = _run("--vendors", "CISCO")
        assert result.exit_code == 0
        _, kwargs = mock_repo.get_devices.call_args
        assert "cisco" in kwargs["vendors"]

    def test_vendors_strips_whitespace(self):
        """Whitespace around comma-separated vendor tokens is stripped."""
        result, mock_repo = _run("--vendors", "cisco, juniper")
        assert result.exit_code == 0
        _, kwargs = mock_repo.get_devices.call_args
        assert "cisco" in kwargs["vendors"]
        assert "juniper" in kwargs["vendors"]

    def test_vendors_from_env_var(self):
        """VENDORS env var is split and forwarded to get_devices."""
        result, mock_repo = _run(env={"VENDORS": "cisco,juniper"})
        assert result.exit_code == 0
        _, kwargs = mock_repo.get_devices.call_args
        assert "cisco" in kwargs["vendors"]
        assert "juniper" in kwargs["vendors"]


# ── CLI slugs ─────────────────────────────────────────────────────────────────

class TestCliSlugs:
    """Tests that --slugs parses, normalises, and forwards device type slugs correctly."""

    def test_single_slug(self):
        """A single --slugs flag is forwarded as a one-element list."""
        result, mock_repo = _run("--slugs", "cisco-asr-1001")
        assert result.exit_code == 0
        _, kwargs = mock_repo.parse_data_files_multiprocess.call_args
        assert kwargs["slugs"] == ["cisco-asr-1001"]

    def test_multiple_slugs_repeated_flag(self):
        """Multiple --slugs flags are collected into a single list."""
        result, mock_repo = _run("--slugs", "cisco-asr-1001", "--slugs", "juniper-ex2300")
        assert result.exit_code == 0
        _, kwargs = mock_repo.parse_data_files_multiprocess.call_args
        assert "cisco-asr-1001" in kwargs["slugs"]
        assert "juniper-ex2300" in kwargs["slugs"]

    def test_comma_separated_slugs(self):
        """A comma-separated --slugs value is split into individual entries."""
        result, mock_repo = _run("--slugs", "cisco-asr-1001,juniper-ex2300")
        assert result.exit_code == 0
        _, kwargs = mock_repo.parse_data_files_multiprocess.call_args
        assert "cisco-asr-1001" in kwargs["slugs"]
        assert "juniper-ex2300" in kwargs["slugs"]

    def test_slugs_casefolded(self):
        """Slug values are lower-cased before being forwarded."""
        result, mock_repo = _run("--slugs", "CISCO-ASR-1001")
        assert result.exit_code == 0
        _, kwargs = mock_repo.parse_data_files_multiprocess.call_args
        assert "cisco-asr-1001" in kwargs["slugs"]


# ── CLI flags ─────────────────────────────────────────────────────────────────

class TestCliFlags:
    """Tests for boolean flag and string option overrides."""

    def test_ignore_cert_errors_flag(self):
        """--ignore-cert-errors sets ignore_cert_errors=True on the args namespace."""
        runner = CliRunner()
        captured = {}

        def fake_dtl(args, repo_path):
            captured["ignore"] = args.ignore_cert_errors
            mock = MagicMock()
            mock.get_devices.return_value = ([], [])
            mock.parse_data_files_multiprocess.return_value = []
            return mock

        with patch("devicetype_importer.cli.DTLRepo", side_effect=fake_dtl):
            result = runner.invoke(
                cli, ["--ignore-cert-errors"], env=BASE_ENV, catch_exceptions=False
            )
        assert result.exit_code == 0
        assert captured["ignore"] is True

    def test_ignore_cert_errors_false_by_default(self):
        """ignore_cert_errors defaults to False when the flag is not supplied."""
        runner = CliRunner()
        captured = {}

        def fake_dtl(args, repo_path):
            captured["ignore"] = args.ignore_cert_errors
            mock = MagicMock()
            mock.get_devices.return_value = ([], [])
            mock.parse_data_files_multiprocess.return_value = []
            return mock

        with patch("devicetype_importer.cli.DTLRepo", side_effect=fake_dtl):
            result = runner.invoke(cli, [], env=BASE_ENV, catch_exceptions=False)
        assert result.exit_code == 0
        assert captured["ignore"] is False

    def test_netbox_url_override(self):
        """--netbox-url is passed to the args namespace."""
        runner = CliRunner()
        captured = {}

        def fake_dtl(args, repo_path):
            captured["url"] = args.netbox_url
            mock = MagicMock()
            mock.get_devices.return_value = ([], [])
            mock.parse_data_files_multiprocess.return_value = []
            return mock

        with patch("devicetype_importer.cli.DTLRepo", side_effect=fake_dtl):
            result = runner.invoke(
                cli,
                ["--netbox-url", "http://override.example.com"],
                env=BASE_ENV,
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert captured["url"] == "http://override.example.com"

    def test_repo_branch_override(self):
        """--repo-branch is passed to the args namespace."""
        runner = CliRunner()
        captured = {}

        def fake_dtl(args, repo_path):
            captured["branch"] = args.repo_branch
            mock = MagicMock()
            mock.get_devices.return_value = ([], [])
            mock.parse_data_files_multiprocess.return_value = []
            return mock

        with patch("devicetype_importer.cli.DTLRepo", side_effect=fake_dtl):
            result = runner.invoke(
                cli, ["--repo-branch", "develop"], env=BASE_ENV, catch_exceptions=False
            )
        assert result.exit_code == 0
        assert captured["branch"] == "develop"

    def test_repo_url_override(self):
        """--repo-url is passed to the args namespace."""
        runner = CliRunner()
        captured = {}

        def fake_dtl(args, repo_path):
            captured["repo_url"] = args.repo_url
            mock = MagicMock()
            mock.get_devices.return_value = ([], [])
            mock.parse_data_files_multiprocess.return_value = []
            return mock

        with patch("devicetype_importer.cli.DTLRepo", side_effect=fake_dtl):
            result = runner.invoke(
                cli,
                ["--repo-url", "https://custom.example.com/repo.git"],
                env=BASE_ENV,
                catch_exceptions=False,
            )
        assert result.exit_code == 0
        assert captured["repo_url"] == "https://custom.example.com/repo.git"
