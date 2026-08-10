from __future__ import annotations

import json
import sys
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

import numpy as np
import rasterio
from PIL import Image
from rasterio.transform import from_bounds


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))

from nautikos_server.local_products import (  # noqa: E402
    LOCAL_PRODUCT_IDS,
    PRODUCT_SPECS,
    InvalidLocalProduct,
    LocalProductStore,
    LocalProductUnavailable,
)


EXPECTED_PRODUCTS = {
    "rivers",
    "water_extent",
    "coastal_vegetation",
    "oil_candidates",
    "water_temperature",
    "water_colour",
}


class LocalProductStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LocalProductStore(self.root)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _build(self, product: str = "water_extent", year: int = 2020) -> None:
        cog = self.root / "cog" / product / f"{year}.tif"
        cog.parent.mkdir(parents=True)
        values = np.linspace(-0.2, 0.8, 256 * 256, dtype=np.float32).reshape(256, 256)
        values[128:160, 128:160] = -9999
        with rasterio.open(
            cog,
            "w",
            driver="GTiff",
            width=256,
            height=256,
            count=1,
            dtype="float32",
            crs="EPSG:4326",
            transform=from_bounds(-180, -85, 180, 85, 256, 256),
            tiled=True,
            blockxsize=128,
            blockysize=128,
            nodata=-9999,
        ) as target:
            target.write(values, 1)

        manifest = self.root / "manifests" / product / f"{year}.json"
        manifest.parent.mkdir(parents=True)
        manifest.write_text(
            json.dumps(
                {
                    "schema": 3,
                    "product": product,
                    "year": year,
                    "source": PRODUCT_SPECS[product].source,
                    "acquisition_ids": ["real-scene-id"],
                    "asset": f"cog/{product}/{year}.tif",
                }
            ),
            encoding="utf-8",
        )

    def test_registry_contains_only_new_contract_products(self) -> None:
        self.assertEqual(LOCAL_PRODUCT_IDS, EXPECTED_PRODUCTS)
        for product in ("rivers", "water_extent", "coastal_vegetation"):
            self.assertEqual(PRODUCT_SPECS[product].satellite, "Sentinel-2 MSI")
            self.assertEqual(PRODUCT_SPECS[product].source, "sentinel-2-l2a")
        self.assertEqual(PRODUCT_SPECS["oil_candidates"].source, "sentinel-1-grd")
        self.assertEqual(PRODUCT_SPECS["oil_candidates"].satellite, "Sentinel-1 C-SAR")
        self.assertEqual(PRODUCT_SPECS["water_temperature"].source, "sentinel-3-slstr-l2-wst")
        self.assertEqual(PRODUCT_SPECS["water_temperature"].satellite, "Sentinel-3 SLSTR")
        self.assertEqual(PRODUCT_SPECS["water_temperature"].resolution_m, 1000)
        self.assertEqual(PRODUCT_SPECS["water_colour"].source, "sentinel-3-olci-l2-water")
        self.assertEqual(PRODUCT_SPECS["water_colour"].satellite, "Sentinel-3 OLCI")
        self.assertEqual(PRODUCT_SPECS["water_colour"].resolution_m, 300)
        self.assertEqual(PRODUCT_SPECS["water_colour"].units, "log10(g/m^3)")
        self.assertEqual(PRODUCT_SPECS["water_colour"].value_range, (-2.5, 3.0))

    def test_json_contract_matches_runtime_registry(self) -> None:
        config = json.loads((SERVER_ROOT / "config" / "products.json").read_text(encoding="utf-8"))
        self.assertEqual(config["schema"], 3)
        self.assertEqual(config["mode"], "local-only")
        self.assertEqual(set(config["products"]), EXPECTED_PRODUCTS)
        for product, spec in PRODUCT_SPECS.items():
            configured = config["products"][product]
            self.assertEqual(configured["source"], spec.source)
            self.assertEqual(configured["resolution_m"], spec.resolution_m)
            self.assertEqual(tuple(configured["range"]), spec.value_range)

    def test_missing_manifest_never_falls_back(self) -> None:
        with self.assertRaises(LocalProductUnavailable):
            self.store.resolve("rivers", 2020)

    def test_rejects_wrong_sensor_and_synthetic_manifest(self) -> None:
        self._build()
        path = self.root / "manifests" / "water_extent" / "2020.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["source"] = "sentinel-1-grd"
        manifest["synthetic"] = True
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(InvalidLocalProduct):
            self.store.resolve("water_extent", 2020)

    def test_rejects_asset_path_escape(self) -> None:
        self._build()
        path = self.root / "manifests" / "water_extent" / "2020.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["asset"] = "../../outside.tif"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(InvalidLocalProduct):
            self.store.resolve("water_extent", 2020)

    def test_renders_real_local_cog_with_fixed_transparency(self) -> None:
        self._build()
        payload = self.store.render_xyz_png("water_extent", 2020, 0, 0, 0)
        image = np.asarray(Image.open(BytesIO(payload)).convert("RGBA"))
        self.assertEqual(image.shape, (256, 256, 4))
        self.assertGreater(int(image[..., 3].max()), 0)
        self.assertEqual(int(image[0, 0, 3]), 0)

    def test_contract_reports_missing_assets_without_creating_them(self) -> None:
        contract = self.store.contract()
        self.assertEqual(contract["mode"], "local-only")
        self.assertFalse(contract["products"]["rivers"]["years"]["2020"]["available"])
        self.assertFalse((self.root / "cog").exists())


if __name__ == "__main__":
    unittest.main()
