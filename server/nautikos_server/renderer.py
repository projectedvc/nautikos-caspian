from __future__ import annotations

import json
import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

import boto3
import numpy as np
import rasterio
from PIL import Image, ImageFilter
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds

from .settings import Settings


TILE_SIZE = 256
WEB_MERCATOR_LIMIT = 20037508.342789244
SUPPORTED_PRODUCTS = {
    "rgb",
    "water_colour",
    "water_extent",
    "turbidity",
    "suspended_matter",
    "vegetation",
    "soil_stress",
    "oil_candidates",
}

LOCAL_SCENE_PRODUCTS = {
    "rivers",
    "water_extent",
    "coastal_vegetation",
    "oil_candidates",
}


def tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    tiles = 1 << z
    if not (0 <= x < tiles and 0 <= y < tiles):
        raise ValueError("tile coordinate is outside the zoom grid")
    span = (WEB_MERCATOR_LIMIT * 2) / tiles
    left = -WEB_MERCATOR_LIMIT + x * span
    right = left + span
    top = WEB_MERCATOR_LIMIT - y * span
    bottom = top - span
    return left, bottom, right, top


def intersects(first: list[float] | tuple[float, ...], second: tuple[float, ...]) -> bool:
    return first[0] < second[2] and first[2] > second[0] and first[1] < second[3] and first[3] > second[1]


def stretch(values: np.ndarray, low: float = 150.0, high: float = 3200.0) -> np.ndarray:
    return np.clip((values - low) * (255.0 / (high - low)), 0, 255).astype(np.uint8)


def ramp(
    values: np.ndarray,
    valid: np.ndarray,
    low: float,
    high: float,
    alpha: int = 205,
    anomaly_only: bool = False,
) -> np.ndarray:
    scaled = np.clip((values - low) / max(high - low, 1e-6), 0, 1)
    red = np.clip(2.2 * scaled - 0.25, 0, 1)
    green = np.clip(1.35 - np.abs(scaled - 0.5) * 2.3, 0, 1)
    blue = np.clip(1.15 - 2.0 * scaled, 0, 1)
    if anomaly_only:
        # Keep the photographic scene readable. Low/background values are
        # almost transparent; only increasingly unusual values become vivid.
        opacity = np.where(valid & (scaled > 0.08), 28 + scaled * (alpha - 28), 0)
    else:
        opacity = valid.astype(np.uint8) * alpha
    return np.dstack(
        [
            (red * 255).astype(np.uint8),
            (green * 255).astype(np.uint8),
            (blue * 255).astype(np.uint8),
            opacity.astype(np.uint8),
        ]
    )


def gaussian_smooth(values: np.ndarray, sigma: float) -> np.ndarray:
    """Small dependency-free float Gaussian for 256 px analytical tiles."""
    radius = max(1, int(round(sigma * 3)))
    coordinates = np.arange(-radius, radius + 1, dtype=np.float32)
    kernel = np.exp(-(coordinates * coordinates) / (2.0 * sigma * sigma))
    kernel /= kernel.sum()

    def convolve_line(line: np.ndarray) -> np.ndarray:
        padded = np.pad(line, radius, mode="reflect")
        return np.convolve(padded, kernel, mode="valid")

    horizontal = np.apply_along_axis(convolve_line, 1, values)
    return np.apply_along_axis(convolve_line, 0, horizontal).astype(np.float32)


