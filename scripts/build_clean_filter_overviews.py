"""Build presentation-grade, seam-free analytical overlays.

The satellite RGB layer stays untouched.  Analytical rasters are rendered as
semi-transparent screening overlays, so acquisition footprints and nodata
rectangles can never hide the underlying Caspian image.
"""
from __future__ import annotations

import os
import shutil
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

APP_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("NAUTIKOS_DATA_DIR", "/home/jovyan/work/caspiansea/data/cube"))
PUBLIC_ROOT = APP_ROOT / "public" / "overviews" / "annual"
BACKUP_ROOT = DATA_ROOT / "quality-source-overlays"
YEARS = range(2020, 2027)

CONFIG = {
    "shoreline": (1.0, 178, "water"),
    "water-quality": (5.5, 150, "water"),
    "chlorophyll": (7.0, 146, "water"),
    "suspended-matter": (8.0, 148, "water"),
    "water-temperature": (24.0, 142, "water"),
    "oil-roughness": (5.0, 158, "water"),
    "vegetation": (4.0, 148, "land"),
    "coast-moisture": (6.0, 146, "land"),
    "soil-stress": (6.0, 148, "land"),
    "erosion-risk": (5.0, 154, "land"),
}

TARGET_WIDTH = 1536


def disk(radius: int) -> np.ndarray:
    yy, xx = np.ogrid[-radius:radius + 1, -radius:radius + 1]
    return xx * xx + yy * yy <= radius * radius


def robust_mask(mask: np.ndarray, *, close_radius: int) -> np.ndarray:
    mask = ndimage.binary_closing(mask, structure=disk(close_radius))
    labels, count = ndimage.label(mask)
    if count:
        sizes = np.bincount(labels.ravel())
        keep = sizes >= max(120, int(mask.size * 0.00015))
        keep[0] = False
        mask = keep[labels]
    return ndimage.binary_fill_holes(mask)


def nearest_fill(rgb: np.ndarray, valid: np.ndarray) -> np.ndarray:
    if not np.any(valid):
        return rgb.astype(np.float32)
    indices = ndimage.distance_transform_edt(
        ~valid, return_distances=False, return_indices=True
    )
    return rgb[tuple(indices)].astype(np.float32)


def smooth_rgb(rgb: np.ndarray, valid: np.ndarray, sigma: float) -> np.ndarray:
    filled = nearest_fill(rgb, valid)
    broad = np.stack(
        [ndimage.gaussian_filter(filled[..., c], sigma=sigma, mode="nearest") for c in range(3)],
        axis=-1,
    )
    if sigma >= 18:
        result = broad
    else:
        detail_sigma = max(1.3, sigma * 0.28)
        detail = np.stack(
            [ndimage.gaussian_filter(filled[..., c], sigma=detail_sigma, mode="nearest") for c in range(3)],
            axis=-1,
        )
        result = broad * 0.72 + detail * 0.28
    return np.clip(result, 0, 255).astype(np.uint8)


def build_year(year: int) -> None:
    folder = PUBLIC_ROOT / str(year)
    backup = BACKUP_ROOT / str(year)
    backup.mkdir(parents=True, exist_ok=True)

    shore_path = folder / "shoreline.webp"
    if not shore_path.exists():
        print(f"{year}: no shoreline mask")
        return

    shore = np.asarray(Image.open(shore_path).convert("RGBA"))
    shore_alpha = shore[..., 3] > 15
    water_seed = shore_alpha & (shore[..., 2].astype(np.int16) > shore[..., 0].astype(np.int16) + 18)
    water_mask = robust_mask(water_seed, close_radius=6)

    for name, (sigma, alpha_level, domain) in CONFIG.items():
        path = folder / f"{name}.webp"
        if not path.exists():
            continue

        source = backup / path.name
        if not source.exists():
            shutil.copy2(path, source)

        rgba = np.asarray(Image.open(source).convert("RGBA"))
        rgb = rgba[..., :3]
        original = rgba[..., 3] > 10
        brightness = rgb.astype(np.float32).mean(axis=-1)
        chroma = rgb.max(axis=-1).astype(np.int16) - rgb.min(axis=-1).astype(np.int16)

        # Black/grey acquisition rectangles are nodata, not ecological signals.
        valid_colour = original & (brightness > 52) & ((chroma > 11) | (brightness > 92))
        cleaned = smooth_rgb(rgb, valid_colour, sigma)

        if domain == "water":
            semantic = water_mask
        else:
            semantic = robust_mask(original & ~water_mask, close_radius=3)

        # Keep the satellite photograph visible and strengthen only coloured anomalies.
        colour_strength = np.clip(chroma.astype(np.float32) / 95.0, 0.0, 1.0)
        alpha = semantic.astype(np.float32) * (
            alpha_level * (0.55 + 0.45 * colour_strength)
        )
        feather = ndimage.gaussian_filter(semantic.astype(np.float32), sigma=1.15)
        alpha *= np.clip(feather, 0, 1)

        if name == "shoreline":
            edge = semantic ^ ndimage.binary_erosion(semantic, structure=disk(2))
            cleaned[:] = np.array([20, 151, 199], dtype=np.uint8)
            alpha = edge.astype(np.float32) * 215

        out = np.dstack([cleaned, np.clip(alpha, 0, 220).astype(np.uint8)])
        target_height = round(out.shape[0] * TARGET_WIDTH / out.shape[1])
        rendered = Image.fromarray(out, "RGBA").resize(
            (TARGET_WIDTH, target_height), Image.Resampling.LANCZOS
        )
        rendered.save(path, "WEBP", quality=92, method=6, exact=True)
        print(f"{year} {name}: {rendered.size} {path.stat().st_size // 1024} KiB")


def main() -> None:
    for year in YEARS:
        build_year(year)


if __name__ == "__main__":
    main()
