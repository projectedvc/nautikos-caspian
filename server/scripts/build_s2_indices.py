#!/usr/bin/env python3
"""Build aligned, masked shoreline and vegetation products from S2 L3 quarterly mosaics."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.enums import Resampling


BBOX = (46.0, 36.0, 55.8, 47.4)


def require(program: str) -> str:
    path = shutil.which(program)
    if not path:
        raise SystemExit(f"Required program is not installed: {program}")
    return path


def command(*parts: str) -> None:
    print("+", " ".join(parts))
    subprocess.run(parts, check=True)


def read_half(path: Path) -> tuple[np.ndarray, np.ndarray, dict]:
    with rasterio.open(path) as source:
        height = max(1, source.height // 2)
        width = max(1, source.width // 2)
        data = source.read(1, out_shape=(height, width), resampling=Resampling.average).astype(np.float32)
        valid = source.read_masks(1, out_shape=(height, width), resampling=Resampling.nearest) > 0
        valid &= np.isfinite(data) & (data != -32768)
        profile = source.profile.copy()
        profile.update(
            width=width,
            height=height,
            transform=source.transform * Affine.scale(source.width / width, source.height / height),
        )
        return data, valid, profile


def water_rgba(ndwi: np.ndarray, valid: np.ndarray) -> np.ndarray:
    water = valid & (ndwi > 0.04)
    edge = water & (
        ~np.roll(water, 1, axis=0)
        | ~np.roll(water, -1, axis=0)
        | ~np.roll(water, 1, axis=1)
        | ~np.roll(water, -1, axis=1)
    )
    rgba = np.zeros((*ndwi.shape, 4), dtype=np.uint8)
    rgba[water] = (15, 112, 165, 42)
    rgba[edge] = (42, 202, 232, 235)
    return rgba


def vegetation_rgba(ndvi: np.ndarray, water: np.ndarray, valid: np.ndarray) -> np.ndarray:
    mask = valid & ~water & (ndvi > 0.05)
    scaled = np.clip((ndvi - 0.05) / 0.65, 0, 1)
    red = np.clip(1.35 - 1.75 * scaled, 0.08, 1.0)
    green = np.clip(0.25 + 1.25 * scaled, 0.2, 0.92)
    blue = np.clip(0.12 + 0.18 * (1 - scaled), 0.08, 0.30)
    rgba = np.zeros((*ndvi.shape, 4), dtype=np.uint8)
    rgba[..., 0] = (red * 255).astype(np.uint8)
    rgba[..., 1] = (green * 255).astype(np.uint8)
    rgba[..., 2] = (blue * 255).astype(np.uint8)
    rgba[..., 3] = mask.astype(np.uint8) * 175
    return rgba


def write_rgba(path: Path, rgba: np.ndarray, source_profile: dict) -> None:
    profile = source_profile.copy()
    profile.update(
        driver="GTiff",
        dtype="uint8",
        count=4,
        nodata=None,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        compress="DEFLATE",
        predictor=2,
        photometric="RGB",
        interleave="pixel",
        BIGTIFF="IF_SAFER",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part.tif")
    with rasterio.open(temporary, "w", **profile) as target:
        target.write(np.moveaxis(rgba, 2, 0))
        target.colorinterp = (
            rasterio.enums.ColorInterp.red,
            rasterio.enums.ColorInterp.green,
            rasterio.enums.ColorInterp.blue,
            rasterio.enums.ColorInterp.alpha,
        )
    temporary.replace(path)


def write_index(path: Path, values: np.ndarray, valid: np.ndarray, source_profile: dict) -> None:
    profile = source_profile.copy()
    profile.update(
        driver="GTiff",
        dtype="float32",
        count=1,
        nodata=-9999.0,
        tiled=True,
        blockxsize=512,
        blockysize=512,
        compress="DEFLATE",
        predictor=3,
        BIGTIFF="IF_SAFER",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part.tif")
    with rasterio.open(temporary, "w", **profile) as target:
        target.write(np.where(valid, values, -9999.0).astype(np.float32), 1)
    temporary.replace(path)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--years", default="2020,2021,2022,2023,2024,2025,2026")
    parser.add_argument("--data-root", type=Path, default=Path("/home/jovyan/work/caspiansea/data-v2"))
    parser.add_argument("--processes", type=int, default=max(1, (os.cpu_count() or 4) // 2))
    parser.add_argument("--zoom", default="3-13")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()

    gdalbuildvrt = require("gdalbuildvrt")
    gdalwarp = require("gdalwarp")
    gdal2tiles = require("gdal2tiles.py")

    for year in [int(value) for value in args.years.split(",")]:
        catalog_path = args.data_root / "catalog" / "sentinel-2-l3-quarterly" / f"{year}.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        raw = args.data_root / "raw" / "sentinel-2-l3-quarterly" / str(year)
        rendered_roots = {
            "water_extent": args.data_root / "derived" / "water_extent" / str(year),
            "vegetation": args.data_root / "derived" / "vegetation" / str(year),
        }
        index_roots = {
            "water_extent": args.data_root / "analysis" / "water_extent" / str(year),
            "vegetation": args.data_root / "analysis" / "vegetation" / str(year),
        }
        for item in catalog["items"]:
            item_id = item["id"]
            outputs = [rendered_roots[p] / f"{item_id}.tif" for p in rendered_roots]
            indices = [index_roots[p] / f"{item_id}.tif" for p in index_roots]
            if args.resume and all(path.is_file() for path in outputs + indices):
                continue
            green, green_valid, profile = read_half(raw / item_id / "B03.tif")
            nir, nir_valid, _ = read_half(raw / item_id / "B08.tif")
            red, red_valid, _ = read_half(raw / item_id / "B04.tif")
            valid = green_valid & nir_valid & red_valid
            ndwi = np.divide(green - nir, green + nir, out=np.zeros_like(green), where=np.abs(green + nir) > 1e-6)
            ndvi = np.divide(nir - red, nir + red, out=np.zeros_like(nir), where=np.abs(nir + red) > 1e-6)
            water = valid & (ndwi > 0.04)
            write_rgba(outputs[0], water_rgba(ndwi, valid), profile)
            write_rgba(outputs[1], vegetation_rgba(ndvi, water, valid), profile)
            write_index(indices[0], ndwi, valid, profile)
            write_index(indices[1], ndvi, valid & ~water, profile)
            print(f"derived {year}/{item_id}")

        for product in rendered_roots:
            product_work = args.data_root / "vrt" / product / str(year)
            product_work.mkdir(parents=True, exist_ok=True)
            render_sources = sorted(rendered_roots[product].glob("*.tif"))
            analysis_sources = sorted(index_roots[product].glob("*.tif"))
            if len(render_sources) != catalog["item_count"] or len(analysis_sources) != catalog["item_count"]:
                raise SystemExit(f"{product}/{year}: incomplete derived source set")
            render_list = product_work / "render.txt"
            analysis_list = product_work / "analysis.txt"
            render_list.write_text("\n".join(str(path.resolve()) for path in render_sources) + "\n", encoding="utf-8")
            analysis_list.write_text("\n".join(str(path.resolve()) for path in analysis_sources) + "\n", encoding="utf-8")
            render_vrt = product_work / "render-source.vrt"
            analysis_vrt = args.data_root / "vrt" / product / f"{year}.vrt"
            command(gdalbuildvrt, "-overwrite", "-input_file_list", str(render_list), str(render_vrt))
            command(gdalbuildvrt, "-overwrite", "-srcnodata", "-9999", "-vrtnodata", "-9999", "-input_file_list", str(analysis_list), str(analysis_vrt))
            warped_vrt = product_work / "render-3857.vrt"
            command(
                gdalwarp,
                "-overwrite", "-of", "VRT",
                "-t_srs", "EPSG:3857",
                "-te_srs", "EPSG:4326",
                "-te", *(str(value) for value in BBOX),
                "-tr", "20", "20",
                "-r", "bilinear",
                "-srcalpha", "-dstalpha",
                "-multi", "-wo", "NUM_THREADS=ALL_CPUS",
                str(render_vrt), str(warped_vrt),
            )
            tile_root = args.data_root / "tiles" / product / str(year)
            tile_root.mkdir(parents=True, exist_ok=True)
            tile_args = [
                gdal2tiles,
                "--xyz", "--webviewer=none", "--resampling=antialias",
                f"--processes={args.processes}", f"--zoom={args.zoom}",
            ]
            if args.resume:
                tile_args.append("--resume")
            tile_args.extend([str(warped_vrt), str(tile_root)])
            command(*tile_args)
            manifest = {
                "schema": 2,
                "product": product,
                "year": year,
                "period": catalog["period"],
                "scene_set_id": catalog["scene_set_id"],
                "source_collection": catalog["collection"],
                "source_item_count": catalog["item_count"],
                "resolution_m": 20,
                "crs": "EPSG:3857",
                "bbox_wgs84": BBOX,
                "scope": "water-and-coast" if product == "water_extent" else "land-and-coast",
                "built_at": datetime.now(timezone.utc).isoformat(),
                "complete": True,
            }
            manifest_path = args.data_root / "manifests" / product / f"{year}.json"
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Published {manifest_path}")


if __name__ == "__main__":
    main()
