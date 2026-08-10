#!/usr/bin/env python3
"""Build the six immutable Nautikos annual analytical COG products.

The command is intended to run on the Jupyter host in
``/home/jovyan/work/caspiansea``.  It uses a fixed 1--31 July acquisition
window and a common EPSG:3857 grid.  Sentinel Hub Process API requests are
split into deterministic tiles, cached with SHA-256 sidecars and assembled
into one Cloud Optimized GeoTIFF per product/year.  Existing real analytical
GeoTIFFs can be ingested through a provenance sidecar instead.

No preview PNGs, placeholders, synthetic pixels or data-source fallbacks are
created.  Sentinel-3 SLSTR L2 WST is deliberately local-ingest only because
CDSE Sentinel Hub exposes SLSTR L1B, not the L2 WST product.  Point the local
ingest manifest at genuine WST GeoTIFF exports (for example assets prepared
from the official ``sentinel-3-sl-2-wst-ntc`` STAC collection).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
import rasterio
import requests
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.shutil import copy as rio_copy
from rasterio.transform import Affine, from_origin
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window


FIRST_YEAR = 2020
LAST_YEAR = 2026
YEARS = tuple(range(FIRST_YEAR, LAST_YEAR + 1))
PRODUCT_IDS = (
    "rivers",
    "water_extent",
    "coastal_vegetation",
    "oil_candidates",
    "water_temperature",
    "water_colour",
)

BBOX_WGS84 = (46.0, 36.0, 55.8, 47.4)
OUTPUT_CRS = "EPSG:3857"
NODATA = -9999.0
PERIOD_TEMPLATE = {
    "start": "{year}-07-01T00:00:00Z",
    "end_inclusive": "{year}-07-31T23:59:59Z",
}

TOKEN_URL = (
    "https://identity.dataspace.copernicus.eu/auth/realms/CDSE/"
    "protocol/openid-connect/token"
)
PROCESS_URL = "https://sh.dataspace.copernicus.eu/process/v1"
CATALOG_URL = "https://sh.dataspace.copernicus.eu/catalog/v1/search"
WST_STAC_COLLECTION = "sentinel-3-sl-2-wst-ntc"


S2_NDWI_EVALSCRIPT = r"""//VERSION=3
function setup() {
  return {
    input: [{bands: ["B03", "B08", "SCL", "dataMask"]}],
    mosaicking: "ORBIT",
    output: {bands: 2, sampleType: "FLOAT32"}
  };
}
function clearSample(sample) {
  return sample.dataMask && (sample.SCL === 4 || sample.SCL === 5 ||
    sample.SCL === 6 || sample.SCL === 7);
}
function evaluatePixel(samples) {
  var sum = 0.0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    var sample = samples[i];
    if (!clearSample(sample)) continue;
    var denominator = sample.B03 + sample.B08;
    if (Math.abs(denominator) < 0.000001) continue;
    sum += (sample.B03 - sample.B08) / denominator;
    count++;
  }
  return count ? [sum / count, 1] : [-9999, 0];
}
"""


S2_NDVI_EVALSCRIPT = r"""//VERSION=3
function setup() {
  return {
    input: [{bands: ["B03", "B04", "B08", "SCL", "dataMask"]}],
    mosaicking: "ORBIT",
    output: {bands: 2, sampleType: "FLOAT32"}
  };
}
function clearSample(sample) {
  return sample.dataMask && (sample.SCL === 4 || sample.SCL === 5 ||
    sample.SCL === 7);
}
function evaluatePixel(samples) {
  var sum = 0.0, count = 0;
  for (var i = 0; i < samples.length; i++) {
    var sample = samples[i];
    if (!clearSample(sample)) continue;
    var ndviDenominator = sample.B08 + sample.B04;
    var ndwiDenominator = sample.B03 + sample.B08;
    if (Math.abs(ndviDenominator) < 0.000001 ||
        Math.abs(ndwiDenominator) < 0.000001) continue;
    // Keep the moist coastal/riparian land response, not the full inland
    // vegetation rectangle and not open water.
    var ndwi = (sample.B03 - sample.B08) / ndwiDenominator;
    if (ndwi < -0.25 || ndwi > 0.08) continue;
    sum += (sample.B08 - sample.B04) / ndviDenominator;
    count++;
  }
  return count ? [sum / count, 1] : [-9999, 0];
}
"""


S1_DARK_INPUT_EVALSCRIPT = r"""//VERSION=3
function setup() {
  return {
    input: [
      {datasource: "sar", bands: ["VV", "dataMask"], units: ["LINEAR_POWER", "DN"]},
      {datasource: "water", bands: ["B03", "B08", "SCL", "dataMask"]}
    ],
    mosaicking: "ORBIT",
    output: {bands: 3, sampleType: "FLOAT32"}
  };
}
function clearS2(sample) {
  return sample.dataMask && (sample.SCL === 4 || sample.SCL === 5 ||
    sample.SCL === 6 || sample.SCL === 7);
}
function evaluatePixel(samples) {
  var sarSum = 0.0, sarCount = 0;
  var radar = samples.sar || [];
  for (var i = 0; i < radar.length; i++) {
    if (!radar[i].dataMask || radar[i].VV <= 0) continue;
    sarSum += 10.0 * Math.log(radar[i].VV) / Math.LN10;
    sarCount++;
  }
  var ndwiSum = 0.0, ndwiCount = 0;
  var optical = samples.water || [];
  for (var j = 0; j < optical.length; j++) {
    var sample = optical[j];
    if (!clearS2(sample)) continue;
    var denominator = sample.B03 + sample.B08;
    if (Math.abs(denominator) < 0.000001) continue;
    ndwiSum += (sample.B03 - sample.B08) / denominator;
    ndwiCount++;
  }
  if (!sarCount || !ndwiCount) return [-9999, -9999, 0];
  return [sarSum / sarCount, ndwiSum / ndwiCount, 1];
}
"""


OLCI_TSM_EVALSCRIPT = r"""//VERSION=3
function setup() {
  return {
    input: [{bands: ["TSM_NN", "dataMask"]}],
    // Copernicus Browser renders one deterministic least-cloudy mosaic for
    // the selected period.  ORBIT would evaluate every OLCI acquisition in
    // July for every output pixel, consuming hundreds of processing units per
    // tile and producing a temporal average that does not match Browser.
    mosaicking: "SIMPLE",
    output: {bands: 2, sampleType: "FLOAT32"}
  };
}
function evaluatePixel(sample) {
  if (!sample.dataMask || !isFinite(sample.TSM_NN)) return [-9999, 0];
  return [sample.TSM_NN, 1];
}
"""


@dataclass(frozen=True, slots=True)
class ProductSpec:
    product: str
    source: str
    source_collection: str
    measurement: str
    units: str
    resolution_m: int
    algorithm: str
    evalscript: str | None
    ancillary_collections: tuple[str, ...] = ()
    halo_pixels: int = 0
    local_only_reason: str | None = None


PRODUCTS: dict[str, ProductSpec] = {
    "rivers": ProductSpec(
        "rivers",
        "sentinel-2-l2a",
        "sentinel-2-l2a",
        "NDWI open-water and river response",
        "index",
        10,
        "July clear-pixel mean NDWI = (B03-B08)/(B03+B08)",
        S2_NDWI_EVALSCRIPT,
    ),
    "water_extent": ProductSpec(
        "water_extent",
        "sentinel-2-l2a",
        "sentinel-2-l2a",
        "NDWI annual water extent",
        "index",
        10,
        "July clear-pixel mean NDWI = (B03-B08)/(B03+B08)",
        S2_NDWI_EVALSCRIPT,
    ),
    "coastal_vegetation": ProductSpec(
        "coastal_vegetation",
        "sentinel-2-l2a",
        "sentinel-2-l2a",
        "NDVI coastal vegetation response",
        "index",
        10,
        "July clear-pixel mean NDVI on moist coastal/riparian land (-0.25 <= NDWI <= 0.08)",
        S2_NDVI_EVALSCRIPT,
    ),
    "oil_candidates": ProductSpec(
        "oil_candidates",
        "sentinel-1-grd",
        "sentinel-1-grd",
        "VV local-background dark anomaly over NDWI water",
        "dB contrast",
        20,
        "max(local 31x31 mean VV dB - July mean VV dB, 0); NDWI >= 0.02",
        S1_DARK_INPUT_EVALSCRIPT,
        ancillary_collections=("sentinel-2-l2a",),
        halo_pixels=16,
    ),
    "water_temperature": ProductSpec(
        "water_temperature",
        "sentinel-3-slstr-l2-wst",
        WST_STAC_COLLECTION,
        "SLSTR L2 WST sea/water surface temperature",
        "degC",
        1000,
        "July mean of quality-controlled official SLSTR L2 WST pixels",
        None,
        local_only_reason=(
            "CDSE Sentinel Hub Process API exposes SLSTR L1B, not L2 WST; "
            "provide genuine georeferenced L2 WST GeoTIFF assets and provenance"
        ),
    ),
    "water_colour": ProductSpec(
        "water_colour",
        "sentinel-3-olci-l2-water",
        "sentinel-3-olci-l2",
        "OLCI L2 WATER total suspended matter (TSM_NN)",
        "log10(g/m^3)",
        300,
        "July least-cloudy Copernicus mosaic of official OLCI L2 TSM_NN",
        OLCI_TSM_EVALSCRIPT,
    ),
}

assert tuple(PRODUCTS) == PRODUCT_IDS


class BuildError(RuntimeError):
    """The requested real-data product could not be completed."""


@dataclass(frozen=True, slots=True)
class Grid:
    left: float
    bottom: float
    right: float
    top: float
    resolution: float
    width: int
    height: int

    @property
    def transform(self) -> Affine:
        return from_origin(self.left, self.top, self.resolution, self.resolution)


@dataclass(frozen=True, slots=True)
class Tile:
    row: int
    col: int
    row_off: int
    col_off: int
    width: int
    height: int

    def bounds(self, grid: Grid, halo: int = 0) -> tuple[float, float, float, float]:
        left = grid.left + (self.col_off - halo) * grid.resolution
        top = grid.top - (self.row_off - halo) * grid.resolution
        right = grid.left + (self.col_off + self.width + halo) * grid.resolution
        bottom = grid.top - (self.row_off + self.height + halo) * grid.resolution
        return left, bottom, right, top

    @property
    def name(self) -> str:
        return f"r{self.row:05d}-c{self.col:05d}"


@dataclass(frozen=True, slots=True)
class LocalInput:
    manifest_path: Path
    assets: tuple[dict[str, Any], ...]
    acquisition_ids: tuple[str, ...]
    input_units: str


def annual_period(year: int) -> dict[str, str]:
    if year not in YEARS:
        raise ValueError(f"year must be between {FIRST_YEAR} and {LAST_YEAR}")
    return {key: value.format(year=year) for key, value in PERIOD_TEMPLATE.items()}


def parse_years(value: str) -> list[int]:
    selected: set[int] = set()
    for token in (part.strip() for part in value.split(",")):
        if not token:
            continue
        separator = ":" if ":" in token else "-" if "-" in token else None
        if separator:
            first, last = (int(part) for part in token.split(separator, 1))
            if first > last:
                raise argparse.ArgumentTypeError(f"descending year range: {token}")
            selected.update(range(first, last + 1))
        else:
            selected.add(int(token))
    result = sorted(selected)
    if not result or any(year not in YEARS for year in result):
        raise argparse.ArgumentTypeError(
            f"years must be within {FIRST_YEAR}:{LAST_YEAR}"
        )
    return result


def parse_products(value: str) -> list[str]:
    if value == "all":
        return list(PRODUCT_IDS)
    selected = list(dict.fromkeys(part.strip() for part in value.split(",") if part.strip()))
    unknown = [product for product in selected if product not in PRODUCTS]
    if not selected or unknown:
        raise argparse.ArgumentTypeError(
            f"products must be 'all' or a comma list from: {', '.join(PRODUCT_IDS)}"
        )
    return selected


def make_grid(resolution: float, bbox: Sequence[float] = BBOX_WGS84) -> Grid:
    transformer = Transformer.from_crs("EPSG:4326", OUTPUT_CRS, always_xy=True)
    west, south = transformer.transform(float(bbox[0]), float(bbox[1]))
    east, north = transformer.transform(float(bbox[2]), float(bbox[3]))
    left = math.floor(west / resolution) * resolution
    bottom = math.floor(south / resolution) * resolution
    right_hint = math.ceil(east / resolution) * resolution
    top = math.ceil(north / resolution) * resolution
    width = int(math.ceil((right_hint - left) / resolution))
    height = int(math.ceil((top - bottom) / resolution))
    right = left + width * resolution
    bottom = top - height * resolution
    return Grid(left, bottom, right, top, resolution, width, height)


def grid_tiles(grid: Grid, tile_size: int) -> list[Tile]:
    rows = math.ceil(grid.height / tile_size)
    columns = math.ceil(grid.width / tile_size)
    return [
        Tile(
            row,
            col,
            row * tile_size,
            col * tile_size,
            min(tile_size, grid.width - col * tile_size),
            min(tile_size, grid.height - row * tile_size),
        )
        for row in range(rows)
        for col in range(columns)
    ]


def stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def relative_asset(data_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(data_root.resolve()).as_posix()
    except ValueError as exc:
        raise BuildError(f"output asset escapes data root: {path}") from exc


def provenance_path(data_root: Path, path: Path) -> str:
    """Use a portable relative path when possible, otherwise an explicit path."""
    try:
        return path.resolve().relative_to(data_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


class TokenProvider:
    def __init__(self, client_id: str, client_secret: str, retries: int):
        self.client_id = client_id
        self.client_secret = client_secret
        self.retries = retries
        self._token: str | None = None
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def invalidate(self) -> None:
        with self._lock:
            self._expires_at = 0.0

    def token(self) -> str:
        with self._lock:
            if self._token and time.monotonic() < self._expires_at - 60:
                return self._token
            response = request_with_retry(
                "POST",
                TOKEN_URL,
                retries=self.retries,
                timeout=(30, 120),
                data={
                    "grant_type": "client_credentials",
                    "client_id": self.client_id,
                    "client_secret": self.client_secret,
                },
                redact_response=True,
            )
            payload = response.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise BuildError("CDSE OAuth response did not contain an access token")
            self._token = token
            self._expires_at = time.monotonic() + int(payload.get("expires_in", 600))
            return token


def request_with_retry(
    method: str,
    url: str,
    *,
    retries: int,
    timeout: tuple[int, int],
    redact_response: bool = False,
    return_statuses: frozenset[int] = frozenset(),
    **kwargs: Any,
) -> requests.Response:
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            response = requests.request(method, url, timeout=timeout, **kwargs)
            if response.status_code < 400 or response.status_code in return_statuses:
                return response
            if response.status_code not in {408, 425, 429, 500, 502, 503, 504}:
                detail = "" if redact_response else response.text[:500].replace("\n", " ")
                raise BuildError(
                    f"{method} {url} failed with HTTP {response.status_code}"
                    + (f": {detail}" if detail else "")
                )
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else 2**attempt
            last_error = BuildError(f"HTTP {response.status_code}")
        except (requests.RequestException, ValueError) as exc:
            delay = 2**attempt
            last_error = exc
        if attempt < retries:
            time.sleep(min(delay + random.random(), 60.0))
    raise BuildError(f"request failed after {retries + 1} attempts: {last_error}")


def authorized_request(
    token_provider: TokenProvider,
    method: str,
    url: str,
    *,
    retries: int,
    timeout: tuple[int, int],
    **kwargs: Any,
) -> requests.Response:
    base_headers = dict(kwargs.pop("headers", {}))
    for auth_attempt in range(2):
        headers = dict(base_headers)
        headers["authorization"] = f"Bearer {token_provider.token()}"
        response = request_with_retry(
            method,
            url,
            retries=retries,
            timeout=timeout,
            headers=headers,
            return_statuses=frozenset({401}),
            **kwargs,
        )
        if response.status_code != 401:
            return response
        if auth_attempt:
            raise BuildError("CDSE authorization failed after token refresh")
        token_provider.invalidate()
    raise BuildError("CDSE authorization failed")


def load_cached_catalog(
    path: Path,
    collection: str,
    year: int,
    bbox: Sequence[float],
    catalog_filter: dict[str, Any] | None,
) -> tuple[str, ...] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    expected = annual_period(year)
    if (
        payload.get("schema") != 1
        or payload.get("collection") != collection
        or payload.get("year") != year
        or payload.get("period") != expected
        or tuple(payload.get("bbox_wgs84", ())) != tuple(bbox)
        or payload.get("filter") != catalog_filter
    ):
        return None
    ids = payload.get("acquisition_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        return None
    return tuple(sorted(set(ids)))


def catalog_acquisition_ids(
    data_root: Path,
    token_provider: TokenProvider,
    collection: str,
    year: int,
    bbox: Sequence[float],
    retries: int,
    *,
    orbit_state: str | None = None,
    catalog_filter: dict[str, Any] | None = None,
) -> tuple[str, ...]:
    qualifier = f"-{orbit_state.lower()}" if orbit_state else ""
    cache_path = data_root / "catalog" / "cog-builder" / collection / f"{year}{qualifier}.json"
    cached = load_cached_catalog(
        cache_path, collection, year, bbox, catalog_filter
    )
    if cached is not None:
        return cached

    period = annual_period(year)
    base_body: dict[str, Any] = {
        "collections": [collection],
        "bbox": list(bbox),
        "datetime": f"{period['start']}/{period['end_inclusive']}",
        "limit": 100,
    }
    if catalog_filter is not None:
        base_body.update({"filter-lang": "cql2-json", "filter": catalog_filter})
    body = dict(base_body)
    features: dict[str, dict[str, Any]] = {}
    url = CATALOG_URL
    method = "POST"
    while url:
        response = authorized_request(
            token_provider,
            method,
            url,
            retries=retries,
            timeout=(30, 180),
            headers={"content-type": "application/json"},
            json=body,
        )
        payload = response.json()
        for feature in payload.get("features", []):
            properties = feature.get("properties", {})
            if orbit_state and str(properties.get("sat:orbit_state", "")).lower() != orbit_state.lower():
                continue
            identifier = feature.get("id")
            if isinstance(identifier, str) and identifier:
                features[identifier] = feature
        next_link = next(
            (link for link in payload.get("links", []) if link.get("rel") == "next"), None
        )
        if not next_link:
            next_token = payload.get("context", {}).get("next")
            if next_token is None:
                break
            # Sentinel Hub Catalog documents token pagination in `context`.
            # Keep the complete original query and only add the opaque token.
            body = {**base_body, "next": next_token}
            url = CATALOG_URL
            method = "POST"
            continue
        url = next_link.get("href")
        if not isinstance(url, str) or not url:
            break
        method = str(next_link.get("method", "GET")).upper()
        if method == "POST":
            body = {**base_body, **next_link.get("body", {})}
        else:
            body = {}

    ids = tuple(sorted(features))
    if not ids:
        raise BuildError(f"CDSE catalog returned no {collection} acquisitions for July {year}")
    atomic_json(
        cache_path,
        {
            "schema": 1,
            "provider": "Copernicus Data Space Ecosystem Sentinel Hub Catalog API",
            "collection": collection,
            "year": year,
            "period": period,
            "bbox_wgs84": list(bbox),
            "orbit_state": orbit_state,
            "filter": catalog_filter,
            "acquisition_ids": list(ids),
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    return ids


def process_inputs(spec: ProductSpec, year: int) -> list[dict[str, Any]]:
    period = annual_period(year)
    time_range = {"from": period["start"], "to": period["end_inclusive"]}
    if spec.product == "oil_candidates":
        return [
            {
                "type": "sentinel-1-grd",
                "id": "sar",
                "dataFilter": {
                    "timeRange": time_range,
                    "mosaickingOrder": "mostRecent",
                    "acquisitionMode": "IW",
                    "polarization": "DV",
                    "resolution": "HIGH",
                    "orbitDirection": "ASCENDING",
                },
                "processing": {
                    "backCoeff": "SIGMA0_ELLIPSOID",
                    "orthorectify": True,
                    "demInstance": "COPERNICUS_30",
                    "speckleFilter": {"type": "LEE", "windowSizeX": 5, "windowSizeY": 5},
                    "upsampling": "BILINEAR",
                },
            },
            {
                "type": "sentinel-2-l2a",
                "id": "water",
                "dataFilter": {"timeRange": time_range, "mosaickingOrder": "leastCC"},
                # SCL is categorical. Nearest-neighbour resampling keeps the
                # cloud/water classes integral in the evalscript.
                "processing": {"upsampling": "NEAREST", "downsampling": "NEAREST"},
            },
        ]
    # Every optical evalscript also consumes SCL or dataMask. Nearest-neighbour
    # is the only safe common resampler for these categorical mask bands.
    processing = {"upsampling": "NEAREST", "downsampling": "NEAREST"}
    order = "leastCC" if spec.source_collection != "sentinel-1-grd" else "mostRecent"
    return [
        {
            "type": spec.source_collection,
            "dataFilter": {"timeRange": time_range, "mosaickingOrder": order},
            "processing": processing,
        }
    ]


def process_payload(spec: ProductSpec, year: int, grid: Grid, tile: Tile) -> dict[str, Any]:
    if spec.evalscript is None:
        raise BuildError(f"{spec.product} has no Process API representation")
    halo = spec.halo_pixels
    bounds = tile.bounds(grid, halo)
    return {
        "input": {
            "bounds": {
                "bbox": list(bounds),
                "properties": {
                    "crs": "http://www.opengis.net/def/crs/EPSG/0/3857"
                },
            },
            "data": process_inputs(spec, year),
        },
        "output": {
            "width": tile.width + 2 * halo,
            "height": tile.height + 2 * halo,
            "responses": [
                {"identifier": "default", "format": {"type": "image/tiff"}}
            ],
        },
        "evalscript": spec.evalscript,
    }


def box_mean(values: np.ndarray, valid: np.ndarray, radius: int) -> tuple[np.ndarray, np.ndarray]:
    size = radius * 2 + 1
    weighted = np.where(valid, values, 0.0).astype(np.float64, copy=False)
    counts = valid.astype(np.float64, copy=False)

    def window_sum(array: np.ndarray) -> np.ndarray:
        padded = np.pad(array, radius, mode="constant")
        integral = np.pad(padded, ((1, 0), (1, 0)), mode="constant")
        integral = integral.cumsum(axis=0).cumsum(axis=1)
        return (
            integral[size:, size:]
            - integral[:-size, size:]
            - integral[size:, :-size]
            + integral[:-size, :-size]
        )

    total = window_sum(weighted)
    count = window_sum(counts)
    mean = np.divide(total, count, out=np.zeros_like(total), where=count > 0)
    return mean.astype(np.float32), count.astype(np.int32)


def decode_process_tiff(spec: ProductSpec, payload: bytes) -> np.ndarray:
    try:
        with rasterio.MemoryFile(payload) as memory:
            with memory.open() as source:
                data = source.read().astype(np.float32)
    except rasterio.errors.RasterioError as exc:
        raise BuildError("Process API did not return a readable GeoTIFF") from exc
    minimum_bands = 3 if spec.product == "oil_candidates" else 2
    if data.shape[0] < minimum_bands:
        raise BuildError(
            f"Process API returned {data.shape[0]} bands; expected {minimum_bands}"
        )
    if spec.product != "oil_candidates":
        valid = (data[1] > 0.5) & np.isfinite(data[0]) & (data[0] != NODATA)
        return np.where(valid, data[0], NODATA).astype(np.float32)

    vv_db, ndwi, data_mask = data[:3]
    valid = (
        (data_mask > 0.5)
        & np.isfinite(vv_db)
        & (vv_db != NODATA)
        & np.isfinite(ndwi)
        & (ndwi >= 0.02)
    )
    local_mean, count = box_mean(vv_db, valid, radius=15)
    enough_neighbours = count >= 225
    anomaly = np.maximum(local_mean - vv_db, 0.0)
    return np.where(valid & enough_neighbours, anomaly, NODATA).astype(np.float32)


def tile_transform(grid: Grid, tile: Tile) -> Affine:
    return grid.transform * Affine.translation(tile.col_off, tile.row_off)


def write_tile(path: Path, values: np.ndarray, grid: Grid, tile: Tile) -> str:
    if values.shape != (tile.height, tile.width):
        raise BuildError(
            f"tile {tile.name} shape {values.shape} != {(tile.height, tile.width)}"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".part.tif")
    profile: dict[str, Any] = {
        "driver": "GTiff",
        "width": tile.width,
        "height": tile.height,
        "count": 1,
        "dtype": "float32",
        "crs": OUTPUT_CRS,
        "transform": tile_transform(grid, tile),
        "nodata": NODATA,
        "compress": "DEFLATE",
        "predictor": 3,
    }
    if tile.width >= 16 and tile.height >= 16:
        profile.update(
            tiled=True,
            blockxsize=min(512, max(16, tile.width // 16 * 16)),
            blockysize=min(512, max(16, tile.height // 16 * 16)),
        )
    with rasterio.open(temporary, "w", **profile) as target:
        target.write(values, 1)
    digest = sha256_file(temporary)
    temporary.replace(path)
    atomic_json(
        path.with_suffix(".json"),
        {
            "schema": 1,
            "sha256": digest,
            "bytes": path.stat().st_size,
            "width": tile.width,
            "height": tile.height,
            "transform": list(tile_transform(grid, tile))[:6],
        },
    )
    return digest


def valid_cached_tile(path: Path, grid: Grid, tile: Tile) -> bool:
    sidecar = path.with_suffix(".json")
    if not path.is_file() or not sidecar.is_file():
        return False
    try:
        metadata = json.loads(sidecar.read_text(encoding="utf-8"))
        if metadata.get("bytes") != path.stat().st_size:
            return False
        if metadata.get("sha256") != sha256_file(path):
            return False
        with rasterio.open(path) as source:
            return (
                source.count == 1
                and source.width == tile.width
                and source.height == tile.height
                and source.crs is not None
                and source.crs.to_epsg() == 3857
                and source.transform.almost_equals(tile_transform(grid, tile))
            )
    except (OSError, ValueError, json.JSONDecodeError, rasterio.errors.RasterioError):
        return False


def crop_halo(values: np.ndarray, tile: Tile, halo: int) -> np.ndarray:
    if not halo:
        return values
    expected = (tile.height + 2 * halo, tile.width + 2 * halo)
    if values.shape != expected:
        raise BuildError(f"halo tile shape {values.shape} != {expected}")
    return values[halo : halo + tile.height, halo : halo + tile.width]


def fetch_process_tile(
    spec: ProductSpec,
    year: int,
    grid: Grid,
    tile: Tile,
    path: Path,
    token_provider: TokenProvider,
    retries: int,
) -> None:
    if valid_cached_tile(path, grid, tile):
        return
    payload = process_payload(spec, year, grid, tile)
    response = authorized_request(
        token_provider,
        "POST",
        PROCESS_URL,
        retries=retries,
        timeout=(30, 600),
        headers={"content-type": "application/json", "accept": "image/tiff"},
        json=payload,
    )
    values = decode_process_tiff(spec, response.content)
    values = crop_halo(values, tile, spec.halo_pixels)
    write_tile(path, values, grid, tile)


def normalized_local_assets(payload: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    raw_assets = payload.get("assets")
    if raw_assets is None and payload.get("asset") is not None:
        raw_assets = [payload["asset"]]
    if not isinstance(raw_assets, list) or not raw_assets:
        raise BuildError("local ingest manifest requires a non-empty assets list")
    assets: list[dict[str, Any]] = []
    for value in raw_assets:
        if isinstance(value, str):
            assets.append({"path": value, "band": 1})
        elif isinstance(value, dict) and isinstance(value.get("path"), str):
            asset = dict(value)
            asset.setdefault("band", 1)
            assets.append(asset)
        else:
            raise BuildError("each local ingest asset must be a path string or object")
    return tuple(assets)


def find_local_input(
    input_root: Path, spec: ProductSpec, year: int, *, validate: bool = True
) -> LocalInput | None:
    manifest_path = input_root / spec.product / str(year) / "manifest.json"
    if not manifest_path.is_file():
        return None
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BuildError(f"invalid local ingest manifest: {manifest_path}") from exc
    period = annual_period(year)
    if payload.get("product") != spec.product or payload.get("year") != year:
        raise BuildError(f"local ingest identity mismatch: {manifest_path}")
    if payload.get("source") != spec.source:
        raise BuildError(
            f"local ingest source for {spec.product}/{year} must be {spec.source}"
        )
    if payload.get("period") != period:
        raise BuildError(f"local ingest period must be exactly 1-31 July {year}")
    if payload.get("synthetic") is True or payload.get("fallback") is True:
        raise BuildError("synthetic/fallback local assets are forbidden")
    ids = payload.get("acquisition_ids")
    if not isinstance(ids, list) or not ids or not all(isinstance(item, str) for item in ids):
        raise BuildError("local ingest manifest requires acquisition_ids")
    assets = normalized_local_assets(payload)
    if validate:
        root = manifest_path.parent.resolve()
        for asset in assets:
            source_path = (root / asset["path"]).resolve()
            if not source_path.is_file():
                raise BuildError(f"local ingest asset is missing: {source_path}")
            try:
                with rasterio.open(source_path) as source:
                    band = int(asset.get("band", 1))
                    if source.crs is None or not 1 <= band <= source.count:
                        raise BuildError(f"local asset is not a georeferenced raster: {source_path}")
            except rasterio.errors.RasterioError as exc:
                raise BuildError(f"unreadable local raster: {source_path}") from exc
    return LocalInput(
        manifest_path,
        assets,
        tuple(sorted(set(ids))),
        str(payload.get("units", spec.units)),
    )


def local_tile_values(
    spec: ProductSpec, local_input: LocalInput, grid: Grid, tile: Tile
) -> np.ndarray:
    total = np.zeros((tile.height, tile.width), dtype=np.float64)
    count = np.zeros((tile.height, tile.width), dtype=np.uint16)
    transform = tile_transform(grid, tile)
    for asset in local_input.assets:
        source_path = (local_input.manifest_path.parent / asset["path"]).resolve()
        with rasterio.open(source_path) as source:
            with WarpedVRT(
                source,
                crs=OUTPUT_CRS,
                transform=transform,
                width=tile.width,
                height=tile.height,
                src_nodata=source.nodata,
                nodata=NODATA,
                resampling=Resampling.bilinear,
            ) as vrt:
                values = vrt.read(int(asset.get("band", 1)), masked=True)
        raw = values.filled(NODATA).astype(np.float32)
        valid = ~np.ma.getmaskarray(values) & np.isfinite(raw) & (raw != NODATA)
        if spec.product == "water_temperature":
            units = local_input.input_units.strip().lower()
            if units in {"k", "kelvin"}:
                raw = raw - 273.15
            elif units not in {"degc", "c", "celsius", "degree_celsius"}:
                raise BuildError(
                    "water_temperature local units must be degC/celsius or kelvin"
                )
        total[valid] += raw[valid]
        count[valid] += 1
    output = np.full((tile.height, tile.width), NODATA, dtype=np.float32)
    np.divide(total, count, out=output, where=count > 0)
    return output


def build_local_tiles(
    spec: ProductSpec,
    local_input: LocalInput,
    grid: Grid,
    tiles: Sequence[Tile],
    tile_root: Path,
    workers: int,
) -> None:
    def task(tile: Tile) -> None:
        path = tile_root / f"{tile.name}.tif"
        if valid_cached_tile(path, grid, tile):
            return
        write_tile(path, local_tile_values(spec, local_input, grid, tile), grid, tile)

    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {executor.submit(task, tile): tile for tile in tiles}
    try:
        for index, future in enumerate(as_completed(futures), 1):
            tile = futures[future]
            future.result()
            if index % 25 == 0 or index == len(futures):
                print(f"  local tiles {index}/{len(futures)} ({tile.name})", flush=True)
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)


def build_process_tiles(
    spec: ProductSpec,
    year: int,
    grid: Grid,
    tiles: Sequence[Tile],
    tile_root: Path,
    token_provider: TokenProvider,
    workers: int,
    retries: int,
) -> None:
    executor = ThreadPoolExecutor(max_workers=max(1, workers))
    futures = {
        executor.submit(
            fetch_process_tile,
            spec,
            year,
            grid,
            tile,
            tile_root / f"{tile.name}.tif",
            token_provider,
            retries,
        ): tile
        for tile in tiles
    }
    try:
        for index, future in enumerate(as_completed(futures), 1):
            tile = futures[future]
            future.result()
            if index % 25 == 0 or index == len(futures):
                print(f"  Process API tiles {index}/{len(futures)} ({tile.name})", flush=True)
    except BaseException:
        for future in futures:
            future.cancel()
        executor.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        executor.shutdown(wait=True)


def overview_factors(width: int, height: int) -> list[int]:
    factors: list[int] = []
    factor = 2
    while max(math.ceil(width / factor), math.ceil(height / factor)) > 256:
        factors.append(factor)
        factor *= 2
    if max(width, height) > 512 and not factors:
        factors.append(2)
    return factors


def assemble_cog(
    output: Path,
    spec: ProductSpec,
    year: int,
    grid: Grid,
    tiles: Sequence[Tile],
    tile_root: Path,
) -> tuple[str, int, list[int]]:
    output.parent.mkdir(parents=True, exist_ok=True)
    work = output.with_suffix(".assemble.tif")
    profile = {
        "driver": "GTiff",
        "width": grid.width,
        "height": grid.height,
        "count": 1,
        "dtype": "float32",
        "crs": OUTPUT_CRS,
        "transform": grid.transform,
        "nodata": NODATA,
        "tiled": True,
        "blockxsize": 512,
        "blockysize": 512,
        "compress": "DEFLATE",
        "predictor": 3,
        "BIGTIFF": "YES",
        "SPARSE_OK": "TRUE",
    }
    with rasterio.open(work, "w", **profile) as target:
        target.update_tags(
            schema="3",
            product=spec.product,
            year=str(year),
            source=spec.source,
            period_start=annual_period(year)["start"],
            period_end_inclusive=annual_period(year)["end_inclusive"],
        )
        for index, tile in enumerate(tiles, 1):
            path = tile_root / f"{tile.name}.tif"
            if not valid_cached_tile(path, grid, tile):
                raise BuildError(f"missing or corrupt staging tile: {path}")
            with rasterio.open(path) as source:
                target.write(
                    source.read(1),
                    1,
                    window=Window(tile.col_off, tile.row_off, tile.width, tile.height),
                )
            if index % 100 == 0:
                print(f"  assembled {index}/{len(tiles)} tiles", flush=True)

    temporary = output.with_suffix(".part.tif")
    if temporary.exists():
        temporary.unlink()
    factors = overview_factors(grid.width, grid.height)
    rio_copy(
        work,
        temporary,
        driver="COG",
        BLOCKSIZE=512,
        COMPRESS="DEFLATE",
        PREDICTOR="YES",
        BIGTIFF="YES",
        OVERVIEWS="AUTO",
        OVERVIEW_RESAMPLING="AVERAGE",
    )
    temporary.replace(output)
    work.unlink(missing_ok=True)
    with rasterio.open(output) as source:
        actual_factors = source.overviews(1)
        if (
            source.driver != "GTiff"
            or not bool(source.profile.get("tiled"))
            or source.crs is None
            or source.crs.to_epsg() != 3857
            or source.width != grid.width
            or source.height != grid.height
            or (max(grid.width, grid.height) > 512 and not actual_factors)
        ):
            raise BuildError(f"COG validation failed: {output}")
        factors = actual_factors
    digest = sha256_file(output)
    return digest, output.stat().st_size, factors


def manifest_matches(
    manifest_path: Path,
    output: Path,
    spec: ProductSpec,
    year: int,
    fingerprint: str | None,
    grid: Grid | None = None,
) -> bool:
    if not manifest_path.is_file() or not output.is_file():
        return False
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            payload.get("schema") != 3
            or payload.get("product") != spec.product
            or payload.get("year") != year
            or payload.get("source") != spec.source
            or payload.get("period") != annual_period(year)
            or (
                fingerprint is not None
                and payload.get("build_fingerprint") != fingerprint
            )
            or payload.get("complete") is not True
            or not payload.get("acquisition_ids")
            or payload.get("synthetic") is True
            or payload.get("fallback") is True
            or payload.get("asset") != f"cog/{spec.product}/{year}.tif"
            or payload.get("measurement") != spec.measurement
            or payload.get("units") != spec.units
            or payload.get("algorithm") != spec.algorithm
            or payload.get("resolution_m") != spec.resolution_m
            or payload.get("processing_version") != 1
            or payload.get("processing_definition_sha256")
            != processing_definition_sha256(spec, year, grid or make_grid(spec.resolution_m))
        ):
            return False
        checksum = payload.get("checksum", {}).get("value")
        if not isinstance(checksum, str):
            return False
        stat = output.stat()
        if payload.get("asset_size") != stat.st_size:
            return False
        if payload.get("asset_mtime_ns") != stat.st_mtime_ns and sha256_file(output) != checksum:
            return False
        expected_grid = grid or make_grid(spec.resolution_m)
        with rasterio.open(output) as source:
            return (
                bool(source.profile.get("tiled"))
                and source.crs is not None
                and source.crs.to_epsg() == 3857
                and source.width == expected_grid.width
                and source.height == expected_grid.height
                and source.transform.almost_equals(expected_grid.transform)
                and (max(source.width, source.height) <= 512 or bool(source.overviews(1)))
            )
    except (OSError, ValueError, json.JSONDecodeError, rasterio.errors.RasterioError):
        return False


def build_fingerprint(
    spec: ProductSpec,
    year: int,
    grid: Grid,
    mode: str,
    acquisition_ids: Sequence[str],
    ancillary_ids: dict[str, Sequence[str]],
    local_descriptor: dict[str, Any] | None,
) -> str:
    payload = {
        "schema": 3,
        "product": asdict(spec),
        "year": year,
        "period": annual_period(year),
        "bbox_wgs84": BBOX_WGS84,
        "grid": asdict(grid),
        "mode": mode,
        "acquisition_ids": sorted(acquisition_ids),
        "ancillary_acquisition_ids": {
            key: sorted(value) for key, value in sorted(ancillary_ids.items())
        },
        "local_input": local_descriptor,
        "processing_version": 1,
    }
    return sha256_bytes(stable_json(payload).encode("utf-8"))


def processing_definition_sha256(spec: ProductSpec, year: int, grid: Grid) -> str:
    """Hash code/config semantics independently of catalog or local inputs."""
    payload = {
        "processing_version": 1,
        "product": asdict(spec),
        "period": annual_period(year),
        "bbox_wgs84": BBOX_WGS84,
        "grid": asdict(grid),
        "process_inputs": process_inputs(spec, year) if spec.evalscript else None,
    }
    return sha256_bytes(stable_json(payload).encode("utf-8"))


def local_checksums(local_input: LocalInput) -> list[str]:
    checksums = []
    for asset in local_input.assets:
        path = (local_input.manifest_path.parent / asset["path"]).resolve()
        actual = sha256_file(path)
        expected = asset.get("sha256")
        if expected and expected != actual:
            raise BuildError(f"local ingest checksum mismatch: {path}")
        checksums.append(actual)
    return checksums


def local_fingerprint_descriptor(
    local_input: LocalInput, checksums: Sequence[str]
) -> dict[str, Any]:
    return {
        "units": local_input.input_units,
        "assets": [
            {
                "path": str(asset["path"]),
                "band": int(asset.get("band", 1)),
                "sha256": digest,
            }
            for asset, digest in zip(local_input.assets, checksums, strict=True)
        ],
    }


def write_manifest(
    path: Path,
    data_root: Path,
    output: Path,
    spec: ProductSpec,
    year: int,
    grid: Grid,
    mode: str,
    acquisition_ids: Sequence[str],
    ancillary_ids: dict[str, Sequence[str]],
    fingerprint: str,
    checksum: str,
    byte_size: int,
    overviews: Sequence[int],
    local_input: LocalInput | None,
    input_checksums: Sequence[str],
) -> None:
    payload: dict[str, Any] = {
        "schema": 3,
        "product": spec.product,
        "year": year,
        "source": spec.source,
        "source_collection": spec.source_collection,
        "provider": "Copernicus Data Space Ecosystem",
        "period": annual_period(year),
        "acquisition_ids": sorted(set(acquisition_ids)),
        "ancillary_acquisition_ids": {
            key: sorted(set(value)) for key, value in sorted(ancillary_ids.items())
        },
        "asset": relative_asset(data_root, output),
        "checksum": {"algorithm": "sha256", "value": checksum},
        "asset_sha256": checksum,
        "asset_size": byte_size,
        "asset_mtime_ns": output.stat().st_mtime_ns,
        "crs": OUTPUT_CRS,
        "bbox_wgs84": list(BBOX_WGS84),
        "bounds": [grid.left, grid.bottom, grid.right, grid.top],
        "width": grid.width,
        "height": grid.height,
        "resolution_m": grid.resolution,
        "dtype": "float32",
        "nodata": NODATA,
        "tiled": True,
        "internal_overviews": list(overviews),
        "measurement": spec.measurement,
        "units": spec.units,
        "algorithm": spec.algorithm,
        "ingestion_mode": mode,
        "build_fingerprint": fingerprint,
        "processing_definition_sha256": processing_definition_sha256(
            spec, year, grid
        ),
        "processing_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "complete": True,
        "synthetic": False,
        "fallback": False,
    }
    if local_input is not None:
        payload["input_manifest"] = provenance_path(data_root, local_input.manifest_path)
        payload["input_asset_sha256"] = list(input_checksums)
    atomic_json(path, payload)


def credentials(retries: int) -> TokenProvider:
    client_id = os.environ.get("CDSE_CLIENT_ID", "").strip()
    client_secret = os.environ.get("CDSE_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise BuildError(
            "set CDSE_CLIENT_ID and CDSE_CLIENT_SECRET in the Jupyter environment"
        )
    return TokenProvider(client_id, client_secret, retries)


def process_cache_key(
    spec: ProductSpec,
    year: int,
    grid: Grid,
    tile_size: int,
    acquisition_ids: Sequence[str],
    ancillary_ids: dict[str, Sequence[str]],
) -> str:
    payload = {
        "year": year,
        "period": annual_period(year),
        "grid": asdict(grid),
        "tile_size": tile_size,
        "evalscript": spec.evalscript,
        "input": process_inputs(spec, year) if spec.evalscript else None,
        "halo": spec.halo_pixels,
        # Process API input is a temporal query rather than a scene-id query.
        # Pin the cache namespace to the catalog snapshot so a refreshed
        # acquisition set can never be mislabeled with stale raster tiles.
        "acquisition_ids": sorted(acquisition_ids),
        "ancillary_acquisition_ids": {
            key: sorted(value) for key, value in sorted(ancillary_ids.items())
        },
        "processing_version": 1,
    }
    return sha256_bytes(stable_json(payload).encode("utf-8"))[:24]


def run_job(
    spec: ProductSpec,
    year: int,
    args: argparse.Namespace,
    token_holder: dict[str, TokenProvider],
) -> str:
    grid = make_grid(spec.resolution_m)
    tiles = grid_tiles(grid, args.tile_size)
    output = args.data_root / "cog" / spec.product / f"{year}.tif"
    manifest = args.data_root / "manifests" / spec.product / f"{year}.json"
    input_manifest_path = args.input_root / spec.product / str(year) / "manifest.json"
    # A finished immutable asset remains usable after raw/local ingest inputs
    # are archived away. If an input manifest is still present, evaluate its
    # full fingerprint below so band or unit changes trigger a rebuild.
    if (
        not args.force
        and not input_manifest_path.is_file()
        and manifest_matches(manifest, output, spec, year, None, grid)
    ):
        return "complete"
    local_input = find_local_input(args.input_root, spec, year)
    mode = "local-ingest" if local_input else "process-api"
    if not local_input and spec.evalscript is None:
        raise BuildError(
            f"{spec.product}/{year}: {spec.local_only_reason}; expected "
            f"{args.input_root / spec.product / str(year) / 'manifest.json'}"
        )
    if not local_input and args.local_only:
        raise BuildError(f"{spec.product}/{year}: no validated local ingest manifest")

    input_digests = local_checksums(local_input) if local_input else []
    input_descriptor = (
        local_fingerprint_descriptor(local_input, input_digests)
        if local_input
        else None
    )
    ancillary: dict[str, Sequence[str]] = {}
    if local_input:
        acquisition_ids = local_input.acquisition_ids
    else:
        provider = token_holder.get("provider")
        if provider is None:
            provider = credentials(args.retries)
            token_holder["provider"] = provider
        orbit = "ascending" if spec.product == "oil_candidates" else None
        catalog_filter = None
        if spec.product == "oil_candidates":
            catalog_filter = {
                "op": "and",
                "args": [
                    {
                        "op": "eq",
                        "args": [{"property": "sar:instrument_mode"}, "IW"],
                    },
                    {
                        "op": "eq",
                        "args": [{"property": "sat:orbit_state"}, "ascending"],
                    },
                    {
                        "op": "eq",
                        "args": [{"property": "s1:polarization"}, "DV"],
                    },
                    {
                        "op": "eq",
                        "args": [{"property": "s1:resolution"}, "HIGH"],
                    },
                ],
            }
        acquisition_ids = catalog_acquisition_ids(
            args.data_root,
            provider,
            spec.source_collection,
            year,
            BBOX_WGS84,
            args.retries,
            orbit_state=orbit,
            catalog_filter=catalog_filter,
        )
        for collection in spec.ancillary_collections:
            ancillary[collection] = catalog_acquisition_ids(
                args.data_root,
                provider,
                collection,
                year,
                BBOX_WGS84,
                args.retries,
            )

    fingerprint = build_fingerprint(
        spec, year, grid, mode, acquisition_ids, ancillary, input_descriptor
    )
    if not args.force and manifest_matches(
        manifest, output, spec, year, fingerprint, grid
    ):
        return "complete"

    if local_input:
        tile_root = (
            args.data_root
            / "work"
            / "cog-builder"
            / "local-tiles"
            / fingerprint[:24]
        )
        build_local_tiles(spec, local_input, grid, tiles, tile_root, args.workers)
    else:
        provider = token_holder["provider"]
        tile_root = (
            args.data_root
            / "work"
            / "cog-builder"
            / "process-tiles"
            / process_cache_key(
                spec,
                year,
                grid,
                args.tile_size,
                acquisition_ids,
                ancillary,
            )
        )
        build_process_tiles(
            spec,
            year,
            grid,
            tiles,
            tile_root,
            provider,
            args.workers,
            args.retries,
        )

    checksum, byte_size, overviews = assemble_cog(
        output, spec, year, grid, tiles, tile_root
    )
    write_manifest(
        manifest,
        args.data_root,
        output,
        spec,
        year,
        grid,
        mode,
        acquisition_ids,
        ancillary,
        fingerprint,
        checksum,
        byte_size,
        overviews,
        local_input,
        input_digests,
    )
    return "built"


def dry_run(args: argparse.Namespace) -> None:
    jobs = []
    for year in args.years:
        for product in args.products:
            spec = PRODUCTS[product]
            grid = make_grid(spec.resolution_m)
            local = find_local_input(args.input_root, spec, year, validate=False)
            if local:
                route = "local-ingest"
            elif spec.evalscript and not args.local_only:
                route = "process-api"
            else:
                route = "missing-local-input"
            jobs.append(
                {
                    "product": product,
                    "year": year,
                    "source": spec.source,
                    "route": route,
                    "resolution_m": spec.resolution_m,
                    "grid": {"width": grid.width, "height": grid.height},
                    "tiles": len(grid_tiles(grid, args.tile_size)),
                    "asset": f"cog/{product}/{year}.tif",
                    "manifest": f"manifests/{product}/{year}.json",
                }
            )
    print(
        json.dumps(
            {
                "dry_run": True,
                "writes": False,
                "network": False,
                "schema": 3,
                "period": "July 1-31",
                "crs": OUTPUT_CRS,
                "job_count": len(jobs),
                "products": list(PRODUCT_IDS),
                "jobs": jobs,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def parser() -> argparse.ArgumentParser:
    default_root = Path(
        os.environ.get("NAUTIKOS_DATA_ROOT", "/home/jovyan/work/caspiansea/data-v2")
    )
    command = argparse.ArgumentParser(
        description=(
            "Build exactly six 2020-2026 July analytical COG products on a "
            "fixed EPSG:3857 Caspian grid."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"""
