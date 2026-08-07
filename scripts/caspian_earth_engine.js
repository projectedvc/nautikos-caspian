// Caspian Twin — full-basin analysis stack for Google Earth Engine Code Editor.
// The script does not export terabytes of imagery. It builds analysis-ready layers
// for the whole Caspian and lets the operator draw a smaller AOI for export.

var caspian = ee.Geometry.Rectangle([46.0, 36.0, 55.8, 47.4], null, false);
var start = ee.Date('2025-09-01');
var end = ee.Date('2026-09-01');

Map.setOptions('SATELLITE');
Map.centerObject(caspian, 5);

// Sentinel-2 surface reflectance with Cloud Score+ masking.
var s2 = ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
  .filterBounds(caspian)
  .filterDate(start, end)
  .linkCollection(
    ee.ImageCollection('GOOGLE/CLOUD_SCORE_PLUS/V1/S2_HARMONIZED'),
    ['cs_cdf']
  )
  .map(function (image) {
    return image
      .updateMask(image.select('cs_cdf').gte(0.62))
      .divide(10000)
      .copyProperties(image, ['system:time_start']);
  });

var s2Median = s2.median().clip(caspian);
var ndvi = s2Median.normalizedDifference(['B8', 'B4']).rename('NDVI');
var ndmi = s2Median.normalizedDifference(['B8', 'B11']).rename('NDMI');
var ndwi = s2Median.normalizedDifference(['B3', 'B8']).rename('NDWI');
var shoreline = ndwi.gt(0).selfMask();

Map.addLayer(s2Median, {bands: ['B4', 'B3', 'B2'], min: 0.02, max: 0.32}, 'S2 true color 2025–2026', true);
Map.addLayer(shoreline, {palette: ['2e9fd6']}, 'S2 water mask / shoreline', false);
Map.addLayer(ndvi, {min: -0.15, max: 0.75, palette: ['5e5368', 'd7b777', '4f8c4a', '123b24']}, 'NDVI', false);
Map.addLayer(ndmi, {min: -0.5, max: 0.5, palette: ['c88b4a', 'e5d9ab', '5a9a88', '1d5278']}, 'NDMI', false);

// Sentinel-1 all-weather radar. Dark water formations are candidates, not proof of oil.
var s1 = ee.ImageCollection('COPERNICUS/S1_GRD')
  .filterBounds(caspian)
  .filterDate(start, end)
  .filter(ee.Filter.eq('instrumentMode', 'IW'))
  .filter(ee.Filter.listContains('transmitterReceiverPolarisation', 'VV'))
  .select(['VV', 'VH']);

var s1Median = s1.median().clip(caspian);
Map.addLayer(s1Median, {bands: ['VV'], min: -25, max: 2}, 'S1 VV surface roughness', false);

// Sentinel-3 OLCI: basin-scale water colour. Native resolution is about 300 m.
var s3 = ee.ImageCollection('COPERNICUS/S3/OLCI')
  .filterBounds(caspian)
  .filterDate(start, end)
  .select(['Oa04_radiance', 'Oa06_radiance', 'Oa08_radiance'])
  .median()
  .clip(caspian);
Map.addLayer(s3, {min: 0, max: 100, gamma: 1.5}, 'S3 OLCI water colour', false);

// Near-real-time land-cover probabilities.
var dynamicWorld = ee.ImageCollection('GOOGLE/DYNAMICWORLD/V1')
  .filterBounds(caspian)
  .filterDate(start, end)
  .select('label')
  .mode()
  .clip(caspian);
Map.addLayer(
  dynamicWorld,
  {min: 0, max: 8, palette: ['419bdf', '397d49', '88b053', '7a87c6', 'e49635', 'dfc35a', 'c4281b', 'a59b8f', 'b39fe1']},
  'Dynamic World land cover',
  false
);

// Long-term water history (1984–2021) for shoreline context.
var historicWater = ee.Image('JRC/GSW1_4/GlobalSurfaceWater').select('occurrence').clip(caspian);
Map.addLayer(historicWater, {min: 0, max: 100, palette: ['ffffff', '8fc9e8', '144f8a']}, 'JRC water occurrence', false);

// AlphaEarth annual embeddings: semantic change, not just spectral difference.
var embeddings = ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL').filterBounds(caspian);
var emb2023 = embeddings.filterDate('2023-01-01', '2024-01-01').mosaic();
var emb2024 = embeddings.filterDate('2024-01-01', '2025-01-01').mosaic();
var similarity = emb2023.multiply(emb2024).reduce(ee.Reducer.sum()).clip(caspian);
var semanticChange = ee.Image(1).subtract(similarity).rename('semantic_change');
Map.addLayer(semanticChange, {min: 0, max: 0.18, palette: ['ffffff', 'ffc857', 'f2545b']}, 'AlphaEarth change 2023→2024', false);

// Context layers for restoration suitability.
var dem = ee.ImageCollection('COPERNICUS/DEM/GLO30').select('DEM').mosaic().clip(caspian);
var slope = ee.Terrain.slope(dem);
Map.addLayer(slope, {min: 0, max: 18, palette: ['f7f5e8', 'c9a66b', '6f4e37']}, 'Copernicus DEM slope', false);

var era5 = ee.ImageCollection('ECMWF/ERA5_LAND/MONTHLY_AGGR')
  .filterDate(start, end)
  .select(['total_precipitation_sum', 'volumetric_soil_water_layer_1'])
  .mean()
  .clip(caspian);
Map.addLayer(era5.select('volumetric_soil_water_layer_1'), {min: 0.05, max: 0.45, palette: ['d7b06e', 'd9e4ca', '4d8b8b']}, 'ERA5-Land soil moisture', false);

print('Caspian AOI km²', caspian.area(100).divide(1e6));
print('Sentinel-2 scenes', s2.size());
print('Sentinel-1 scenes', s1.size());
print('Draw a polygon in the Code Editor and export only that AOI at native scale.');
