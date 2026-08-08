#!/usr/bin/env python3
"""Build fixed annual Sentinel-2 L2A catalogues from the public AWS archive.

Earth Search exposes the same ESA Sentinel-2 observations as public COGs. The
catalogue chooses one least-cloudy Q1 acquisition for every MGRS grid cell so
each year is real, reproducible and independent from runtime API credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests


SEARCH_URL = "https://earth-search.aws.element84.com/v1/search"
COLLECTION = "sentinel-2-l2a"
BBOX = (46.0, 36.0, 55.8, 47.4)
ASSETS = ("visual", "blue", "green", "red", "nir", "scl")


def years(value: str) -> list[int]:
    if ":" in value:
        first, last = (int(part) for part in value.split(":", 1))
        return list(range(first, last + 1))
    return [int(part) for part in value.split(",")]


def grid_id(feature: dict) -> str:
    props = feature["properties"]
    return f"{props['mgrs:utm_zone']}{props['mgrs:latitude_band']}{props['mgrs:grid_square']}"


def fetch_year(session: requests.Session, year: int, cloud: float) -> list[dict]:
    body = {
        "collections": [COLLECTION],
        "bbox": BBOX,
        "datetime": f"{year}-01-01T00:00:00Z/{year}-03-31T23:59:59Z",
        "limit": 1000,
        "query": {"eo:cloud_cover": {"lt": cloud}},
        "sortby": [{"field": "properties.eo:cloud_cover", "direction": "asc"}],
    }
    features: list[dict] = []
    url = SEARCH_URL
    while url:
        response = session.post(url, json=body, timeout=120)
        response.raise_for_status()
        payload = response.json()
        features.extend(payload.get("features", []))
        next_link = next((link for link in payload.get("links", []) if link.get("rel") == "next"), None)
        if not next_link:
            break
        url = next_link["href"]
        body = next_link.get("body", body)
        if len(features) >= 5000:
            raise RuntimeError("Earth Search pagination safety limit reached")
    return features


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=years, default=years("2020:2026"))
    parser.add_argument("--cloud", type=float, default=35.0)
    parser.add_argument("--output-root", type=Path, default=Path("/home/jovyan/work/caspiansea/data-v2"))
    args = parser.parse_args()
    output = args.output_root / "catalog" / "sentinel-2-earth-search"
    output.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    for year in args.years:
        candidates = fetch_year(session, year, args.cloud)
        grouped: dict[str, list[dict]] = defaultdict(list)
        for feature in candidates:
            if all(name in feature.get("assets", {}) for name in ASSETS):
                grouped[grid_id(feature)].append(feature)
        selected = [min(group, key=lambda item: float(item["properties"].get("eo:cloud_cover", 100))) for group in grouped.values()]
        selected.sort(key=lambda item: grid_id(item))
        records = []
        for item in selected:
            assets = {name: {"href": item["assets"][name]["href"]} for name in ASSETS}
            visual = assets["visual"]
            assets.update(
                {
                    "TCI_R": {**visual, "band": 1},
                    "TCI_G": {**visual, "band": 2},
                    "TCI_B": {**visual, "band": 3},
                    "B02": assets["blue"],
                    "B03": assets["green"],
                    "B04": assets["red"],
                    "B08": assets["nir"],
                }
            )
            records.append(
                {
                    "id": item["id"],
                    "grid": grid_id(item),
                    "bbox": item["bbox"],
                    "geometry": item["geometry"],
                    "datetime": item["properties"].get("datetime"),
                    "cloud_cover": item["properties"].get("eo:cloud_cover"),
                    "assets": assets,
                }
            )
        if not records:
            raise RuntimeError(f"No public Sentinel-2 scenes selected for {year}")
        scene_set_id = hashlib.sha256("\n".join(record["id"] for record in records).encode()).hexdigest()[:20]
        catalogue = {
            "schema": 2,
            "collection": COLLECTION,
            "provider": "Earth Search / AWS Open Data",
            "year": year,
            "quarter": 1,
            "period": {"start": f"{year}-01-01T00:00:00Z", "end_inclusive": f"{year}-03-31T23:59:59Z"},
            "bbox": BBOX,
            "scene_set_id": scene_set_id,
            "item_count": len(records),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "items": records,
        }
        destination = output / f"{year}.json"
        destination.write_text(json.dumps(catalogue, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{year}: {len(records)} MGRS scenes, {scene_set_id} -> {destination}", flush=True)


if __name__ == "__main__":
    main()
