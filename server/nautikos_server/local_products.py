"""Strict local-only product registry and COG renderer.

The public filter contract intentionally accepts only six documented
Copernicus-derived products.  A product is available only when both its
provenance manifest and its local Cloud Optimized GeoTIFF exist.  This module
contains no catalogue client, network request, synthetic mask, or fallback.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from io import BytesIO
from pathlib import Path
import numpy as np
import rasterio
from PIL import Image
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT


YEARS = tuple(range(2020, 2027))
TILE_SIZE = 256
WEB_MERCATOR_LIMIT = 20037508.342789244


class LocalProductUnavailable(FileNotFoundError):
    """The requested year/product has not been built locally."""


class InvalidLocalProduct(ValueError):
    """A local asset or its provenance manifest violates the contract."""


@dataclass(frozen=True, slots=True)
class ProductSpec:
    product: str
    satellite: str
    source: str
    measurement: str
    units: str
    resolution_m: int
    value_range: tuple[float, float]
    palette: tuple[str, ...]
    threshold: float | None = None
    opacity: float = 0.82


# Ranges are fixed for every tile and year.  They mirror the physical/index
# ranges used by the corresponding Copernicus Browser products; unlike a
# percentile stretch, they cannot make neighbouring tiles change colour.
PRODUCT_SPECS: dict[str, ProductSpec] = {
    "rivers": ProductSpec(
        "rivers",
        "Sentinel-2 MSI",
        "sentinel-2-l2a",
        "NDWI (B03-B08)/(B03+B08), open-water and river response",
        "index",
        10,
        (-0.15, 0.75),
        ("#fff7ec00", "#fdae6b99", "#f03b20dd", "#99000dff"),
        threshold=0.02,
    ),
    "water_extent": ProductSpec(
        "water_extent",
        "Sentinel-2 MSI",
        "sentinel-2-l2a",
        "NDWI (B03-B08)/(B03+B08), annual water extent",
        "index",
        10,
        (-0.15, 0.75),
        ("#deebf700", "#9ecae1aa", "#3182bddd", "#08519cff"),
        threshold=0.01,
    ),
    "coastal_vegetation": ProductSpec(
        "coastal_vegetation",
        "Sentinel-2 MSI",
        "sentinel-2-l2a",
        "NDVI (B08-B04)/(B08+B04), coastal vegetation",
        "index",
        10,
        (-0.1, 0.85),
        ("#ffffcc00", "#c2e69999", "#31a354dd", "#006837ff"),
        threshold=0.05,
    ),
    "oil_candidates": ProductSpec(
        "oil_candidates",
        "Sentinel-1 C-SAR",
        "sentinel-1-grd",
        "VV local-background contrast, dark surface-film candidates",
        "dB contrast",
        20,
        (3.5, 10.0),
        ("#ffffcc00", "#ffeda0aa", "#feb24cdd", "#f03b20ff", "#bd0026ff"),
        threshold=3.5,
    ),
    "water_temperature": ProductSpec(
        "water_temperature",
        "Sentinel-3 SLSTR",
        "sentinel-3-slstr-l2-wst",
        "SLSTR L2 WST, sea/water surface temperature",
        "degC",
        1000,
        (-2.0, 35.0),
        ("#30123bcc", "#466be3dd", "#28bbecdd", "#32f298ee", "#a4fc3cff", "#faba39ff", "#e12a1cff"),
    ),
    "water_colour": ProductSpec(
        "water_colour",
        "Sentinel-3 OLCI",
        "sentinel-3-olci-l2-water",
        "OLCI L2 WATER total suspended matter (TSM_NN)",
        "log10(g/m^3)",
        300,
        (-2.5, 3.0),
        ("#081d58cc", "#225ea8dd", "#1d91c0dd", "#41b6c4ee", "#7fcdbbff", "#edf8b1ff"),
    ),
}

LOCAL_PRODUCT_IDS = frozenset(PRODUCT_SPECS)


@dataclass(frozen=True, slots=True)
class LocalProductAsset:
    spec: ProductSpec
    year: int
    cog_path: Path
    manifest_path: Path
    manifest: dict


def _parse_colour(value: str) -> tuple[int, int, int, int]:
    raw = value.removeprefix("#")
    if len(raw) not in (6, 8):
        raise InvalidLocalProduct(f"Invalid palette colour: {value}")
    try:
        red, green, blue = (int(raw[offset : offset + 2], 16) for offset in (0, 2, 4))
        alpha = int(raw[6:8], 16) if len(raw) == 8 else 255
    except ValueError as exc:
        raise InvalidLocalProduct(f"Invalid palette colour: {value}") from exc
    return red, green, blue, alpha


def _xyz_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    if z < 0 or z > 22:
        raise InvalidLocalProduct("Zoom must be between 0 and 22")
    size = 1 << z
    if not (0 <= x < size and 0 <= y < size):
        raise InvalidLocalProduct("Tile coordinate is outside the selected zoom")
    span = WEB_MERCATOR_LIMIT * 2.0 / size
    left = -WEB_MERCATOR_LIMIT + x * span
    right = left + span
    top = WEB_MERCATOR_LIMIT - y * span
    bottom = top - span
    return left, bottom, right, top


class LocalProductStore:
    """Resolve and render only prebuilt, provenance-backed local COGs."""

    def __init__(self, data_root: Path):
        self.data_root = data_root.resolve()

    def _safe_asset_path(self, product: str, year: int, manifest: dict) -> Path:
        default = Path("cog") / product / f"{year}.tif"
        relative = Path(str(manifest.get("asset", default)))
        if relative.is_absolute():
            raise InvalidLocalProduct("Manifest asset must be relative to the data root")
        resolved = (self.data_root / relative).resolve()
        try:
            resolved.relative_to(self.data_root)
        except ValueError as exc:
            raise InvalidLocalProduct("Manifest asset escapes the data root") from exc
        return resolved

    def resolve(self, product: str, year: int, *, validate_cog: bool = True) -> LocalProductAsset:
        if product not in PRODUCT_SPECS:
            raise InvalidLocalProduct(f"Unsupported local product: {product}")
        if year not in YEARS:
            raise InvalidLocalProduct(f"Unsupported year: {year}")

        spec = PRODUCT_SPECS[product]
        manifest_path = self.data_root / "manifests" / product / f"{year}.json"
        if not manifest_path.is_file():
            raise LocalProductUnavailable(f"Local manifest is missing for {product}/{year}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise InvalidLocalProduct(f"Invalid manifest for {product}/{year}") from exc

        if manifest.get("schema") != 3:
            raise InvalidLocalProduct(f"Manifest schema 3 is required for {product}/{year}")
        if manifest.get("product") != product or manifest.get("year") != year:
            raise InvalidLocalProduct(f"Manifest identity mismatch for {product}/{year}")
        if manifest.get("source") != spec.source:
            raise InvalidLocalProduct(
                f"Manifest source for {product}/{year} must be {spec.source}"
            )
        if manifest.get("synthetic") is True or manifest.get("fallback") is True:
            raise InvalidLocalProduct(f"Synthetic/fallback product rejected: {product}/{year}")
        if not manifest.get("acquisition_ids"):
            raise InvalidLocalProduct(f"Manifest acquisition_ids are required for {product}/{year}")

        cog_path = self._safe_asset_path(product, year, manifest)
        if not cog_path.is_file():
            raise LocalProductUnavailable(f"Local COG is missing for {product}/{year}")
        asset = LocalProductAsset(spec, year, cog_path, manifest_path, manifest)
        if validate_cog:
            self._validate_cog(asset)
        return asset

    @staticmethod
    def _validate_cog(asset: LocalProductAsset) -> None:
        try:
            with rasterio.open(asset.cog_path) as source:
                if source.driver != "GTiff" or not bool(source.profile.get("tiled")):
                    raise InvalidLocalProduct(f"Asset is not a tiled GeoTIFF: {asset.cog_path.name}")
                if source.crs is None or source.transform is None:
                    raise InvalidLocalProduct(f"Asset is not georeferenced: {asset.cog_path.name}")
                if source.count not in (1, 3, 4):
                    raise InvalidLocalProduct("Local product COG must contain 1, 3 or 4 bands")
                if source.count in (3, 4) and source.dtypes[0] != "uint8":
                    raise InvalidLocalProduct("Pre-coloured COGs must use uint8 RGB/RGBA bands")
                if max(source.width, source.height) > 512 and not source.overviews(1):
                    raise InvalidLocalProduct(
                        f"COG overviews are required for zoom performance: {asset.cog_path.name}"
                    )
        except rasterio.errors.RasterioError as exc:
            raise InvalidLocalProduct(f"Unreadable local COG: {asset.cog_path.name}") from exc

    def contract(self) -> dict:
        products: dict[str, dict] = {}
        for product, spec in PRODUCT_SPECS.items():
            years: dict[str, dict[str, str | bool]] = {}
            for year in YEARS:
                try:
                    asset = self.resolve(product, year, validate_cog=True)
                    years[str(year)] = {
                        "available": True,
                        "manifest": str(asset.manifest_path.relative_to(self.data_root)),
                        "asset": str(asset.cog_path.relative_to(self.data_root)),
                    }
                except (LocalProductUnavailable, InvalidLocalProduct) as exc:
                    years[str(year)] = {"available": False, "reason": str(exc)}
            products[product] = {**asdict(spec), "years": years}
        return {"schema": 3, "mode": "local-only", "years": YEARS, "products": products}

    @staticmethod
    def _colourize(values: np.ma.MaskedArray, spec: ProductSpec) -> np.ndarray:
        raw = values.filled(np.nan).astype(np.float32)
        valid = ~np.ma.getmaskarray(values) & np.isfinite(raw)
        if spec.threshold is not None:
            valid &= raw >= spec.threshold
        low, high = spec.value_range
        normalized = np.clip((raw - low) / (high - low), 0.0, 1.0)
        normalized = np.where(valid, normalized, 0.0)
        palette = np.asarray([_parse_colour(value) for value in spec.palette], dtype=np.float32)
        position = normalized * (len(palette) - 1)
        lower = np.floor(position).astype(np.int16)
        upper = np.clip(lower + 1, 0, len(palette) - 1)
        fraction = (position - lower)[..., None]
        rgba = palette[lower] * (1.0 - fraction) + palette[upper] * fraction
        rgba[..., 3] *= spec.opacity
        rgba[~valid] = 0
        return np.clip(np.rint(rgba), 0, 255).astype(np.uint8)

    @staticmethod
    def _rgba_from_data(data: np.ma.MaskedArray, spec: ProductSpec) -> np.ndarray:
        if data.shape[0] == 1:
            return LocalProductStore._colourize(data[0], spec)

        rgb = np.moveaxis(data[:3].filled(0), 0, 2).astype(np.uint8)
        valid = ~np.any(np.ma.getmaskarray(data[:3]), axis=0)
        if data.shape[0] == 4:
            alpha = data[3].filled(0).astype(np.uint8)
            alpha[~valid] = 0
        else:
            alpha = valid.astype(np.uint8) * round(255 * spec.opacity)
        return np.dstack([rgb, alpha])

    def render_xyz_png(self, product: str, year: int, z: int, x: int, y: int) -> bytes:
        asset = self.resolve(product, year)
        bounds = _xyz_bounds(z, x, y)
        try:
            with rasterio.open(asset.cog_path) as source:
                with WarpedVRT(
                    source,
                    crs="EPSG:3857",
                    transform=from_bounds(*bounds, width=TILE_SIZE, height=TILE_SIZE),
                    width=TILE_SIZE,
                    height=TILE_SIZE,
                    resampling=Resampling.bilinear,
                ) as vrt:
                    data = vrt.read(masked=True)
        except rasterio.errors.RasterioError as exc:
            raise InvalidLocalProduct(f"Unable to read {product}/{year} COG window") from exc

        rgba = self._rgba_from_data(data, asset.spec)
        output = BytesIO()
        Image.fromarray(rgba, "RGBA").save(output, format="PNG", optimize=True)
        return output.getvalue()
