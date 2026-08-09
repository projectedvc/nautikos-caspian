from __future__ import annotations

import json
import math
import re
from io import BytesIO
from pathlib import Path
from threading import BoundedSemaphore
from typing import Literal

import numpy as np
import rasterio
from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from PIL import Image
from pydantic import BaseModel, Field, model_validator
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.vrt import WarpedVRT
from rasterio.warp import transform_bounds
from rasterio.windows import from_bounds as window_from_bounds

from . import __version__
from .renderer import CatalogRenderer, SUPPORTED_PRODUCTS
from .settings import get_settings


YEARS = tuple(range(2020, 2027))
PRODUCT_RE = re.compile(r"^[a-z][a-z0-9_-]{1,48}$")
TILE_RE = re.compile(r"^[0-9]+$")
settings = get_settings()
renderer = CatalogRenderer(settings)
# A browser can request dozens of tiles after a single zoom gesture.  Each
# uncached render opens several COG windows, so unbounded FastAPI worker threads
# can exhaust memory and kill the data service.  Two concurrent builds keep
# the interactive API healthy; the rest wait and then reuse the immutable tile.
render_slots = BoundedSemaphore(2)

app = FastAPI(title="Nautikos data API", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins or ["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


class BBoxRequest(BaseModel):
    bbox: tuple[float, float, float, float]
    year: int = Field(ge=2020, le=2026)
    product: str = "rgb"

    @model_validator(mode="after")
    def validate_bbox(self) -> "BBoxRequest":
        west, south, east, north = self.bbox
        if not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
            raise ValueError("bbox must be [west, south, east, north]")
        if not PRODUCT_RE.fullmatch(self.product):
            raise ValueError("invalid product")
        return self


class ExportRequest(BBoxRequest):
    overlay: str | None = None
    width: int = Field(default=1600, ge=256, le=4096)
    height: int = Field(default=1200, ge=256, le=4096)
    format: Literal["png", "webp"] = "png"

    @model_validator(mode="after")
    def validate_overlay(self) -> "ExportRequest":
        if self.overlay is not None and not PRODUCT_RE.fullmatch(self.overlay):
            raise ValueError("invalid overlay")
        return self


def product_raster(product: str, year: int) -> Path:
    candidates = (
        settings.nautikos_data_root / "cog" / product / f"{year}.tif",
        settings.nautikos_data_root / "vrt" / product / f"{year}.vrt",
    )
    for path in candidates:
        if path.is_file():
            return path
    raise HTTPException(status_code=409, detail=f"Product {product}/{year} is not built")


def read_manifest(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Invalid manifest: {path.name}") from exc


def rgba_from_raster(path: Path, bbox: tuple[float, float, float, float], width: int, height: int) -> np.ndarray:
    with rasterio.open(path) as source:
        with WarpedVRT(
            source,
            crs="EPSG:4326",
            transform=from_bounds(*bbox, width=width, height=height),
            width=width,
            height=height,
            resampling=Resampling.bilinear,
        ) as vrt:
            data = vrt.read(out_shape=(vrt.count, height, width), masked=True)

    if data.shape[0] >= 3:
        rgb = np.moveaxis(data[:3].filled(0), 0, 2)
        if rgb.dtype != np.uint8:
            rgb = np.clip(rgb, 0, 255).astype(np.uint8)
        alpha = (~np.all(np.ma.getmaskarray(data[:3]), axis=0) & np.any(rgb > 0, axis=2)).astype(np.uint8) * 255
        return np.dstack([rgb, alpha])

    values = data[0]
    valid = ~np.ma.getmaskarray(values) & np.isfinite(values.filled(np.nan))
    raw = values.filled(np.nan).astype(np.float32)
    if not np.any(valid):
        return np.zeros((height, width, 4), dtype=np.uint8)
    low, high = np.nanpercentile(raw[valid], [5, 95])
    if not math.isfinite(low) or not math.isfinite(high) or high <= low:
        low, high = float(np.nanmin(raw[valid])), float(np.nanmax(raw[valid]) + 1e-6)
    scaled = np.clip((raw - low) / (high - low), 0, 1)
    # Transparent blue-yellow-red scientific palette; invalid pixels remain clear.
    red = np.clip(2.0 * scaled - 0.15, 0, 1)
    green = np.clip(1.6 - np.abs(scaled - 0.5) * 2.5, 0, 1)
    blue = np.clip(1.15 - 2.0 * scaled, 0, 1)
    alpha = valid.astype(np.uint8) * 190
    return np.dstack([(red * 255).astype(np.uint8), (green * 255).astype(np.uint8), (blue * 255).astype(np.uint8), alpha])


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "nautikos-data",
        "version": __version__,
        "data_root": str(settings.nautikos_data_root),
    }


@app.get("/v2/manifest")
def manifest() -> dict:
    root = settings.nautikos_data_root / "manifests"
    products: dict[str, dict[str, dict]] = {}
    if root.is_dir():
        for path in sorted(root.glob("*/*.json")):
            product = path.parent.name
            products.setdefault(product, {})[path.stem] = read_manifest(path)
    return {"schema": 2, "years": YEARS, "products": products}


@app.get("/v2/tiles/{product}/{year}/{z}/{x}/{y}.{extension}")
def tile(product: str, year: int, z: str, x: str, y: str, extension: Literal["webp", "png", "jpg"]):
    if year not in YEARS or not PRODUCT_RE.fullmatch(product) or not all(TILE_RE.fullmatch(value) for value in (z, x, y)):
        raise HTTPException(status_code=400, detail="Invalid tile path")
    path = settings.nautikos_data_root / "tiles-v5" / product / str(year) / z / x / f"{y}.{extension}"
    if not path.is_file() and extension == "png" and product in SUPPORTED_PRODUCTS:
        try:
            with render_slots:
                path = renderer.render(product, year, int(z), int(x), int(y))
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"Satellite tile is temporarily unavailable: {exc}") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Tile not found")
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@app.post("/v2/aoi/statistics")
def statistics(request: BBoxRequest) -> dict:
    path = product_raster(request.product, request.year)
    with rasterio.open(path) as source:
        left, bottom, right, top = transform_bounds("EPSG:4326", source.crs, *request.bbox)
        window = window_from_bounds(left, bottom, right, top, source.transform).round_offsets().round_lengths()
        if window.width <= 0 or window.height <= 0:
            raise HTTPException(status_code=400, detail="AOI does not intersect product")
        max_side = 2048
        scale = max(1.0, max(window.width, window.height) / max_side)
        out_h = max(1, round(window.height / scale))
        out_w = max(1, round(window.width / scale))
        band = source.read(1, window=window, out_shape=(out_h, out_w), masked=True, resampling=Resampling.average)
    values = band.compressed()
    if values.size == 0:
        raise HTTPException(status_code=409, detail="No valid observations inside AOI")
    return {
        "year": request.year,
        "product": request.product,
        "bbox": request.bbox,
        "valid_pixels": int(values.size),
        "valid_share": float(values.size / band.size),
        "min": float(np.nanmin(values)),
        "p10": float(np.nanpercentile(values, 10)),
        "median": float(np.nanmedian(values)),
        "p90": float(np.nanpercentile(values, 90)),
        "max": float(np.nanmax(values)),
    }


@app.post("/v2/aoi/export")
def export_image(request: ExportRequest) -> Response:
    base = rgba_from_raster(product_raster("rgb", request.year), request.bbox, request.width, request.height)
    image = Image.fromarray(base, "RGBA")
    if request.overlay and request.overlay != "rgb":
        overlay = rgba_from_raster(product_raster(request.overlay, request.year), request.bbox, request.width, request.height)
        image = Image.alpha_composite(image, Image.fromarray(overlay, "RGBA"))
    output = BytesIO()
    if request.format == "webp":
        image.convert("RGB").save(output, format="WEBP", quality=92, method=6)
        media_type = "image/webp"
    else:
        image.save(output, format="PNG", optimize=True)
        media_type = "image/png"
    return Response(
        output.getvalue(),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="nautikos-{request.year}-{request.product}.{request.format}"'},
    )
