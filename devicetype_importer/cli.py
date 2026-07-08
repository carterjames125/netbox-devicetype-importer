#!/usr/bin/python3
"""Command-line interface definition for the NetBox Device Type Importer."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import click
from dotenv import load_dotenv
from loguru import logger

from devicetype_importer.netbox_api import NetBox
from devicetype_importer.repo import DTLRepo

load_dotenv()


async def import_device_types(args: SimpleNamespace) -> None:
    """Import device types from the Device Type Library into NetBox."""
    repo_path = Path(args.repo_path).resolve()
    dtl_repo = DTLRepo(args, repo_path)
    dtl_repo.clone_or_update_repo()

    logger.info("Scanning for Device Types...")
    files, _vendors = dtl_repo.get_devices(path=repo_path / "device-types", vendors=args.vendors)
    device_types = dtl_repo.parse_data_files_multiprocess(files=files, slugs=args.slugs)
    logger.info("Parsed {} device/module definitions.", len(device_types))
    netbox = NetBox(
        netbox_url=args.netbox_url,
        netbox_token=args.netbox_token,
        ignore_ssl=args.ignore_cert_errors,
    )
    logger.info("Vendors: {} | Slugs: {}", _vendors or "all", args.slugs or "all")

    # Create Manufacturer and DeviceType objects in NetBox
    netbox.create_manufacturers(_vendors)

    # Process Device Types (Async/Concurrent)
    start_time = asyncio.get_running_loop().time()
    await netbox.create_device_types(device_types)
    duration = asyncio.get_running_loop().time() - start_time
    logger.info("Device Types import completed in {:.2f} seconds.", duration)

    if netbox.modules:
        logger.info("NetBox Modules optimization enabled. Scanning for Module Types...")
        m_files, m_vendors = dtl_repo.get_devices(
            f"{dtl_repo.repo_path}/module-types/", args.vendors
        )
        module_types = dtl_repo.parse_files(m_files, slugs=args.slugs)
        logger.info(f"Parsed {len(module_types)} valid Module-Type definitions")
        netbox.create_manufacturers(m_vendors)
        start_time = asyncio.get_running_loop().time()
        await netbox.create_module_types(module_types)
        duration = asyncio.get_running_loop().time() - start_time
        logger.info("Module Types import completed in {:.2f} seconds.", duration)

    if netbox.rack_types_supported:
        rt_path = dtl_repo.repo_path / "rack-types"
        if rt_path.exists():
            logger.info("Rack Types support enabled. Scanning for Rack Types...")
            rt_files, rt_vendors = dtl_repo.get_devices(str(rt_path), args.vendors)
            rack_types = dtl_repo.parse_files(rt_files, slugs=args.slugs)
            logger.info(f"Parsed {len(rack_types)} valid Rack-Type definitions")
            netbox.create_manufacturers(rt_vendors)
            start_time = asyncio.get_running_loop().time()
            await netbox.create_rack_types(rack_types)
            duration = asyncio.get_running_loop().time() - start_time
            logger.info("Rack Types import completed in {:.2f} seconds.", duration)
        else:
            logger.debug("No rack-types directory found in repo — skipping.")

    logger.info("=" * 30)
    logger.info("Import Summary:")
    logger.info(f" - Manufacturers Created: {netbox.counter['manufacturer']}")
    logger.info(f" - Device Types Created:  {netbox.counter['added']}")
    logger.info(f" - Module Types Created:  {netbox.counter['module_added']}")
    logger.info(f" - Rack Types Created:    {netbox.counter['rack_added']}")
    logger.info(f" - Images Uploaded:       {netbox.counter['images']}")
    logger.info(f" - Templates Updated:     {netbox.counter['updated']}")
    logger.info("=" * 30)

def _normalize(values: tuple[str, ...]) -> list[str]:
    """Split comma-separated tokens, strip whitespace, and casefold each entry."""
    return [v.strip().casefold() for item in values for v in item.split(",") if v.strip()]


@click.command()
@click.option(
    "--netbox-url",
    envvar="NETBOX_URL",
    default="http://localhost:8000",
    show_default=True,
    help="Base URL of the NetBox instance.",
)
@click.option(
    "--netbox-token",
    envvar="NETBOX_TOKEN",
    required=True,
    help="NetBox API token with read/write access to device types.",
)
@click.option(
    "--repo-url",
    envvar="REPO_URL",
    default="https://github.com/netbox-community/devicetype-library.git",
    show_default=True,
    help="Git URL for the Device Type Library.",
)
@click.option(
    "--repo-branch",
    envvar="REPO_BRANCH",
    default="master",
    show_default=True,
    help="Branch to clone or pull.",
)
@click.option(
    "--vendors",
    envvar="VENDORS",
    multiple=True,
    help="Vendors to import (repeatable; comma-separated values accepted).",
)
@click.option(
    "--slugs",
    envvar="SLUGS",
    multiple=True,
    help="Device type slugs to import (repeatable; comma-separated values accepted).",
)
@click.option(
    "--ignore-cert-errors",
    envvar="IGNORE_CERT_ERRORS",
    is_flag=True,
    default=False,
    help="Disable TLS certificate verification.",
)
def cli(netbox_url, netbox_token, repo_url, repo_branch, vendors, slugs, ignore_cert_errors):
    

    vendors = _normalize(vendors)
    slugs = _normalize(slugs)

    logger.info(
        "NetBox: {} | Vendors: {} | Slugs: {}",
        netbox_url,
        vendors or "all",
        slugs or "all",
    )

    repo_path = Path(__file__).resolve().parent.parent / "repo"

    args = SimpleNamespace(
        repo_url=repo_url,
        repo_branch=repo_branch,
        vendors=vendors,
        slugs=slugs,
        netbox_url=netbox_url,
        netbox_token=netbox_token,
        ignore_cert_errors=ignore_cert_errors,
        repo_path=repo_path,
    )
    try:
        asyncio.run(import_device_types(args))
    except KeyboardInterrupt:
        logger.warning("Process interrupted by user. Exiting...")
    except Exception as e:
        logger.opt(exception=True).error(f"Fatal error during execution: {e}")