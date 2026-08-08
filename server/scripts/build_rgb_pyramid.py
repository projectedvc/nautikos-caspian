#!/usr/bin/env python3
"""Build aligned RGB VRTs and XYZ tiles from local Sentinel-2 L3 COGs.

GDAL works directly from the downloaded per-MGRS COGs through VRTs, so the
pipeline does not create a huge intermediate uncompressed image.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path


BBOX = (46.0, 36.0, 55.8, 47.4)


def command(*parts: str) -> None:
    print("+", " ".join(parts))
    subprocess.run(parts, check=True)


def require(program: str) -> str:
    path = shutil.which(program)
    if not path:
        raise SystemExit(f"Required program is not installed: {program}")
    return path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="2020,2021,2022,2023,2024,2025,2026")
    parser.add_argument("--data-root", type=Path, default=Path("/home/jovyan/work/caspiansea/data-v2"))
    parser.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--zoom", default="3-14")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    gdalbuildvrt = require("gdalbuildvrt")
    gdal_translate = require("gdal_translate")
    gdalwarp = require("gdalwarp")
    gdal2tiles = require("gdal2tiles.py")
    years = [int(value) for value in args.years.split(",")]

    for year in years:
        raw = args.data_root / "raw" / "sentinel-2-l3-quarterly" / str(year)
        if not raw.is_dir():
            raise SystemExit(f"Missing raw data {raw}")
        catalog_path = args.data_root / "catalog" / "sentinel-2-l3-quarterly" / f"{year}.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        work = args.data_root / "vrt" / "rgb" / str(year)
        work.mkdir(parents=True, exist_ok=True)
        band_vrts = []
        for band in ("B04", "B03", "B02"):
            sources = sorted(raw.glob(f"*/{band}.tif"))
            if len(sources) != catalog["item_count"]:
                raise SystemExit(f"{year}/{band}: expected {catalog['item_count']} files, found {len(sources)}")
            list_path = work / f"{band}.txt"
            list_path.write_text("\n".join(str(path.resolve()) for path in sources) + "\n", encoding="utf-8")
            band_vrt = work / f"{band}.vrt"
            command(gdalbuildvrt, "-overwrite", "-srcnodata", "-32768", "-vrtnodata", "-32768", "-input_file_list", str(list_path), str(band_vrt))
            band_vrts.append(band_vrt)

        rgb_vrt = work / "rgb-source.vrt"
        command(gdalbuildvrt, "-overwrite", "-separate", "-srcnodata", "-32768", "-vrtnodata", "0", str(rgb_vrt), *(str(path) for path in band_vrts))
        byte_vrt = work / "rgb-byte.vrt"
        # One fixed stretch for every year is essential for an honest swipe.
        command(gdal_translate, "-of", "VRT", "-ot", "Byte", "-scale", "150", "3200", "0", "255", str(rgb_vrt), str(byte_vrt))
        warped_vrt = args.data_root / "vrt" / "rgb" / f"{year}.vrt"
        command(
            gdalwarp,
            "-overwrite",
            "-of", "VRT",
            "-t_srs", "EPSG:3857",
            "-te_srs", "EPSG:4326",
            "-te", *(str(value) for value in BBOX),
            "-tr", "10", "10",
            "-r", "cubic",
            "-srcnodata", "0",
            "-dstnodata", "0",
            "-multi",
            "-wo", "NUM_THREADS=ALL_CPUS",
            str(byte_vrt), str(warped_vrt),
        )
        tile_root = args.data_root / "tiles" / "rgb" / str(year)
        tile_root.mkdir(parents=True, exist_ok=True)
        tile_args = [
            gdal2tiles,
            "--xyz",
            "--webviewer=none",
            "--resampling=antialias",
            f"--processes={args.processes}",
            f"--zoom={args.zoom}",
        ]
        if args.resume:
            tile_args.append("--resume")
        tile_args.extend([str(warped_vrt), str(tile_root)])
        command(*tile_args)

        manifest = {
            "schema": 2,
            "product": "rgb",
            "year": year,
            "period": catalog["period"],
            "scene_set_id": catalog["scene_set_id"],
            "source_item_count": catalog["item_count"],
            "source_collection": catalog["collection"],
            "resolution_m": 10,
            "crs": "EPSG:3857",
            "bbox_wgs84": BBOX,
            "zoom": args.zoom,
            "radiometry": {"source_min": 150, "source_max": 3200, "output_min": 0, "output_max": 255},
            "built_at": datetime.now(timezone.utc).isoformat(),
            "complete": True,
        }
        manifest_path = args.data_root / "manifests" / "rgb" / f"{year}.json"
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Published {manifest_path}")


if __name__ == "__main__":
    main()
