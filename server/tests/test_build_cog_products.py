from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import rasterio


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_cog_products.py"
SPEC = importlib.util.spec_from_file_location("build_cog_products", SCRIPT)
assert SPEC and SPEC.loader
builder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = builder
SPEC.loader.exec_module(builder)


class CogProductBuilderTests(unittest.TestCase):
    def test_registry_is_exact_and_uses_required_real_sources(self) -> None:
        self.assertEqual(
            tuple(builder.PRODUCTS),
            (
                "rivers",
                "water_extent",
                "coastal_vegetation",
                "oil_candidates",
                "water_temperature",
                "water_colour",
            ),
        )
        for product in ("rivers", "water_extent", "coastal_vegetation"):
            self.assertEqual(builder.PRODUCTS[product].source, "sentinel-2-l2a")
        self.assertIn("TSM_NN", builder.PRODUCTS["water_colour"].evalscript)
        self.assertIn('mosaicking: "SIMPLE"', builder.PRODUCTS["water_colour"].evalscript)
        self.assertNotIn('mosaicking: "ORBIT"', builder.PRODUCTS["water_colour"].evalscript)
        self.assertIn(
            "least-cloudy Copernicus mosaic",
            builder.PRODUCTS["water_colour"].algorithm,
        )
        self.assertEqual(
            builder.PRODUCTS["water_temperature"].source_collection,
            "sentinel-3-sl-2-wst-ntc",
        )
        self.assertIsNone(builder.PRODUCTS["water_temperature"].evalscript)

    def test_period_is_exactly_full_july(self) -> None:
        self.assertEqual(
            builder.annual_period(2026),
            {
                "start": "2026-07-01T00:00:00Z",
                "end_inclusive": "2026-07-31T23:59:59Z",
            },
        )
        with self.assertRaises(ValueError):
            builder.annual_period(2019)

    def test_process_payload_uses_tiled_web_mercator_grid_and_fixed_window(self) -> None:
        grid = builder.Grid(0, 0, 2048, 2048, 1, 2048, 2048)
        tile = builder.grid_tiles(grid, 1024)[0]
        payload = builder.process_payload(
            builder.PRODUCTS["oil_candidates"], 2020, grid, tile
        )
        self.assertEqual(
            payload["input"]["bounds"]["properties"]["crs"],
            "http://www.opengis.net/def/crs/EPSG/0/3857",
        )
        self.assertEqual(payload["output"]["width"], 1056)
        self.assertEqual(payload["input"]["data"][0]["type"], "sentinel-1-grd")
        self.assertEqual(payload["input"]["data"][1]["type"], "sentinel-2-l2a")
        self.assertIn('units: ["LINEAR_POWER", "DN"]', payload["evalscript"])
        self.assertEqual(
            payload["input"]["data"][0]["dataFilter"]["timeRange"],
            {
                "from": "2020-07-01T00:00:00Z",
                "to": "2020-07-31T23:59:59Z",
            },
        )

    def test_dark_anomaly_is_local_background_minus_dark_pixel(self) -> None:
        values = np.full((31, 31), -12.0, dtype=np.float32)
        values[15, 15] = -20.0
        mean, count = builder.box_mean(values, np.ones_like(values, dtype=bool), 15)
        self.assertEqual(int(count[15, 15]), 31 * 31)
        self.assertGreater(float(mean[15, 15] - values[15, 15]), 7.9)

    def test_process_tile_cache_is_pinned_to_acquisition_set(self) -> None:
        grid = builder.Grid(0, 0, 2048, 2048, 10, 205, 205)
        first = builder.process_cache_key(
            builder.PRODUCTS["rivers"], 2020, grid, 1024, ["S2_A"], {}
        )
        same_ndwi = builder.process_cache_key(
            builder.PRODUCTS["water_extent"], 2020, grid, 1024, ["S2_A"], {}
        )
        refreshed = builder.process_cache_key(
            builder.PRODUCTS["rivers"], 2020, grid, 1024, ["S2_B"], {}
        )
        self.assertEqual(first, same_ndwi)
        self.assertNotEqual(first, refreshed)

    def test_local_fingerprint_includes_band_and_units(self) -> None:
        grid = builder.Grid(0, 0, 1000, 1000, 1000, 1, 1)
        first = builder.build_fingerprint(
            builder.PRODUCTS["water_temperature"],
            2020,
            grid,
            "local-ingest",
            ["S3_REAL"],
            {},
            {
                "units": "kelvin",
                "assets": [{"path": "wst.tif", "band": 1, "sha256": "abc"}],
            },
        )
        changed = builder.build_fingerprint(
            builder.PRODUCTS["water_temperature"],
            2020,
            grid,
            "local-ingest",
            ["S3_REAL"],
            {},
            {
                "units": "degC",
                "assets": [{"path": "wst.tif", "band": 2, "sha256": "abc"}],
            },
        )
        self.assertNotEqual(first, changed)

    def test_local_manifest_rejects_synthetic_and_requires_acquisitions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            folder = root / "water_temperature" / "2020"
            folder.mkdir(parents=True)
            (folder / "manifest.json").write_text(
                json.dumps(
                    {
                        "product": "water_temperature",
                        "year": 2020,
                        "source": "sentinel-3-slstr-l2-wst",
                        "period": builder.annual_period(2020),
                        "acquisition_ids": ["S3A_SL_2_WST_REAL"],
                        "assets": ["wst.tif"],
                        "synthetic": True,
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(builder.BuildError):
                builder.find_local_input(
                    root, builder.PRODUCTS["water_temperature"], 2020, validate=False
                )

    def test_assembly_writes_tiled_cog_with_internal_overviews(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            grid = builder.Grid(0, 0, 10240, 10240, 10, 1024, 1024)
            tiles = builder.grid_tiles(grid, 256)
            tile_root = root / "tiles"
            for tile in tiles:
                values = np.full(
                    (tile.height, tile.width), tile.row + tile.col / 10, dtype=np.float32
                )
                builder.write_tile(tile_root / f"{tile.name}.tif", values, grid, tile)
            output = root / "cog" / "water_extent" / "2020.tif"
            checksum, size, overviews = builder.assemble_cog(
                output,
                builder.PRODUCTS["water_extent"],
                2020,
                grid,
                tiles,
                tile_root,
            )
            self.assertEqual(checksum, builder.sha256_file(output))
            self.assertEqual(size, output.stat().st_size)
            self.assertTrue(overviews)
            with rasterio.open(output) as source:
                self.assertTrue(bool(source.profile.get("tiled")))
                self.assertEqual(source.crs.to_epsg(), 3857)
                self.assertEqual(source.count, 1)
                self.assertTrue(source.overviews(1))
            manifest_path = root / "manifests" / "water_extent" / "2020.json"
            builder.write_manifest(
                manifest_path,
                root,
                output,
                builder.PRODUCTS["water_extent"],
                2020,
                grid,
                "process-api",
                ["S2A_REAL_ACQUISITION"],
                {},
                "fingerprint",
                checksum,
                size,
                overviews,
                None,
                [],
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema"], 3)
            self.assertEqual(manifest["source"], "sentinel-2-l2a")
            self.assertEqual(manifest["asset"], "cog/water_extent/2020.tif")
            self.assertEqual(manifest["acquisition_ids"], ["S2A_REAL_ACQUISITION"])
            self.assertEqual(manifest["checksum"]["value"], checksum)
            self.assertFalse(manifest["synthetic"])
            self.assertFalse(manifest["fallback"])
            self.assertTrue(
                builder.manifest_matches(
                    manifest_path,
                    output,
                    builder.PRODUCTS["water_extent"],
                    2020,
                    "fingerprint",
                    grid,
                )
            )

    def test_dry_run_needs_no_credentials_network_or_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "does-not-exist"
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = builder.main(
                    [
                        "--dry-run",
                        "--years",
                        "2020",
                        "--products",
                        "all",
                        "--data-root",
                        str(root),
                    ]
                )
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(payload["job_count"], 6)
            self.assertFalse(payload["network"])
            self.assertFalse(payload["writes"])
            self.assertFalse(root.exists())


if __name__ == "__main__":
    unittest.main()
