"""Unit tests for devicetype_importer.config.

Covers Config dataclass construction, environment-variable validation,
default values, env-var overrides, and serialisation helpers.
"""
import pytest
from devicetype_importer.config import Config


@pytest.fixture(autouse=True)
def _clear_mandatory_vars(monkeypatch):
    """Remove any .env-loaded mandatory vars so each test owns its own environment."""
    monkeypatch.delenv("NETBOX_URL", raising=False)
    monkeypatch.delenv("NETBOX_TOKEN", raising=False)


def _make_config(monkeypatch, **extras):
    """Construct a Config with mandatory vars set, plus any additional env overrides."""
    monkeypatch.setenv("NETBOX_URL", "http://localhost:8000")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    for k, v in extras.items():
        monkeypatch.setenv(k, v)
    return Config()


class TestConfigValidation:
    """Tests that Config raises SystemExit when mandatory environment variables are absent."""

    def test_raises_when_netbox_url_missing(self, monkeypatch):
        """SystemExit is raised when NETBOX_URL is not set."""
        monkeypatch.delenv("NETBOX_URL", raising=False)
        monkeypatch.delenv("NETBOX_TOKEN", raising=False)
        with pytest.raises(SystemExit):
            Config()

    def test_raises_when_netbox_token_missing(self, monkeypatch):
        """SystemExit is raised when NETBOX_TOKEN is not set."""
        monkeypatch.setenv("NETBOX_URL", "http://localhost:8000")
        monkeypatch.delenv("NETBOX_TOKEN", raising=False)
        with pytest.raises(SystemExit):
            Config()

    def test_raises_when_netbox_token_empty(self, monkeypatch):
        """SystemExit is raised when NETBOX_TOKEN is set to an empty string."""
        monkeypatch.setenv("NETBOX_URL", "http://localhost:8000")
        monkeypatch.setenv("NETBOX_TOKEN", "")
        with pytest.raises(SystemExit):
            Config()


class TestConfigDefaults:
    """Tests that Config uses correct defaults when optional variables are absent."""

    def test_default_repo_url(self, monkeypatch):
        """REPO_URL defaults to the official netbox-community repository."""
        monkeypatch.delenv("REPO_URL", raising=False)
        config = _make_config(monkeypatch)
        assert "netbox-community/devicetype-library" in config.repo_url

    def test_default_repo_branch(self, monkeypatch):
        """REPO_BRANCH defaults to 'master'."""
        monkeypatch.delenv("REPO_BRANCH", raising=False)
        config = _make_config(monkeypatch)
        assert config.repo_branch == "master"

    def test_default_vendors_empty(self, monkeypatch):
        """VENDORS defaults to an empty list (import all vendors)."""
        monkeypatch.delenv("VENDORS", raising=False)
        config = _make_config(monkeypatch)
        assert config.vendors == []

    def test_default_slugs_empty(self, monkeypatch):
        """SLUGS defaults to an empty list (import all device types)."""
        monkeypatch.delenv("SLUGS", raising=False)
        config = _make_config(monkeypatch)
        assert config.slugs == []

    def test_default_ignore_cert_errors_false(self, monkeypatch):
        """IGNORE_CERT_ERRORS defaults to False."""
        monkeypatch.delenv("IGNORE_CERT_ERRORS", raising=False)
        config = _make_config(monkeypatch)
        assert config.ignore_cert_errors is False

    def test_default_modules_feature_false(self, monkeypatch):
        """NETBOX_FEATURE_MODULES defaults to False."""
        monkeypatch.delenv("NETBOX_FEATURE_MODULES", raising=False)
        config = _make_config(monkeypatch)
        assert config.netbox_features["modules"] is False


