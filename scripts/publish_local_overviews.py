"""Publish fixed whole-Caspian previews for every monitoring layer.

The detailed XYZ tiles remain the source of truth after zooming in.  This
script only creates a low-zoom preview from those same local tiles so that a
filter is visible immediately at the whole-sea extent and does not require a
Copernicus request at runtime.
"""

from __future__ import annotations

import math
import os
import shutil
from pathlib import Path

from PIL import Image


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("NAUTIKOS_DATA_DIR", r"D:\CaspianTwinData\cube"))
OUTPUT_ROOT = PROJECT_ROOT / "public" / "overviews" / "annual"
BBOX = (46.0, 36.0, 55.8, 47.4)
YEARS = range(2020, 2027)
LAYERS = (
    "true-color",
    "olci-true-color",
    "shoreline",
    "water-quality",
    "oil-roughness",
    "chlorophyll",
    "suspended-matter",
    "water-temperature",
    "vegetation",
    "coast-moisture",
    "soil-stress",
    "erosion-risk",
)


def world_pixel(lon: float, lat: float, zoom: int, tile_size: int) -> tuple[float, float]:
    scale = (2**zoom) * tile_size
    x = (lon + 180.0) / 360.0 * scale
    clipped = max(-85.05112878, min(85.05112878, lat))
    radians = math.radians(clipped)
    y = (1.0 - math.asinh(math.tan(radians)) / math.pi) / 2.0 * scale
    return x, y


def tile_files(layer: str, year: int, zoom: int = 5) -> list[tuple[int, int, Path]]:
    root = DATA_ROOT / "tiles" / layer / str(year) / str(zoom)
    found: list[tuple[int, int, Path]] = []
    if not root.exists():
        return found
    for x_dir in root.iterdir():
        if not x_dir.is_dir() or not x_dir.name.isdigit():
            continue
        for path in x_dir.iterdir():
            if path.is_file() and path.stem.isdigit():
                found.append((int(x_dir.name), int(path.stem), path))
    return found


def build_from_tiles(layer: str, year: int, destination: Path) -> None:
    zoom = 5
    tiles = tile_files(layer, year, zoom)
    if not tiles:
        raise FileNotFoundError(f"No z{zoom} tiles for {layer} {year}")

    with Image.open(tiles[0][2]) as sample:
        tile_size = sample.width
    min_x = min(x for x, _, _ in tiles)
    max_x = max(x for x, _, _ in tiles)
    min_y = min(y for _, y, _ in tiles)
    max_y = max(y for _, y, _ in tiles)
    mosaic = Image.new("RGBA", ((max_x - min_x + 1) * tile_size, (max_y - min_y + 1) * tile_size))

    for x, y, path in tiles:
        with Image.open(path) as image:
            mosaic.alpha_composite(image.convert("RGBA"), ((x - min_x) * tile_size, (y - min_y) * tile_size))

    west, south, east, north = BBOX
    left, top = world_pixel(west, north, zoom, tile_size)
    right, bottom = world_pixel(east, south, zoom, tile_size)
    crop = mosaic.crop((
        round(left - min_x * tile_size),
        round(top - min_y * tile_size),
        round(right - min_x * tile_size),
        round(bottom - min_y * tile_size),
    ))
    crop = crop.resize((1024, 1192), Image.Resampling.BILINEAR)
    destination.parent.mkdir(parents=True, exist_ok=True)
    crop.save(destination, "WEBP", lossless=True, method=6)


def publish(year: int, layer: str) -> Path:
    destination = OUTPUT_ROOT / str(year) / f"{layer}.webp"
    annual = DATA_ROOT / "overviews" / "annual" / str(year)
    source = annual / f"{layer}.webp"
    if layer == "true-color":
        raw = annual / "true-color-raw.webp"
        if raw.exists():
            source = raw

    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.exists():
        shutil.copy2(source, destination)
    else:
        build_from_tiles(layer, year, destination)
    return destination


def main() -> None:
    for year in YEARS:
        for layer in LAYERS:
            output = publish(year, layer)
            print(f"{year} · {layer} · {output.stat().st_size:,} bytes")


if __name__ == "__main__":
    main()
