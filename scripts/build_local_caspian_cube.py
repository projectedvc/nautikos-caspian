"""Build fixed local Nautikos products for the Caspian Sea only.

The builder queries the public Earth Search catalogue once, streams the
required Sentinel-2 COG windows, and writes deterministic yearly images to
D:\\CaspianTwinData\\cube. The web app reads those files before attempting any
remote processing request, so changing zoom, year or filter does not select a
new acquisition.

This first stage builds whole-Caspian annual overviews and all Sentinel-2
derived ecology layers. It is resumable: existing files are skipped unless
--force is supplied. Monthly animation and the 10 m coastal tile pyramid are
separate stages so a usable offline overview is available first.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import requests
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject


STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-c1-l2a"
CASPIAN_BBOX = (46.0, 36.0, 55.8, 47.4)
YEARS = tuple(range(2020, 2027))
DEFAULT_ROOT = Path(os.environ.get("NAUTIKOS_DATA_DIR", r"D:\CaspianTwinData\cube"))
TARGET_WIDTH = 1024
TARGET_HEIGHT = 1440
LAYERS = (
    "true-color",
    "shoreline",
    "water-quality",
    "vegetation",
    "coast-moisture",
    "soil-stress",
)


@dataclass
class Product:
    year: int
    scenes: list[dict[str, Any]]
    coverage: float
    files: dict[str, str]


def search_scenes(year: int) -> list[dict[str, Any]]:
    west, south, east, north = CASPIAN_BBOX
    step = (north - south) / 4
    unique: dict[str, dict[str, Any]] = {}
    for stripe in range(4):
        stripe_bbox = [west, south + stripe * step, east, south + (stripe + 1) * step]
        payload = {
            "collections": [COLLECTION],
            "bbox": stripe_bbox,
            "datetime": f"{year}-06-01T00:00:00Z/{year}-08-05T23:59:59Z",
            "query": {"eo:cloud_cover": {"lte": 15}},
            "limit": 100,
            "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
        }
        last_error: Exception | None = None
        for attempt in range(5):
            try:
                response = requests.post(
                    STAC_URL,
                    json=payload,
                    timeout=120,
                    headers={"User-Agent": "Nautikos-Caspian-local-cube/1.0"},
                )
                response.raise_for_status()
                for item in response.json().get("features", []):
                    unique[str(item.get("id"))] = item
                break
            except Exception as exc:
                last_error = exc
                if attempt < 4:
                    time.sleep(2 ** attempt)
        else:
            raise RuntimeError(f"Earth Search failed after retries: {last_error}")
    ordered = sorted(unique.values(), key=lambda item: float(item.get("properties", {}).get("eo:cloud_cover", 100)))
    # One least-cloudy acquisition per MGRS tile is enough for the annual
    # overview and prevents rereading the same 100 km tile dozens of times.
    by_grid: dict[str, dict[str, Any]] = {}
    for item in ordered:
        grid = str(item.get("properties", {}).get("grid:code") or item.get("id"))
        by_grid.setdefault(grid, item)
    return list(by_grid.values())


def href(item: dict[str, Any], key: str) -> str:
    asset = item.get("assets", {}).get(key)
    if not asset or not asset.get("href"):
        raise KeyError(f"{item.get('id')} has no {key} asset")
    return str(asset["href"])


def warp_asset(url: str, destination: np.ndarray, resampling: Resampling) -> None:
    env = {
        "AWS_NO_SIGN_REQUEST": "YES",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
        "GDAL_HTTP_MAX_RETRY": "4",
        "GDAL_HTTP_RETRY_DELAY": "1",
    }
    with rasterio.Env(**env):
        with rasterio.open(url) as source:
            reproject(
                source=rasterio.band(source, 1),
                destination=destination,
                src_transform=source.transform,
                src_crs=source.crs,
                src_nodata=source.nodata or 0,
                dst_transform=from_bounds(*CASPIAN_BBOX, TARGET_WIDTH, TARGET_HEIGHT),
                dst_crs="EPSG:4326",
                dst_nodata=0,
                resampling=resampling,
                init_dest_nodata=True,
                num_threads=2,
            )


def build_year(year: int) -> tuple[dict[str, np.ndarray], list[dict[str, Any]], float]:
    items = search_scenes(year)
    if not items:
        raise RuntimeError(f"No Sentinel-2 scenes found for {year}")

    print(f"  selected {len(items)} least-cloudy MGRS tiles", flush=True)
    bands = {name: np.zeros((TARGET_HEIGHT, TARGET_WIDTH), dtype=np.float32) for name in ("blue", "green", "red", "nir", "swir16")}
    filled = np.zeros((TARGET_HEIGHT, TARGET_WIDTH), dtype=bool)
    used: list[dict[str, Any]] = []

    for index, item in enumerate(items, 1):
        scl = np.zeros((TARGET_HEIGHT, TARGET_WIDTH), dtype=np.uint8)
        try:
            warp_asset(href(item, "scl"), scl, Resampling.nearest)
        except Exception as exc:
            print(f"  skip {item.get('id')}: SCL {exc}")
            continue
        clear = (scl > 0) & ~np.isin(scl, (1, 3, 8, 9, 10, 11)) & ~filled
        if not np.any(clear):
            continue

        warped: dict[str, np.ndarray] = {}
        try:
            for name in bands:
                target = np.zeros((TARGET_HEIGHT, TARGET_WIDTH), dtype=np.float32)
                warp_asset(href(item, name), target, Resampling.bilinear)
                warped[name] = target
        except Exception as exc:
            print(f"  skip {item.get('id')}: band {exc}")
            continue

        valid = clear
        for target in warped.values():
            valid &= target > 0
        if not np.any(valid):
            continue
        for name, target in warped.items():
            bands[name][valid] = target[valid]
        filled[valid] = True
        used.append({
            "id": item.get("id"),
            "date": str(item.get("properties", {}).get("datetime", ""))[:10],
            "cloud": item.get("properties", {}).get("eo:cloud_cover"),
        })
        coverage = float(filled.mean())
        print(f"  {index:03d}/{len(items)} {item.get('id')} -> {coverage * 100:.1f}%")
        if coverage >= 0.992:
            break
    return bands, used, float(filled.mean())


def stretch(channel: np.ndarray, valid: np.ndarray) -> np.ndarray:
    values = channel[valid]
    if values.size == 0:
        return np.zeros_like(channel, dtype=np.uint8)
    low, high = np.percentile(values, (2, 98))
    high = max(high, low + 1)
    scaled = np.clip((channel - low) / (high - low), 0, 1) ** 0.82
    return np.round(scaled * 255).astype(np.uint8)


def color_ramp(value: np.ndarray, stops: tuple[tuple[int, int, int], ...], alpha: int = 214) -> np.ndarray:
    t = np.clip(value, 0, 1)
    rgba = np.zeros((*value.shape, 4), dtype=np.uint8)
    if len(stops) == 2:
        left, right = np.array(stops[0]), np.array(stops[1])
        rgba[..., :3] = np.round(left + (right - left) * t[..., None]).astype(np.uint8)
    else:
        low, middle, high = (np.array(stop) for stop in stops)
        first = t <= 0.5
        rgba[first, :3] = np.round(low + (middle - low) * (t[first, None] * 2)).astype(np.uint8)
        rgba[~first, :3] = np.round(middle + (high - middle) * ((t[~first, None] - 0.5) * 2)).astype(np.uint8)
    rgba[..., 3] = alpha
    return rgba


def products_from_bands(bands: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
    blue, green, red, nir, swir = (bands[name] for name in ("blue", "green", "red", "nir", "swir16"))
    valid = (blue > 0) & (green > 0) & (red > 0) & (nir > 0) & (swir > 0)
    eps = 1e-5
    ndwi = (green - nir) / (green + nir + eps)
    ndvi = (nir - red) / (nir + red + eps)
    ndmi = (nir - swir) / (nir + swir + eps)
    bsi = ((swir + red) - (nir + blue)) / ((swir + red) + (nir + blue) + eps)

    rgb = np.stack((stretch(red, valid), stretch(green, valid), stretch(blue, valid), np.where(valid, 255, 0).astype(np.uint8)), axis=-1)

    shoreline = np.zeros((*ndwi.shape, 4), dtype=np.uint8)
    water_mask = valid & (ndwi > 0.02)
    shoreline[water_mask] = (8, 91, 151, 220)

    turbidity = np.clip((red - blue) / 1800 + 0.36, 0, 1)
    water_quality = color_ramp(turbidity, ((25, 151, 190), (239, 164, 48), (211, 57, 42)), 210)
    water_quality[~water_mask, 3] = 0

    vegetation = color_ramp(np.clip((ndvi + 0.1) / 0.8, 0, 1), ((172, 123, 58), (104, 159, 75), (24, 124, 67)), 206)
    vegetation[~valid | water_mask, 3] = 0

    moisture = color_ramp(np.clip((ndmi + 0.35) / 0.9, 0, 1), ((213, 138, 52), (102, 168, 91), (23, 108, 164)), 210)
    moisture[~valid | water_mask, 3] = 0

    stress = np.clip((bsi + 0.12) * 1.8 + np.maximum(0, 0.22 - ndvi), 0, 1)
    soil = color_ramp(stress, ((38, 138, 91), (240, 167, 47), (239, 64, 45)), 210)
    soil[~valid | water_mask, 3] = 0

    return {
        "true-color": rgb,
        "shoreline": shoreline,
        "water-quality": water_quality,
        "vegetation": vegetation,
        "coast-moisture": moisture,
        "soil-stress": soil,
    }


def save_webp(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(rgba, "RGBA").save(path, "WEBP", quality=90, method=6, exact=True)


def parse_years(value: str) -> list[int]:
    if value == "all":
        return list(YEARS)
    result = sorted({int(part) for part in value.split(",")})
    if any(year not in YEARS for year in result):
        raise argparse.ArgumentTypeError("years must be 2020..2026 or all")
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--years", type=parse_years, default=list(YEARS))
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    annual_root = args.root / "overviews" / "annual"
    manifest_path = args.root / "manifest.json"
    manifest: dict[str, Any] = {
        "name": "Nautikos local Caspian cube",
        "bbox": list(CASPIAN_BBOX),
        "collection": COLLECTION,
        "source": "Copernicus Sentinel-2 L2A via Earth Search public COGs",
        "period": "2020-06-01/2026-08-05",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "products": {},
    }
    if manifest_path.exists() and not args.force:
        try:
            manifest.update(json.loads(manifest_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    for year in args.years:
        expected = [annual_root / str(year) / f"{layer}.webp" for layer in LAYERS]
        if not args.force and all(path.exists() for path in expected):
            print(f"{year}: complete, skip")
            continue
        print(f"{year}: building whole-Caspian annual products")
        bands, scenes, coverage = build_year(year)
        rendered = products_from_bands(bands)
        files: dict[str, str] = {}
        for layer, image in rendered.items():
            output = annual_root / str(year) / f"{layer}.webp"
            save_webp(output, image)
            files[layer] = str(output.relative_to(args.root)).replace("\\", "/")
        manifest.setdefault("products", {})[str(year)] = Product(year, scenes, coverage, files).__dict__
        args.root.mkdir(parents=True, exist_ok=True)
        manifest["generatedAt"] = datetime.now(timezone.utc).isoformat()
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{year}: saved {len(files)} layers, coverage={coverage * 100:.1f}%")

    total = sum(path.stat().st_size for path in args.root.rglob("*") if path.is_file())
    print(f"Local Caspian cube: {total / 1024**3:.3f} GB at {args.root}")


if __name__ == "__main__":
    main()
