#!/usr/bin/env python3
"""Download the selected Sentinel-2 L3 assets from CDSE S3.

The command writes to a temporary `.part` file, validates object size and only
then renames the download. Existing complete files are never downloaded twice.
"""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Asset:
    year: int
    item_id: str
    band: str
    bucket: str
    key: str
    target: Path


def human_bytes(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.2f} {unit}"
        amount /= 1024
    return f"{value} B"


def assets_from_catalog(path: Path, root: Path, bands: set[str]) -> list[Asset]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    year = int(payload["year"])
    result = []
    for item in payload["items"]:
        for band in sorted(bands):
            source = item["assets"][band]
            result.append(
                Asset(
                    year=year,
                    item_id=item["id"],
                    band=band,
                    bucket=source["bucket"],
                    key=source["key"],
                    target=root / "raw" / "sentinel-2-l3-quarterly" / str(year) / item["id"] / f"{band}.tif",
                )
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="2020,2021,2022,2023,2024,2025,2026")
    parser.add_argument("--bands", default="B02,B03,B04,B08,observations")
    parser.add_argument("--output-root", type=Path, default=Path("/home/jovyan/work/caspiansea/data-v2"))
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    years = [int(value) for value in args.years.split(",")]
    bands = {value.strip() for value in args.bands.split(",") if value.strip()}
    catalog_root = args.output_root / "catalog" / "sentinel-2-l3-quarterly"
    assets: list[Asset] = []
    for year in years:
        path = catalog_root / f"{year}.json"
        if not path.is_file():
            raise SystemExit(f"Missing catalogue {path}; run catalog_s2_l3.py first")
        assets.extend(assets_from_catalog(path, args.output_root, bands))

    catalog_sizes: dict[tuple[str, str], int] = {}
    for year in years:
        payload = json.loads((catalog_root / f"{year}.json").read_text(encoding="utf-8"))
        for item in payload["items"]:
            for band in bands:
                source = item["assets"][band]
                if int(source.get("size", 0)) > 0:
                    catalog_sizes[(source["bucket"], source["key"])] = int(source["size"])

    if args.dry_run and len(catalog_sizes) == len(assets):
        total = sum(catalog_sizes.values())
        existing = sum(
            size
            for asset in assets
            if asset.target.is_file() and asset.target.stat().st_size == (size := catalog_sizes[(asset.bucket, asset.key)])
        )
        print(f"Objects: {len(assets)}; total: {human_bytes(total)}; already complete: {human_bytes(existing)}; remaining: {human_bytes(total-existing)}")
        return

    endpoint = os.getenv("CDSE_S3_ENDPOINT", "https://eodata.dataspace.copernicus.eu")
    access = os.getenv("CDSE_S3_ACCESS_KEY", "")
    secret = os.getenv("CDSE_S3_SECRET_KEY", "")
    if not access or not secret:
        raise SystemExit("Set CDSE_S3_ACCESS_KEY and CDSE_S3_SECRET_KEY in the server environment")

    import boto3
    from boto3.s3.transfer import TransferConfig

    session = boto3.session.Session()
    client = session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
        region_name="default",
    )

    sizes: dict[tuple[str, str], int] = {}
    for index, asset in enumerate(assets, start=1):
        head = client.head_object(Bucket=asset.bucket, Key=asset.key)
        sizes[(asset.bucket, asset.key)] = int(head["ContentLength"])
        if index % 100 == 0:
            print(f"Inspected {index}/{len(assets)} objects")
    total = sum(sizes.values())
    existing = sum(
        size
        for asset in assets
        if asset.target.is_file() and asset.target.stat().st_size == (size := sizes[(asset.bucket, asset.key)])
    )
    print(f"Objects: {len(assets)}; total: {human_bytes(total)}; already complete: {human_bytes(existing)}; remaining: {human_bytes(total-existing)}")
    if args.dry_run:
        return

    transfer = TransferConfig(
        multipart_threshold=64 * 1024 * 1024,
        multipart_chunksize=32 * 1024 * 1024,
        max_concurrency=4,
        use_threads=True,
    )

    def download(asset: Asset) -> tuple[Asset, str]:
        expected = sizes[(asset.bucket, asset.key)]
        if asset.target.is_file() and asset.target.stat().st_size == expected:
            return asset, "cached"
        asset.target.parent.mkdir(parents=True, exist_ok=True)
        temporary = asset.target.with_suffix(asset.target.suffix + ".part")
        if temporary.exists():
            temporary.unlink()
        client.download_file(asset.bucket, asset.key, str(temporary), Config=transfer)
        actual = temporary.stat().st_size
        if actual != expected:
            temporary.unlink(missing_ok=True)
            raise IOError(f"Size mismatch for {asset.key}: {actual} != {expected}")
        temporary.replace(asset.target)
        return asset, "downloaded"

    completed = 0
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(download, asset): asset for asset in assets}
        for future in as_completed(futures):
            asset, status = future.result()
            completed += 1
            print(f"[{completed}/{len(assets)}] {status}: {asset.year}/{asset.item_id}/{asset.band}")


if __name__ == "__main__":
    main()
