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


def colour_ramp(score: np.ndarray, mask: np.ndarray) -> np.ndarray:
    red = np.clip(2.2 * score - 0.25, 0, 1)
    green = np.clip(1.35 - np.abs(score - 0.5) * 2.3, 0, 1)
    blue = np.clip(1.15 - 2.0 * score, 0, 1)
    alpha = np.where(mask, 38 + score * 180, 0)
    return np.dstack((red * 255, green * 255, blue * 255, alpha)).astype(np.uint8)


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
    ndti_8 = np.clip((ndti + 0.28) * (255.0 / 0.52), 0, 255).astype(np.uint8)
    ndti_smooth = np.asarray(
        Image.fromarray(ndti_8, "L").filter(ImageFilter.GaussianBlur(1.2)),
        dtype=np.float32,
    )
    ndti_smooth = ndti_smooth * (0.52 / 255.0) - 0.28
    turbidity = np.clip((ndti_smooth + 0.16) / 0.28, 0, 1)
    Image.fromarray(colour_ramp(turbidity, water), "RGBA").save(
        year_root / "water-quality.webp", "WEBP", lossless=True, method=6
    )

    ratio = red / (green + 1.0)
    suspended = np.clip((ratio - 0.68) / 0.62, 0, 1)
    Image.fromarray(colour_ramp(suspended, water), "RGBA").save(
        year_root / "suspended-matter.webp", "WEBP", lossless=True, method=6
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