class TestConfigEnvOverrides:
    """Tests that Config reads all supported environment variables into the correct fields."""

    def test_loads_netbox_url(self, monkeypatch):
        """NETBOX_URL is mapped to config.netbox_url."""
        config = _make_config(monkeypatch, NETBOX_URL="http://netbox.example.com")
        assert config.netbox_url == "http://netbox.example.com"

    def test_loads_netbox_token(self, monkeypatch):
        """NETBOX_TOKEN is mapped to config.netbox_token."""
        config = _make_config(monkeypatch, NETBOX_TOKEN="my-secret-token")
        assert config.netbox_token == "my-secret-token"

    def test_loads_repo_url(self, monkeypatch):
        """REPO_URL is mapped to config.repo_url."""
        config = _make_config(monkeypatch, REPO_URL="https://example.com/repo.git")
        assert config.repo_url == "https://example.com/repo.git"

    def test_loads_repo_branch(self, monkeypatch):
        """REPO_BRANCH is mapped to config.repo_branch."""
        config = _make_config(monkeypatch, REPO_BRANCH="develop")
        assert config.repo_branch == "develop"

    def test_loads_vendors_comma_separated(self, monkeypatch):
        """VENDORS is split on commas and mapped to config.vendors."""
        config = _make_config(monkeypatch, VENDORS="cisco,juniper")
        assert config.vendors == ["cisco", "juniper"]

    def test_loads_slugs_comma_separated(self, monkeypatch):
        """SLUGS is split on commas and mapped to config.slugs."""
        config = _make_config(monkeypatch, SLUGS="cisco-asr-1001,juniper-ex2300")
        assert config.slugs == ["cisco-asr-1001", "juniper-ex2300"]

    def test_ignore_cert_errors_true(self, monkeypatch):
        """IGNORE_CERT_ERRORS='true' is parsed as True."""
        config = _make_config(monkeypatch, IGNORE_CERT_ERRORS="true")
        assert config.ignore_cert_errors is True

    def test_ignore_cert_errors_accepts_1(self, monkeypatch):
        """IGNORE_CERT_ERRORS='1' is parsed as True."""
        config = _make_config(monkeypatch, IGNORE_CERT_ERRORS="1")
        assert config.ignore_cert_errors is True

    def test_ignore_cert_errors_accepts_yes(self, monkeypatch):
        """IGNORE_CERT_ERRORS='yes' is parsed as True."""
        config = _make_config(monkeypatch, IGNORE_CERT_ERRORS="yes")
        assert config.ignore_cert_errors is True

    def test_modules_feature_enabled(self, monkeypatch):
        """NETBOX_FEATURE_MODULES='true' enables the modules feature flag."""
        config = _make_config(monkeypatch, NETBOX_FEATURE_MODULES="true")
        assert config.netbox_features["modules"] is True


class TestConfigMethods:
    """Tests for Config helper methods: repo_path property, to_dict, and __str__."""

    def test_repo_path_is_absolute(self, monkeypatch):
        """repo_path always returns an absolute filesystem path."""
        config = _make_config(monkeypatch)
        assert config.repo_path.is_absolute()

    def test_repo_path_ends_with_repo(self, monkeypatch):
        """repo_path points to a directory named 'repo' under the project base."""
        config = _make_config(monkeypatch)
        assert config.repo_path.name == "repo"

    def test_to_dict_hides_token(self, monkeypatch):
        """to_dict masks the API token with '***' when include_secrets=False."""
        config = _make_config(monkeypatch, NETBOX_TOKEN="super-secret")
        d = config.to_dict(include_secrets=False)
        assert d["netbox_token"] == "***"
        assert "super-secret" not in str(d)

    def test_to_dict_exposes_token(self, monkeypatch):
        """to_dict includes the plaintext token when include_secrets=True."""
        config = _make_config(monkeypatch, NETBOX_TOKEN="super-secret")
        d = config.to_dict(include_secrets=True)
        assert d["netbox_token"] == "super-secret"

    def test_to_dict_contains_expected_keys(self, monkeypatch):
        """to_dict returns all expected configuration keys."""
        config = _make_config(monkeypatch)
        d = config.to_dict()
        for key in ("repo_url", "repo_branch", "netbox_url", "netbox_token",
                    "ignore_cert_errors", "repo_path", "vendors", "slugs", "netbox_features"):
            assert key in d

    def test_str_does_not_expose_token(self, monkeypatch):
        """__str__ never includes the plaintext API token."""
        config = _make_config(monkeypatch, NETBOX_TOKEN="super-secret")
        assert "super-secret" not in str(config)

    def test_str_contains_config_prefix(self, monkeypatch):
        """__str__ output starts with 'Config(' for easy identification."""
        config = _make_config(monkeypatch)
        assert str(config).startswith("Config(")
