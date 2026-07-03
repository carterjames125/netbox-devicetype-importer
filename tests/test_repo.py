"""Unit tests for devicetype_importer.repo.

Covers the standalone _parse_data_file_worker process-pool helper and the
DTLRepo class: initialisation, file discovery, YAML/JSON parsing, and
parallel execution (threads and multiprocessing with thread fallback).
"""
import json
import yaml
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
from devicetype_importer.repo import DTLRepo, _parse_data_file_worker


# ── helpers ──────────────────────────────────────────────────────────────────

def _write_yaml(path: Path, data: dict) -> str:
    """Write data as YAML to path and return the path as a string."""
    path.write_text(yaml.dump(data), encoding="utf-8")
    return str(path)


def _write_json(path: Path, data: dict) -> str:
    """Write data as JSON to path and return the path as a string."""
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


VALID_DEVICE = {"manufacturer": "Cisco", "model": "ASR-1001", "slug": "cisco-asr-1001"}


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def mock_args():
    """Return a MagicMock satisfying the DTLRepo repo_url/repo_branch interface."""
    args = MagicMock()
    args.repo_url = "https://github.com/netbox-community/devicetype-library.git"
    args.repo_branch = "master"
    return args


@pytest.fixture()
def repo(mock_args, tmp_path):
    """Return a DTLRepo instance with the git clone step patched out."""
    with patch.object(DTLRepo, "_clone_or_pull", return_value=MagicMock()):
        instance = DTLRepo(mock_args, str(tmp_path))
    return instance


# ── _parse_data_file_worker ───────────────────────────────────────────────────

