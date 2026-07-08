"""Repository management and device-type file parsing for the NetBox Device Type Importer.

DTLRepo clones or pulls the Device Type Library, discovers definition files,
and parses them in parallel using threads or multiple processes.
"""
import json
import os
import re
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from pathlib import Path

import git
import yaml
from loguru import logger


def _slugify(name: str) -> str:
    """Convert a name to a NetBox-compatible slug (letters, numbers, underscores, hyphens)."""
    slug = name.casefold()
    slug = re.sub(r"[^a-z0-9_-]+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-")


def _parse_data_file_worker(payload: tuple[str, tuple[str, ...]]) -> dict | None:
    """Process-safe worker to parse one YAML/YML/JSON file."""
    file_path, slugs = payload
    extension = Path(file_path).suffix.casefold()

    try:
        with open(file_path, encoding="utf-8") as f:
            if extension in (".yaml", ".yml"):
                data = yaml.safe_load(f)
            elif extension == ".json":
                data = json.load(f)
            else:
                return None
    except (yaml.YAMLError, json.JSONDecodeError, OSError):
        return None

    if not isinstance(data, dict):
        return None

    for required in ("manufacturer", "model", "slug"):
        if required not in data:
            return None

    if slugs and data["slug"] not in slugs:
        return None

    if isinstance(data["manufacturer"], str):
        name = data["manufacturer"]
        data["manufacturer"] = {
            "name": name,
            "slug": _slugify(name),
        }

    data["src"] = file_path
    return data


