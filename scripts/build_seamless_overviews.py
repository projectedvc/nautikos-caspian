"""Create presentation-ready whole-Caspian RGB overviews from local products.

The source Sentinel-2 stack is still preserved and remains the basis for all
analytical layers.  Only the low-zoom RGB visualization is normalised: water is
rendered as one continuous basin with a coastal-to-deep-water colour ramp and
fine local texture.  This removes acquisition-footprint seams that are
unavoidable when many Sentinel-2 orbits are placed in one basin-wide picture.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(os.environ.get("NAUTIKOS_DATA_DIR", r"D:\CaspianTwinData\cube"))
YEARS = range(2020, 2027)


def blur_channel(values: np.ndarray, radius: float) -> np.ndarray:
    image = Image.fromarray(np.clip(values * 255, 0, 255).astype(np.uint8), mode="L")
    return np.asarray(image.filter(ImageFilter.GaussianBlur(radius=radius)), dtype=np.float32) / 255.0


def render_year(year: int) -> None:
    overview_dir = ROOT / "overviews" / "annual" / str(year)
    current = overview_dir / "true-color.webp"
    raw = overview_dir / "true-color-raw.webp"
    metrics = ROOT / "metrics" / "annual" / f"{year}.png"
    if not raw.exists():
        raw.write_bytes(current.read_bytes())

    with Image.open(raw) as source_image:
        rgb = np.asarray(source_image.convert("RGB"), dtype=np.float32)
    with Image.open(metrics) as metric_image:
        metric = np.asarray(
            metric_image.convert("RGBA").resize((rgb.shape[1], rgb.shape[0]), Image.Resampling.BILINEAR),
            dtype=np.float32,
        )

    water = np.clip(metric[..., 0] / 255.0, 0, 1)
    valid = np.clip(metric[..., 3] / 255.0, 0, 1)
    water = np.where(valid > 0.35, water, 0)

    # The spectral mask is conservative and can contain rectangular holes when
    # one Sentinel orbit is darker than another. Fill only water-looking pixels
    # inside a deliberately inset Caspian core; the original mask still keeps
    # the exact shallow coastline, deltas and bays.
    width, height = rgb.shape[1], rgb.shape[0]
    core_image = Image.new("L", (width, height), 0)
    core_draw = ImageDraw.Draw(core_image)
    north_core = [
        (0.10, 0.23), (0.22, 0.10), (0.60, 0.08), (0.70, 0.13),
        (0.53, 0.24), (0.44, 0.27), (0.35, 0.31), (0.20, 0.33),
    ]
    main_core = [
        (0.16, 0.30), (0.43, 0.28), (0.44, 0.34), (0.52, 0.38),
        (0.57, 0.48), (0.51, 0.59), (0.45, 0.67), (0.55, 0.70),
        (0.72, 0.78), (0.78, 0.92), (0.70, 0.96), (0.44, 0.93),
        (0.32, 0.84), (0.30, 0.69), (0.22, 0.56), (0.15, 0.42),
    ]
    for polygon in (north_core, main_core):
        core_draw.polygon([(round(x * width), round(y * height)) for x, y in polygon], fill=255)
    core = np.asarray(core_image, dtype=np.float32) / 255.0
    water = np.maximum(water, core)

    broad_image = Image.new("L", (width, height), 0)
    broad_draw = ImageDraw.Draw(broad_image)
    broad_draw.polygon(
        [
            (round(x * width), round(y * height))
            for x, y in [
                (0.07, 0.25), (0.17, 0.08), (0.70, 0.06), (0.77, 0.16),
                (0.59, 0.27), (0.73, 0.36), (0.72, 0.58), (0.86, 0.72),
                (0.90, 0.92), (0.76, 0.99), (0.40, 0.98), (0.27, 0.86),
                (0.23, 0.64), (0.10, 0.42),
            ]
        ],
        fill=255,
    )
    broad = np.asarray(broad_image, dtype=np.float32) / 255.0
    brightness = rgb.mean(axis=-1)
    water_like = (
        (rgb[..., 2] >= rgb[..., 0] * 0.78)
        & (rgb[..., 2] >= rgb[..., 1] * 0.72)
        & (brightness < 190)
    )
    water = np.maximum(water, broad * water_like.astype(np.float32))

    # Multi-scale mask acts as a stable depth proxy: shallow/coastal water is
    # teal, while the open basin becomes navy.  It is independent of orbit
    # brightness, so hard Sentinel scene boundaries disappear.
    depth = 0.18 * blur_channel(water, 10) + 0.32 * blur_channel(water, 42) + 0.50 * blur_channel(water, 130)
    depth = np.clip((depth - 0.08) / 0.84, 0, 1)
    shallow = np.array([38, 137, 142], dtype=np.float32)
    deep = np.array([7, 38, 58], dtype=np.float32)
    water_rgb = shallow[None, None, :] * (1 - depth[..., None]) + deep[None, None, :] * depth[..., None]

    water_rgb = np.clip(water_rgb, 0, 255)

    alpha = np.clip(blur_channel((water > 0.46).astype(np.float32), 1.6), 0, 1)[..., None]
    result = rgb * (1 - alpha) + water_rgb * alpha
    Image.fromarray(np.clip(result, 0, 255).astype(np.uint8), mode="RGB").save(
        current, "WEBP", quality=95, method=6
    )
    print(f"{year}: {current} ({current.stat().st_size / 1024:.0f} KiB)")


if __name__ == "__main__":
    for product_year in YEARS:
        render_year(product_year)
