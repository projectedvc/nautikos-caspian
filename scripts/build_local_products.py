"""Build Nautikos annual products from the fixed local Sentinel-2 cube.

Inputs are four north-to-south, five-band GeoTIFF stripes per year.  Every
visual layer is derived from those exact pixels, so changing layer or zoom
cannot silently switch acquisition.  The products are deliberately scoped to
the Caspian bounding box only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.merge import merge


ROOT = Path(r"D:\CaspianTwinData\cube")
YEARS = range(2020, 2027)
BBOX = [46.0, 36.0, 55.8, 47.4]
BANDS = ["B02-blue", "B03-green", "B04-red", "B08-nir", "B11-swir"]


def safe_ratio(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return (a - b) / np.maximum(a + b, 1e-6)


def rgba_from_stops(values: np.ndarray, stops: list[tuple[float, tuple[int, int, int]]], mask: np.ndarray, alpha: int = 220) -> np.ndarray:
    out = np.zeros((*values.shape, 4), dtype=np.uint8)
    xs = np.array([stop[0] for stop in stops], dtype=np.float32)
    for channel in range(3):
        ys = np.array([stop[1][channel] for stop in stops], dtype=np.float32)
        out[..., channel] = np.interp(values, xs, ys).astype(np.uint8)
    out[~mask, :3] = 0
    out[..., 3] = np.where(mask, alpha, 0).astype(np.uint8)
    return out


def save_webp(array: np.ndarray, path: Path, quality: int = 90) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "RGBA" if array.shape[-1] == 4 else "RGB"
    Image.fromarray(array, mode=mode).save(path, "WEBP", quality=quality, method=6)


def true_color(red: np.ndarray, green: np.ndarray, blue: np.ndarray, valid: np.ndarray) -> np.ndarray:
    # Fixed stretch preserves comparability between years.  No per-image
    # histogram equalisation is used because that would invent apparent change.
    rgb = np.stack([red, green, blue], axis=-1) / 3400.0
    rgb = np.power(np.clip(rgb, 0, 1), 0.82)
    rgb = (rgb * 255).astype(np.uint8)
    rgb[~valid] = (26, 40, 46)
    return rgb


def edge(mask: np.ndarray) -> np.ndarray:
    result = np.zeros_like(mask)
    result[1:, :] |= mask[1:, :] != mask[:-1, :]
    result[:-1, :] |= mask[:-1, :] != mask[1:, :]
    result[:, 1:] |= mask[:, 1:] != mask[:, :-1]
    result[:, :-1] |= mask[:, :-1] != mask[:, 1:]
    return result


def build_year(year: int) -> dict[str, object]:
    source_dir = ROOT / "source" / "s2" / "annual" / str(year)
    paths = [source_dir / f"stripe-{index}.tif" for index in range(4)]
    missing = [str(path) for path in paths if not path.exists()]
    if missing:
        raise FileNotFoundError(f"{year}: missing {missing}")

    datasets = [rasterio.open(path) for path in paths]
    try:
        cube, transform = merge(datasets, bounds=BBOX, method="first")
        profile = datasets[0].profile.copy()
    finally:
        for dataset in datasets:
            dataset.close()

    profile.update(
        driver="GTiff",
        height=cube.shape[1],
        width=cube.shape[2],
        count=5,
        transform=transform,
        compress="DEFLATE",
        tiled=True,
        blockxsize=256,
        blockysize=256,
    )
    spectral_path = ROOT / "derived" / "annual" / str(year) / "spectral.tif"
    spectral_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(spectral_path, "w", **profile) as dst:
        dst.write(cube)
        dst.update_tags(
            NAUTIKOS_PRODUCT="fixed-summer-composite",
            PERIOD=f"{year}-07-01/{year}-07-15",
            BANDS=",".join(BANDS),
        )

    blue, green, red, nir, swir = cube.astype(np.float32)
    valid = np.any(cube > 0, axis=0)
    ndwi = safe_ratio(green, nir)
    mndwi = safe_ratio(green, swir)
    ndvi = safe_ratio(nir, red)
    ndmi = safe_ratio(nir, swir)
    bsi = ((swir + red) - (nir + blue)) / np.maximum((swir + red) + (nir + blue), 1e-6)

    # A conservative optical water mask.  The SWIR/NIR limits suppress bright
    # salt flats and bare desert that a plain NDWI threshold misclassifies.
    water = valid & (mndwi > 0.10) & (ndwi > -0.02) & (nir < 1700) & (swir < 1100)
    land = valid & ~water
    shoreline = edge(water) & valid

    product_dir = ROOT / "overviews" / "annual" / str(year)
    save_webp(true_color(red, green, blue, valid), product_dir / "true-color.webp", 92)

    shore_rgba = np.zeros((*water.shape, 4), dtype=np.uint8)
    shore_rgba[water] = (20, 113, 163, 105)
    shore_rgba[shoreline] = (255, 193, 61, 245)
    save_webp(shore_rgba, product_dir / "shoreline.webp", 92)

    # NDTI is a screening proxy for suspended sediment / discharge plumes.
    # It is not labelled as confirmed pollution without an in-situ sample.
    ndti = safe_ratio(red, green)
    water_quality = rgba_from_stops(
        ndti,
        [(-0.20, (15, 84, 135)), (-0.04, (35, 170, 183)), (0.06, (242, 197, 61)), (0.22, (217, 72, 43))],
        water,
        218,
    )
    save_webp(water_quality, product_dir / "water-quality.webp", 91)

    vegetation = rgba_from_stops(
        ndvi,
        [(-0.10, (128, 96, 65)), (0.10, (220, 190, 95)), (0.35, (91, 155, 76)), (0.72, (17, 91, 55))],
        land,
        208,
    )
    save_webp(vegetation, product_dir / "vegetation.webp", 90)

    moisture = rgba_from_stops(
        ndmi,
        [(-0.45, (193, 111, 50)), (-0.10, (225, 196, 116)), (0.18, (74, 164, 151)), (0.55, (28, 89, 137))],
        land,
        212,
    )
    save_webp(moisture, product_dir / "coast-moisture.webp", 90)

    stress = np.clip((bsi + 0.10) * 1.45 + np.maximum(0, 0.22 - ndvi) * 1.15, 0, 1)
    soil = rgba_from_stops(
        stress,
        [(0.0, (33, 126, 86)), (0.34, (231, 181, 54)), (0.62, (232, 104, 39)), (1.0, (181, 36, 48))],
        land,
        214,
    )
    save_webp(soil, product_dir / "soil-stress.webp", 90)

    # Compact, lossless analysis grid consumed by the local API.  Channels are
    # water mask, NDVI, soil stress and valid-data mask respectively.
    metrics = np.stack(
        [
            water.astype(np.uint8) * 255,
            (np.clip((ndvi + 1) / 2, 0, 1) * 255).astype(np.uint8),
            (stress * 255).astype(np.uint8),
            valid.astype(np.uint8) * 255,
        ],
        axis=-1,
    )
    metrics_path = ROOT / "metrics" / "annual" / f"{year}.png"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(metrics, mode="RGBA").resize((512, 596), Image.Resampling.BILINEAR).save(metrics_path, "PNG", optimize=True)

    return {
        "year": year,
        "period": f"{year}-07-01/{year}-07-15",
        "width": int(cube.shape[2]),
        "height": int(cube.shape[1]),
        "waterShare": round(float(water.sum() / max(valid.sum(), 1)), 6),
        "meanNdvi": round(float(np.nanmean(np.where(land, ndvi, np.nan))), 6),
        "meanNdmi": round(float(np.nanmean(np.where(land, ndmi, np.nan))), 6),
        "highStressShare": round(float((stress[land] > 0.62).mean()), 6),
        "sourceBytes": sum(path.stat().st_size for path in paths),
    }


def main() -> None:
    records = [build_year(year) for year in YEARS]
    manifest = {
        "product": "Nautikos Caspian local annual cube",
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "bbox": BBOX,
        "source": "COPERNICUS/S2_SR_HARMONIZED via Earth Engine one-time export",
        "bands": BANDS,
        "years": records,
        "layers": ["true-color", "shoreline", "water-quality", "vegetation", "coast-moisture", "soil-stress"],
        "disclaimer": "Spectral layers are screening products; pollution confirmation requires field validation.",
    }
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
