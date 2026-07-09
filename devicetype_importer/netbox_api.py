import asyncio
import contextlib
import glob
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

import httpx
import pynetbox
from loguru import logger

# ── Retry constants ──────────────────────────────────────────────────────────

#: HTTP status codes that indicate a transient server-side problem worth retrying.
_RETRIABLE_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
_MAX_RETRIES = 4
_RETRY_BASE_DELAY = 1.0  # seconds — doubled on each attempt (1 s, 2 s, 4 s, 8 s)

_image_md5_cache: dict[tuple, str] = {}


def _md5(path: str) -> str:
    stat = os.stat(path)
    key  = (path, stat.st_mtime_ns, stat.st_size)
    if key not in _image_md5_cache:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        _image_md5_cache[key] = h.hexdigest()
    return _image_md5_cache[key]


class _ImageUploadCache:
    """Persist MD5 hashes of successfully uploaded images across runs.

    After a successful upload the local file path is mapped to its MD5.  On
    the next run, if the local file has the same MD5 the upload is skipped
    entirely — no download from NetBox is needed to verify it.
    """

    _DEFAULT_PATH = Path.home() / ".cache" / "netbox-devicetype-importer" / "image_cache.json"

    def __init__(self, path: Path | None = None):
        self._path = path or self._DEFAULT_PATH
        self._lock = asyncio.Lock()
        self._data: dict[str, str] = self._load()

    def _load(self) -> dict[str, str]:
        try:
            if self._path.exists():
                return json.loads(self._path.read_text())
        except Exception as exc:
            logger.warning(f"⚠️ Could not load image upload cache ({self._path}): {exc}")
        return {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(self._data, indent=2))
        except Exception as exc:
            logger.warning(f"⚠️ Could not save image upload cache ({self._path}): {exc}")

    def already_uploaded(self, local_path: str, md5: str) -> bool:
        """Return True if this exact file content was already uploaded successfully."""
        return self._data.get(local_path) == md5

    async def mark_uploaded(self, local_path: str, md5: str) -> None:
        """Record a successful upload and persist the cache to disk."""
        async with self._lock:
            self._data[local_path] = md5
            await asyncio.to_thread(self._save)


