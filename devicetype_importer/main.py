#!/usr/bin/env python3
"""Entry point for the NetBox Device Type Importer CLI tool."""
from argparse import Namespace

from loguru import logger

from devicetype_importer.cli import device_type_importer_cli
from devicetype_importer.config import Config
from devicetype_importer.log_config import setup_logging
from devicetype_importer.repo import DTLRepo


def main():
    """Clone/update the Device Type Library, parse definitions, and import them into NetBox."""
    setup_logging()
    config: Config = Config()
    args: Namespace = device_type_importer_cli(config)
    logger.info(f"Configuration: {config.to_dict(include_secrets=False)}")

    dtl_repo = DTLRepo(args, config.repo_path)
    dtl_repo.clone_or_update_repo()
    files, _vendors = dtl_repo.get_devices(
        path=config.repo_path / "device-types",
        vendors=args.vendors,
    )
    devices = dtl_repo.parse_data_files_multiprocess(files=files, slugs=args.slugs)
    logger.info(f"Parsed {len(devices)} device/module definitions.")