#!/usr/bin/env python3
"""Build fixed July Sentinel-1 GRD catalogues for oil-slick screening.

The catalogue contains one acquisition for every relative-orbit/slice pair.
It is deterministic for 2020-2026 and points to the public AWS Sentinel-1
measurement COGs, so the Jupyter server can cache exactly the Caspian windows
used by Nautikos without downloading the rest of the planet.
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
COLLECTION = "sentinel-1-grd"
BBOX = (46.0, 36.0, 55.8, 47.4)


def parse_years(value: str) -> list[int]:
    if ":" in value:
        first, last = (int(part) for part in value.split(":", 1))
        return list(range(first, last + 1))
    return [int(part) for part in value.split(",")]


def https_asset(href: str) -> str:
    prefix = "s3://sentinel-s1-l1c/"
    if href.startswith(prefix):
        return "https://sentinel-s1-l1c.s3.eu-central-1.amazonaws.com/" + href[len(prefix):]
    return href


def fetch_year(session: requests.Session, year: int) -> list[dict]:
    body = {
        "collections": [COLLECTION],
        "bbox": BBOX,
        "datetime": f"{year}-07-01T00:00:00Z/{year}-07-31T23:59:59Z",
        "limit": 1000,
        "query": {
            "sar:instrument_mode": {"eq": "IW"},
            "sar:product_type": {"eq": "GRD"},
        },
    }
    found: dict[str, dict] = {}
    url = SEARCH_URL
    while url:
        response = session.post(url, json=body, timeout=120)
        response.raise_for_status()
        payload = response.json()
        for item in payload.get("features", []):
            properties = item.get("properties", {})
            if "VV" in properties.get("sar:polarizations", []) and "vv" in item.get("assets", {}):
                found[item["id"]] = item
        next_link = next((link for link in payload.get("links", []) if link.get("rel") == "next"), None)
        if not next_link:
            break
        url = next_link["href"]
        body = next_link.get("body", body)
    return list(found.values())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", type=parse_years, default=parse_years("2020:2026"))
    parser.add_argument(
        "--output-root",
        type=Path,
        default=Path("server/seed-data/catalog/sentinel-1-earth-search"),
    )
    args = parser.parse_args()
    args.output_root.mkdir(parents=True, exist_ok=True)
    session = requests.Session()

    for year in args.years:
        candidates = fetch_year(session, year)
        grouped: dict[tuple[int, str, int], list[dict]] = defaultdict(list)
        for item in candidates:
            props = item["properties"]
            key = (
                int(props.get("sat:relative_orbit", -1)),
                str(props.get("sat:orbit_state", "unknown")),
                int(props.get("s1:slice_number", 0)),
            )
            grouped[key].append(item)

        target = datetime(year, 7, 16, tzinfo=timezone.utc)
        selected = []
        for group in grouped.values():
            selected.append(
                min(
                    group,
                    key=lambda item: abs(
                        datetime.fromisoformat(item["properties"]["datetime"].replace("Z", "+00:00")) - target
                    ),
                )
            )
        selected.sort(key=lambda item: item["id"])
        records = []
        for item in selected:
            props = item["properties"]
            records.append(
                {
                    "id": item["id"],
                    "bbox": item["bbox"],
                    "geometry": item["geometry"],
                    "datetime": props.get("datetime"),
                    "relative_orbit": props.get("sat:relative_orbit"),
                    "orbit_state": props.get("sat:orbit_state"),
                    "slice_number": props.get("s1:slice_number"),
                    "assets": {"vv": {"href": https_asset(item["assets"]["vv"]["href"])}},
                }
            )
        if not records:
            raise RuntimeError(f"No Sentinel-1 GRD VV scenes selected for {year}")
        scene_set_id = hashlib.sha256("\n".join(record["id"] for record in records).encode()).hexdigest()[:20]
        output = {
            "schema": 2,
            "collection": COLLECTION,
            "provider": "Earth Search / AWS Open Data",
            "year": year,
            "month": 7,
            "bbox": BBOX,
            "scene_set_id": scene_set_id,
            "item_count": len(records),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "warning": "SAR dark-spot candidates require wind, AIS and field verification.",
            "items": records,
        }
        destination = args.output_root / f"{year}.json"
        destination.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"{year}: {len(records)} fixed Sentinel-1 scenes -> {destination}")


if __name__ == "__main__":
    main()