class CatalogRenderer:
    """Render immutable XYZ tiles from the public ESA Sentinel-2 L2A COG archive.

    Every first request is read from the fixed July scene set and written to
    the local cache. Prewarming does this before a presentation, so later map
    movements and Vercel requests only read finished local PNG tiles.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalog_roots = (
            settings.nautikos_data_root / "catalog" / "sentinel-2-earth-search",
            settings.nautikos_data_root / "catalog" / "sentinel-2-l3-quarterly",
        )
        self.radar_catalog_roots = (
            settings.nautikos_data_root / "catalog" / "sentinel-1-earth-search",
            Path(__file__).resolve().parents[1] / "seed-data" / "catalog" / "sentinel-1-earth-search",
        )
        # v4 invalidates legacy TCI-based tiles. Those files used independent
        # per-scene display stretches and caused the dark vertical strips that
        # were visible in the previous deployment.
        self.cache_root = settings.nautikos_data_root / "tiles-v5"
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()
        endpoint = settings.cdse_s3_endpoint.rstrip("/")
        self.s3 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.cdse_s3_access_key,
            aws_secret_access_key=settings.cdse_s3_secret_key,
            region_name="default",
        )

    @lru_cache(maxsize=14)
    def catalog(self, year: int) -> dict:
        for root in self.catalog_roots:
            path = root / f"{year}.json"
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"catalog is not built for {year}")

    @lru_cache(maxsize=14)
    def radar_catalog(self, year: int) -> dict:
        for root in self.radar_catalog_roots:
            path = root / f"{year}.json"
            if path.is_file():
                return json.loads(path.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"Sentinel-1 catalogue is not built for {year}")

    def cache_path(self, product: str, year: int, z: int, x: int, y: int) -> Path:
        cache_product = "oil_candidates-v4" if product == "oil_candidates" else product
        return self.cache_root / cache_product / str(year) / str(z) / str(x) / f"{y}.png"

    def spectral_cache_path(self, year: int, z: int, x: int, y: int) -> Path:
        """One reflectance mosaic shared by every Sentinel-2 product.

        Reading the same COGs again for RGB, NDWI, turbidity, vegetation and
        soil made a single map position take minutes.  The first request now
        writes one compact spectral tile; every other filter for that year and
        position is then a local array operation.
        """
        return self.settings.nautikos_data_root / "spectral-v2" / str(year) / str(z) / str(x) / f"{y}.npz"

    def local_spectral_cache_path(self, year: int, z: int, x: int, y: int) -> Path:
        return self.settings.nautikos_data_root / "spectral-local-v1" / str(year) / str(z) / str(x) / f"{y}.npz"

    def local_filter_cache_path(self, product: str, year: int, z: int, x: int, y: int) -> Path:
        return self.settings.nautikos_data_root / "tiles-local-v1" / product / str(year) / str(z) / str(x) / f"{y}.png"

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def signed_url(self, bucket: str, key: str) -> str:
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=21600,
        )

    def _asset_url(self, asset: dict) -> str:
        url = asset.get("href")
        if not url or url.startswith("s3://"):
            return self.signed_url(asset["bucket"], asset["key"])
        local = self._local_asset_path(asset)
        return str(local) if local and local.is_file() else url

    def _local_asset_path(self, asset: dict) -> Path | None:
        url = asset.get("href")
        if not url or not url.startswith("http"):
            return None
        suffix = Path(urlparse(url).path).suffix or ".tif"
        return self.settings.nautikos_data_root / "raw" / "earth-search" / "assets" / f"{hashlib.sha256(url.encode()).hexdigest()}{suffix}"

    def _item_is_local(self, item: dict) -> bool:
        assets = item.get("assets", {})
        for name in ("B02", "B03", "B04", "B08", "scl"):
            asset = assets.get(name)
            local = self._local_asset_path(asset) if asset else None
            if local is None or not local.is_file():
                return False
        return True

    def _asset_is_local(self, item: dict, name: str) -> bool:
        asset = item.get("assets", {}).get(name)
        local = self._local_asset_path(asset) if asset else None
        return bool(local and local.is_file() and local.stat().st_size > 0)

    @staticmethod
    def _footprint_area(item: dict) -> float:
        bbox = item.get("bbox") or (0, 0, 0, 0)
        return max(0.0, float(bbox[2]) - float(bbox[0])) * max(0.0, float(bbox[3]) - float(bbox[1]))

    @lru_cache(maxsize=14)
    def stable_local_spectral_items(self, year: int) -> tuple[dict, ...]:
        """Pin two real local acquisitions per MGRS grid for every zoom.

        The selection is frozen for the life of the API process. Download
        progress therefore cannot swap scenes while the user zooms or moves
        the swipe divider.
        """
        grouped: dict[str, list[dict]] = {}
        for item in self.catalog(year).get("items", []):
            if not self._item_is_local(item):
                continue
            grouped.setdefault(str(item.get("grid") or item.get("id")), []).append(item)
        selected: list[dict] = []
        for group in grouped.values():
            group.sort(
                key=lambda item: (
                    -self._footprint_area(item),
                    float(item.get("cloud_cover", 1000.0)),
                    str(item.get("datetime", "")),
                )
            )
            selected.extend(group[:2])
        return tuple(selected)

    @lru_cache(maxsize=14)
    def stable_local_radar_items(self, year: int) -> tuple[dict, ...]:
        return tuple(
            item for item in self.radar_catalog(year).get("items", [])
            if self._asset_is_local(item, "vv")
        )

    def _read_asset(
        self,
        asset: dict,
        bounds: tuple[float, float, float, float],
        resampling: Resampling = Resampling.bilinear,
    ) -> np.ma.MaskedArray:
        url = self._asset_url(asset)
        transform = from_bounds(*bounds, TILE_SIZE, TILE_SIZE)
        env = {
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
            "GDAL_HTTP_MULTIPLEX": "YES",
            "VSI_CACHE": "TRUE",
            "VSI_CACHE_SIZE": 16 * 1024 * 1024,
        }
        with rasterio.Env(**env):
            with rasterio.open(url) as source:
                with WarpedVRT(
                    source,
                    crs="EPSG:3857",
                    transform=transform,
                    width=TILE_SIZE,
                    height=TILE_SIZE,
                    resampling=resampling,
                    src_nodata=source.nodata,
                ) as vrt:
                    data = vrt.read(int(asset.get("band", 1)), masked=True, out_dtype="float32")
        mask = np.ma.getmaskarray(data) | ~np.isfinite(data.filled(np.nan))
        mask |= data.filled(-32768) <= -32000
        return np.ma.array(data.filled(0), mask=mask)

    def _read_asset_bands(
        self,
        asset: dict,
        indexes: list[int],
        bounds: tuple[float, float, float, float],
    ) -> list[np.ma.MaskedArray]:
        """Read several bands from one COG with a single remote open."""
        url = self._asset_url(asset)
        transform = from_bounds(*bounds, TILE_SIZE, TILE_SIZE)
        env = {
            "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
            "GDAL_HTTP_MERGE_CONSECUTIVE_RANGES": "YES",
            "GDAL_HTTP_MULTIPLEX": "YES",
            "VSI_CACHE": "TRUE",
            "VSI_CACHE_SIZE": 16 * 1024 * 1024,
        }
        with rasterio.Env(**env):
            with rasterio.open(url) as source:
                with WarpedVRT(
                    source,
                    crs="EPSG:3857",
                    transform=transform,
                    width=TILE_SIZE,
                    height=TILE_SIZE,
                    resampling=Resampling.bilinear,
                    src_nodata=source.nodata,
                ) as vrt:
                    stack = vrt.read(indexes, masked=True, out_dtype="float32")
        arrays = []
        for data in stack:
            mask = np.ma.getmaskarray(data) | ~np.isfinite(data.filled(np.nan))
            mask |= data.filled(-32768) <= -32000
            arrays.append(np.ma.array(data.filled(0), mask=mask))
        return arrays

    def _mosaic(
        self,
        items: list[dict],
        bands: tuple[str, ...],
        bounds: tuple[float, float, float, float],
    ) -> tuple[dict[str, np.ndarray], np.ndarray, np.ndarray]:
        sums = {band: np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float64) for band in bands}
        counts = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint16)
        water_votes = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint16)
        scl_votes = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint16)
        def read_item(item: dict) -> tuple[dict[str, np.ma.MaskedArray], np.ma.MaskedArray | None]:
            arrays: dict[str, np.ma.MaskedArray] = {}
            grouped: dict[tuple[str, str, str], list[tuple[str, int, dict]]] = {}
            for band in bands:
                asset = item["assets"][band]
                identity = (asset.get("href", ""), asset.get("bucket", ""), asset.get("key", ""))
                grouped.setdefault(identity, []).append((band, int(asset.get("band", 1)), asset))
            for entries in grouped.values():
                names = [entry[0] for entry in entries]
                indexes = [entry[1] for entry in entries]
                values = self._read_asset_bands(entries[0][2], indexes, bounds)
                arrays.update(zip(names, values))
            scl_asset = item.get("assets", {}).get("scl")
            scl = self._read_asset(scl_asset, bounds, Resampling.nearest) if scl_asset else None
            return arrays, scl

        # Low zoom tiles can intersect most of the Caspian scene grid. Opening
        # independent COG ranges concurrently removes request latency while the
        # CDSE account bandwidth limit still caps total transfer safely.
        workers = min(12, max(1, len(items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nautikos-cog") as pool:
            futures = [pool.submit(read_item, item) for item in items]
            for future in as_completed(futures):
                arrays, scl = future.result()
                valid = np.ones((TILE_SIZE, TILE_SIZE), dtype=bool)
                for array in arrays.values():
                    valid &= ~np.ma.getmaskarray(array)
                # Sentinel-2 COGs use an all-zero pixel outside the real
                # detector footprint even when nodata is absent from metadata.
                # Mask the combined pixel, not individual zero-valued bands,
                # so genuinely dark water remains visible.
                valid &= np.any(np.stack([array.data > 0 for array in arrays.values()]), axis=0)
                if scl is not None:
                    # Exclude no-data, saturated pixels, cloud shadow, cloud,
                    # cirrus and snow.  This keeps every yearly product tied to
                    # real pixels while removing the rectangular cloud masks.
                    bad_scl = np.isin(scl.data.astype(np.uint8), (0, 1, 3, 8, 9, 10, 11))
                    valid &= ~np.ma.getmaskarray(scl) & ~bad_scl
                if not np.any(valid):
                    continue
                counts[valid] += 1
                if scl is not None:
                    scl_valid = valid & ~np.ma.getmaskarray(scl)
                    scl_votes[scl_valid] += 1
                    water_votes[scl_valid & (scl.data.astype(np.uint8) == 6)] += 1
                for band, array in arrays.items():
                    sums[band][valid] += array.data[valid]
        valid = counts > 0
        denominator = np.maximum(counts, 1)
        water_probability = water_votes.astype(np.float32) / np.maximum(scl_votes, 1)
        return {band: (values / denominator).astype(np.float32) for band, values in sums.items()}, valid, water_probability

    def _spectral_mosaic(
        self,
        year: int,
        z: int,
        x: int,
        y: int,
        items: list[dict],
        bounds: tuple[float, float, float, float],
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        cache = self.spectral_cache_path(year, z, x, y)
        if cache.is_file():
            with np.load(cache) as stored:
                arrays = {name: stored[name] for name in ("B02", "B03", "B04", "B08", "water_probability")}
                valid = stored["valid"].astype(bool)
            return arrays, valid

        cache_key = f"spectral/{year}/{z}/{x}/{y}"
        with self._lock(cache_key):
            if cache.is_file():
                with np.load(cache) as stored:
                    arrays = {name: stored[name] for name in ("B02", "B03", "B04", "B08", "water_probability")}
                    valid = stored["valid"].astype(bool)
                return arrays, valid

            # A whole-Caspian low-zoom tile can intersect most of the Caspian scene grid. Opening
            # independent COG ranges concurrently removes request latency while the
            # CDSE account bandwidth limit still caps total transfer safely.
            if z <= 7:
                grouped: dict[str, list[dict]] = {}
                for item in items:
                    grouped.setdefault(str(item.get("grid") or item.get("id")), []).append(item)
                items = [next((item for item in group if self._item_is_local(item)), group[0]) for group in grouped.values()]
            else:
                local_items = [item for item in items if self._item_is_local(item)]
                if local_items:
                    items = local_items

            arrays, valid, water_probability = self._mosaic(items, ("B02", "B03", "B04", "B08"), bounds)
            arrays["water_probability"] = water_probability
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(f".{threading.get_ident()}.tmp.npz")
            np.savez_compressed(temporary, **arrays, valid=valid.astype(np.uint8))
            temporary.replace(cache)
            return arrays, valid

    def _local_spectral_mosaic(
        self,
        year: int,
        z: int,
        x: int,
        y: int,
        bounds: tuple[float, float, float, float],
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        cache = self.local_spectral_cache_path(year, z, x, y)
        if cache.is_file():
            with np.load(cache) as stored:
                arrays = {name: stored[name] for name in ("B02", "B03", "B04", "B08", "water_probability")}
                return arrays, stored["valid"].astype(bool)
        key = f"spectral-local-v1/{year}/{z}/{x}/{y}"
        with self._lock(key):
            if cache.is_file():
                with np.load(cache) as stored:
                    arrays = {name: stored[name] for name in ("B02", "B03", "B04", "B08", "water_probability")}
                    return arrays, stored["valid"].astype(bool)
            geographic = transform_bounds("EPSG:3857", "EPSG:4326", *bounds, densify_pts=21)
            items = [
                item for item in self.stable_local_spectral_items(year)
                if item.get("bbox") and intersects(item["bbox"], geographic)
            ]
            if not items:
                raise FileNotFoundError(f"no pinned local Sentinel-2 scene intersects {year}/{z}/{x}/{y}")
            arrays, valid, water_probability = self._mosaic(items, ("B02", "B03", "B04", "B08"), bounds)
            arrays["water_probability"] = water_probability
            cache.parent.mkdir(parents=True, exist_ok=True)
            temporary = cache.with_suffix(f".{threading.get_ident()}.tmp.npz")
            np.savez_compressed(temporary, **arrays, valid=valid.astype(np.uint8))
            temporary.replace(cache)
            return arrays, valid

    def _radar_mosaic(
        self,
        items: list[dict],
        bounds: tuple[float, float, float, float],
    ) -> tuple[np.ndarray, np.ndarray]:
        def read_item(item: dict) -> np.ma.MaskedArray:
            return self._read_asset(item["assets"]["vv"], bounds, Resampling.bilinear)

        measurements: list[np.ndarray] = []
        workers = min(8, max(1, len(items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nautikos-sar") as pool:
            futures = [pool.submit(read_item, item) for item in items]
            for future in as_completed(futures):
                measurement = future.result()
                raw = measurement.data.astype(np.float32)
                good = ~np.ma.getmaskarray(measurement) & np.isfinite(raw) & (raw > 0)
                calibrated_relative = np.full(raw.shape, np.nan, dtype=np.float32)
                # Earth Search exposes the original GRD amplitude COG. For
                # screening we use relative dB inside one acquisition; the
                # layer is deliberately labelled as a candidate, never a
                # measured oil concentration.
                calibrated_relative[good] = 20.0 * np.log10(np.maximum(raw[good], 1e-6))
                measurements.append(calibrated_relative)
        if not measurements:
            return np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float32), np.zeros((TILE_SIZE, TILE_SIZE), dtype=bool)
        stack = np.stack(measurements)
        valid = np.any(np.isfinite(stack), axis=0)
        with np.errstate(all="ignore"):
            mosaic = np.nanmedian(stack, axis=0)
        return np.nan_to_num(mosaic, nan=0.0), valid

    def _oil_candidates(
        self,
        year: int,
        z: int,
        x: int,
        y: int,
        bounds: tuple[float, float, float, float],
        spectral: dict[str, np.ndarray],
        spectral_valid: np.ndarray,
    ) -> np.ndarray:
        geographic = transform_bounds("EPSG:3857", "EPSG:4326", *bounds, densify_pts=21)
        radar_items = [
            item
            for item in self.stable_local_radar_items(year)
            if item.get("bbox") and intersects(item["bbox"], geographic)
        ]
        if not radar_items:
            raise FileNotFoundError("no fixed Sentinel-1 scene intersects this tile")
        radar, radar_valid = self._radar_mosaic(radar_items, bounds)
        green, nir = spectral["B03"], spectral["B08"]
        ndwi = (green - nir) / (green + nir + 1e-6)
        scl_water = spectral.get("water_probability", np.zeros_like(ndwi)) >= 0.5
        water = spectral_valid & radar_valid & (scl_water | ((ndwi > 0.08) & (nir < 1800)))
        rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
        if np.count_nonzero(water) < 32:
            return rgba

        # Oil dampens centimetre-scale surface waves. Compare each pixel with
        # a broad local SAR background; only unusually dark, coherent water
        # patches receive alpha. PIL supplies deterministic local smoothing
        # without adding another server dependency.
        finite_fill = float(np.median(radar[water]))
        filled = np.where(radar_valid, radar, finite_fill)
        fine = gaussian_smooth(filled, 1.4)
        background = gaussian_smooth(filled, 7.0)
        dark = np.clip(background - fine, 0, None)
        # The Copernicus workflow treats pixels more than 3.5 dB darker than
        # their local background as candidates. A fixed physical threshold is
        # stable across tile boundaries and years; no per-tile percentile is
        # allowed here.
        score = np.clip((dark - 3.5) / 6.0, 0, 1)
        candidate = water & (dark > 3.5)
        # Remove thin scan/speckle traces. Operational slick candidates must
        # form a spatially coherent patch at the current map scale, not a
        # one-pixel line. The larger dilation restores the footprint after
        # erosion, while the small tile rim avoids convolution edge echoes.
        opened = (
            Image.fromarray(candidate.astype(np.uint8) * 255, "L")
            .filter(ImageFilter.MinFilter(5))
            .filter(ImageFilter.MaxFilter(9))
        )
        candidate &= np.asarray(opened) > 0
        candidate[:12, :] = False
        candidate[-12:, :] = False
        candidate[:, :12] = False
        candidate[:, -12:] = False
        rgba[..., 0] = (238 + score * 17).astype(np.uint8)
        rgba[..., 1] = (178 - score * 112).astype(np.uint8)
        rgba[..., 2] = (45 + score * 20).astype(np.uint8)
        rgba[..., 3] = np.where(candidate, 45 + score * 195, 0).astype(np.uint8)
        return rgba

    def _rgba(self, product: str, arrays: dict[str, np.ndarray], valid: np.ndarray) -> np.ndarray:
        blue = arrays.get("B02")
        green = arrays.get("B03")
        red = arrays.get("B04")
        nir = arrays.get("B08")
        if product == "rgb":
            alpha = valid.astype(np.uint8) * 255
            if "TCI_R" in arrays:
                return np.dstack(
                    [
                        np.clip(arrays["TCI_R"], 0, 255).astype(np.uint8),
                        np.clip(arrays["TCI_G"], 0, 255).astype(np.uint8),
                        np.clip(arrays["TCI_B"], 0, 255).astype(np.uint8),
                        alpha,
                    ]
                )
            return np.dstack([stretch(red), stretch(green), stretch(blue), alpha])

        epsilon = 1e-6
        ndwi = (green - nir) / (green + nir + epsilon)
        # SCL class 6 is the primary water decision. A conservative NDWI/NIR
        # fallback retains shallow and sediment-rich coastal water that SCL can
        # label as bare surface. Bright desert is explicitly excluded.
        scl_water = arrays.get("water_probability", np.zeros_like(ndwi)) >= 0.5
        water = valid & (scl_water | ((ndwi > 0.08) & (nir < 1800)))
        if product == "water_colour":
            return np.dstack([stretch(red), stretch(green), stretch(blue), water.astype(np.uint8) * 235])
        if product == "water_extent":
            rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
            strength = np.clip((ndwi + 0.02) / 0.55, 0, 1)
            rgba[..., 0] = (18 + strength * 12).astype(np.uint8)
            rgba[..., 1] = (112 + strength * 76).astype(np.uint8)
            rgba[..., 2] = (176 + strength * 65).astype(np.uint8)
            rgba[..., 3] = np.where(water, 70 + strength * 150, 0).astype(np.uint8)
            return rgba
        if product == "rivers":
            # Suppress the homogeneous interior of large open-water bodies.
            # Narrow waterways and their real NDWI edges remain bright red.
            local_water_fraction = gaussian_smooth(water.astype(np.float32), 5.0)
            waterways = water & (local_water_fraction < 0.94)
            strength = np.clip((ndwi - 0.02) / 0.45, 0, 1)
            rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
            rgba[..., 0] = 246
            rgba[..., 1] = (78 - strength * 48).astype(np.uint8)
            rgba[..., 2] = (70 - strength * 42).astype(np.uint8)
            rgba[..., 3] = np.where(waterways, 70 + strength * 175, 0).astype(np.uint8)
            return rgba
        if product == "turbidity":
            ndti = (red - green) / (red + green + epsilon)
            return ramp(ndti, water, -0.12, 0.22, anomaly_only=True)
        if product == "suspended_matter":
            red_ratio = red / (green + epsilon)
            return ramp(red_ratio, water, 0.55, 1.35, anomaly_only=True)
        if product == "vegetation":
            ndvi = (nir - red) / (nir + red + epsilon)
            land = valid & ~water & (ndvi > 0.05)
            scaled = np.clip((ndvi - 0.05) / 0.75, 0, 1)
            rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
            rgba[..., 0] = (176 - scaled * 132).astype(np.uint8)
            rgba[..., 1] = (118 + scaled * 116).astype(np.uint8)
            rgba[..., 2] = (55 + scaled * 40).astype(np.uint8)
            rgba[..., 3] = land.astype(np.uint8) * 205
            return rgba
        if product == "coastal_vegetation":
            ndvi = (nir - red) / (nir + red + epsilon)
            near_water = gaussian_smooth(water.astype(np.float32), 7.0) > 0.003
            coastal = valid & ~water & near_water & (ndvi > 0.08)
            strength = np.clip((ndvi - 0.08) / 0.72, 0, 1)
            rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
            rgba[..., 0] = (206 - strength * 166).astype(np.uint8)
            rgba[..., 1] = (118 + strength * 114).astype(np.uint8)
            rgba[..., 2] = (48 + strength * 35).astype(np.uint8)
            rgba[..., 3] = np.where(coastal, 50 + strength * 190, 0).astype(np.uint8)
            return rgba
        if product == "soil_stress":
            ndvi = (nir - red) / (nir + red + epsilon)
            stress = np.clip((0.28 - ndvi) / 0.5, 0, 1)
            land = valid & ~water & (ndvi < 0.3)
            return ramp(stress, land, 0, 1)
        raise ValueError(f"unsupported product: {product}")

    def render(self, product: str, year: int, z: int, x: int, y: int) -> Path:
        if product not in SUPPORTED_PRODUCTS:
            raise ValueError(f"unsupported product: {product}")
        destination = self.cache_path(product, year, z, x, y)
        if destination.is_file():
            return destination
        key = f"{product}/{year}/{z}/{x}/{y}"
        with self._lock(key):
            if destination.is_file():
                return destination
            bounds = tile_bounds(z, x, y)
            geographic = transform_bounds("EPSG:3857", "EPSG:4326", *bounds, densify_pts=21)
            catalog = self.catalog(year)
            items = [item for item in catalog["items"] if item.get("bbox") and intersects(item["bbox"], geographic)]
            if not items:
                raise FileNotFoundError("tile does not intersect the fixed scene set")
            # All products share one raw-reflectance mosaic. TCI files are not
            # used because their independent per-scene display stretches are
            # the source of visible dark/bright strips.
            arrays, valid = self._spectral_mosaic(year, z, x, y, items, bounds)
            rgba = (
                self._oil_candidates(year, z, x, y, bounds, arrays, valid)
                if product == "oil_candidates"
                else self._rgba(product, arrays, valid)
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(f".{threading.get_ident()}.tmp")
            Image.fromarray(rgba, "RGBA").save(temporary, format="PNG", optimize=True)
            temporary.replace(destination)
            return destination

    def render_local_filter(self, product: str, year: int, z: int, x: int, y: int) -> Path:
        if product not in LOCAL_SCENE_PRODUCTS:
            raise ValueError(f"unsupported local scene product: {product}")
        destination = self.local_filter_cache_path(product, year, z, x, y)
        if destination.is_file():
            return destination
        key = f"filter-local-v1/{product}/{year}/{z}/{x}/{y}"
        with self._lock(key):
            if destination.is_file():
                return destination
            bounds = tile_bounds(z, x, y)
            arrays, valid = self._local_spectral_mosaic(year, z, x, y, bounds)
            rgba = (
                self._oil_candidates(year, z, x, y, bounds, arrays, valid)
                if product == "oil_candidates"
                else self._rgba(product, arrays, valid)
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(f".{threading.get_ident()}.tmp")
            Image.fromarray(rgba, "RGBA").save(temporary, format="PNG", optimize=True)
            temporary.replace(destination)
            return destination
