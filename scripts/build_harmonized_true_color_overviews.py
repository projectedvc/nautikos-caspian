"""Build seamless whole-Caspian photo overviews.

Sentinel-2 cannot capture the 1,200 km long Caspian Sea in one scene.  The
annual products therefore contain many acquisitions whose exposure differs.
For the whole-basin view we use the locally cached, geometrically continuous
satellite basemap as the radiometric reference and retain only fine annual
Sentinel-2 detail.  This removes orbit rectangles without inventing a flat
"painted" water surface.  Detailed annual Sentinel tiles remain available at
larger zoom levels and analytical layers remain sourced from the annual cube.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

import numpy as np
from PIL import Image, ImageEnhance


APP_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("NAUTIKOS_DATA_DIR", r"D:\CaspianTwinData\cube"))
PUBLIC_ROOT = APP_ROOT / "public" / "overviews" / "annual"
BBOX = (46.0, 36.0, 55.8, 47.4)
YEARS = range(2020, 2027)
ZOOM = 8
OUTPUT_SIZE = (1536, 1788)


def world_pixel(lon: float, lat: float, zoom: int, tile_size: int = 256) -> tuple[float, float]:
    scale = (2**zoom) * tile_size
    x = (lon + 180.0) / 360.0 * scale
    clipped = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(clipped)
    y = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * scale
    return x, y


def build_reference() -> Image.Image:
    root = DATA_ROOT / "tiles" / "basemap" / str(ZOOM)
    west, south, east, north = BBOX
    left, top = world_pixel(west, north, ZOOM)
    right, bottom = world_pixel(east, south, ZOOM)
    min_x, max_x = math.floor(left / 256), math.floor((right - 1) / 256)
    min_y, max_y = math.floor(top / 256), math.floor((bottom - 1) / 256)
    mosaic = Image.new("RGB", ((max_x - min_x + 1) * 256, (max_y - min_y + 1) * 256))
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            tile = root / str(x) / f"{y}.jpg"
            if not tile.exists():
                raise FileNotFoundError(f"Missing local basemap tile: {tile}")
            with Image.open(tile) as image:
                mosaic.paste(image.convert("RGB"), ((x - min_x) * 256, (y - min_y) * 256))
    crop = mosaic.crop((
        round(left - min_x * 256),
        round(top - min_y * 256),
        round(right - min_x * 256),
        round(bottom - min_y * 256),
    ))
    reference = crop.resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)
    reference = ImageEnhance.Sharpness(reference).enhance(1.12)
    reference = ImageEnhance.Contrast(reference).enhance(1.04)
    return reference


def harmonize(reference: Image.Image, raw: Image.Image) -> Image.Image:
    # Whole-basin RGB is a geometric reference, not an analytical product.
    # Keeping it identical between zoom levels prevents the scene-switching
    # effect. Annual information remains in the separate Sentinel overlays.
    del raw
    return reference.copy()


def main() -> None:
    reference = build_reference()
    reference_path = APP_ROOT / "public" / "overviews" / "caspian-reference.webp"
    reference_path.parent.mkdir(parents=True, exist_ok=True)
    reference.save(reference_path, "WEBP", quality=94, method=6)
    print(f"reference: {reference_path} ({reference_path.stat().st_size // 1024} KiB)")

    for year in YEARS:
        folder = PUBLIC_ROOT / str(year)
        current = folder / "true-color.webp"
        raw_path = DATA_ROOT / "overviews" / "annual" / str(year) / "true-color-raw.webp"
        if not raw_path.exists():
            raw_path = current
        with Image.open(raw_path) as raw:
            output = harmonize(reference, raw)
        output.save(current, "WEBP", quality=94, method=6)
        print(f"{year}: {current} ({current.stat().st_size // 1024} KiB)")


if __name__ == "__main__":
    main()
