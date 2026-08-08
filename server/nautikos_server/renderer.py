from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache
from pathlib import Path

import boto3
import numpy as np
import rasterio
from PIL import Image
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


def ramp(values: np.ndarray, valid: np.ndarray, low: float, high: float, alpha: int = 205) -> np.ndarray:
    scaled = np.clip((values - low) / max(high - low, 1e-6), 0, 1)
    red = np.clip(2.2 * scaled - 0.25, 0, 1)
    green = np.clip(1.35 - np.abs(scaled - 0.5) * 2.3, 0, 1)
    blue = np.clip(1.15 - 2.0 * scaled, 0, 1)
    return np.dstack(
        [
            (red * 255).astype(np.uint8),
            (green * 255).astype(np.uint8),
            (blue * 255).astype(np.uint8),
            valid.astype(np.uint8) * alpha,
        ]
    )


class CatalogRenderer:
    """Render immutable XYZ tiles from the official CDSE L3 COG catalogue.

    Every first request is read from the fixed quarterly scene set and written
    to the local cache. Later map movements and Vercel requests never contact
    Copernicus again for the same tile.
    """

    def __init__(self, settings: Settings):
        self.settings = settings
        self.catalog_root = settings.nautikos_data_root / "catalog" / "sentinel-2-l3-quarterly"
        self.cache_root = settings.nautikos_data_root / "tiles"
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
        path = self.catalog_root / f"{year}.json"
        if not path.is_file():
            raise FileNotFoundError(f"catalog is not built for {year}")
        return json.loads(path.read_text(encoding="utf-8"))

    def cache_path(self, product: str, year: int, z: int, x: int, y: int) -> Path:
        return self.cache_root / product / str(year) / str(z) / str(x) / f"{y}.png"

    def _lock(self, key: str) -> threading.Lock:
        with self._locks_guard:
            return self._locks.setdefault(key, threading.Lock())

    def signed_url(self, bucket: str, key: str) -> str:
        return self.s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": bucket, "Key": key},
            ExpiresIn=21600,
        )

    def _read_asset(
        self,
        asset: dict,
        bounds: tuple[float, float, float, float],
        resampling: Resampling = Resampling.bilinear,
    ) -> np.ma.MaskedArray:
        url = self.signed_url(asset["bucket"], asset["key"])
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
                    data = vrt.read(1, masked=True, out_dtype="float32")
        mask = np.ma.getmaskarray(data) | ~np.isfinite(data.filled(np.nan))
        mask |= data.filled(-32768) <= -32000
        return np.ma.array(data.filled(0), mask=mask)

    def _mosaic(
        self,
        items: list[dict],
        bands: tuple[str, ...],
        bounds: tuple[float, float, float, float],
    ) -> tuple[dict[str, np.ndarray], np.ndarray]:
        sums = {band: np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.float64) for band in bands}
        counts = np.zeros((TILE_SIZE, TILE_SIZE), dtype=np.uint16)
        def read_item(item: dict) -> dict[str, np.ma.MaskedArray]:
            return {band: self._read_asset(item["assets"][band], bounds) for band in bands}

        # Low zoom tiles can intersect most of the Caspian scene grid. Opening
        # independent COG ranges concurrently removes request latency while the
        # CDSE account bandwidth limit still caps total transfer safely.
        workers = min(12, max(1, len(items)))
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="nautikos-cog") as pool:
            futures = [pool.submit(read_item, item) for item in items]
            for future in as_completed(futures):
                arrays = future.result()
                valid = np.ones((TILE_SIZE, TILE_SIZE), dtype=bool)
                for array in arrays.values():
                    valid &= ~np.ma.getmaskarray(array)
                if not np.any(valid):
                    continue
                counts[valid] += 1
                for band, array in arrays.items():
                    sums[band][valid] += array.data[valid]
        valid = counts > 0
        denominator = np.maximum(counts, 1)
        return {band: (values / denominator).astype(np.float32) for band, values in sums.items()}, valid

    def _rgba(self, product: str, arrays: dict[str, np.ndarray], valid: np.ndarray) -> np.ndarray:
        blue = arrays.get("B02")
        green = arrays.get("B03")
        red = arrays.get("B04")
        nir = arrays.get("B08")
        if product == "rgb":
            alpha = valid.astype(np.uint8) * 255
            return np.dstack([stretch(red), stretch(green), stretch(blue), alpha])

        epsilon = 1e-6
        ndwi = (green - nir) / (green + nir + epsilon)
        water = valid & (ndwi > -0.05)
        if product == "water_colour":
            return np.dstack([stretch(red), stretch(green), stretch(blue), water.astype(np.uint8) * 235])
        if product == "water_extent":
            rgba = np.zeros((TILE_SIZE, TILE_SIZE, 4), dtype=np.uint8)
            rgba[water] = (13, 128, 174, 205)
            edge = water & (
                ~np.roll(water, 1, 0) | ~np.roll(water, -1, 0) | ~np.roll(water, 1, 1) | ~np.roll(water, -1, 1)
            )
            rgba[edge] = (255, 198, 42, 245)
            return rgba
        if product == "turbidity":
            ndti = (red - green) / (red + green + epsilon)
            return ramp(ndti, water, -0.12, 0.22)
        if product == "suspended_matter":
            red_ratio = red / (green + epsilon)
            return ramp(red_ratio, water, 0.55, 1.35)
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
            if product == "rgb":
                bands = ("B04", "B03", "B02")
            elif product in {"water_colour"}:
                bands = ("B04", "B03", "B02", "B08")
            elif product in {"water_extent"}:
                bands = ("B03", "B08")
            elif product in {"turbidity", "suspended_matter"}:
                bands = ("B04", "B03", "B08")
            else:
                bands = ("B04", "B03", "B08")
            arrays, valid = self._mosaic(items, bands, bounds)
            rgba = self._rgba(product, arrays, valid)
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_suffix(f".{threading.get_ident()}.tmp")
            Image.fromarray(rgba, "RGBA").save(temporary, format="PNG", optimize=True)
            temporary.replace(destination)
            return destination