Credentials (Process API jobs only):
  CDSE_CLIENT_ID and CDSE_CLIENT_SECRET

Local ingestion convention (required for SLSTR L2 WST):
  <input-root>/<product>/<year>/manifest.json

The input manifest contains product, year, source, the exact July period,
acquisition_ids, units, and an assets list. Asset paths are relative to that
manifest and may include a band and sha256, for example:
  {{"product":"water_temperature","year":2020,
   "source":"sentinel-3-slstr-l2-wst",
   "period":{{"start":"2020-07-01T00:00:00Z",
              "end_inclusive":"2020-07-31T23:59:59Z"}},
   "acquisition_ids":["S3A_SL_2_WST_..."],"units":"kelvin",
   "assets":[{{"path":"wst-july.tif","band":1,"sha256":"..."}}]}}

The WST assets must be genuine, georeferenced analytical rasters from the
official CDSE {WST_STAC_COLLECTION} collection; no L1B temperature proxy is used.
""",
    )
    command.add_argument("--years", type=parse_years, default=list(YEARS))
    command.add_argument("--products", type=parse_products, default=list(PRODUCT_IDS))
    command.add_argument("--data-root", type=Path, default=default_root)
    command.add_argument("--input-root", type=Path)
    command.add_argument("--tile-size", type=int, default=2048)
    command.add_argument("--workers", type=int, default=2)
    command.add_argument("--retries", type=int, default=6)
    command.add_argument("--local-only", action="store_true")
    command.add_argument("--force", action="store_true")
    command.add_argument("--continue-on-error", action="store_true")
    command.add_argument("--dry-run", action="store_true")
    return command


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.data_root = args.data_root.expanduser().resolve()
    args.input_root = (
        args.input_root.expanduser().resolve()
        if args.input_root
        else args.data_root / "inputs"
    )
    if not 256 <= args.tile_size <= 2400:
        raise SystemExit("--tile-size must be between 256 and 2400")
    maximum_halo = max(PRODUCTS[product].halo_pixels for product in args.products)
    if args.tile_size + maximum_halo * 2 > 2500:
        raise SystemExit("--tile-size plus the oil anomaly halo must not exceed 2500")
    if not 1 <= args.workers <= 8:
        raise SystemExit("--workers must be between 1 and 8")
    if not 0 <= args.retries <= 12:
        raise SystemExit("--retries must be between 0 and 12")
    if args.dry_run:
        dry_run(args)
        return 0

    failures: list[str] = []
    token_holder: dict[str, TokenProvider] = {}
    for year in args.years:
        for product in args.products:
            label = f"{product}/{year}"
            print(f"[{label}] starting", flush=True)
            try:
                result = run_job(PRODUCTS[product], year, args, token_holder)
                print(f"[{label}] {result}", flush=True)
            except Exception as exc:
                message = f"{label}: {exc}"
                print(f"[{label}] FAILED: {exc}", file=sys.stderr, flush=True)
                failures.append(message)
                if not args.continue_on_error:
                    break
        if failures and not args.continue_on_error:
            break
    if failures:
        print("Incomplete jobs:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
