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
    "shoreline": (1.0, 215, "water"),
    "water-quality": (30.0, 246, "water"),
    "chlorophyll": (34.0, 246, "water"),
    "suspended-matter": (36.0, 246, "water"),
    "water-temperature": (32.0, 246, "water"),
    "oil-roughness": (28.0, 246, "water"),
    "vegetation": (12.0, 212, "land"),
    "coast-moisture": (14.0, 212, "land"),
    "soil-stress": (14.0, 212, "land"),
    "erosion-risk": (12.0, 216, "land"),
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


def water_mask_from_true_color(folder: Path, fallback: np.ndarray) -> np.ndarray:
    """Recover one continuous Caspian water body from the annual RGB mosaic.

    Product rasters are assembled from several satellite footprints and their
    alpha channels can contain long rectangular gaps.  Those gaps must never
    become holes in a water filter, so the semantic mask is derived from the
    RGB colour itself and only the largest connected water body is retained.
    """
    rgb = np.asarray(Image.open(folder / "true-color.webp").convert("RGB"))
    if fallback.shape != rgb.shape[:2]:
        fallback = np.asarray(
            Image.fromarray(fallback.astype(np.uint8) * 255).resize(
                (rgb.shape[1], rgb.shape[0]), Image.Resampling.NEAREST
            )
        ) > 0
    r, g, b = [rgb[..., channel].astype(np.float32) for channel in range(3)]
    brightness = (r + g + b) / 3.0
    colour_seed = (
        (brightness < 168)
        & (b > r * 1.08 + 2)
        & (b > g * 0.56)
        & ((g + b) > r * 1.72)
    )
    seed = colour_seed | fallback
    seed = ndimage.binary_closing(seed, structure=disk(7))
    labels, count = ndimage.label(seed)
    if not count:
        return robust_mask(fallback, close_radius=8)
    sizes = np.bincount(labels.ravel())
    sizes[0] = 0
    mask = labels == int(np.argmax(sizes))
    mask = ndimage.binary_closing(mask, structure=disk(13))
    mask = ndimage.binary_fill_holes(mask)
    return ndimage.binary_opening(mask, structure=disk(2))


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
    water_mask = water_mask_from_true_color(folder, water_seed)

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

        # A constant semantic opacity prevents acquisition footprints in the
        # RGB mosaic below from reappearing as rectangular artefacts.  The
        # analytical variation remains encoded in the smoothed colour field.
        alpha = semantic.astype(np.float32) * alpha_level
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
