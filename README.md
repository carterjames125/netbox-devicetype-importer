# NetBox Device Type Importer

A fast CLI tool for importing device types from the [NetBox Device Type Library](https://github.com/netbox-community/devicetype-library) into a [NetBox](https://netbox.dev/) instance. It clones or updates the library repository locally, parses YAML/JSON definitions in parallel using multiprocessing, and pushes them to NetBox via its API.

## Features

- Clones or pulls the Device Type Library automatically
- Parses YAML, YML, and JSON device type definitions in parallel (multiprocessing with thread fallback)
- Filters by vendor name or device type slug
- Supports SSL/TLS certificate error bypass for self-signed NetBox instances
- Configurable via environment variables or CLI flags
- Structured JSON logging or human-readable console output
- Optional file-based log rotation and retention

## Requirements

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) (recommended) or pip
- A running NetBox instance with API access

## Installation

### With uv (recommended)

```bash
git clone <this-repo>
cd netbox-devicetype-importer
uv sync
```

### With pip

```bash
git clone <this-repo>
cd netbox-devicetype-importer
pip install .
```

## Configuration

Copy the example environment file and fill in your values:

```bash
cp devicetype_importer/.env.example .env
```

| Variable | Required | Default | Description |
|---|---|---|---|
| `NETBOX_URL` | Yes | `http://localhost:8000` | Base URL of your NetBox instance |
| `NETBOX_TOKEN` | Yes | — | NetBox API token with read/write access to device types |
| `REPO_URL` | No | `https://github.com/netbox-community/devicetype-library.git` | Git URL of the device type library |
| `REPO_BRANCH` | No | `master` | Branch to clone/pull |
| `IGNORE_CERT_ERRORS` | No | `false` | Skip TLS certificate verification (`true`/`false`) |
| `VENDORS` | No | _(all)_ | Comma-separated vendor names to import |
| `SLUGS` | No | _(all)_ | Comma-separated device type slugs to import |
| `NETBOX_FEATURE_MODULES` | No | `false` | Enable module bay/module type import support |
| `LOG_LEVEL` | No | `INFO` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `LOG_JSON` | No | `false` | Output logs as JSON (`true`/`false`) |
| `LOG_FILE` | No | _(console only)_ | Path to write logs to a file |
| `LOG_ROTATION` | No | `10 MB` | Log file rotation policy |
| `LOG_RETENTION` | No | `7 days` | Log file retention policy |

## Usage

All configuration can be provided via `.env`, environment variables, or CLI flags. CLI flags override environment variables.

```bash
nb-dt-import [OPTIONS]
```

### Options

| Flag | Description |
|---|---|
| `--netbox-url URL` | NetBox base URL |
| `--netbox-token TOKEN` | NetBox API token |
| `--repo-url URL` | Git URL for the device type library |
| `--repo-branch BRANCH` | Branch to use |
| `--vendors VENDOR [...]` | One or more vendor names to import (space or comma separated) |
| `--slugs SLUG [...]` | One or more device type slugs to import (space or comma separated) |
| `--ignore-cert-errors` | Disable TLS certificate verification |

### Examples

Import all device types:

```bash
nb-dt-import
```

Import only Cisco and Juniper devices:

```bash
nb-dt-import --vendors cisco juniper
```

Import specific device types by slug:

```bash
nb-dt-import --slugs cisco-catalyst-9300 juniper-ex2300-48p
```

Import with inline credentials (no `.env`):

```bash
nb-dt-import --netbox-url https://netbox.example.com --netbox-token abc123
```

## Development

```bash
uv sync --group dev
```

Run linting:

```bash
uv run ruff check .
uv run ruff format .
```

Run tests:

```bash
uv run pytest
```

## License

MIT
