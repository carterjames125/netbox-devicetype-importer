"""Unit tests for devicetype_importer.cli.

Covers argument parsing, vendor/slug normalisation (casefold, comma splitting,
whitespace stripping), and override of Config defaults via CLI flags.
"""
import sys
import pytest
from devicetype_importer.cli import device_type_importer_cli
from devicetype_importer.config import Config


@pytest.fixture()
def config(monkeypatch):
    """Return a Config with clean mandatory vars and no optional env overrides."""
    monkeypatch.delenv("NETBOX_URL", raising=False)
    monkeypatch.delenv("NETBOX_TOKEN", raising=False)
    monkeypatch.delenv("VENDORS", raising=False)
    monkeypatch.delenv("SLUGS", raising=False)
    monkeypatch.delenv("REPO_BRANCH", raising=False)
    monkeypatch.delenv("IGNORE_CERT_ERRORS", raising=False)
    monkeypatch.setenv("NETBOX_URL", "http://localhost:8000")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    return Config()


class TestCliDefaults:
    """Tests that the CLI uses Config-derived defaults when no flags are supplied."""

    def test_empty_vendors_by_default(self, config, monkeypatch):
        """vendors is an empty list when --vendors is not passed."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import"])
        args = device_type_importer_cli(config)
        assert args.vendors == []

    def test_empty_slugs_by_default(self, config, monkeypatch):
        """slugs is an empty list when --slugs is not passed."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import"])
        args = device_type_importer_cli(config)
        assert args.slugs == []

    def test_uses_config_netbox_url(self, config, monkeypatch):
        """netbox_url falls back to the value from Config when not overridden."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import"])
        args = device_type_importer_cli(config)
        assert args.netbox_url == "http://localhost:8000"

    def test_ignore_cert_errors_false_by_default(self, config, monkeypatch):
        """ignore_cert_errors is False unless --ignore-cert-errors is supplied."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import"])
        args = device_type_importer_cli(config)
        assert args.ignore_cert_errors is False


class TestCliVendors:
    """Tests that --vendors parses, normalises, and filters vendor names correctly."""

    def test_single_vendor(self, config, monkeypatch):
        """A single vendor name is parsed into a one-element list."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--vendors", "cisco"])
        args = device_type_importer_cli(config)
        assert args.vendors == ["cisco"]

    def test_multiple_vendors_space_separated(self, config, monkeypatch):
        """Multiple vendor names passed as separate args are all collected."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--vendors", "cisco", "juniper"])
        args = device_type_importer_cli(config)
        assert sorted(args.vendors) == ["cisco", "juniper"]

    def test_vendors_comma_separated(self, config, monkeypatch):
        """A comma-separated vendor string is split into individual entries."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--vendors", "cisco,juniper"])
        args = device_type_importer_cli(config)
        assert "cisco" in args.vendors
        assert "juniper" in args.vendors

    def test_vendors_casefolded(self, config, monkeypatch):
        """Vendor names are lower-cased regardless of the input casing."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--vendors", "CISCO", "Juniper"])
        args = device_type_importer_cli(config)
        assert args.vendors == ["cisco", "juniper"]

    def test_vendors_strips_whitespace(self, config, monkeypatch):
        """Leading/trailing whitespace around comma-separated vendors is stripped."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--vendors", "cisco, juniper"])
        args = device_type_importer_cli(config)
        assert "cisco" in args.vendors
        assert "juniper" in args.vendors


class TestCliSlugs:
    """Tests that --slugs parses, normalises, and filters device type slugs correctly."""

    def test_single_slug(self, config, monkeypatch):
        """A single slug is parsed into a one-element list."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--slugs", "cisco-asr-1001"])
        args = device_type_importer_cli(config)
        assert args.slugs == ["cisco-asr-1001"]

    def test_multiple_slugs(self, config, monkeypatch):
        """Multiple slugs passed as separate args are all collected."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--slugs", "cisco-asr-1001", "juniper-ex2300"])
        args = device_type_importer_cli(config)
        assert "cisco-asr-1001" in args.slugs
        assert "juniper-ex2300" in args.slugs

    def test_slugs_casefolded(self, config, monkeypatch):
        """Slug values are lower-cased regardless of input casing."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--slugs", "CISCO-ASR-1001"])
        args = device_type_importer_cli(config)
        assert args.slugs == ["cisco-asr-1001"]

    def test_slugs_comma_separated(self, config, monkeypatch):
        """A comma-separated slug string is split into individual entries."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--slugs", "cisco-asr-1001,juniper-ex2300"])
        args = device_type_importer_cli(config)
        assert "cisco-asr-1001" in args.slugs
        assert "juniper-ex2300" in args.slugs


class TestCliFlags:
    """Tests for individual CLI flag overrides that bypass Config defaults."""

    def test_ignore_cert_errors_flag(self, config, monkeypatch):
        """--ignore-cert-errors sets ignore_cert_errors to True."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--ignore-cert-errors"])
        args = device_type_importer_cli(config)
        assert args.ignore_cert_errors is True

    def test_repo_branch_override(self, config, monkeypatch):
        """--repo-branch overrides the default branch from Config."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--repo-branch", "develop"])
        args = device_type_importer_cli(config)
        assert args.repo_branch == "develop"

    def test_netbox_url_override(self, config, monkeypatch):
        """--netbox-url overrides the URL from Config."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--netbox-url", "http://override.example.com"])
        args = device_type_importer_cli(config)
        assert args.netbox_url == "http://override.example.com"

    def test_netbox_token_override(self, config, monkeypatch):
        """--netbox-token overrides the token from Config."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--netbox-token", "new-token"])
        args = device_type_importer_cli(config)
        assert args.netbox_token == "new-token"

    def test_repo_url_override(self, config, monkeypatch):
        """--repo-url overrides the repository URL from Config."""
        monkeypatch.setattr(sys, "argv", ["nb-dt-import", "--repo-url", "https://custom.example.com/repo.git"])
        args = device_type_importer_cli(config)
        assert args.repo_url == "https://custom.example.com/repo.git"