class TestParseDataFileWorker:
    """Tests for the standalone _parse_data_file_worker process-pool worker function."""

    def test_valid_yaml(self, tmp_path):
        """A valid YAML device file is parsed and returned as a dict with a 'src' key."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        result = _parse_data_file_worker((str(f), ()))
        assert result is not None
        assert result["slug"] == "cisco-asr-1001"
        assert result["src"] == str(f)

    def test_valid_yml_extension(self, tmp_path):
        """Files with a .yml extension are treated identically to .yaml files."""
        f = tmp_path / "device.yml"
        _write_yaml(f, VALID_DEVICE)
        result = _parse_data_file_worker((str(f), ()))
        assert result is not None

    def test_valid_json(self, tmp_path):
        """A valid JSON device file is parsed and returned as a dict."""
        f = tmp_path / "device.json"
        _write_json(f, VALID_DEVICE)
        result = _parse_data_file_worker((str(f), ()))
        assert result is not None
        assert result["slug"] == "cisco-asr-1001"

    def test_unknown_extension_returns_none(self, tmp_path):
        """Files with unsupported extensions (e.g. .txt) are skipped."""
        f = tmp_path / "device.txt"
        f.write_text("manufacturer: Cisco\n", encoding="utf-8")
        result = _parse_data_file_worker((str(f), ()))
        assert result is None

    def test_invalid_yaml_returns_none(self, tmp_path):
        """Malformed YAML that raises a parse error results in None."""
        f = tmp_path / "device.yaml"
        f.write_text("key: [unclosed bracket\n", encoding="utf-8")
        result = _parse_data_file_worker((str(f), ()))
        assert result is None

    def test_invalid_json_returns_none(self, tmp_path):
        """Malformed JSON that raises a decode error results in None."""
        f = tmp_path / "device.json"
        f.write_text("{not: valid json}", encoding="utf-8")
        result = _parse_data_file_worker((str(f), ()))
        assert result is None

    def test_missing_manufacturer_returns_none(self, tmp_path):
        """A device file without a 'manufacturer' field is skipped."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, {"model": "ASR-1001", "slug": "cisco-asr-1001"})
        result = _parse_data_file_worker((str(f), ()))
        assert result is None

    def test_missing_model_returns_none(self, tmp_path):
        """A device file without a 'model' field is skipped."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, {"manufacturer": "Cisco", "slug": "cisco-asr-1001"})
        result = _parse_data_file_worker((str(f), ()))
        assert result is None

    def test_missing_slug_returns_none(self, tmp_path):
        """A device file without a 'slug' field is skipped."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, {"manufacturer": "Cisco", "model": "ASR-1001"})
        result = _parse_data_file_worker((str(f), ()))
        assert result is None

    def test_non_dict_yaml_returns_none(self, tmp_path):
        """YAML that parses to a list rather than a mapping is skipped."""
        f = tmp_path / "device.yaml"
        f.write_text("- item1\n- item2\n", encoding="utf-8")
        result = _parse_data_file_worker((str(f), ()))
        assert result is None

    def test_slug_filter_match(self, tmp_path):
        """A file whose slug matches the filter tuple is returned."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        result = _parse_data_file_worker((str(f), ("cisco-asr-1001",)))
        assert result is not None

    def test_slug_filter_no_match(self, tmp_path):
        """A file whose slug is not in the filter tuple is skipped."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        result = _parse_data_file_worker((str(f), ("other-slug",)))
        assert result is None

    def test_empty_slug_filter_includes_all(self, tmp_path):
        """An empty slug filter tuple means all device files are included."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        result = _parse_data_file_worker((str(f), ()))
        assert result is not None

    def test_manufacturer_string_normalized_to_dict(self, tmp_path):
        """A plain-string manufacturer is expanded to a {name, slug} dict."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, {"manufacturer": "Cisco Systems", "model": "X", "slug": "cisco-x"})
        result = _parse_data_file_worker((str(f), ()))
        assert isinstance(result["manufacturer"], dict)
        assert result["manufacturer"]["name"] == "Cisco Systems"
        assert result["manufacturer"]["slug"] == "cisco-systems"

    def test_manufacturer_dict_preserved(self, tmp_path):
        """A manufacturer already in dict form is left unchanged."""
        f = tmp_path / "device.yaml"
        data = {**VALID_DEVICE, "manufacturer": {"name": "Cisco", "slug": "cisco"}}
        _write_yaml(f, data)
        result = _parse_data_file_worker((str(f), ()))
        assert result["manufacturer"]["name"] == "Cisco"
        assert result["manufacturer"]["slug"] == "cisco"

    def test_nonexistent_file_returns_none(self, tmp_path):
        """A path that does not exist on disk results in None."""
        result = _parse_data_file_worker((str(tmp_path / "missing.yaml"), ()))
        assert result is None


# ── DTLRepo.__init__ ──────────────────────────────────────────────────────────

class TestDTLRepoInit:
    """Tests for DTLRepo.__init__ — URL validation and attribute initialisation."""

    def test_raises_when_repo_url_missing(self, tmp_path):
        """SystemExit is raised when neither repo_url nor url is set on args."""
        args = MagicMock()
        args.repo_url = None
        args.url = None
        with pytest.raises(SystemExit):
            DTLRepo(args, str(tmp_path))

    def test_accepts_repo_url_from_args(self, mock_args, tmp_path):
        """repo_url is stored on the instance when supplied via args."""
        with patch.object(DTLRepo, "_clone_or_pull", return_value=MagicMock()):
            instance = DTLRepo(mock_args, str(tmp_path))
        assert instance.repo_url == mock_args.repo_url

    def test_accepts_repo_branch_from_args(self, mock_args, tmp_path):
        """repo_branch is stored on the instance when supplied via args."""
        mock_args.repo_branch = "develop"
        with patch.object(DTLRepo, "_clone_or_pull", return_value=MagicMock()):
            instance = DTLRepo(mock_args, str(tmp_path))
        assert instance.repo_branch == "develop"


# ── DTLRepo.get_devices ───────────────────────────────────────────────────────

