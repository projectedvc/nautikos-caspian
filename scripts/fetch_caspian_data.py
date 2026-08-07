"""Build a compact, real Sentinel-2 time series for the Caspian Twin MVP.

The script uses the public Earth Search STAC catalogue and streams only the
requested Cloud Optimized GeoTIFF windows. It deliberately produces a small,
auditable demo data cube that can later be swapped for Copernicus Data Space
Sentinel Hub or Earth Engine processing without changing the web UI contract.
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
import requests
from PIL import Image
from rasterio.enums import Resampling
from rasterio.windows import from_bounds
from rasterio.warp import transform_bounds
from shapely.geometry import Point, shape


STAC_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-c1-l2a"
AOI_NAME = "Aktau coastal pilot"
AOI_BBOX = [51.08, 43.50, 51.43, 43.83]  # west, south, east, north
OUT_SIZE = 768
PUBLIC_DIR = Path(__file__).resolve().parents[1] / "public" / "data"
SCENE_DIR = PUBLIC_DIR / "sentinel"

RU_MONTHS = [
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]


@dataclass(frozen=True)
class Month:
    year: int
    month: int

    @property
    def key(self) -> str:
        return f"{self.year:04d}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{RU_MONTHS[self.month - 1]} {str(self.year)[2:]}"


def last_twelve_months(today: date) -> list[Month]:
    total = today.year * 12 + today.month - 1
    result: list[Month] = []
    for offset in range(11, -1, -1):
        value = total - offset
        result.append(Month(value // 12, value % 12 + 1))
    return result


def next_month(month: Month) -> Month:
    if month.month == 12:
        return Month(month.year + 1, 1)
    return Month(month.year, month.month + 1)


def iso_range(months: list[Month], today: date) -> str:
    start = f"{months[0].year:04d}-{months[0].month:02d}-01T00:00:00Z"
    end = datetime(
        today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc
    ).isoformat().replace("+00:00", "Z")
    return f"{start}/{end}"


def fetch_items(months: list[Month]) -> list[dict[str, Any]]:
    response = requests.post(
        STAC_URL,
        json={
            "collections": [COLLECTION],
            "bbox": AOI_BBOX,
            "datetime": iso_range(months, date.today()),
            "limit": 250,
            "sortby": [{"field": "properties.datetime", "direction": "asc"}],
        },
        timeout=60,
        headers={"User-Agent": "CaspianTwin-Hackathon-MVP/0.1"},
    )
    response.raise_for_status()
    return response.json()["features"]


def scene_month(item: dict[str, Any]) -> str:
    return item["properties"]["datetime"][:7]


def choose_monthly_scenes(
    items: list[dict[str, Any]], months: list[Month]
) -> list[tuple[Month, dict[str, Any]]]:
    center = Point((AOI_BBOX[0] + AOI_BBOX[2]) / 2, (AOI_BBOX[1] + AOI_BBOX[3]) / 2)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        try:
            covers_center = shape(item["geometry"]).covers(center)
        except Exception:
            covers_center = False
        if covers_center:
            grouped[scene_month(item)].append(item)

    selected: list[tuple[Month, dict[str, Any]]] = []
    for month in months:
        candidates = grouped.get(month.key, [])
        if not candidates:
            continue
        candidates.sort(
            key=lambda item: (
                float(item["properties"].get("eo:cloud_cover", 100)),
                abs(int(item["properties"]["datetime"][8:10]) - 15),
            )
        )
        selected.append((month, candidates[0]))
    return selected


def asset_href(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        asset = item.get("assets", {}).get(key)
        if asset and asset.get("href"):
            return asset["href"]
    available = ", ".join(sorted(item.get("assets", {}).keys()))
    raise KeyError(f"None of {keys!r} found. Available assets: {available}")


def read_band(href: str, resampling: Resampling = Resampling.bilinear) -> np.ndarray:
    env = {
        "AWS_NO_SIGN_REQUEST": "YES",
        "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
        "GDAL_HTTP_MULTIRANGE": "YES",
        "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    }
    with rasterio.Env(**env):
        with rasterio.open(href) as src:
            bounds = transform_bounds("EPSG:4326", src.crs, *AOI_BBOX, densify_pts=21)
            window = from_bounds(*bounds, transform=src.transform)
            data = src.read(
                1,
                window=window,
                out_shape=(OUT_SIZE, OUT_SIZE),
                boundless=True,
                fill_value=0,
                resampling=resampling,
            )
    return data.astype(np.float32)


def percentile_stretch(channel: np.ndarray, valid: np.ndarray) -> np.ndarray:
    values = channel[valid]
    if values.size == 0:
        return np.zeros_like(channel, dtype=np.uint8)
    low, high = np.percentile(values, (2, 98))
    if high <= low:
        high = low + 1
    scaled = np.clip((channel - low) / (high - low), 0, 1)
    scaled = np.power(scaled, 0.82)
    return (scaled * 255).astype(np.uint8)


def save_rgba(path: Path, rgba: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    image = Image.fromarray(rgba, "RGBA")
    if path.suffix.lower() == ".png":
        image.save(path, "PNG", optimize=True)
    else:
        image.save(path, "WEBP", quality=88, method=6)


def rgba_overlay(
    score: np.ndarray,
    positive: tuple[int, int, int],
    negative: tuple[int, int, int] | None = None,
    threshold: float = 0.15,
) -> np.ndarray:
    rgba = np.zeros((*score.shape, 4), dtype=np.uint8)
    magnitude = np.abs(score)
    alpha = np.clip((magnitude - threshold) / max(1e-6, 0.5 - threshold), 0, 1)
    alpha = (alpha * 205).astype(np.uint8)
    pos = score >= 0
    rgba[pos, :3] = positive
    if negative is None:
        rgba[~pos, :3] = positive
    else:
        rgba[~pos, :3] = negative
    rgba[..., 3] = alpha
    return rgba


def grid_events(
    score: np.ndarray,
    kind: str,
    title: str,
    action: str,
    count: int = 2,
    absolute: bool = False,
) -> list[dict[str, Any]]:
    grid = 24
    cell_h = score.shape[0] // grid
    cell_w = score.shape[1] // grid
    ranked: list[tuple[float, int, int]] = []
    for gy in range(grid):
        for gx in range(grid):
            block = score[gy * cell_h : (gy + 1) * cell_h, gx * cell_w : (gx + 1) * cell_w]
            value = float(np.nanmean(np.abs(block) if absolute else block))
            if math.isfinite(value):
                ranked.append((value, gx, gy))
    ranked.sort(reverse=True)
    events: list[dict[str, Any]] = []
    used: list[tuple[int, int]] = []
    for value, gx, gy in ranked:
        if any(abs(gx - ux) <= 3 and abs(gy - uy) <= 3 for ux, uy in used):
            continue
        used.append((gx, gy))
        west, south, east, north = AOI_BBOX
        lon = west + (gx + 0.5) / grid * (east - west)
        lat = north - (gy + 0.5) / grid * (north - south)
        confidence = int(np.clip(58 + value * 95, 58, 94))
        events.append(
            {
                "id": f"{kind}-{len(events) + 1}",
                "kind": kind,
                "title": title,
                "coordinates": [round(lon, 5), round(lat, 5)],
                "score": round(value, 3),
                "confidence": confidence,
                "action": action,
                "evidence": "Sentinel-2 L2A · временной сигнал · проверка на облачность",
            }
        )
        if len(events) >= count:
            break
    return events


def build() -> None:
    today = date.today()
    months = last_twelve_months(today)
    items = fetch_items(months)
    selected = choose_monthly_scenes(items, months)
    if len(selected) < 8:
        raise RuntimeError(f"Only {len(selected)} monthly scenes found; expected at least 8")

    SCENE_DIR.mkdir(parents=True, exist_ok=True)
    series: list[dict[str, Any]] = []
    ndvi_stack: list[np.ndarray] = []
    ndwi_stack: list[np.ndarray] = []

    for month, item in selected:
        print(f"{month.key}: {item['id']} cloud={item['properties'].get('eo:cloud_cover')}")
        blue = read_band(asset_href(item, "blue"))
        green = read_band(asset_href(item, "green"))
        red = read_band(asset_href(item, "red"))
        nir = read_band(asset_href(item, "nir", "nir08"))
        scl = read_band(asset_href(item, "scl"), Resampling.nearest)
        radiometric_valid = (red > 0) & (green > 0) & (blue > 0) & (nir > 0)
        clear_surface = ~np.isin(scl.astype(np.int16), [0, 1, 3, 8, 9, 10, 11])
        valid = radiometric_valid & clear_surface

        rgb = np.stack(
            [
                percentile_stretch(red, radiometric_valid),
                percentile_stretch(green, radiometric_valid),
                percentile_stretch(blue, radiometric_valid),
                np.where(radiometric_valid, 255, 0).astype(np.uint8),
            ],
            axis=-1,
        )
        image_name = f"{month.key}.webp"
        save_rgba(SCENE_DIR / image_name, rgb)

        eps = 1e-6
        ndvi = np.where(valid, (nir - red) / (nir + red + eps), np.nan)
        ndwi = np.where(valid, (green - nir) / (green + nir + eps), np.nan)
        ndvi_stack.append(ndvi)
        ndwi_stack.append(ndwi)
        series.append(
            {
                "key": month.key,
                "label": month.label,
                "date": item["properties"]["datetime"][:10],
                "cloud": round(float(item["properties"].get("eo:cloud_cover", 0)), 1),
                "image": f"/data/sentinel/{image_name}",
                "itemId": item["id"],
                "source": "Copernicus Sentinel-2 L2A via Earth Search/AWS Open Data",
            }
        )

    ndvi_cube = np.stack(ndvi_stack)
    ndwi_cube = np.stack(ndwi_stack)
    first_ndvi, last_ndvi = ndvi_cube[0], ndvi_cube[-1]
    first_ndwi, last_ndwi = ndwi_cube[0], ndwi_cube[-1]
    comparable = np.isfinite(first_ndvi) & np.isfinite(last_ndvi)
    ndvi_delta = np.where(comparable, last_ndvi - first_ndvi, 0)
    water_delta = np.where(
        comparable,
        (last_ndwi > 0.12).astype(float) - (first_ndwi > 0.12).astype(float),
        0,
    )

    change_rgba = rgba_overlay(ndvi_delta, (30, 226, 166), (255, 92, 102), threshold=0.12)
    water_mask = np.abs(water_delta) > 0
    change_rgba[water_mask & (water_delta > 0), :3] = (43, 181, 255)
    change_rgba[water_mask & (water_delta < 0), :3] = (255, 180, 48)
    change_rgba[water_mask, 3] = 205
    save_rgba(PUBLIC_DIR / "change.png", change_rgba)

    median_ndvi = np.nanmedian(ndvi_cube, axis=0)
    stability = 1 - np.clip(np.nanstd(ndvi_cube, axis=0) / 0.35, 0, 1)
    suitability = (
        np.clip(1 - np.abs(median_ndvi - 0.12) / 0.30, 0, 1) * 0.62
        + stability * 0.38
    )
    median_ndwi = np.nanmedian(ndwi_cube, axis=0)
    suitability[
        (median_ndvi < 0.02)
        | (median_ndvi > 0.42)
        | (median_ndwi > 0.08)
        | ~np.isfinite(median_ndvi)
    ] = 0
    positive_suitability = suitability[suitability > 0]
    suitability_cutoff = (
        float(np.percentile(positive_suitability, 96))
        if positive_suitability.size
        else 1.0
    )
    suitability_display = np.where(suitability >= suitability_cutoff, suitability, 0)
    suitability_rgba = rgba_overlay(
        suitability_display,
        (75, 244, 171),
        threshold=0.55,
    )
    save_rgba(PUBLIC_DIR / "suitability.png", suitability_rgba)

    vegetation_anomaly = np.nan_to_num(np.abs(last_ndvi - median_ndvi))
    vegetation_anomaly[(last_ndvi < 0.12) | ~np.isfinite(last_ndvi)] = 0
    positive_anomaly = vegetation_anomaly[vegetation_anomaly > 0]
    anomaly_cutoff = (
        max(0.08, float(np.percentile(positive_anomaly, 94)))
        if positive_anomaly.size
        else 1.0
    )
    anomaly_display = np.where(vegetation_anomaly >= anomaly_cutoff, vegetation_anomaly, 0)
    anomaly_rgba = rgba_overlay(
        anomaly_display,
        (208, 115, 255),
        threshold=0.07,
    )
    save_rgba(PUBLIC_DIR / "vegetation-anomaly.png", anomaly_rgba)

    lat_mid = (AOI_BBOX[1] + AOI_BBOX[3]) / 2
    width_km = (AOI_BBOX[2] - AOI_BBOX[0]) * 111.32 * math.cos(math.radians(lat_mid))
    height_km = (AOI_BBOX[3] - AOI_BBOX[1]) * 111.32
    pixel_area_km2 = width_km * height_km / (OUT_SIZE * OUT_SIZE)
    changed_area = float(np.sum(np.abs(ndvi_delta) > 0.18) * pixel_area_km2)
    water_area = float(np.sum(np.abs(water_delta) > 0) * pixel_area_km2)
    restore_area = float(np.sum(suitability_display > 0) * pixel_area_km2)

    events: list[dict[str, Any]] = []
    events += grid_events(
        np.abs(ndvi_delta),
        "change",
        "Устойчивое изменение покрова",
        "Сравнить исходный пиксель и отправить участок на полевую проверку.",
        count=3,
        absolute=False,
    )
    events += grid_events(
        suitability,
        "restore",
        "Кандидат на восстановление",
        "Проверить засоление, воду и выбрать местные кустарники вместо массовой посадки деревьев.",
        count=2,
    )
    events += grid_events(
        anomaly_display,
        "vegetation",
        "Аномальный рост растительности",
        "Провести фотофиксацию: спутник отмечает участок, но вид растения подтверждает эксперт.",
        count=2,
    )

    payload = {
        "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "aoi": {"name": AOI_NAME, "bbox": AOI_BBOX, "center": [51.18, 43.66]},
        "series": series,
        "layers": {
            "change": "/data/change.png",
            "suitability": "/data/suitability.png",
            "vegetation": "/data/vegetation-anomaly.png",
        },
        "metrics": {
            "scenes": len(series),
            "latestDate": series[-1]["date"],
            "changedAreaKm2": round(changed_area, 1),
            "waterChangeKm2": round(water_area, 1),
            "restorationCandidateKm2": round(restore_area, 1),
            "meanCloud": round(float(np.mean([entry["cloud"] for entry in series])), 1),
        },
        "events": events,
        "method": {
            "current": "Hybrid EO baseline: NDVI/NDWI time series + robust temporal anomaly scoring",
            "next": "AlphaEarth annual embeddings + Prithvi-EO-2.0 fine-tuning with field labels",
            "caveat": "Species and final planting decisions require field verification, soil salinity and water constraints.",
        },
    }
    PUBLIC_DIR.mkdir(parents=True, exist_ok=True)
    (PUBLIC_DIR / "caspian.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote {PUBLIC_DIR / 'caspian.json'}")


if __name__ == "__main__":
    os.environ.setdefault("PROJ_NETWORK", "OFF")
    build()
