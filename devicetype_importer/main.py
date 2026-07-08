#!/usr/bin/env python3
"""Entry point for the NetBox Device Type Importer CLI tool."""
from __future__ import annotations
from devicetype_importer.log_config import DEFAULT_LOG_FILE, setup_logging
from devicetype_importer.cli import cli
from loguru import logger

def main():
    """Invoke the Click CLI entry point."""
    setup_logging(log_file=DEFAULT_LOG_FILE)
    cli()
