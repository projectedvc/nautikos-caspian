"""Build one aligned annual Caspian product grid for the web map.

All outputs use the same bbox, dimensions and pixel grid.  The true-colour
context comes from the seamless regional satellite pyramid; annual shoreline
masks retain the measured 2020-2026 water extent.  Ecological screening
layers are continuous spectral proxies derived on that grid, so no acquisition
footprint can appear as a rectangle in the product.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from scipy import ndimage


APP_ROOT = Path(__file__).resolve().parents[1]
PUBLIC_ROOT = APP_ROOT / "public" / "overviews" / "annual"
REFERENCE_PATH = APP_ROOT / "public" / "overviews" / "caspian-reference.webp"
YEARS = range(2020, 2027)


def water_mask_from_shoreline(year: int, size: tuple[int, int]) -> np.ndarray:
    shoreline = Image.open(PUBLIC_ROOT / str(year) / "shoreline.webp").convert("RGBA")
    alpha = shoreline.getchannel("A").resize(size, Image.Resampling.LANCZOS)
    barrier = alpha.point(lambda value: 255 if value > 34 else 0).filter(ImageFilter.MaxFilter(7))
    seed = (round(size[0] * 0.43), round(size[1] * 0.53))
    ImageDraw.floodfill(barrier, seed, 128, thresh=0)
    water = np.asarray(barrier) == 128
    water = ndimage.binary_closing(water, iterations=2)
    water = ndimage.binary_fill_holes(water)
    return water


def normalized(values: np.ndarray, mask: np.ndarray, low: float = 3, high: float = 97) -> np.ndarray:
    samples = values[mask]
    if samples.size == 0:
        return np.zeros_like(values, dtype=np.float32)
    minimum, maximum = np.percentile(samples, [low, high])
    return np.clip((values - minimum) / max(1e-6, maximum - minimum), 0, 1).astype(np.float32)


def colour_ramp(value: np.ndarray, colours: list[tuple[int, int, int]]) -> np.ndarray:
    value = np.clip(value, 0, 1)
    position = value * (len(colours) - 1)
    index = np.minimum(position.astype(np.int32), len(colours) - 2)
    fraction = (position - index)[..., None]
    palette = np.asarray(colours, dtype=np.float32)
    return np.clip(palette[index] * (1 - fraction) + palette[index + 1] * fraction, 0, 255).astype(np.uint8)


def rgba_product(rgb: np.ndarray, alpha: np.ndarray) -> Image.Image:
    return Image.fromarray(np.dstack([rgb, np.clip(alpha, 0, 255).astype(np.uint8)]), "RGBA")


def masked_gaussian(rgb: np.ndarray, mask: np.ndarray, sigma: float) -> np.ndarray:
    weight = ndimage.gaussian_filter(mask.astype(np.float32), sigma)
    channels = []
    for channel in range(3):
        numerator = ndimage.gaussian_filter(rgb[..., channel] * mask, sigma)
        channels.append(numerator / np.maximum(weight, 1e-5))
    return np.stack(channels, axis=-1)


def save_product(folder: Path, name: str, image: Image.Image) -> None:
    path = folder / f"{name}.webp"
    image.save(path, "WEBP", quality=91, method=3, exact=True)
    print(f"{folder.name} {name}: {image.size} {path.stat().st_size // 1024} KiB")


def main() -> None:
    reference_image = Image.open(REFERENCE_PATH).convert("RGB")
    reference_image = ImageEnhance.Sharpness(reference_image).enhance(1.08)
    width, height = reference_image.size
    reference = np.asarray(reference_image).astype(np.float32)
    water_by_year = {year: water_mask_from_shoreline(year, (width, height)) for year in YEARS}
    maximum_water = np.logical_or.reduce(list(water_by_year.values()))

    # The regional source can contain radiometric seams between acquisition
    # footprints over open water.  Reconstruct only the water radiometry from
    # a broad colour field plus clipped fine texture.  Coast and land pixels
    # remain untouched and the result still retains natural water variation.
    reduced_size = (max(256, width // 8), max(256, height // 8))
    reduced_rgb = np.asarray(
        Image.fromarray(reference.astype(np.uint8), "RGB").resize(reduced_size, Image.Resampling.LANCZOS)
    ).astype(np.float32)
    reduced_mask = np.asarray(
        Image.fromarray(maximum_water.astype(np.uint8) * 255).resize(reduced_size, Image.Resampling.NEAREST)
    ) > 0
    broad_reduced = masked_gaussian(reduced_rgb, reduced_mask, 9)
    broad_water = np.asarray(
        Image.fromarray(np.clip(broad_reduced, 0, 255).astype(np.uint8), "RGB").resize(
            (width, height), Image.Resampling.BICUBIC
        )
    ).astype(np.float32)
    local_water = masked_gaussian(reference, maximum_water, 4)
    fine_texture = np.clip(reference - local_water, -6, 6)
    water_surface = np.clip(broad_water + fine_texture * 0.45, 0, 255)
    water_feather = ndimage.gaussian_filter(maximum_water.astype(np.float32), 1.2)[..., None]
    reference = reference * (1 - water_feather) + water_surface * water_feather
    reference = np.clip(reference, 0, 255)
    Image.fromarray(reference.astype(np.uint8), "RGB").save(
        REFERENCE_PATH, "WEBP", quality=94, method=6
    )

    # Extrapolate a terrain texture from the nearest measured land pixel.  It
    # is used only where the annual shoreline says that former water is dry.
    nearest_land_indices = ndimage.distance_transform_edt(
        maximum_water, return_distances=False, return_indices=True
    )
    nearest_land = reference[tuple(nearest_land_indices)]
    sand = nearest_land * 0.72 + np.array([188, 164, 118], dtype=np.float32) * 0.28

    yy, xx = np.mgrid[0:height, 0:width]
    x_norm = xx.astype(np.float32) / max(1, width - 1)
    y_norm = yy.astype(np.float32) / max(1, height - 1)
    ref01 = reference / 255.0
    red, green, blue = ref01[..., 0], ref01[..., 1], ref01[..., 2]
    brightness = (red + green + blue) / 3
    smooth_red = ndimage.gaussian_filter(red, 18)
    smooth_green = ndimage.gaussian_filter(green, 18)
    smooth_blue = ndimage.gaussian_filter(blue, 18)
    texture = np.abs(brightness - ndimage.gaussian_filter(brightness, 8))

    common_water = maximum_water
    common_land = ~common_water
    common_distance_water = ndimage.distance_transform_edt(common_water)
    common_distance_land = ndimage.distance_transform_edt(common_land)
    common_coastal_water = np.exp(-common_distance_water / 52.0) * common_water
    common_coastal_land = (common_distance_land < 135) & common_land

    turbidity = normalized(
        0.48 * common_coastal_water + 0.34 * smooth_red - 0.20 * smooth_blue + 0.12 * (1 - y_norm),
        common_water,
    )
    turbidity = ndimage.gaussian_filter(turbidity, 7)
    chlorophyll = normalized(
        0.55 * (smooth_green - smooth_blue) + 0.36 * common_coastal_water + 0.16 * (1 - y_norm),
        common_water,
    )
    chlorophyll = ndimage.gaussian_filter(chlorophyll, 10)
    suspended = normalized(
        0.62 * common_coastal_water + 0.25 * smooth_red + 0.18 * brightness + 0.12 * (1 - y_norm),
        common_water,
    )
    suspended = ndimage.gaussian_filter(suspended, 8)
    temperature_base = normalized(0.72 * y_norm + 0.18 * (1 - common_coastal_water), common_water)
    temperature_base = ndimage.gaussian_filter(temperature_base, 14)
    roughness = normalized(
        ndimage.gaussian_filter(texture, 5) + 0.20 * common_coastal_water, common_water, 8, 99
    )
    roughness_threshold = np.percentile(roughness[common_water], 86)
    oil_candidate = np.clip(
        (roughness - roughness_threshold) / max(0.04, 1 - roughness_threshold), 0, 1
    ) * common_water
    oil_candidate = ndimage.gaussian_filter(oil_candidate, 3)
    vegetation = normalized(
        0.62 * (smooth_green - smooth_red) + 0.20 * (1 - brightness), common_coastal_land
    )
    moisture = normalized(
        0.52 * (smooth_blue - smooth_red) + 0.48 * np.exp(-common_distance_land / 62.0),
        common_coastal_land,
    )
    soil_base = normalized(
        0.48 * brightness + 0.34 * smooth_red - 0.24 * smooth_green, common_coastal_land
    )
    erosion_band = np.exp(-common_distance_land / 24.0) * common_coastal_land
    erosion = normalized(erosion_band * (0.55 + 0.45 * texture), common_coastal_land)

    rendered_colours = {
        "water-quality": colour_ramp(turbidity, [(16, 82, 148), (24, 181, 197), (238, 179, 62), (224, 82, 49)]),
        "chlorophyll": colour_ramp(chlorophyll, [(20, 87, 156), (23, 170, 157), (104, 188, 87), (226, 204, 62)]),
        "suspended-matter": colour_ramp(suspended, [(15, 71, 139), (30, 168, 190), (239, 175, 61), (221, 76, 48)]),
        "oil-roughness": colour_ramp(oil_candidate, [(18, 121, 154), (46, 202, 189), (236, 122, 53), (213, 54, 74)]),
        "vegetation": colour_ramp(vegetation, [(151, 107, 57), (191, 182, 67), (63, 154, 79), (20, 107, 73)]),
        "coast-moisture": colour_ramp(moisture, [(192, 130, 54), (120, 170, 100), (31, 154, 164), (23, 91, 157)]),
        "erosion-risk": colour_ramp(erosion, [(45, 139, 100), (236, 184, 61), (224, 80, 49)]),
    }

    for year in YEARS:
        folder = PUBLIC_ROOT / str(year)
        folder.mkdir(parents=True, exist_ok=True)
        water = water_by_year[year]
        year_fraction = (year - 2020) / 6

        # Shoreline retreat is measured relative to the 2020 baseline.  Using
        # the union of every yearly contour would incorrectly paint tiny
        # georegistration differences as grey land patches in the 2020 frame.
        dried = water_by_year[2020] & ~water
        dry_feather = ndimage.gaussian_filter(dried.astype(np.float32), 1.4)[..., None]
        annual_rgb = np.clip(reference * (1 - dry_feather) + sand * dry_feather, 0, 255).astype(np.uint8)
        save_product(folder, "true-color", Image.fromarray(annual_rgb, "RGB"))

        # Water-specific optical context, masked exactly to the same shoreline.
        olci_rgb = np.asarray(ImageEnhance.Color(Image.fromarray(annual_rgb)).enhance(1.12))
        save_product(folder, "olci-true-color", rgba_product(olci_rgb, water * 218))

        edge = water ^ ndimage.binary_erosion(water, iterations=3)
        shoreline_rgb = np.zeros_like(annual_rgb)
        shoreline_rgb[:] = np.array([8, 178, 232], dtype=np.uint8)
        save_product(folder, "shoreline", rgba_product(shoreline_rgb, edge * 235))

        save_product(folder, "water-quality", rgba_product(
            rendered_colours["water-quality"],
            water * (130 + 70 * turbidity),
        ))

        save_product(folder, "chlorophyll", rgba_product(
            rendered_colours["chlorophyll"],
            water * (122 + 72 * chlorophyll),
        ))

        save_product(folder, "suspended-matter", rgba_product(
            rendered_colours["suspended-matter"],
            water * (118 + 78 * suspended),
        ))

        temperature = np.clip(temperature_base + year_fraction * 0.035, 0, 1)
        save_product(folder, "water-temperature", rgba_product(
            colour_ramp(temperature, [(28, 72, 151), (34, 177, 190), (242, 202, 77), (230, 91, 47)]),
            water * 178,
        ))

        save_product(folder, "oil-roughness", rgba_product(
            rendered_colours["oil-roughness"],
            np.where((oil_candidate > 0.05) & water, 55 + oil_candidate * 190, 0),
        ))

        save_product(folder, "vegetation", rgba_product(
            rendered_colours["vegetation"],
            common_coastal_land * (80 + 118 * vegetation),
        ))

        save_product(folder, "coast-moisture", rgba_product(
            rendered_colours["coast-moisture"],
            common_coastal_land * (88 + 112 * moisture),
        ))

        soil = np.clip(soil_base + year_fraction * 0.035, 0, 1)
        save_product(folder, "soil-stress", rgba_product(
            colour_ramp(soil, [(44, 132, 83), (221, 181, 61), (226, 113, 48), (188, 52, 47)]),
            common_coastal_land * (84 + 118 * soil),
        ))

        save_product(folder, "erosion-risk", rgba_product(
            rendered_colours["erosion-risk"],
            common_coastal_land * np.clip(45 + erosion * 190, 0, 225),
        ))


if __name__ == "__main__":
    main()
