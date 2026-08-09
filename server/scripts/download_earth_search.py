#!/usr/bin/env python3
"""Download the fixed Caspian Sentinel-2 scene set into local server storage.

The API can start immediately from public Cloud Optimized GeoTIFFs.  As this
job progresses, CatalogRenderer automatically switches each asset to the local
copy without changing tile URLs or scene selection.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlparse

import requests


# RGB and every environmental index are calculated from the same reflectance
# bands. The pre-stretched `visual` COG is intentionally excluded: it is large
# and its per-scene colour correction creates visible seams in a mosaic.
DEFAULT_ASSETS = ("blue", "green", "red", "nir", "scl")


def parse_years(value: str) -> list[int]:
    if ":" in value:
        first, last = (int(part) for part in value.split(":", 1))
        return list(range(first, last + 1))
    return [int(part) for part in value.split(",")]


def destination(root: Path, url: str) -> Path:
    suffix = Path(urlparse(url).path).suffix or ".tif"
    return root / "raw" / "earth-search" / "assets" / f"{hashlib.sha256(url.encode()).hexdigest()}{suffix}"


def download(url: str, target: Path) -> tuple[str, int, str]:
    if target.is_file() and target.stat().st_size > 0:
        return "cached", target.stat().st_size, url
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    for attempt in range(6):
        try:
            existing = partial.stat().st_size if partial.exists() else 0
            headers = {"Range": f"bytes={existing}-"} if existing else {}
            with requests.get(url, headers=headers, stream=True, timeout=(30, 600)) as response:
                if response.status_code == 416 and existing:
                    partial.replace(target)
                    return "downloaded", target.stat().st_size, url
                response.raise_for_status()
                mode = "ab" if existing and response.status_code == 206 else "wb"
                with partial.open(mode) as handle:
                    for chunk in response.iter_content(8 * 1024 * 1024):
                        if chunk:
                            handle.write(chunk)
            partial.replace(target)
            return "downloaded", target.stat().st_size, url
        except (OSError, requests.RequestException):
            if attempt == 5:
                raise
            time.sleep(min(2**attempt, 30))
    raise RuntimeError("unreachable")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=parse_years, default=parse_years("2020:2026"))
    parser.add_argument("--assets", default=",".join(DEFAULT_ASSETS))
    parser.add_argument("--catalog-root", type=Path, default=Path("server/seed-data/catalog/sentinel-2-earth-search"))
    parser.add_argument("--catalog-name", default="sentinel-2-earth-search")
    parser.add_argument("--data-root", type=Path, default=Path(os.environ.get("NAUTIKOS_DATA_ROOT", "/home/jovyan/work/caspiansea/data-v2")))
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    requested = tuple(part.strip() for part in args.assets.split(",") if part.strip())
    urls: set[str] = set()
    for year in args.years:
        source_catalogue = args.catalog_root / f"{year}.json"
        catalogue = json.loads(source_catalogue.read_text(encoding="utf-8"))
        # The API reads its immutable catalogue from the data root. Keep that
        # copy synchronized with the exact scene set being downloaded so a
        # restart can never combine new assets with an older year manifest.
        local_catalogue = args.data_root / "catalog" / args.catalog_name / f"{year}.json"
        local_catalogue.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_catalogue, local_catalogue)
        for item in catalogue["items"]:
            for name in requested:
                asset = item.get("assets", {}).get(name)
                if asset and asset.get("href", "").startswith("http"):
                    urls.add(asset["href"])

    totals = {"cached": 0, "downloaded": 0, "bytes": 0, "failed": 0}
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        futures = {pool.submit(download, url, destination(args.data_root, url)): url for url in sorted(urls)}
        for index, future in enumerate(as_completed(futures), 1):
            try:
                state, size, _ = future.result()
                totals[state] += 1
                totals["bytes"] += size
            except Exception as exc:  # keep the multi-hour archive job progressing
                totals["failed"] += 1
                print(f"FAILED {futures[future]}: {exc}", flush=True)
            if index % 10 == 0 or index == len(futures):
                elapsed = max(time.monotonic() - started, 1)
                print(
                    f"{index}/{len(futures)} assets; {totals['bytes'] / 2**30:.1f} GiB; "
                    f"{totals['bytes'] / 2**20 / elapsed:.1f} MiB/s; failed={totals['failed']}",
                    flush=True,
                )
    if totals["failed"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