class TestGetDevices:
    """Tests for DTLRepo.get_devices — file discovery with and without vendor filters."""

    def test_discovers_all_vendor_dirs(self, repo, tmp_path):
        """Without a vendor filter, all vendor subdirectories are returned."""
        for vendor in ("cisco", "juniper"):
            d = tmp_path / vendor
            d.mkdir()
            _write_yaml(d / "device.yaml", {**VALID_DEVICE, "slug": f"{vendor}-device"})

        files, vendors = repo.get_devices(str(tmp_path), vendors=[])
        vendor_names = {v["name"] for v in vendors}
        assert "cisco" in vendor_names
        assert "juniper" in vendor_names

    def test_discovers_yaml_files(self, repo, tmp_path):
        """YAML files under a vendor directory are included in the discovered file list."""
        d = tmp_path / "cisco"
        d.mkdir()
        _write_yaml(d / "device.yaml", VALID_DEVICE)
        files, _ = repo.get_devices(str(tmp_path), vendors=[])
        assert len(files) == 1
        assert files[0].endswith(".yaml")

    def test_discovers_json_files(self, repo, tmp_path):
        """JSON files under a vendor directory are included in the discovered file list."""
        d = tmp_path / "cisco"
        d.mkdir()
        _write_json(d / "device.json", VALID_DEVICE)
        files, _ = repo.get_devices(str(tmp_path), vendors=[])
        assert len(files) == 1
        assert files[0].endswith(".json")

    def test_vendor_filter_limits_results(self, repo, tmp_path):
        """Only files from the requested vendor directory are returned when filtering."""
        for vendor in ("cisco", "juniper", "arista"):
            d = tmp_path / vendor
            d.mkdir()
            _write_yaml(d / "device.yaml", {**VALID_DEVICE, "slug": f"{vendor}-device"})

        files, vendors = repo.get_devices(str(tmp_path), vendors=["cisco"])
        assert len(files) == 1
        assert "cisco" in files[0]

    def test_missing_vendor_returns_empty(self, repo, tmp_path):
        """Requesting a vendor with no matching directory returns empty lists."""
        files, vendors = repo.get_devices(str(tmp_path), vendors=["nonexistent"])
        assert files == []
        assert vendors == []

    def test_vendor_dict_contains_name_and_slug(self, repo, tmp_path):
        """Each vendor dict in the returned list has both 'name' and 'slug' keys."""
        d = tmp_path / "cisco"
        d.mkdir()
        _write_yaml(d / "device.yaml", VALID_DEVICE)
        _, vendors = repo.get_devices(str(tmp_path), vendors=[])
        assert all("name" in v and "slug" in v for v in vendors)

    def test_empty_vendor_dir_not_included(self, repo, tmp_path):
        """A vendor directory that contains no matching files yields an empty file list."""
        (tmp_path / "cisco").mkdir()
        files, _ = repo.get_devices(str(tmp_path), vendors=["cisco"])
        assert files == []


# ── DTLRepo._parse_file ───────────────────────────────────────────────────────

