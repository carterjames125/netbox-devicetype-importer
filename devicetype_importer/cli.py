#!/usr/bin/python3
"""Command-line interface definition for the NetBox Device Type Importer."""
import argparse
from typing import Literal
from devicetype_importer.config import Config
from loguru import logger


def device_type_importer_cli(config: Literal[Config]) -> None:
    """Command-line interface for the device type importer."""
    parser = argparse.ArgumentParser(
        description="Import device types into NetBox from a Git repository.")
    parser.add_argument(
        "--vendors",
        nargs="+",
        default=config.vendors,
        help="List of vendors to import. If not specified, all vendors will be imported."
    )
    parser.add_argument("--netbox-url", default=config.netbox_url, help="NetBox URL")
    parser.add_argument("--repo-url", default=config.repo_url, help="Git repository URL")
    parser.add_argument("--netbox-token", default=config.netbox_token, help="NetBox API Token")
    parser.add_argument(
        "--slugs",
        nargs="+",
        default=config.slugs,
        help="List of device type slugs to import. If not specified, all device types will be imported."
    )
    parser.add_argument(
        "--repo-branch",
        default=config.repo_branch,
        help="Git branch to use for the repository"
    )
    parser.add_argument(
        "--ignore-cert-errors",
        action="store_true",
        default=config.ignore_cert_errors,
        help="Ignore SSL certificate errors when connecting to NetBox.")
    
    args = parser.parse_args()
    args.vendors = [v.strip().casefold() for vendor in args.vendors for v in vendor.split(",") if v.strip()]
    args.slugs = [s.strip().casefold() for slug in args.slugs for s in slug.split(",") if s.strip()]
    return args

    
    