class NetBox:
    def __init__(self, netbox_url, netbox_token, ignore_ssl: bool = False):
        self.counter = Counter(
            added=0,
            updated=0,
            manufacturer=0,
            module_added=0,
            rack_added=0,
            images=0,
        )
        self.url        = netbox_url
        self.token      = netbox_token
        self.ignore_ssl = ignore_ssl
        self.modules             = False
        self.new_filters         = False
        self.rack_types_supported = False

        self._image_sem    = asyncio.Semaphore(3)   # Cap concurrent image uploads
        self._http_sem     = asyncio.Semaphore(20)  # Cap total concurrent NetBox requests
        self._upload_cache = _ImageUploadCache()    # Avoid re-uploading unchanged images

        self._connect_api()
        self._verify_compatibility()

        # Fetch and cache all manufacturers once
        self.existing_manufacturers = self._get_manufacturers()

        self.device_types = DeviceTypes(
            self.netbox, self.counter, self.ignore_ssl, self.new_filters, self._http_sem
        )

    # ── API Connection ───────────────────────────────────────────────────────

    def _connect_api(self):
        try:
            self.netbox = pynetbox.api(self.url, token=self.token)
            if self.ignore_ssl:
                logger.warning("⚠️ SSL verification is disabled (IGNORE_SSL_ERRORS=True).")
                self.netbox.http_session.verify = False
        except Exception as e:
            logger.opt(exception=True).critical(f"❌ Failed to connect to NetBox API: {e}")
            raise SystemExit(1)

    def _verify_compatibility(self):
        version_split = [int(x) for x in self.netbox.version.split(".")]

        if version_split[0] > 3 or (version_split[0] == 3 and version_split[1] >= 2):
            self.modules = True

        if version_split[0] >= 4:
            self.rack_types_supported = True

        if version_split[0] >= 4 and version_split[1] >= 1:
            self.new_filters = True
            logger.info(f"ℹ️ NetBox {self.netbox.version} detected — using new filter names.")

    # ── Manufacturers ────────────────────────────────────────────────────────

    def _get_manufacturers(self) -> dict:
        return {str(item): item for item in self.netbox.dcim.manufacturers.all()}

    def _ensure_manufacturer(self, manufacturer: dict):
        """Create a manufacturer on-the-fly if it doesn't exist in the cache."""
        name = manufacturer["name"]
        if name not in self.existing_manufacturers:
            logger.info(f"🚀 Manufacturer '{name}' not found — creating on-the-fly.")
            try:
                created = self.netbox.dcim.manufacturers.create([manufacturer])
                for m in created:
                    logger.info(f"✅ Manufacturer created: {m.name} - {m.id}")
                    self.counter.update({"manufacturer": 1})
                    self.existing_manufacturers[m.name] = m
            except pynetbox.RequestError as e:
                logger.error(f"❌ Error creating manufacturer '{name}': {e.error}")
        else:
            logger.debug(f"ℹ️ Manufacturer already exists: {name}")

    def create_manufacturers(self, vendors: list):
        """Batch-create all missing manufacturers in a single API call."""
        to_create = []
        for vendor in vendors:
            if vendor["name"] in self.existing_manufacturers:
                m = self.existing_manufacturers[vendor["name"]]
                logger.info(f"ℹ️ Manufacturer exists: {m.name} - {m.id}")
            else:
                to_create.append(vendor)
                logger.info(f"📝 Manufacturer queued for creation: {vendor['name']}")

        if to_create:
            try:
                created = self.netbox.dcim.manufacturers.create(to_create)
                for m in created:
                    logger.info(f"✅ Manufacturer created: {m.name} - {m.id}")
                    self.counter.update({"manufacturer": 1})
                    self.existing_manufacturers[m.name] = m
            except pynetbox.RequestError as e:
                logger.error(f"❌ Error creating manufacturers: {e.error}")

    # ── Device Types ─────────────────────────────────────────────────────────

    async def create_device_types(self, device_types_to_add: list):
        # Without a semaphore, all 500+ device types could fire at once
        sem = asyncio.Semaphore(10)  # Process 50 at a time

        async def _guarded(dt):
            async with sem:
                await self._process_device_type(dt)

        tasks = [_guarded(dt) for dt in device_types_to_add]
        await asyncio.gather(*tasks)

    async def _process_device_type(self, device_type: dict):
        src_file = device_type.pop("src")

        # Ensure manufacturer exists before creating device type
        await asyncio.to_thread(self._ensure_manufacturer, device_type["manufacturer"])

        # Handle front/rear images
        saved_images = {}
        image_base = os.path.dirname(src_file).replace("device-types", "elevation-images")
        for img_key in ("front_image", "rear_image"):
            if img_key in device_type:
                if device_type[img_key]:
                    pattern = f"{image_base}/{device_type['slug']}.{img_key.split('_')[0]}.*"
                    matches = glob.glob(pattern)
                    if matches:
                        saved_images[img_key] = matches[0]
                    else:
                        logger.warning(f"⚠️ Image not found using pattern: '{pattern}'")
                del device_type[img_key]

        # Get or create the device type
        try:
            dt = self.device_types.existing_device_types[device_type["model"]]
            logger.info(f"ℹ️ Device Type exists: {dt.manufacturer.name} - {dt.model} - {dt.id}")
        except KeyError:
            try:
                dt = await asyncio.to_thread(self.netbox.dcim.device_types.create, device_type)
                self.counter.update({"added": 1})
                logger.info(
                    f"✅ Device Type created: {dt.manufacturer.name} - {dt.model} - {dt.id}"
                )
            except pynetbox.RequestError as e:
                logger.error(
                    f"❌ Error creating device type "
                    f"{device_type['manufacturer']['name']} {device_type['model']}: {e.error}"
                )
                return

        # Create all port templates concurrently
        await self.device_types.create_all_ports(device_type, dt.id)

        # Upload images if any, skipping ones that haven't changed
        if saved_images:
            to_upload = await self._filter_unchanged_images(saved_images, dt)
            if to_upload:
                async with self._image_sem:
                    await self.device_types.upload_images(self.url, self.token, to_upload, dt.id)
                for local_path in to_upload.values():
                    await self._upload_cache.mark_uploaded(local_path, _md5(local_path))
            else:
                logger.info(f"ℹ️ Images already up to date for [{dt.model}], skipping.")

    async def _filter_unchanged_images(self, images: dict, dt) -> dict:
        """Return only images whose local content differs from what's already in NetBox.

        Check order:
        1. Local cache hit — if the file's MD5 matches the last successfully uploaded
           MD5, skip without contacting NetBox at all.
        2. Remote check — download the current image from NetBox and compare MD5s.
           This handles the first run and cases where NetBox was changed out-of-band.
        """
        headers = {"Authorization": f"Token {self.token}"}
        loop    = asyncio.get_running_loop()

        async def _check(img_key: str, local_path: str, client) -> tuple | None:
            local_md5 = await loop.run_in_executor(None, _md5, local_path)

            # Fast path: trust the local cache — no network call needed.
            if self._upload_cache.already_uploaded(local_path, local_md5):
                logger.debug(
                    f"✅ Image '{img_key}' unchanged (cache hit) for [{dt.model}], skipping."
                )
                return None

            # Slow path: no cache entry yet, or file changed — verify against NetBox.
            remote_url = getattr(dt, img_key, None)
            if not remote_url:
                return img_key, local_path
            try:
                response = await client.get(str(remote_url), headers=headers)
                if response.status_code != 200:
                    return img_key, local_path
                remote_md5 = hashlib.md5(response.content).hexdigest()
                if local_md5 != remote_md5:
                    return img_key, local_path
                # Remote matches local — seed the cache so future runs skip the download.
                await self._upload_cache.mark_uploaded(local_path, local_md5)
                logger.debug(f"✅ Image '{img_key}' unchanged for [{dt.model}], skipping.")
                return None
            except Exception as exc:
                logger.warning(
                    f"❌ Could not verify remote image '{img_key}' for [{dt.model}]: "
                    f"{exc} — re-uploading."
                )
                return img_key, local_path

        async with httpx.AsyncClient(verify=not self.ignore_ssl, timeout=30.0) as client:
            results = await asyncio.gather(*(
                _check(k, v, client) for k, v in images.items()
            ))

        return dict(r for r in results if r is not None)

    # ── Module Types ─────────────────────────────────────────────────────────

    async def create_module_types(self, module_types: list):
        """Fetch all existing module types once, then process concurrently."""
        loop = asyncio.get_running_loop()
        raw  = await loop.run_in_executor(None, lambda: list(self.netbox.dcim.module_types.all()))

        all_module_types: dict[str, dict] = {}
        for mt in raw:
            all_module_types.setdefault(mt.manufacturer.slug, {})[mt.model] = mt

        sem = asyncio.Semaphore(10)

        async def _guarded(mt):
            async with sem:
                await self._process_module_type(mt, all_module_types)

        await asyncio.gather(*[_guarded(mt) for mt in module_types])

    async def _process_module_type(self, curr_mt: dict, all_module_types: dict):
        # Ensure manufacturer exists before creating module type
        await asyncio.to_thread(self._ensure_manufacturer, curr_mt["manufacturer"])

        mfg_slug = curr_mt["manufacturer"]["slug"]
        try:
            mt_res = all_module_types[mfg_slug][curr_mt["model"]]
            logger.info(
                f"ℹ️ Module Type exists: {mt_res.manufacturer.name} - {mt_res.model} - {mt_res.id}"
            )
        except KeyError:
            try:
                # Strip fields that are not valid NetBox API params for module types:
                # - src: local file path, internal only
                # - profile: string in DTL YAMLs but NetBox expects a numeric ID
                # - attribute_data: newer DTL field, not universally supported
                # - front_image / rear_image: uploaded separately via PATCH
                _strip = {"src", "profile", "attribute_data", "front_image", "rear_image"}
                mt_payload = {k: v for k, v in curr_mt.items() if k not in _strip}
                mt_res = await asyncio.to_thread(self.netbox.dcim.module_types.create, mt_payload)
                self.counter.update({"module_added": 1})
                mfr = mt_res.manufacturer.name
                logger.info(f"✅ Module Type created: {mfr} - {mt_res.model} - {mt_res.id}")
            except pynetbox.RequestError as e:
                logger.error(f"❌ Error creating module type {curr_mt['model']}: {e.error}")
                return

        # Create all module port templates concurrently
        await self.device_types.create_all_module_ports(curr_mt, mt_res.id)

        # Look for front/rear images by model name in the module-images directory.
        # Module type YAMLs carry no front_image/rear_image fields — images are
        # discovered by convention: module-images/{Manufacturer}/{Model}.front.*
        saved_images = {}
        src_file   = curr_mt.get("src", "")
        image_base = os.path.dirname(src_file).replace("module-types", "module-images")
        for img_key in ("front_image", "rear_image"):
            side    = img_key.split("_")[0]  # "front" or "rear"
            pattern = f"{image_base}/{curr_mt['model']}.{side}.*"
            matches = glob.glob(pattern)
            if matches:
                saved_images[img_key] = matches[0]

        if saved_images:
            to_upload = await self._filter_unchanged_images(saved_images, mt_res)
            if to_upload:
                async with self._image_sem:
                    await self.device_types.upload_images(
                        self.url, self.token, to_upload, mt_res.id, endpoint="module-types"
                    )
                for local_path in to_upload.values():
                    await self._upload_cache.mark_uploaded(local_path, _md5(local_path))
            else:
                logger.info(f"ℹ️ Images already up to date for [{mt_res.model}], skipping.")

    # ── Rack Types ───────────────────────────────────────────────────────────

    async def create_rack_types(self, rack_types: list):
        """Fetch all existing rack types once, then process concurrently."""
        loop = asyncio.get_running_loop()
        raw  = await loop.run_in_executor(None, lambda: list(self.netbox.dcim.rack_types.all()))

        all_rack_types: dict[str, dict] = {}
        for rt in raw:
            all_rack_types.setdefault(rt.manufacturer.slug, {})[rt.model] = rt

        sem = asyncio.Semaphore(10)

        async def _guarded(rt):
            async with sem:
                await self._process_rack_type(rt, all_rack_types)

        await asyncio.gather(*[_guarded(rt) for rt in rack_types])

    async def _process_rack_type(self, curr_rt: dict, all_rack_types: dict):
        await asyncio.to_thread(self._ensure_manufacturer, curr_rt["manufacturer"])

        mfg_slug = curr_rt["manufacturer"]["slug"]
        try:
            rt_res = all_rack_types[mfg_slug][curr_rt["model"]]
            logger.info(
                f"ℹ️ Rack Type exists: {rt_res.manufacturer.name} - {rt_res.model} - {rt_res.id}"
            )
        except KeyError:
            try:
                rt_payload = {k: v for k, v in curr_rt.items() if k != "src"}
                rt_res = await asyncio.to_thread(
                    self.netbox.dcim.rack_types.create, rt_payload
                )
                self.counter.update({"rack_added": 1})
                logger.info(
                    f"✅ Rack Type created: "
                    f"{rt_res.manufacturer.name} - {rt_res.model} - {rt_res.id}"
                )
            except pynetbox.RequestError as e:
                logger.error(f"❌ Error creating rack type {curr_rt['model']}: {e.error}")