class TestParseFile:
    """Tests for DTLRepo._parse_file — YAML parsing, field validation, and slug filtering."""

    def test_valid_yaml_returns_dict(self, repo, tmp_path):
        """A well-formed YAML device file is parsed into a dict with a 'src' key."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        result = repo._parse_file(str(f), slugs=[])
        assert result["slug"] == "cisco-asr-1001"
        assert result["src"] == str(f)

    def test_invalid_yaml_returns_none(self, repo, tmp_path):
        """Malformed YAML that cannot be parsed returns None."""
        f = tmp_path / "device.yaml"
        f.write_text("key: [unclosed", encoding="utf-8")
        assert repo._parse_file(str(f), slugs=[]) is None

    def test_nonexistent_file_returns_none(self, repo, tmp_path):
        """A path that does not exist returns None."""
        assert repo._parse_file(str(tmp_path / "missing.yaml"), slugs=[]) is None

    def test_missing_required_field_returns_none(self, repo, tmp_path):
        """A YAML file missing a required field (e.g. 'slug') returns None."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, {"manufacturer": "Cisco", "model": "ASR-1001"})
        assert repo._parse_file(str(f), slugs=[]) is None

    def test_slug_filter_match(self, repo, tmp_path):
        """A file whose slug is in the filter list is returned."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        assert repo._parse_file(str(f), slugs=["cisco-asr-1001"]) is not None

    def test_slug_filter_no_match(self, repo, tmp_path):
        """A file whose slug is not in the filter list is skipped."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        assert repo._parse_file(str(f), slugs=["other-slug"]) is None

    def test_manufacturer_string_normalized(self, repo, tmp_path):
        """A plain-string manufacturer is expanded to a {name, slug} dict."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, {"manufacturer": "Cisco Systems", "model": "X", "slug": "cisco-x"})
        result = repo._parse_file(str(f), slugs=[])
        assert result["manufacturer"]["slug"] == "cisco-systems"

    def test_non_dict_yaml_returns_none(self, repo, tmp_path):
        """YAML that parses to a list rather than a mapping returns None."""
        f = tmp_path / "device.yaml"
        f.write_text("- a\n- b\n", encoding="utf-8")
        assert repo._parse_file(str(f), slugs=[]) is None


# ── DTLRepo._parse_json_file ──────────────────────────────────────────────────

class TestParseJsonFile:
    """Tests for DTLRepo._parse_json_file — JSON parsing, field validation, and slug filtering."""

    def test_valid_json_returns_dict(self, repo, tmp_path):
        """A well-formed JSON device file is parsed into a dict with a 'src' key."""
        f = tmp_path / "device.json"
        _write_json(f, VALID_DEVICE)
        result = repo._parse_json_file(str(f), slugs=[])
        assert result["slug"] == "cisco-asr-1001"
        assert result["src"] == str(f)

    def test_invalid_json_returns_none(self, repo, tmp_path):
        """Malformed JSON that cannot be decoded returns None."""
        f = tmp_path / "device.json"
        f.write_text("{invalid json}", encoding="utf-8")
        assert repo._parse_json_file(str(f), slugs=[]) is None

    def test_nonexistent_file_returns_none(self, repo, tmp_path):
        """A path that does not exist returns None."""
        assert repo._parse_json_file(str(tmp_path / "missing.json"), slugs=[]) is None

    def test_missing_required_field_returns_none(self, repo, tmp_path):
        """A JSON file missing a required field (e.g. 'slug') returns None."""
        f = tmp_path / "device.json"
        _write_json(f, {"manufacturer": "Cisco", "model": "ASR-1001"})
        assert repo._parse_json_file(str(f), slugs=[]) is None

    def test_slug_filter_no_match(self, repo, tmp_path):
        """A file whose slug is not in the filter list is skipped."""
        f = tmp_path / "device.json"
        _write_json(f, VALID_DEVICE)
        assert repo._parse_json_file(str(f), slugs=["other-slug"]) is None

    def test_manufacturer_string_normalized(self, repo, tmp_path):
        """A plain-string manufacturer is expanded to a {name, slug} dict."""
        f = tmp_path / "device.json"
        _write_json(f, {"manufacturer": "Cisco Systems", "model": "X", "slug": "cisco-x"})
        result = repo._parse_json_file(str(f), slugs=[])
        assert result["manufacturer"]["slug"] == "cisco-systems"

    def test_non_dict_json_returns_none(self, repo, tmp_path):
        """JSON that parses to a list rather than an object returns None."""
        f = tmp_path / "device.json"
        f.write_text('["a", "b"]', encoding="utf-8")
        assert repo._parse_json_file(str(f), slugs=[]) is None


# ── DTLRepo.parse_files ───────────────────────────────────────────────────────

class TestParseFiles:
    """Tests for DTLRepo.parse_files — parallel YAML parsing via ThreadPoolExecutor."""

    def test_returns_all_valid_files(self, repo, tmp_path):
        """All valid YAML files in the input list are parsed and returned."""
        files = []
        for i in range(4):
            f = tmp_path / f"device{i}.yaml"
            _write_yaml(f, {**VALID_DEVICE, "slug": f"vendor-model-{i}"})
            files.append(str(f))
        results = repo.parse_files(files)
        assert len(results) == 4

    def test_filters_invalid_files(self, repo, tmp_path):
        """Invalid YAML files are silently dropped; only valid devices are returned."""
        valid = tmp_path / "valid.yaml"
        _write_yaml(valid, VALID_DEVICE)
        invalid = tmp_path / "invalid.yaml"
        invalid.write_text("- not: a: device\n", encoding="utf-8")
        results = repo.parse_files([str(valid), str(invalid)])
        assert len(results) == 1

    def test_empty_file_list_returns_empty(self, repo):
        """An empty input list returns an empty list without error."""
        assert repo.parse_files([]) == []

    def test_applies_slug_filter(self, repo, tmp_path):
        """Files whose slug is not in the filter list are excluded from results."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        results = repo.parse_files([str(f)], slugs=["other-slug"])
        assert results == []


