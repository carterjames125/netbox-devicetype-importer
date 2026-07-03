#!/usr/bin/python3
"""Configuration dataclass for the NetBox Device Type Importer.

Values are read from environment variables (or a .env file) at construction time.
NETBOX_URL and NETBOX_TOKEN are mandatory; all other fields have sensible defaults.
"""
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from loguru import logger

load_dotenv()
@dataclass
class Config:
    """Application configuration loaded from environment variables.

    Mandatory: NETBOX_URL, NETBOX_TOKEN.
    All other fields are optional and have sensible defaults.
    """
    base_dir: Path = field(default_factory=lambda: Path(__file__).resolve().parent.parent)
    repo_url: str = field(default_factory=lambda: os.getenv("REPO_URL", "https://github.com/netbox-community/devicetype-library.git"))
    repo_branch: str = field(default_factory=lambda: os.getenv("REPO_BRANCH", "master"))
    netbox_url: str = field(default_factory=lambda: os.getenv("NETBOX_URL", "http://localhost:8000"))
    netbox_token: str = field(default_factory=lambda: os.getenv("NETBOX_TOKEN", ""), repr=False)
    ignore_cert_errors: bool = field(
        default_factory=lambda: os.getenv(
            "IGNORE_CERT_ERRORS", "false"
        ).lower() in ["true", "1", "yes"]
    )
    vendors: list[str] = field(
        default_factory=lambda: os.getenv("VENDORS", "").split(",") if os.getenv("VENDORS") else []
    )
    slugs: list[str] = field(
        default_factory=lambda: os.getenv("SLUGS", "").split(",") if os.getenv("SLUGS") else []
    )
    netbox_features: dict[str, bool] = field(
        default_factory=lambda: {
            "modules": os.getenv("NETBOX_FEATURE_MODULES", "false").lower()
            in ["true", "1", "yes"]
        }
    )
    @property
    def repo_path(self) -> Path:
        """Return the path to the local clone of the repository."""
        return Path(self.base_dir / "repo").resolve()
    
    def __post_init__(self):
        """Validate that all mandatory environment variables are present and non-empty."""
        MANDATORY_VARS = ["NETBOX_URL", "NETBOX_TOKEN"]
        for var in MANDATORY_VARS:
            if not os.getenv(var):
                logger.error(f"Mandatory environment variable {var} is not set.")
                raise SystemExit(1)

    def to_dict(self, include_secrets: bool = False) -> dict:
        """Return config as a dict, masking the API token unless include_secrets=True."""
        token_value = self.netbox_token if include_secrets else ("***" if self.netbox_token else "")
        return {
            "repo_url": self.repo_url,
            "repo_branch": self.repo_branch,
            "netbox_url": self.netbox_url,
            "netbox_token": token_value,
            "ignore_cert_errors": self.ignore_cert_errors,
            "repo_path": str(self.repo_path),
            "vendors": self.vendors,
            "slugs": self.slugs,
            "netbox_features": self.netbox_features,
        }

    def __str__(self) -> str:
        """Return a human-readable representation of the config with the token masked."""
        return f"Config({self.to_dict(include_secrets=False)})"