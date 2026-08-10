from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np


SERVER_ROOT = Path(__file__).resolve().parents[1]
if str(SERVER_ROOT) not in sys.path:
    sys.path.insert(0, str(SERVER_ROOT))
if "boto3" not in sys.modules:
    sys.modules["boto3"] = SimpleNamespace(client=lambda *args, **kwargs: None)
if "pydantic_settings" not in sys.modules:
    sys.modules["pydantic_settings"] = SimpleNamespace(
        BaseSettings=object,
        SettingsConfigDict=lambda **kwargs: {},
    )

from nautikos_server.renderer import CatalogRenderer, LOCAL_SCENE_PRODUCTS  # noqa: E402


class LocalSceneFilterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.renderer = object.__new__(CatalogRenderer)
        shape = (256, 256)
        self.arrays = {
            "B02": np.full(shape, 900.0, dtype=np.float32),
            "B03": np.full(shape, 700.0, dtype=np.float32),
            "B04": np.full(shape, 500.0, dtype=np.float32),
            "B08": np.full(shape, 1600.0, dtype=np.float32),
            "water_probability": np.zeros(shape, dtype=np.float32),
        }
        self.valid = np.ones(shape, dtype=bool)

    def test_registry_is_only_real_local_s2_and_s1_filters(self) -> None:
        self.assertEqual(
            LOCAL_SCENE_PRODUCTS,
            {"rivers", "water_extent", "coastal_vegetation", "oil_candidates"},
        )

    def test_local_asset_guard_rejects_error_body_with_tif_suffix(self) -> None:
        CatalogRenderer._local_raster_is_valid.cache_clear()
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            error_asset = root / "failed.tif"
            error_asset.write_bytes(b"<Error>expired or incomplete download</Error>" * 40)
            valid_asset = root / "valid.tif"
            valid_asset.write_bytes(b"II*\x00" + b"\x00" * 2048)
            self.assertFalse(self.renderer._local_raster_is_valid(error_asset))
            self.assertTrue(self.renderer._local_raster_is_valid(valid_asset))

    def test_river_filter_is_fixed_colour_infrared_composite(self) -> None:
        rgba = self.renderer._rgba("rivers", self.arrays, self.valid)
        self.assertEqual(int(rgba[..., 3].min()), 255)
        self.assertGreater(int(rgba[..., 0].mean()), int(rgba[..., 1].mean()))
        self.valid[20, 20] = False
        rgba = self.renderer._rgba("rivers", self.arrays, self.valid)
        self.assertEqual(int(rgba[20, 20, 3]), 0)

    def test_water_extent_draws_only_fixed_boundary_without_area_fill(self) -> None:
        self.arrays["B03"][:, :128] = 2200.0
        self.arrays["B08"][:, :128] = 300.0
        rgba = self.renderer._rgba("water_extent", self.arrays, self.valid)
        self.assertEqual(int(rgba[:, :90, 3].max()), 0)
        self.assertGreater(int(rgba[:, 124:132, 3].max()), 0)
        visible = rgba[..., 3] > 0
        self.assertTrue(np.all(rgba[..., 0][visible] == 255))
        self.assertTrue(np.all(rgba[..., 1][visible] == 198))

    def test_coastal_vegetation_is_limited_to_water_neighbourhood(self) -> None:
        self.arrays["B03"][:, :80] = 2300.0
        self.arrays["B08"][:, :80] = 250.0
        self.arrays["B04"][:, 80:] = 350.0
        self.arrays["B08"][:, 80:] = 2600.0
        rgba = self.renderer._rgba("coastal_vegetation", self.arrays, self.valid)
        self.assertGreater(int(rgba[:, 80:120, 3].max()), 0)
        self.assertEqual(int(rgba[:, 220:, 3].max()), 0)


if __name__ == "__main__":
    unittest.main()
