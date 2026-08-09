#!/usr/bin/env python3
"""Build instant, pixel-aligned Caspian overview overlays from annual RGB.

These are presentation-scale Red/Green screening products. Native zooms use
the server's raw Sentinel-2 reflectance and SCL mask; the overview prevents a
blank map while those detailed tiles are being materialised.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


ROOT = Path(__file__).resolve().parents[1] / "public" / "overviews" / "annual"
WIDTH = 2048


RAMP_STOPS = np.array([0.0, 0.18, 0.38, 0.58, 0.78, 1.0], dtype=np.float32)
RAMP_RGB = np.array(
    [
        (28, 50, 156),
        (0, 159, 225),
        (0, 204, 153),
        (197, 231, 45),
        (255, 183, 0),
        (211, 32, 32),
    ],
    dtype=np.float32,
)


def fixed_score(value: np.ndarray, low: float, high: float) -> np.ndarray:
    """Use the same physical display interval for every year."""
    return np.clip((value - low) / (high - low), 0, 1)


def smooth(value: np.ndarray, radius: float = 2.4) -> np.ndarray:
    encoded = np.clip(value * 255, 0, 255).astype(np.uint8)
    image = Image.fromarray(encoded, "L").filter(ImageFilter.GaussianBlur(radius))
    return np.asarray(image, dtype=np.float32) / 255.0


def colour_ramp(score: np.ndarray, mask: np.ndarray, alpha: int = 218) -> np.ndarray:
    """Continuous scientific-style blue/cyan/yellow/red raster."""
    score = np.clip(score, 0, 1)
    rgb = np.empty((*score.shape, 3), dtype=np.float32)
    for channel in range(3):
        rgb[..., channel] = np.interp(score, RAMP_STOPS, RAMP_RGB[:, channel])
    rgba = np.dstack((rgb, np.where(mask, alpha, 0))).astype(np.uint8)
    rgba[~mask] = 0
    return rgba


def build_year(year_root: Path) -> None:
    source = Image.open(year_root / "true-color.webp").convert("RGB")
    height = round(source.height * WIDTH / source.width)
    rgb = np.asarray(source.resize((WIDTH, height), Image.Resampling.LANCZOS), dtype=np.float32)

    mask_path = year_root / "water-mask.webp"
    if mask_path.exists():
        water = np.asarray(Image.open(mask_path).convert("L").resize((WIDTH, height), Image.Resampling.NEAREST)) > 127
    else:
        # The previous layer has a georeferenced Caspian water alpha mask. Keep
        # that geometry once, then replace its artificial colour surface.
        legacy = Image.open(year_root / "water-quality.webp").convert("RGBA")
        water = np.asarray(legacy.getchannel("A").resize((WIDTH, height), Image.Resampling.BILINEAR)) > 30
        Image.fromarray(water.astype(np.uint8) * 255, "L").save(mask_path, "WEBP", lossless=True, method=6)

    red, green, blue = (rgb[..., index] for index in range(3))
    ndti = (red - green) / (red + green + 1.0)
    # Deep optical water is near the blue end; high red/green response moves
    # through cyan/yellow to red. Unlike the old thresholded overlay, every
    # valid water pixel receives a readable value like a scientific heatmap.
    turbidity = smooth(fixed_score(ndti, -0.48, 0.02))
    Image.fromarray(colour_ramp(turbidity, water), "RGBA").save(
        year_root / "water-quality.webp", "WEBP", lossless=True, method=6
    )

    ratio = red / (green + 1.0)
    suspended = smooth(fixed_score(ratio, 0.28, 1.08))
    Image.fromarray(colour_ramp(suspended, water), "RGBA").save(
        year_root / "suspended-matter.webp", "WEBP", lossless=True, method=6
    )

    green_excess = (2.0 * green - red - blue) / (red + green + blue + 1.0)
    chlorophyll_proxy = smooth(fixed_score(green_excess, -0.08, 0.48))
    Image.fromarray(colour_ramp(chlorophyll_proxy, water), "RGBA").save(
        year_root / "chlorophyll.webp", "WEBP", lossless=True, method=6
    )

    # Keep transparent pixels genuinely empty. Some WebP renderers retain RGB
    # under zero alpha and can show horizontal colour streaks while decoding.
    water_rgb = np.zeros((*water.shape, 4), dtype=np.uint8)
    water_rgb[water, :3] = rgb[water].astype(np.uint8)
    water_rgb[water, 3] = 225
    Image.fromarray(water_rgb, "RGBA").save(
        year_root / "olci-true-color.webp", "WEBP", quality=91, method=6
    )

    # A crisp edge for whole-basin shoreline comparison.
    water_image = Image.fromarray(water.astype(np.uint8) * 255, "L")
    eroded = np.asarray(water_image.filter(ImageFilter.MinFilter(7))) > 0
    edge = water & ~eroded
    shoreline = np.zeros((*water.shape, 4), dtype=np.uint8)
    shoreline[water] = (16, 129, 190, 92)
    shoreline[edge] = (255, 194, 32, 235)
    Image.fromarray(shoreline, "RGBA").save(
        year_root / "shoreline.webp", "WEBP", lossless=True, method=6
    )

    print(year_root.name, source.size, "->", (WIDTH, height), flush=True)


def main() -> None:
    for year in range(2020, 2027):
        build_year(ROOT / str(year))


if __name__ == "__main__":
    main()