# ── DeviceTypes ──────────────────────────────────────────────────────────────

class DeviceTypes:
    def __init__(self, netbox, counter, ignore_ssl, new_filters, http_sem: asyncio.Semaphore):
        self.netbox      = netbox
        self.counter     = counter
        self.ignore_ssl  = ignore_ssl
        self.new_filters = new_filters
        self._http_sem   = http_sem  # shared semaphore — caps total concurrent NetBox requests
        # Cache all existing device types once at startup
        self.existing_device_types = {
            str(item): item for item in self.netbox.dcim.device_types.all()
        }

    # ── Filter Helpers ───────────────────────────────────────────────────────

    def _dt_filter(self, device_type_id: int) -> dict:
        return {"device_type_id" if self.new_filters else "devicetype_id": device_type_id}

    def _mt_filter(self, module_type_id: int) -> dict:
        return {"module_type_id" if self.new_filters else "moduletype_id": module_type_id}

    # ── Generic Helpers ──────────────────────────────────────────────────────

    def _fetch_existing(self, endpoint, filter_kwargs: dict) -> dict:
        """Fetch all matching objects from *endpoint* (raw, no retry)."""
        return {str(item): item for item in endpoint.filter(**filter_kwargs)}

    async def _fetch_existing_async(self, endpoint, filter_kwargs: dict) -> dict:
        """Async wrapper: acquires the HTTP semaphore per attempt and retries on transient errors.

        The semaphore is released before the backoff sleep so that other coroutines
        can make progress during the wait.
        """
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._http_sem:
                    return await asyncio.to_thread(self._fetch_existing, endpoint, filter_kwargs)
            except pynetbox.RequestError as exc:
                status = getattr(getattr(exc, "req", None), "status_code", None)
                if status not in _RETRIABLE_STATUSES or attempt == _MAX_RETRIES - 1:
                    raise
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "⚠️ NetBox HTTP {} fetching {}, retrying in {:.1f}s "
                    "(attempt {}/{})…",
                    status, endpoint, delay, attempt + 2, _MAX_RETRIES,
                )
                await asyncio.sleep(delay)  # semaphore already released; sleep is safe
        raise RuntimeError("unreachable")

    def _ports_to_create(self, ports: list, type_id: int, existing: dict, id_key: str) -> list:
        to_create = [p for p in ports if p["name"] not in existing]
        for p in to_create:
            p[id_key] = type_id
        return to_create

    async def _create_ports(
        self, endpoint, to_create: list, port_type: str,
        is_module: bool = False, parent_name: str = "",
    ):
        if not to_create:
            return
        for attempt in range(_MAX_RETRIES):
            try:
                async with self._http_sem:
                    created = await asyncio.to_thread(endpoint.create, to_create)
                for port in created:
                    type_str = f" ({port.type})" if hasattr(port, "type") and port.type else ""
                    logger.info(f"✅ {port_type} Created: {port.name}{type_str} [{parent_name}]")
                self.counter.update({"updated": len(created)})
                return
            except pynetbox.RequestError as e:
                status = getattr(getattr(e, "req", None), "status_code", None)
                if status not in _RETRIABLE_STATUSES or attempt == _MAX_RETRIES - 1:
                    logger.error(f"❌ Error creating {port_type}: {e.error}")
                    return
                delay = _RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "⚠️ NetBox HTTP {} creating {}, retrying in {:.1f}s "
                    "(attempt {}/{})…",
                    status, port_type, delay, attempt + 2, _MAX_RETRIES,
                )
                await asyncio.sleep(delay)  # semaphore already released by the failed `async with`

    def _link_bridges(self, bridge_map: dict, existing_ifaces: dict, parent_name: str):
        """PATCH interface templates with resolved bridge IDs after initial creation."""
        for iface_name, bridge_name in bridge_map.items():
            iface  = existing_ifaces.get(iface_name)
            bridge = existing_ifaces.get(bridge_name)
            if iface and bridge:
                iface.bridge = bridge.id
                iface.save()
                logger.info(f"✅ Bridge linked: {iface_name} → {bridge_name} [{parent_name}]")
            else:
                logger.warning(
                    f"⚠️ Could not resolve bridge '{bridge_name}' for interface "
                    f"'{iface_name}' [{parent_name}] — skipping."
                )

    # ── Missing Parent Port Helpers ──────────────────────────────────────────

    async def _ensure_parent_ports(
        self,
        ports: list,
        existing: dict,
        ref_key: str,
        endpoint,
        filt: dict,
        base_payload: dict,
        label: str,
    ) -> dict:
        """Auto-create parent port templates referenced by child ports but not yet existing."""
        missing = {p[ref_key] for p in ports if ref_key in p and p[ref_key] not in existing}
        if missing:
            to_create = [{"name": name, **base_payload} for name in missing]
            try:
                async with self._http_sem:
                    created = await asyncio.to_thread(endpoint.create, to_create)
                for p in created:
                    logger.info(f"✅ Auto-created missing {label}: {p.name} - {p.id}")
                    self.counter.update({"updated": 1})
            except pynetbox.RequestError as e:
                logger.error(f"❌ Error auto-creating missing {label}s: {e.error}")
            existing = await self._fetch_existing_async(endpoint, filt)
        return existing

    # ── Device Type Port Creation ────────────────────────────────────────────

    async def create_all_ports(self, device_type: dict, dt_id: int):
        """Fetch all existing port templates in parallel, then batch-create missing ones."""
        filt        = self._dt_filter(dt_id)
        nb          = self.netbox
        parent_name = device_type["model"]
        type_key    = "device_type"

        endpoints = [
            ("interfaces",           nb.dcim.interface_templates),
            ("power-ports",          nb.dcim.power_port_templates),
            ("console-ports",        nb.dcim.console_port_templates),
            ("power-outlets",        nb.dcim.power_outlet_templates),
            ("console-server-ports", nb.dcim.console_server_port_templates),
            ("rear-ports",           nb.dcim.rear_port_templates),
            ("front-ports",          nb.dcim.front_port_templates),
            ("device-bays",          nb.dcim.device_bay_templates),
            ("module-bays",          nb.dcim.module_bay_templates),
        ]

        # Only fetch endpoint types that the YAML actually uses; pull in parents as needed
        yaml_keys = set(device_type)
        needed    = {key for key, _ in endpoints if key in yaml_keys}
        if yaml_keys & {"power-outlets", "power-port"}:
            needed.add("power-ports")
        if "front-ports" in yaml_keys:
            needed.add("rear-ports")

        to_fetch = [(k, ep) for k, ep in endpoints if k in needed]
        results  = await asyncio.gather(*(
            self._fetch_existing_async(ep, filt) for _, ep in to_fetch
        ))
        existing = {key: {} for key, _ in endpoints}
        existing.update({key: result for (key, _), result in zip(to_fetch, results)})

        # Simple port types with no ordering dependencies
        for yaml_key, endpoint, label in [
            ("console-ports",        nb.dcim.console_port_templates,        "Console Port"),
            ("console-server-ports", nb.dcim.console_server_port_templates, "Console Server Port"),
            ("device-bays",          nb.dcim.device_bay_templates,          "Device Bay"),
            ("module-bays",          nb.dcim.module_bay_templates,          "Module Bay"),
        ]:
            if yaml_key in device_type:
                tc = self._ports_to_create(
                    device_type[yaml_key], dt_id, existing[yaml_key], type_key
                )
                await self._create_ports(endpoint, tc, label, parent_name=parent_name)

        # Interfaces — bridge fields reference other interfaces by name; resolve IDs post-creation
        if "interfaces" in device_type:
            tc = self._ports_to_create(
                device_type["interfaces"], dt_id, existing["interfaces"], type_key
            )
            if tc:
                bridge_map = {
                    iface["name"]: iface.pop("bridge") for iface in tc if "bridge" in iface
                }
                await self._create_ports(
                    nb.dcim.interface_templates, tc, "Interface", parent_name=parent_name
                )
                if bridge_map:
                    existing["interfaces"] = await self._fetch_existing_async(
                        nb.dcim.interface_templates, filt
                    )
                    await asyncio.to_thread(
                        self._link_bridges, bridge_map, existing["interfaces"], parent_name
                    )

        # Power Ports → Power Outlets (outlets reference a power port by name)
        pp_list = device_type.get("power-ports", device_type.get("power-port", []))
        if pp_list:
            tc = self._ports_to_create(pp_list, dt_id, existing["power-ports"], type_key)
            await self._create_ports(
                nb.dcim.power_port_templates, tc, "Power Port", parent_name=parent_name
            )
            existing["power-ports"] = await self._fetch_existing_async(
                nb.dcim.power_port_templates, filt
            )

        if "power-outlets" in device_type:
            tc = self._ports_to_create(
                device_type["power-outlets"], dt_id, existing["power-outlets"], type_key
            )
            if tc:
                existing["power-ports"] = await self._ensure_parent_ports(
                    tc, existing["power-ports"],
                    ref_key="power_port",
                    endpoint=nb.dcim.power_port_templates,
                    filt=filt,
                    base_payload={"device_type": dt_id},
                    label="Device Power Port",
                )
                for outlet in tc:
                    if "power_port" in outlet:
                        if pp := existing["power-ports"].get(outlet["power_port"]):
                            outlet["power_port"] = pp.id
                        else:
                            logger.warning(
                                f"⚠️ Power Port still missing for outlet "
                                f"'{outlet['name']}' — skipping link."
                            )
                            del outlet["power_port"]
                await self._create_ports(
                    nb.dcim.power_outlet_templates, tc, "Power Outlet", parent_name=parent_name
                )

        # Rear Ports → Front Ports (front ports reference a rear port by name)
        if "rear-ports" in device_type:
            tc = self._ports_to_create(
                device_type["rear-ports"], dt_id, existing["rear-ports"], type_key
            )
            await self._create_ports(
                nb.dcim.rear_port_templates, tc, "Rear Port", parent_name=parent_name
            )
            existing["rear-ports"] = await self._fetch_existing_async(
                nb.dcim.rear_port_templates, filt
            )

        if "front-ports" in device_type:
            tc = self._ports_to_create(
                device_type["front-ports"], dt_id, existing["front-ports"], type_key
            )
            if tc:
                existing["rear-ports"] = await self._ensure_parent_ports(
                    tc, existing["rear-ports"],
                    ref_key="rear_port",
                    endpoint=nb.dcim.rear_port_templates,
                    filt=filt,
                    base_payload={"device_type": dt_id, "positions": 1},
                    label="Device Rear Port",
                )
                for port in tc:
                    if rp := existing["rear-ports"].get(port.get("rear_port")):
                        port["rear_port"] = rp.id
                    else:
                        logger.warning(
                            f"⚠️ Rear Port still missing for front port '{port['name']}' - {dt_id}"
                        )
                await self._create_ports(
                    nb.dcim.front_port_templates, tc, "Front Port", parent_name=parent_name
                )

    # ── Module Type Port Creation ────────────────────────────────────────────

    async def create_all_module_ports(self, curr_mt: dict, mt_id: int):
        """Fetch all existing module port templates in parallel, then batch-create missing ones."""
        filt        = self._mt_filter(mt_id)
        nb          = self.netbox
        parent_name = curr_mt["model"]
        type_key    = "module_type"

        endpoints = [
            ("interfaces",           nb.dcim.interface_templates),
            ("power-ports",          nb.dcim.power_port_templates),
            ("console-ports",        nb.dcim.console_port_templates),
            ("power-outlets",        nb.dcim.power_outlet_templates),
            ("console-server-ports", nb.dcim.console_server_port_templates),
            ("rear-ports",           nb.dcim.rear_port_templates),
            ("front-ports",          nb.dcim.front_port_templates),
        ]

        # Only fetch endpoint types that the YAML actually uses; pull in parents as needed
        yaml_keys = set(curr_mt)
        needed    = {key for key, _ in endpoints if key in yaml_keys}
        if "power-outlets" in yaml_keys:
            needed.add("power-ports")
        if "front-ports" in yaml_keys:
            needed.add("rear-ports")

        to_fetch = [(k, ep) for k, ep in endpoints if k in needed]
        results  = await asyncio.gather(*(
            self._fetch_existing_async(ep, filt) for _, ep in to_fetch
        ))
        existing = {key: {} for key, _ in endpoints}
        existing.update({key: result for (key, _), result in zip(to_fetch, results)})

        # Simple port types with no ordering dependencies
        for yaml_key, endpoint, label in [
            ("console-ports",        nb.dcim.console_port_templates,        "Module Console Port"),
            ("console-server-ports", nb.dcim.console_server_port_templates,
             "Module Console Server Port"),
        ]:
            if yaml_key in curr_mt:
                tc = self._ports_to_create(
                    curr_mt[yaml_key], mt_id, existing[yaml_key], type_key
                )
                await self._create_ports(
                    endpoint, tc, label, is_module=True, parent_name=parent_name
                )

        # Interfaces — bridge fields reference other interfaces by name; resolve IDs post-creation
        if "interfaces" in curr_mt:
            tc = self._ports_to_create(
                curr_mt["interfaces"], mt_id, existing["interfaces"], type_key
            )
            if tc:
                bridge_map = {
                    iface["name"]: iface.pop("bridge") for iface in tc if "bridge" in iface
                }
                await self._create_ports(
                    nb.dcim.interface_templates, tc, "Module Interface",
                    is_module=True, parent_name=parent_name,
                )
                if bridge_map:
                    existing["interfaces"] = await self._fetch_existing_async(
                        nb.dcim.interface_templates, filt
                    )
                    await asyncio.to_thread(
                        self._link_bridges, bridge_map, existing["interfaces"], parent_name
                    )

        # Power Ports → Power Outlets (outlets reference a power port by name)
        if "power-ports" in curr_mt:
            tc = self._ports_to_create(
                curr_mt["power-ports"], mt_id, existing["power-ports"], type_key
            )
            await self._create_ports(
                nb.dcim.power_port_templates, tc, "Module Power Port",
                is_module=True, parent_name=parent_name,
            )
            existing["power-ports"] = await self._fetch_existing_async(
                nb.dcim.power_port_templates, filt
            )

        if "power-outlets" in curr_mt:
            tc = self._ports_to_create(
                curr_mt["power-outlets"], mt_id, existing["power-outlets"], type_key
            )
            if tc:
                existing["power-ports"] = await self._ensure_parent_ports(
                    tc, existing["power-ports"],
                    ref_key="power_port",
                    endpoint=nb.dcim.power_port_templates,
                    filt=filt,
                    base_payload={"module_type": mt_id},
                    label="Module Power Port",
                )
                for outlet in tc:
                    if "power_port" in outlet:
                        if pp := existing["power-ports"].get(outlet["power_port"]):
                            outlet["power_port"] = pp.id
                        else:
                            logger.warning(
                                f"⚠️ Module Power Port still missing for outlet "
                                f"'{outlet['name']}' — skipping link."
                            )
                            del outlet["power_port"]
                await self._create_ports(
                    nb.dcim.power_outlet_templates, tc, "Module Power Outlet",
                    is_module=True, parent_name=parent_name,
                )

        # Rear Ports → Front Ports (front ports reference a rear port by name)
        if "rear-ports" in curr_mt:
            tc = self._ports_to_create(
                curr_mt["rear-ports"], mt_id, existing["rear-ports"], type_key
            )
            await self._create_ports(
                nb.dcim.rear_port_templates, tc, "Module Rear Port",
                is_module=True, parent_name=parent_name,
            )
            existing["rear-ports"] = await self._fetch_existing_async(
                nb.dcim.rear_port_templates, filt
            )

        if "front-ports" in curr_mt:
            tc = self._ports_to_create(
                curr_mt["front-ports"], mt_id, existing["front-ports"], type_key
            )
            if tc:
                existing["rear-ports"] = await self._ensure_parent_ports(
                    tc, existing["rear-ports"],
                    ref_key="rear_port",
                    endpoint=nb.dcim.rear_port_templates,
                    filt=filt,
                    base_payload={"module_type": mt_id, "positions": 1},
                    label="Module Rear Port",
                )
                for port in tc:
                    if rp := existing["rear-ports"].get(port.get("rear_port")):
                        port["rear_port"] = rp.id
                    else:
                        logger.warning(
                            f"⚠️ Module Rear Port still missing for front port "
                            f"'{port['name']}' - {mt_id}"
                        )
                await self._create_ports(
                    nb.dcim.front_port_templates, tc, "Module Front Port",
                    is_module=True, parent_name=parent_name,
                )

    # ── Image Upload ─────────────────────────────────────────────────────────

    async def upload_images(
        self,
        baseurl: str,
        token: str,
        images: dict,
        type_id: int,
        endpoint: str = "device-types",
    ):
        url     = f"{baseurl}/api/dcim/{endpoint}/{type_id}/"
        headers = {"Authorization": f"Token {token}"}

        for attempt in range(3):
            try:
                with contextlib.ExitStack() as stack:
                    files = {
                        k: (os.path.basename(v), stack.enter_context(open(v, "rb")))
                        for k, v in images.items()
                    }
                    async with httpx.AsyncClient(
                        verify=not self.ignore_ssl, timeout=120.0
                    ) as client:
                        response = await client.patch(url, headers=headers, files=files)
                break
            except httpx.ReadTimeout:
                if attempt == 2:
                    raise
                wait = 2 ** attempt
                logger.warning(
                    f"⚠️ Image upload timeout for {endpoint} {type_id}, "
                    f"retrying in {wait}s (attempt {attempt + 2}/3)..."
                )
                await asyncio.sleep(wait)

        keys, status = list(images.keys()), response.status_code
        logger.info(f"🖼️ Images {keys} uploaded to {endpoint} {type_id}: HTTP {status}")
        self.counter["images"] += len(images)