class DTLRepo:
    """Manages the Device Type Library repository and parses device-type definition files."""

    def __init__(self, args, repo_path: str):
        """Initialise the repo manager and clone or pull the library to repo_path."""
        self.args      = args
        self.repo_path = Path(repo_path).resolve()
        self.repo_url = getattr(args, "repo_url", getattr(args, "url", None))
        self.repo_branch = getattr(args, "repo_branch", getattr(args, "branch", "main"))
        if not self.repo_url:
            logger.error("Missing repository URL. Provide --repo-url or args.repo_url.")
            raise SystemExit(1)
        self.repo      = self._clone_or_pull()

    # ── Git Operations ───────────────────────────────────────────────────────
    def _clone_or_pull(self):
            """Clone the repo if it doesn't exist, otherwise pull latest changes."""
            logger.info(f"Checking for existing repo at '{self.repo_path}'...")
            if self.repo_path.exists() and (
                (self.repo_path / ".git").is_dir() or (self.repo_path / ".github").is_dir()
            ):
                logger.info(
                    f"Repo already exists at '{self.repo_path}' — pulling latest changes..."
                )
                try:
                    repo = git.Repo(self.repo_path)
                    origin = repo.remotes.origin
                    origin.pull(self.repo_branch)
                    logger.info(f"Repo updated to latest '{self.repo_branch}' branch.")
                    return repo
                except git.GitCommandError as e:
                    logger.error(f"Git pull failed: {e}. Attempting fresh clone...")
                    return self._fresh_clone()
            else:
                return self._fresh_clone()

    def _fresh_clone(self):
        """Perform a fresh clone of the Device Type Library."""
        logger.info(
            f"Cloning '{self.repo_url}' "
            f"(branch: {self.repo_branch}) "
            f"into '{self.repo_path}'..."
        )
        try:
            repo = git.Repo.clone_from(
                self.repo_url,
                self.repo_path,
                branch=self.repo_branch,
                depth=1,  # Shallow clone for speed
            )
            logger.info("Clone complete.")
            return repo
        except git.GitCommandError as e:
            logger.opt(exception=True).critical(f"Git clone failed: {e}")
            raise SystemExit(1)

    def clone_or_update_repo(self):
        """Public wrapper used by callers that expect an explicit update step."""
        self.repo = self._clone_or_pull()
        return self.repo

    # ── File Discovery ───────────────────────────────────────────────────────

    def get_devices(self, path: str, vendors: list) -> tuple[list, list]:
        """
        Discover all YAML/YML/JSON files under the given path,
        optionally filtered by vendor.
        Returns a tuple of (data_files, vendor_dicts).
        """
        base_path = Path(path)

        if vendors:
            logger.info(f"Filtering by vendors: {vendors}")
            data_files = []
            vendor_dirs = []
            for vendor in vendors:
                matches = []
                vendor_path = base_path / vendor
                for ext in ("yaml", "yml", "json"):
                    matches.extend(vendor_path.glob(f"*.{ext}"))
                if matches:
                    data_files.extend(str(match) for match in matches)
                    vendor_dirs.append(vendor_path)
                else:
                    logger.warning(f"No data files found for vendor '{vendor}' at '{path}'")
        else:
            data_files = []
            for ext in ("yaml", "yml", "json"):
                data_files.extend(str(match) for match in base_path.rglob(f"*.{ext}"))
            vendor_dirs = [d for d in base_path.iterdir() if d.is_dir()]

        vendors_out = [
            {
                "name": d.name,
                "slug": _slugify(d.name),
            }
            for d in sorted(set(vendor_dirs))
        ]

        logger.debug(f"Discovered {len(data_files)} data files across {len(vendors_out)} vendors.")
        return data_files, vendors_out

    # ── YAML Parsing ─────────────────────────────────────────────────────────

    def _parse_file(self, file_path: str, slugs: list) -> dict | None:
        """
        Parse a single YAML file. Returns the parsed dict or None if it
        should be skipped (slug filter, parse error, or missing required fields).
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.warning(f"Failed to parse YAML '{file_path}': {e}")
            return None
        except OSError as e:
            logger.warning(f"Failed to read file '{file_path}': {e}")
            return None

        if not isinstance(data, dict):
            logger.warning(f"Skipping '{file_path}': not a valid YAML mapping.")
            return None

        # Validate required fields
        for required in ("manufacturer", "model"):
            if required not in data:
                logger.warning(f"Skipping '{file_path}': missing required field '{required}'.")
                return None

        # Generate slug from model name if absent
        if "slug" not in data:
            data["slug"] = _slugify(data["model"])
            logger.debug(f"Auto-generated slug '{data['slug']}' for '{file_path}'.")

        # Apply slug filter if provided
        if slugs and data["slug"] not in slugs:
            logger.debug(f"Skipping '{file_path}': slug '{data['slug']}' not in filter list.")
            return None

        # Normalize manufacturer to dict format
        if isinstance(data["manufacturer"], str):
            name = data["manufacturer"]
            data["manufacturer"] = {
                "name": name,
                "slug": _slugify(name),
            }

        # Attach source file path for image resolution later
        data["src"] = file_path

        return data

    def parse_files(self, files: list, slugs: list | None = None) -> list:
        """
        Parse all YAML files in parallel using a ThreadPoolExecutor.
        Returns a list of valid device/module type dicts.
        """
        slugs = slugs or []
        results = []

        max_workers = min(32, os.cpu_count() or 4)
        logger.info(f"Parsing {len(files)} YAML files using {max_workers} threads...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._parse_file, f, slugs): f for f in files
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)

        logger.debug(f"Successfully parsed {len(results)} of {len(files)} files.")
        return results

    # ── JSON Parsing ─────────────────────────────────────────────────────────

    def _parse_json_file(self, file_path: str, slugs: list) -> dict | None:
        """
        Parse a single JSON file. Returns the parsed dict or None if it
        should be skipped (slug filter, parse error, or missing required fields).
        """
        try:
            with open(file_path, encoding="utf-8") as f:
                data = json.load(f)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse JSON '{file_path}': {e}")
            return None
        except OSError as e:
            logger.warning(f"Failed to read file '{file_path}': {e}")
            return None

        if not isinstance(data, dict):
            logger.warning(f"Skipping '{file_path}': not a valid JSON object.")
            return None

        for required in ("manufacturer", "model", "slug"):
            if required not in data:
                logger.warning(f"Skipping '{file_path}': missing required field '{required}'.")
                return None

        if slugs and data["slug"] not in slugs:
            logger.debug(f"Skipping '{file_path}': slug '{data['slug']}' not in filter list.")
            return None

        if isinstance(data["manufacturer"], str):
            name = data["manufacturer"]
            data["manufacturer"] = {
                "name": name,
                "slug": _slugify(name),
            }

        data["src"] = file_path
        return data

    def parse_json_files(self, files: list, slugs: list | None = None) -> list:
        """
        Parse all JSON files in parallel using a ThreadPoolExecutor.
        Returns a list of valid device/module type dicts.
        """
        slugs = slugs or []
        results = []
        max_workers = min(32, os.cpu_count() or 4)
        logger.info(f"Parsing {len(files)} JSON files using {max_workers} threads...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._parse_json_file, f, slugs): f for f in files
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)

        logger.debug(f"Successfully parsed {len(results)} of {len(files)} JSON files.")
        return results

    def _parse_data_file(self, file_path: str, slugs: list) -> dict | None:
        """
        Parse a single data file by extension using YAML or JSON parser.
        """
        return _parse_data_file_worker((file_path, tuple(slugs)))

    def parse_data_files(self, files: list, slugs: list | None = None) -> list:
        """
        Parse YAML/YML/JSON files in parallel by dispatching on file extension.
        """
        slugs = slugs or []
        results = []

        max_workers = min(32, os.cpu_count() or 4)
        logger.debug(
            f"Parsing {len(files)} mixed data files (yaml/yml/json) using {max_workers} threads..."
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(self._parse_data_file, f, slugs): f for f in files
            }
            for future in as_completed(futures):
                result = future.result()
                if result is not None:
                    results.append(result)

        logger.debug(f"Successfully parsed {len(results)} of {len(files)} mixed data files.")
        return results

    def parse_data_files_multiprocess(
        self,
        files: list,
        slugs: list | None = None,
        max_workers: int | None = None,
        chunk_size: int = 40,
        fallback_to_threads: bool = True,
    ) -> list:
        """Parse YAML/YML/JSON files using multiprocessing for higher throughput."""
        slugs = slugs or []
        worker_count = max_workers or max(2, min(os.cpu_count() or 2, 8))
        payloads = [(f, tuple(slugs)) for f in files]

        logger.debug(
            "Parsing {} mixed data files using {} worker processes...",
            len(files),
            worker_count,
        )

        try:
            with ProcessPoolExecutor(max_workers=worker_count) as executor:
                parsed = [
                    result
                    for result in executor.map(
                        _parse_data_file_worker, payloads, chunksize=chunk_size
                    )
                    if result is not None
                ]
            logger.debug(
                "Successfully parsed {} of {} mixed data files via multiprocessing.",
                len(parsed),
                len(files),
            )
            return parsed
        except Exception as exc:
            if not fallback_to_threads:
                raise
            logger.warning(
                "Multiprocessing parse failed ({}). Falling back to threaded parsing.",
                exc,
            )
            return self.parse_data_files(files=files, slugs=slugs)