# ── DTLRepo.parse_data_files ──────────────────────────────────────────────────

class TestParseDataFiles:
    """Tests for DTLRepo.parse_data_files — mixed YAML/JSON parallel parsing."""

    def test_parses_mixed_yaml_and_json(self, repo, tmp_path):
        """Both YAML and JSON device files in the same list are parsed correctly."""
        yaml_f = tmp_path / "device.yaml"
        json_f = tmp_path / "device.json"
        _write_yaml(yaml_f, VALID_DEVICE)
        _write_json(json_f, {**VALID_DEVICE, "slug": "cisco-asr-other"})
        results = repo.parse_data_files([str(yaml_f), str(json_f)])
        assert len(results) == 2

    def test_empty_returns_empty(self, repo):
        """An empty input list returns an empty list without error."""
        assert repo.parse_data_files([]) == []


# ── DTLRepo.parse_data_files_multiprocess ────────────────────────────────────

class TestParseDataFilesMultiprocess:
    """Tests for DTLRepo.parse_data_files_multiprocess — multiprocessing path and thread fallback."""

    def test_parses_yaml_files(self, repo, tmp_path):
        """YAML files are parsed correctly via the multiprocessing executor."""
        files = []
        for i in range(3):
            f = tmp_path / f"device{i}.yaml"
            _write_yaml(f, {**VALID_DEVICE, "slug": f"vendor-model-{i}"})
            files.append(str(f))
        results = repo.parse_data_files_multiprocess(files)
        assert len(results) == 3

    def test_empty_returns_empty(self, repo):
        """An empty input list returns an empty list without error."""
        assert repo.parse_data_files_multiprocess([]) == []

    def test_applies_slug_filter(self, repo, tmp_path):
        """Files whose slug is not in the filter list are excluded from results."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        results = repo.parse_data_files_multiprocess([str(f)], slugs=["other-slug"])
        assert results == []

    def test_falls_back_to_threads_on_failure(self, repo, tmp_path):
        """When the process pool raises, threaded parsing is used as fallback."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        with patch("devicetype_importer.repo.ProcessPoolExecutor", side_effect=RuntimeError("no mp")):
            results = repo.parse_data_files_multiprocess([str(f)], fallback_to_threads=True)
        assert len(results) == 1

    def test_raises_when_fallback_disabled(self, repo, tmp_path):
        """When fallback_to_threads=False, a process pool failure propagates as an exception."""
        f = tmp_path / "device.yaml"
        _write_yaml(f, VALID_DEVICE)
        with patch("devicetype_importer.repo.ProcessPoolExecutor", side_effect=RuntimeError("no mp")):
            with pytest.raises(RuntimeError):
                repo.parse_data_files_multiprocess([str(f)], fallback_to_threads=False)
