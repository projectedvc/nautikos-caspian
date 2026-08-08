#!/usr/bin/env python3
"""Create deterministic STAC catalogues for Sentinel-2 L3 quarterly mosaics.

This command only queries public metadata. It does not download pixels and does
not require credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from calendar import monthrange
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests


STAC_URL = "https://stac.dataspace.copernicus.eu/v1"
COLLECTION = "sentinel-2-global-mosaics"
DEFAULT_BBOX = (46.0, 36.0, 55.8, 47.4)
REQUIRED_ASSETS = ("B02", "B03", "B04", "B08", "observations")


def year_range(value: str) -> list[int]:
    if ":" in value:
        first, last = (int(item) for item in value.split(":", 1))
        years = list(range(first, last + 1))
    else:
        years = [int(item) for item in value.split(",")]
    if not years or min(years) < 2020 or max(years) > 2026:
        raise argparse.ArgumentTypeError("years must be inside 2020..2026")
    return years


def s3_location(href: str) -> tuple[str, str]:
    parsed = urlparse(href)
    if parsed.scheme != "s3" or not parsed.netloc or not parsed.path:
        raise ValueError(f"Expected s3 asset, got {href}")
    return parsed.netloc, parsed.path.lstrip("/")


def canonical_hash(payload: object) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def build_catalog(session: requests.Session, year: int, bbox: tuple[float, float, float, float], quarter: int) -> dict:
    month = (quarter - 1) * 3 + 1
    start = f"{year}-{month:02d}-01T00:00:00Z"
    end_month = month + 2
    stop = f"{year}-{end_month:02d}-{monthrange(year, end_month)[1]:02d}T23:59:59Z"
    response = session.post(
        f"{STAC_URL}/search",
        json={"collections": [COLLECTION], "bbox": bbox, "datetime": f"{start}/{stop}", "limit": 1000},
        timeout=120,
    )
    response.raise_for_status()
    features = response.json().get("features", [])
    if len(features) >= 1000:
        raise RuntimeError("STAC response reached the 1000 item safety limit; tighten the extent")
    records: list[dict] = []
    expected_token = f"_{year}_Q{quarter}_"
    for item in features:
        item_id = str(item["id"])
        if expected_token not in item_id:
            continue
        missing = [name for name in REQUIRED_ASSETS if name not in item["assets"]]
        if missing:
            raise RuntimeError(f"{item_id} has no assets: {', '.join(missing)}")
        assets = {}
        for name in REQUIRED_ASSETS:
            asset = item["assets"][name]
            bucket, key = s3_location(asset["href"])
            assets[name] = {
                "href": asset["href"],
                "bucket": bucket,
                "key": key,
                "type": asset.get("type"),
                "roles": asset.get("roles", []),
                "size": int(asset.get("file:size", 0)),
            }
        properties = item.get("properties", {})
        records.append(
            {
                "id": item_id,
                "bbox": item.get("bbox"),
                "geometry": item.get("geometry"),
                "datetime": properties.get("datetime"),
                "properties": {
                    key: value
                    for key, value in properties.items()
                    if key.startswith("proj:") or key in {"start_datetime", "end_datetime", "grid:code"}
                },
                "assets": assets,
            }
        )
    if not records:
        raise RuntimeError(f"No Sentinel-2 L3 Q{quarter} items found for {year}")
    records.sort(key=lambda record: record["id"])
    scene_set_id = canonical_hash([record["id"] for record in records])[:20]
    return {
        "schema": 2,
        "collection": COLLECTION,
        "year": year,
        "quarter": quarter,
        "period": {"start": start, "end_inclusive": stop},
        "bbox": bbox,
        "scene_set_id": scene_set_id,
        "item_count": len(records),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "items": records,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=year_range, default=year_range("2020:2026"))
    parser.add_argument("--quarter", type=int, choices=(1, 2, 3, 4), default=1)
    parser.add_argument("--bbox", nargs=4, type=float, default=DEFAULT_BBOX, metavar=("W", "S", "E", "N"))
    parser.add_argument("--output-root", type=Path, default=Path("/home/jovyan/work/caspiansea/data-v2"))
    args = parser.parse_args()

    session = requests.Session()
    destination = args.output_root / "catalog" / "sentinel-2-l3-quarterly"
    destination.mkdir(parents=True, exist_ok=True)
    scene_sets: set[str] = set()
    for year in args.years:
        catalog = build_catalog(session, year, tuple(args.bbox), args.quarter)
        if catalog["scene_set_id"] in scene_sets:
            raise RuntimeError(f"Duplicate scene set for {year}; refusing to publish aliased years")
        scene_sets.add(catalog["scene_set_id"])
        path = destination / f"{year}.json"
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{year}: {catalog['item_count']} items, scene_set_id={catalog['scene_set_id']} -> {path}")


if __name__ == "__main__":
    main()
