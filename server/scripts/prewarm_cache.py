#!/usr/bin/env python3
"""Warm the immutable local cache for the initial whole-Caspian view."""

from __future__ import annotations

import argparse
import math
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


BBOX = (46.0, 36.0, 55.8, 47.4)


def lonlat_to_tile(lon: float, lat: float, zoom: int) -> tuple[int, int]:
    lat = max(-85.05112878, min(85.05112878, lat))
    scale = 1 << zoom
    x = int((lon + 180.0) / 360.0 * scale)
    lat_rad = math.radians(lat)
    y = int((1.0 - math.asinh(math.tan(lat_rad)) / math.pi) / 2.0 * scale)
    return max(0, min(scale - 1, x)), max(0, min(scale - 1, y))


def tiles(zoom: int):
    west, south, east, north = BBOX
    min_x, min_y = lonlat_to_tile(west, north, zoom)
    max_x, max_y = lonlat_to_tile(east, south, zoom)
    for x in range(min_x, max_x + 1):
        for y in range(min_y, max_y + 1):
            yield zoom, x, y


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api", default="http://127.0.0.1:8787")
    parser.add_argument("--years", default="2020,2026,2021,2022,2023,2024,2025")
    parser.add_argument("--products", default="rgb,water_extent,turbidity,vegetation")
    parser.add_argument("--zooms", default="3,4,5,6")
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()

    years = [int(value) for value in args.years.split(",")]
    products = [value.strip() for value in args.products.split(",") if value.strip()]
    zooms = [int(value) for value in args.zooms.split(",")]
    jobs = [
        (product, year, z, x, y)
        for product in products
        for year in years
        for z in zooms
        for z, x, y in tiles(z)
    ]

    def fetch(job: tuple[str, int, int, int, int]) -> tuple[tuple[str, int, int, int, int], int]:
        product, year, z, x, y = job
        response = requests.get(f"{args.api}/v2/tiles/{product}/{year}/{z}/{x}/{y}.png", timeout=300)
        return job, response.status_code

    complete = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = [pool.submit(fetch, job) for job in jobs]
        for future in as_completed(futures):
            job, status = future.result()
            complete += 1
            print(f"{complete}/{len(jobs)} {job}: HTTP {status}", flush=True)
    failures = [future.result() for future in futures if future.result()[1] != 200]
    if failures:
        raise SystemExit(f"{len(failures)} cache requests failed")


if __name__ == "__main__":
    main()
