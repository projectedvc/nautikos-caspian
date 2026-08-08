#!/usr/bin/env python3
"""Build a compact spatial trend cube shipped with the Vercel frontend."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
ANNUAL = ROOT / "public" / "overviews" / "annual"
OUTPUT = ROOT / "public" / "metrics" / "annual"
SIZE = (512, 596)


def resized(path: Path, mode: str) -> np.ndarray:
    return np.asarray(Image.open(path).convert(mode).resize(SIZE, Image.Resampling.LANCZOS))


def main() -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for year in range(2020, 2027):
        folder = ANNUAL / str(year)
        rgb = resized(folder / "true-color.webp", "RGB").astype(np.float32)
        shoreline = resized(folder / "shoreline.webp", "RGBA")
        soil = resized(folder / "soil-stress.webp", "RGBA")

        water = shoreline[..., 3] > 48
        red, green = rgb[..., 0], rgb[..., 1]
        visible_green = np.clip((green - red) / (green + red + 1.0), -1, 1)
        vegetation_code = np.clip((visible_green + 1) * 127.5, 0, 255).astype(np.uint8)
        soil_code = soil[..., 0].astype(np.uint8)
        soil_code[soil[..., 3] < 24] = np.clip((red[soil[..., 3] < 24] - green[soil[..., 3] < 24]) + 128, 0, 255).astype(np.uint8)

        metric = np.zeros((SIZE[1], SIZE[0], 4), dtype=np.uint8)
        metric[..., 0] = water.astype(np.uint8) * 255
        metric[..., 1] = vegetation_code
        metric[..., 2] = soil_code
        metric[..., 3] = 255
        Image.fromarray(metric, "RGBA").save(OUTPUT / f"{year}.png", optimize=True)
        print(f"{year}: {OUTPUT / f'{year}.png'}")


if __name__ == "__main__":
    main()
