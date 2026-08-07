"""Download only Caspian Earth Engine map tiles and assemble local monthly frames."""

from __future__ import annotations

import io
import json
import math
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from PIL import Image


BBOX = (46.0, 36.0, 55.8, 47.4)
ZOOM = 6
TILE_SIZE = 256
ROOT = Path(r"D:\CaspianTwinData\cube")
MANIFEST = Path(__file__).with_name(".ee-monthly-urls.json")


def world_pixel(lon: float, lat: float, zoom: int) -> tuple[float, float]:
    scale = 2**zoom * TILE_SIZE
    x = (lon + 180.0) / 360.0 * scale
    radians = math.radians(lat)
    y = (1 - math.asinh(math.tan(radians)) / math.pi) / 2 * scale
    return x, y


def download_tile(args: tuple[str, int, int, int]) -> tuple[int, int, Image.Image | None]:
    template, z, x, y = args
    url = template.replace("{z}", str(z)).replace("{x}", str(x)).replace("{y}", str(y))
    for attempt in range(4):
        try:
            response = requests.get(url, timeout=45)
            response.raise_for_status()
            return x, y, Image.open(io.BytesIO(response.content)).convert("RGB")
        except Exception:
            if attempt == 3:
                return x, y, None
    return x, y, None


def build_frame(item: dict[str, object]) -> Path:
    west, south, east, north = BBOX
    left, top = world_pixel(west, north, ZOOM)
    right, bottom = world_pixel(east, south, ZOOM)
    min_x, max_x = math.floor(left / TILE_SIZE), math.floor((right - 1) / TILE_SIZE)
    min_y, max_y = math.floor(top / TILE_SIZE), math.floor((bottom - 1) / TILE_SIZE)
    canvas = Image.new("RGB", ((max_x - min_x + 1) * TILE_SIZE, (max_y - min_y + 1) * TILE_SIZE), (10, 38, 49))
    jobs = [(str(item["url"]), ZOOM, x, y) for x in range(min_x, max_x + 1) for y in range(min_y, max_y + 1)]
    with ThreadPoolExecutor(max_workers=6) as executor:
        for x, y, image in executor.map(download_tile, jobs):
            if image is not None:
                canvas.paste(image, ((x - min_x) * TILE_SIZE, (y - min_y) * TILE_SIZE))

    crop = (
        round(left - min_x * TILE_SIZE),
        round(top - min_y * TILE_SIZE),
        round(right - min_x * TILE_SIZE),
        round(bottom - min_y * TILE_SIZE),
    )
    frame = canvas.crop(crop).resize((640, 800), Image.Resampling.LANCZOS)
    output = ROOT / "overviews" / "monthly" / str(item["year"]) / f"{int(item['month']):02d}.webp"
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.save(output, "WEBP", quality=87, method=4)
    return output


def main() -> None:
    items = json.loads(MANIFEST.read_text(encoding="utf-8"))
    ordered = sorted(items, key=lambda row: (row["year"], row["month"]))
    pending: list[dict[str, object]] = []
    for index, item in enumerate(ordered, 1):
        output = ROOT / "overviews" / "monthly" / str(item["year"]) / f"{int(item['month']):02d}.webp"
        if output.exists() and output.stat().st_size > 10_000:
            print(f"{index}/{len(items)} existing {item['year']}-{int(item['month']):02d}")
            continue
        pending.append(item)
    with ThreadPoolExecutor(max_workers=4) as executor:
        for index, output in enumerate(executor.map(build_frame, pending), 1):
            print(f"{index}/{len(pending)} ready {output.parent.name}-{output.stem}")


if __name__ == "__main__":
    main()
