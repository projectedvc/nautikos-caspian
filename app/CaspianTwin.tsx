"use client";

import {
  BoxSelect,
  Check,
  Download,
  Droplets,
  Focus,
  Leaf,
  LoaderCircle,
  Moon,
  PanelRightClose,
  PanelRightOpen,
  Radar,
  Satellite,
  ScanSearch,
  Sparkles,
  Sun,
  TrendingUp,
  Waves,
  X,
} from "lucide-react";
import { GeoJSONSource, Map as MapLibreMap, type MapOptions, StyleSpecification } from "maplibre-gl";
import { PointerEvent as ReactPointerEvent, useEffect, useMemo, useRef, useState } from "react";
import FilterPhotoGallery from "./FilterPhotoGallery";

type BBox = [number, number, number, number];
type DatasetKey = "s2" | "s1" | "s3";
type ViewKey = "rivers" | "shoreline" | "coastalVegetation" | "oilCandidates" | "waterTemperature" | "waterColour";
type LayerKey = "true-color" | "rivers" | "shoreline" | "vegetation" | "oil-candidates" | "water-temperature" | "water-colour";
type WorkspaceMode = "monitoring" | "solutions" | "filters";
type SidebarSection = "water" | "land" | "tools";
type SolutionKey = "discharge" | "wetland" | "shoreline" | "vegetation" | "oil-response";
type AoiScreen = { left: number; top: number; width: number; height: number };
type ScanStage = "idle" | "scanning" | "analyzing" | "ready" | "error";

type AiResult = {
  summary: string;
  risk: "низкий" | "средний" | "высокий" | "не определён";
  evidence: string[];
  nextSteps: string[];
  limitation: string;
};

type TrendPoint = {
  year: number;
  waterShare: number;
  vegetation: number;
  soilStress: number;
};

type TrendResult = {
  source: string;
  method: string;
  series: TrendPoint[];
  forecast: { year: 2027; waterShare: number | null; vegetation: number | null; soilStress: number | null };
  slopes: { waterShare: number | null; vegetation: number | null; soilStress: number | null };
  confidence: number;
  limitation: string;
};

type Region = { id: string; name: string; bbox: BBox };
type FilterDefinition = {
  id: ViewKey;
  label: string;
  subtitle: string;
  dataset: DatasetKey;
  layer: LayerKey;
  icon: typeof Satellite;
  legend: Array<{ color: string; label: string }>;
  explanation: string;
};

const CASPIAN_BBOX: BBox = [46.0, 36.0, 55.8, 47.4];
const REGIONAL_BASEMAP_BBOX: BBox = [25, 25, 75, 60];
const YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026] as const;
const DATA_API_BASE = (process.env.NEXT_PUBLIC_NAUTIKOS_DATA_URL ?? "").replace(/\/$/, "");
const WATER_FILTERS: ViewKey[] = ["oilCandidates", "waterTemperature", "waterColour"];
const LAND_FILTERS: ViewKey[] = ["rivers", "shoreline", "coastalVegetation"];
const PROBLEM_HOTSPOTS: Record<ViewKey, { bbox: BBox; label: string }> = {
  rivers: { bbox: [51.55, 46.45, 52.35, 47.08], label: "дельта Урала и северное мелководье" },
  shoreline: { bbox: [51.55, 46.45, 52.35, 47.08], label: "северо-восточный берег Каспия" },
  coastalVegetation: { bbox: [48.70, 38.75, 49.35, 39.38], label: "прибрежные влажные земли Гиляна" },
  oilCandidates: { bbox: [49.62, 40.12, 50.28, 40.62], label: "прибрежная зона Апшеронского полуострова" },
  waterTemperature: { bbox: [51.45, 46.20, 52.45, 47.10], label: "северное мелководье у дельты Урала" },
  waterColour: { bbox: [51.45, 46.20, 52.45, 47.10], label: "шлейфы у дельты Урала" },
};
const PRODUCT_BY_LAYER: Record<LayerKey, string> = {
  "true-color": "rgb",
  rivers: "rivers",
  shoreline: "water_extent",
  vegetation: "coastal_vegetation",
  "oil-candidates": "oil_candidates",
  "water-temperature": "water_temperature",
  "water-colour": "water_colour",
};
const SOLUTIONS: Array<{ id: SolutionKey; label: string; detail: string; icon: typeof Sparkles }> = [
  { id: "discharge", label: "Проверка сбросов", detail: "Исток шлейфа, точки отбора проб и маршрут", icon: Droplets },
  { id: "wetland", label: "Восстановление дельты", detail: "Участки удержания воды и защиты нерестилищ", icon: Waves },
  { id: "shoreline", label: "Защита берега", detail: "Эрозионные зоны и природные буферы", icon: ScanSearch },
  { id: "vegetation", label: "Восстановление покрова", detail: "Посадка только по влаге, почве и стоку", icon: Leaf },
  { id: "oil-response", label: "Проверка нефтяного следа", detail: "SAR-кандидат, ветер, AIS и повторный пролёт", icon: Radar },
];

const regions: Region[] = [
  { id: "all", name: "Весь Каспий", bbox: CASPIAN_BBOX },
  { id: "north", name: "Северный Каспий", bbox: [46.2, 44.0, 53.2, 47.4] },
  { id: "middle", name: "Средний Каспий", bbox: [47.0, 40.2, 53.8, 44.5] },
  { id: "south", name: "Южный Каспий", bbox: [48.0, 36.2, 55.8, 40.8] },
  { id: "kz", name: "Побережье Казахстана", bbox: [49.0, 42.0, 55.1, 47.2] },
  { id: "az", name: "Побережье Азербайджана", bbox: [48.6, 38.2, 51.5, 42.2] },
  { id: "tm", name: "Побережье Туркменистана", bbox: [51.8, 36.8, 55.5, 42.5] },
];

const allFilters: FilterDefinition[] = [
  {
    id: "rivers",
    label: "Реки и водотоки",
    subtitle: "Sentinel‑2 L2A · цветной инфракрасный B08/B04/B03 · 10 м",
    dataset: "s2",
    layer: "rivers",
    icon: Waves,
    legend: [{ color: "#8f1424", label: "красный · живая растительность" }, { color: "#071728", label: "тёмный · вода и русла" }],
    explanation: "Настоящий цветной инфракрасный композит Sentinel‑2: ближний ИК B08 выведен в красный канал, B04 — в зелёный, B03 — в синий. Здоровая растительность становится красной, а вода — тёмной, поэтому русла, притоки, устья и влажные поймы читаются как реальные спектральные структуры снимка, без нарисованных масок и прямоугольных заливок.",
  },
  {
    id: "shoreline",
    label: "Водная граница и изменение берега",
    subtitle: "Sentinel‑2 L2A · медиана июля · площадь воды по NDWI · 10 м",
    dataset: "s2",
    layer: "shoreline",
    icon: ScanSearch,
    legend: [{ color: "#ffc620", label: "измеренная граница воды выбранного года" }],
    explanation: "Жёлтая линия построена только по переходу между водой и сушей в реальном NDWI‑поле Sentinel‑2. Внутренняя площадь моря не закрашивается. Шторка сопоставляет одну и ту же методику для двух лет и показывает отступление или наступление воды без искусственной береговой геометрии.",
  },
  {
    id: "coastalVegetation",
    label: "Растительность прибрежного буфера",
    subtitle: "Sentinel‑2 L2A · медиана июля · NDVI B08/B04 · 10 м",
    dataset: "s2",
    layer: "vegetation",
    icon: Leaf,
    legend: [{ color: "#c2e699", label: "низкий NDVI" }, { color: "#31a354", label: "средний NDVI" }, { color: "#006837", label: "высокий NDVI" }],
    explanation: "NDVI (B08−B04)/(B08+B04) рассчитан по медианному июльскому композиту Sentinel‑2 L2A и показан в прибрежном буфере после исключения открытой воды. Единый сезон позволяет сравнивать состояние зелёного покрова между годами. Индекс не определяет вид растения, инвазивность или причину стресса без полевой проверки.",
  },
  {
    id: "oilCandidates",
    label: "Кандидаты поверхностной плёнки",
    subtitle: "Sentinel‑1 IW GRD · тёмные VV‑аномалии · ≈20 м",
    dataset: "s1",
    layer: "oil-candidates",
    icon: Radar,
    legend: [{ color: "#feb24c", label: "кандидат гладкой поверхности" }, { color: "#bd0026", label: "сильная тёмная VV‑аномалия" }],
    explanation: "Sentinel‑1 отмечает связные участки воды, где обратное рассеяние VV ниже локального радиолокационного фона. Такой dark spot совместим с поверхностной плёнкой, но не доказывает наличие нефти: похожий сигнал создают штиль, биогенные плёнки, дождь и ветровые тени. Для тревоги нужны данные о ветре и AIS, повторный пролёт и полевая проверка.",
  },
  {
    id: "waterTemperature",
    label: "Температура поверхности воды",
    subtitle: "Sentinel‑3 SLSTR L2 WST · SST · °C · 1 км",
    dataset: "s3",
    layer: "water-temperature",
    icon: Waves,
    legend: [{ color: "#466be3", label: "−2…10 °C · холоднее" }, { color: "#32f298", label: "10…24 °C · умеренно" }, { color: "#e12a1c", label: "24…35 °C · теплее" }],
    explanation: "Sentinel‑3 SLSTR Level‑2 WST показывает температуру поверхности воды в градусах Цельсия на сетке около 1 км. Фиксированная шкала позволяет сопоставлять крупномасштабную тепловую структуру между годами. Это не температура воздуха и не измерение локального сброса меньшего размера, чем пиксель SLSTR.",
  },
  {
    id: "waterColour",
    label: "Цвет воды и взвесь",
    subtitle: "Sentinel‑3 OLCI L2 WATER · water colour / TSM · 300 м",
    dataset: "s3",
    layer: "water-colour",
    icon: Droplets,
    legend: [{ color: "#225ea8", label: "низкий оптический сигнал взвеси" }, { color: "#41b6c4", label: "изменённый цвет воды" }, { color: "#edf8b1", label: "повышенный TSM‑сигнал" }],
    explanation: "Sentinel‑3 OLCI Level‑2 WATER показывает цвет воды и крупномасштабный оптический сигнал взвешенного вещества (TSM) с разрешением 300 м. Слой подходит для сравнения крупных водных масс и шлейфов, но не различает природный осадок, водоросли и техногенный сброс и не заменяет лабораторную концентрацию по пробе воды.",
  },
];
const filters: FilterDefinition[] = allFilters;

function bboxPolygon(bbox: BBox) {
  const [west, south, east, north] = bbox;
  return {
    type: "Feature" as const,
    properties: {},
    geometry: { type: "Polygon" as const, coordinates: [[[west, south], [east, south], [east, north], [west, north], [west, south]]] },
  };
}

function normalizeBBox(a: [number, number], b: [number, number]): BBox {
  return [Math.min(a[0], b[0]), Math.min(a[1], b[1]), Math.max(a[0], b[0]), Math.max(a[1], b[1])];
}

function bboxAreaKm2(bbox: BBox) {
  const [west, south, east, north] = bbox;
  const midLat = (south + north) / 2 * Math.PI / 180;
  return Math.abs((east - west) * 111.32 * Math.cos(midLat) * (north - south) * 110.57);
}

function bboxAroundPoint(point: [number, number], sideKm: number): BBox {
  const halfLat = sideKm / 110.57 / 2;
  const halfLon = sideKm / (111.32 * Math.max(0.2, Math.cos(point[1] * Math.PI / 180))) / 2;
  return [point[0] - halfLon, point[1] - halfLat, point[0] + halfLon, point[1] + halfLat];
}

function formatArea(area: number) {
  return area >= 1000 ? `${Math.round(area).toLocaleString("ru-RU")} км²` : `${area.toFixed(area < 10 ? 2 : 1)} км²`;
}

function baseStyle(): StyleSpecification {
  return {
    version: 8,
    sources: {
      labels: {
        type: "geojson",
        data: { type: "FeatureCollection", features: [] },
      },
    },
    layers: [
      { id: "background", type: "background", paint: { "background-color": "#0a2631" } },
      // Stable insertion anchor for raster layers. A symbol layer with a text
      // field would require an external glyph service and could keep the map
      // forever in a not-loaded state while offline.
      { id: "place-labels", type: "line", source: "labels", paint: { "line-opacity": 0 } },
    ],
  };
}

function regionalBasemapTileUrl() {
  // The public imagery endpoint supports CORS and is substantially more
  // reliable in the browser than relaying every context tile through a
  // short-lived Vercel function. Analytical Caspian products still come
  // exclusively from the pinned Jupyter data service below.
  return "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}";
}

function addRegionalBasemap(map: MapLibreMap) {
  if (map.getSource("regional-basemap")) return;
  map.addSource("regional-basemap", {
    type: "raster",
    tiles: [regionalBasemapTileUrl()],
    tileSize: 256,
    minzoom: 3,
    maxzoom: 16,
    bounds: REGIONAL_BASEMAP_BBOX,
  });
  map.addLayer({
    id: "regional-basemap",
    type: "raster",
    source: "regional-basemap",
    paint: { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "linear" },
  }, "place-labels");
}

function annualTileUrl(year: number, layer: LayerKey, version: number) {
  return `${DATA_API_BASE}/v2/tiles/${PRODUCT_BY_LAYER[layer]}/${year}/{z}/{x}/{y}.png?v=${version}`;
}

function rgbOverviewUrl(year: number, version: number) {
  return `/overviews/annual/${year}/true-color.webp?v=${version}`;
}

function rgbOverviewCoordinates(): [[number, number], [number, number], [number, number], [number, number]] {
  const [west, south, east, north] = CASPIAN_BBOX;
  return [[west, north], [east, north], [east, south], [west, south]];
}

function productPeriod(year: number, layer: LayerKey) {
  if (layer === "true-color") return `Sentinel-2 L2A · июль ${year} · RGB · 10 м`;
  if (layer === "rivers") return `Sentinel-2 L2A · медиана июля ${year} · NDWI · 10 м`;
  if (layer === "shoreline") return `Sentinel-2 L2A · медиана июля ${year} · площадь воды · 10 м`;
  if (layer === "vegetation") return `Sentinel-2 L2A · медиана июля ${year} · NDVI · 10 м`;
  if (layer === "oil-candidates") return `Sentinel-1 IW GRD · июль ${year} · кандидаты поверхностной плёнки · ≈20 м`;
  if (layer === "water-temperature") return `Sentinel-3 SLSTR L2 WST · ${year} · °C · 1 км`;
  return `Sentinel-3 OLCI L2 WATER · ${year} · water colour / TSM · 300 м`;
}

function updateAnnualTiles(map: MapLibreMap, year: number, layer: LayerKey, version: number) {
  const ids = ["annual-photo-overview", "annual-photo-tiles", "annual-filter-tiles", "monthly-frame"];
  for (const id of ids) {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  }

  map.addSource("annual-photo-overview", {
    type: "image",
    url: rgbOverviewUrl(year, version),
    coordinates: rgbOverviewCoordinates(),
  });
  map.addLayer({
    id: "annual-photo-overview",
    type: "raster",
    source: "annual-photo-overview",
    minzoom: 3,
    // Keep the seamless annual frame underneath the native Sentinel tiles at
    // every zoom.  Transparent cloud/nodata pixels in a detailed tile then
    // reveal the same year's overview instead of a dark hole or a different
    // provider's imagery.
    maxzoom: 16,
    paint: { "raster-opacity": 1, "raster-fade-duration": 0, "raster-resampling": "linear" },
  }, "place-labels");

  map.addSource("annual-photo-tiles", {
    type: "raster",
    tiles: [annualTileUrl(year, "true-color", version)],
    tileSize: 256,
    minzoom: 6,
    maxzoom: 14,
    bounds: CASPIAN_BBOX,
  });
  map.addLayer({
    id: "annual-photo-tiles",
    type: "raster",
    source: "annual-photo-tiles",
    minzoom: 6,
    paint: {
      // The detailed layer is already a cloud-cleaned, consistently stretched
      // composite. Keep valid pixels fully opaque; the overview is visible
      // only through genuine nodata, never through the satellite image.
      "raster-opacity": 1,
      "raster-fade-duration": 0,
      "raster-resampling": "linear",
    },
  }, "place-labels");

  if (layer !== "true-color") {
    // Every analytical filter is served from one local COG-backed XYZ source
    // at every map zoom. No static image overlay is substituted on overview.
    map.addSource("annual-filter-tiles", {
      type: "raster",
      tiles: [annualTileUrl(year, layer, version)],
      tileSize: 256,
      minzoom: 3,
      maxzoom: 15,
      bounds: CASPIAN_BBOX,
    });
    map.addLayer({
      id: "annual-filter-tiles",
      type: "raster",
      source: "annual-filter-tiles",
      minzoom: 3,
      paint: {
        // Opacity is encoded once in the PNG alpha channel. Applying a second
        // layer opacity washes the palette out and exposes rectangular seams.
        "raster-opacity": 1,
        "raster-fade-duration": 0,
        "raster-resampling": "linear",
      },
    }, "place-labels");
  }
}

function TrendChart({ result, metric }: { result: TrendResult; metric: keyof Pick<TrendPoint, "waterShare" | "vegetation" | "soilStress"> }) {
  const values = result.series.map((point) => ({ year: point.year, value: point[metric] }));
  const forecastValue = result.forecast[metric];
  if (forecastValue !== null) values.push({ year: 2027, value: forecastValue });
  if (values.length < 2) return null;
  const min = Math.min(...values.map((item) => item.value));
  const max = Math.max(...values.map((item) => item.value));
  const span = Math.max(0.02, max - min);
  const pointString = values.map((item, index) => `${10 + index * 29.5},${60 - (item.value - min) / span * 46}`).join(" ");
  const last = values[values.length - 1];
  const lastX = 10 + (values.length - 1) * 29.5;
  const lastY = 60 - (last.value - min) / span * 46;
  return (
    <div className="trend-chart">
      <svg viewBox="0 0 230 72" role="img" aria-label="Тренд 2020–2027">
        <path d="M10 60H220" />
        <polyline points={pointString} />
        <circle cx={lastX} cy={lastY} r="4" />
      </svg>
      <div><span>2020</span><span>наблюдения</span><strong>2027</strong></div>
    </div>
  );
}

export default function CaspianTwin() {
  const mapNode = useRef<HTMLDivElement | null>(null);
  const compareMapNode = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<MapLibreMap | null>(null);
  const compareMapRef = useRef<MapLibreMap | null>(null);
  const drawStartRef = useRef<[number, number] | null>(null);
  const drawPixelStartRef = useRef<[number, number] | null>(null);
  const drawRectRef = useRef<{ left: number; top: number; width: number; height: number } | null>(null);
  const noticeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const scanTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const swipeDraggingRef = useRef(false);

  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("monitoring");
  const [solutionType, setSolutionType] = useState<SolutionKey>("discharge");
  const [sidebarSection, setSidebarSection] = useState<SidebarSection>("land");
  const [activeView, setActiveView] = useState<ViewKey>("rivers");
  const [beforeYear, setBeforeYear] = useState<number>(2020);
  const [afterYear, setAfterYear] = useState<number>(2026);
  const [selectedRegion, setSelectedRegion] = useState("all");
  const [aoi, setAoi] = useState<BBox | null>(null);
  const [drawing, setDrawing] = useState(false);
  const [drawRect, setDrawRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  const [selectionNotice, setSelectionNotice] = useState<string | null>(null);
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [mapsReady, setMapsReady] = useState(0);
  const [swipe, setSwipe] = useState(50);
  const [compareEnabled, setCompareEnabled] = useState(true);
  // Bump when a raster contract changes so browsers do not retain tiles from
  // an older product or visualisation contract.
  const tileVersion = 33;
  const [timelapseFromYear, setTimelapseFromYear] = useState(2020);
  const [timelapseToYear, setTimelapseToYear] = useState(2026);
  const [timelapseYear, setTimelapseYear] = useState(2020);
  const [timelapsePlaying, setTimelapsePlaying] = useState(false);
  const [aoiScreen, setAoiScreen] = useState<AoiScreen | null>(null);
  const [trendStatus, setTrendStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [trendResult, setTrendResult] = useState<TrendResult | null>(null);
  const [aiStatus, setAiStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [aiResult, setAiResult] = useState<AiResult | null>(null);
  const [scanStage, setScanStage] = useState<ScanStage>("idle");
  const [scanLocation, setScanLocation] = useState<string>("");

  const activeFilter = useMemo(() => filters.find((item) => item.id === activeView) ?? filters[0], [activeView]);
  const visibleFilters = useMemo(() => {
    const ids = sidebarSection === "land" ? LAND_FILTERS : WATER_FILTERS;
    return ids.map((id) => filters.find((item) => item.id === id)).filter((item): item is FilterDefinition => Boolean(item));
  }, [sidebarSection]);
  const selectedArea = aoi ? bboxAreaKm2(aoi) : null;
  const trendMetric = "waterShare" as const;
  const overlaySlope = trendResult?.slopes.waterShare;
  const overlayAdverse = (overlaySlope ?? 0) < 0;
  const overlayColor = workspaceMode === "solutions" ? trendResult ? overlayAdverse ? "#ef5b4d" : "#20a46b" : "#f0a72f" : "#087dac";

  useEffect(() => {
    if (!mapNode.current || !compareMapNode.current || mapRef.current || compareMapRef.current) return;
    const options: Omit<MapOptions, "container"> = {
      center: [51.2, 41.8] as [number, number],
      zoom: 5,
      minZoom: 3,
      // Sentinel-2 is 10 m. Stopping close to the native detail prevents the
      // interface from pretending that blurred overscaling is new information.
      maxZoom: 15,
      maxBounds: [[25, 25], [75, 60]] as [[number, number], [number, number]],
      dragPan: true,
      scrollZoom: true,
      boxZoom: true,
      touchZoomRotate: true,
      keyboard: true,
      renderWorldCopies: false,
      attributionControl: false,
      canvasContextAttributes: { preserveDrawingBuffer: true },
      style: baseStyle(),
    };
    const primary = new MapLibreMap({ ...options, container: mapNode.current });
    const comparison = new MapLibreMap({ ...options, container: compareMapNode.current, interactive: false });
    (globalThis as typeof globalThis & { __nautikosPrimary?: MapLibreMap; __nautikosComparison?: MapLibreMap }).__nautikosPrimary = primary;
    (globalThis as typeof globalThis & { __nautikosPrimary?: MapLibreMap; __nautikosComparison?: MapLibreMap }).__nautikosComparison = comparison;
    primary.on("error", (event) => console.error("Nautikos primary map error", event.error));
    comparison.on("error", (event) => console.error("Nautikos comparison map error", event.error));

    primary.on("style.load", () => {
      addRegionalBasemap(primary);
      primary.addSource("caspian-frame", { type: "geojson", data: bboxPolygon(CASPIAN_BBOX) });
      primary.addLayer({ id: "caspian-frame", type: "line", source: "caspian-frame", paint: { "line-color": "#0e77a8", "line-width": 1.2, "line-opacity": 0.6, "line-dasharray": [3, 3] } });
      primary.addSource("aoi", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      primary.addLayer({ id: "aoi-fill", type: "fill", source: "aoi", paint: { "fill-color": "#00a8e8", "fill-opacity": 0.12 } });
      primary.addLayer({ id: "aoi-line", type: "line", source: "aoi", paint: { "line-color": "#00a8e8", "line-width": 3, "line-dasharray": [2, 1] } });
      primary.addSource("solution-area", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      primary.addLayer({ id: "solution-area-fill", type: "fill", source: "solution-area", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.22 } });
      primary.addLayer({ id: "solution-area-line", type: "line", source: "solution-area", paint: { "line-color": ["get", "color"], "line-width": 4, "line-dasharray": [2, 1] } });
      primary.addSource("solution-points", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      primary.addLayer({ id: "solution-points", type: "circle", source: "solution-points", paint: { "circle-radius": 17, "circle-color": ["get", "color"], "circle-opacity": 0.82, "circle-stroke-color": "#ffffff", "circle-stroke-width": 3 } });
      // Paint a usable local scene immediately. State-driven effects replace
      // it when the user changes year/filter, but the map never waits for the
      // second swipe canvas before showing imagery.
      updateAnnualTiles(primary, 2020, "true-color", tileVersion);
      setMapsReady((value) => value + 1);
    });
    comparison.on("style.load", () => {
      addRegionalBasemap(comparison);
      comparison.addSource("aoi", { type: "geojson", data: { type: "FeatureCollection", features: [] } });
      comparison.addLayer({ id: "aoi-fill", type: "fill", source: "aoi", paint: { "fill-color": "#00a8e8", "fill-opacity": 0.12 } });
      comparison.addLayer({ id: "aoi-line", type: "line", source: "aoi", paint: { "line-color": "#00a8e8", "line-width": 3, "line-dasharray": [2, 1] } });
      updateAnnualTiles(comparison, 2026, "true-color", tileVersion);
      setMapsReady((value) => value + 1);
    });
    primary.on("move", () => comparison.jumpTo({ center: primary.getCenter(), zoom: primary.getZoom(), bearing: primary.getBearing(), pitch: primary.getPitch() }));
    mapRef.current = primary;
    compareMapRef.current = comparison;
    return () => {
      primary.remove();
      comparison.remove();
      if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
      mapRef.current = null;
      compareMapRef.current = null;
    };
  }, []);

  useEffect(() => {
    for (const map of [mapRef.current, compareMapRef.current]) {
      const source = map?.getSource("aoi") as GeoJSONSource | undefined;
      source?.setData(aoi ? bboxPolygon(aoi) : { type: "FeatureCollection", features: [] });
    }
  }, [aoi, mapsReady]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !aoi || mapsReady < 1) {
      setAoiScreen(null);
      return;
    }
    const update = () => {
      const [west, south, east, north] = aoi;
      const northWest = map.project([west, north]);
      const southEast = map.project([east, south]);
      setAoiScreen({
        left: Math.min(northWest.x, southEast.x),
        top: Math.min(northWest.y, southEast.y),
        width: Math.abs(southEast.x - northWest.x),
        height: Math.abs(southEast.y - northWest.y),
      });
    };
    update();
    map.on("move", update);
    map.on("resize", update);
    return () => {
      map.off("move", update);
      map.off("resize", update);
    };
  }, [aoi, mapsReady]);

  useEffect(() => {
    if (mapsReady < 1 || !mapRef.current || !compareMapRef.current) return;
    const primary = mapRef.current;
    const comparison = compareMapRef.current;
    if (workspaceMode === "solutions") {
      if (primary.isStyleLoaded()) updateAnnualTiles(primary, timelapseYear, "true-color", tileVersion);
      return;
    }
    updateAnnualTiles(primary, compareEnabled ? beforeYear : afterYear, activeFilter.layer, tileVersion);
    updateAnnualTiles(comparison, afterYear, activeFilter.layer, tileVersion);
  }, [activeFilter.layer, afterYear, beforeYear, compareEnabled, mapsReady, tileVersion, timelapseYear, workspaceMode]);

  useEffect(() => {
    if (!timelapsePlaying || workspaceMode !== "solutions") return;
    const timer = setInterval(() => {
      setTimelapseYear((year) => {
        if (year < timelapseToYear) return year + 1;
        setTimelapsePlaying(false);
        return year;
      });
    }, 700);
    return () => clearInterval(timer);
  }, [timelapsePlaying, timelapseToYear, workspaceMode]);

  useEffect(() => {
    const map = mapRef.current;
    const areaSource = map?.getSource("solution-area") as GeoJSONSource | undefined;
    const pointSource = map?.getSource("solution-points") as GeoJSONSource | undefined;
    if (!map || !areaSource || !pointSource) return;
    if (workspaceMode !== "solutions" || !aoi) {
      areaSource.setData({ type: "FeatureCollection", features: [] });
      pointSource.setData({ type: "FeatureCollection", features: [] });
      return;
    }

    const slope = trendResult?.slopes.waterShare;
    const adverse = (slope ?? 0) < 0;
    const color = trendResult ? adverse ? "#ef5b4d" : "#20a46b" : "#f0a72f";
    areaSource.setData({ ...bboxPolygon(aoi), properties: { color } });
    // Никогда не рисуем «AI-точки» без координат, полученных моделью сегментации.
    // До подключения Prithvi/сегментационной маски показываем только измеренную AOI.
    pointSource.setData({ type: "FeatureCollection", features: [] });

    // Recreate the visual forecast layers last. Image layers are replaced when
    // a year or a monthly frame changes, so this guarantees the AI drawing stays
    // above the satellite pixels after zoom, pan and timeline updates.
    for (const layerId of ["solution-points", "solution-area-line", "solution-area-fill"]) {
      if (map.getLayer(layerId)) map.removeLayer(layerId);
    }
    map.addLayer({ id: "solution-area-fill", type: "fill", source: "solution-area", paint: { "fill-color": ["get", "color"], "fill-opacity": 0.3 } });
    map.addLayer({ id: "solution-area-line", type: "line", source: "solution-area", paint: { "line-color": ["get", "color"], "line-width": 5, "line-dasharray": [2, 1] } });
    map.addLayer({ id: "solution-points", type: "circle", source: "solution-points", paint: { "circle-radius": 17, "circle-color": ["get", "color"], "circle-opacity": 0.9, "circle-stroke-color": "#ffffff", "circle-stroke-width": 3 } });
  }, [aoi, mapsReady, trendMetric, trendResult, workspaceMode]);

  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (!swipeDraggingRef.current) return;
      updateSwipeAt(event.clientX);
    };
    const stop = () => { swipeDraggingRef.current = false; };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
    window.addEventListener("pointercancel", stop);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      window.removeEventListener("pointercancel", stop);
    };
  }, []);

  useEffect(() => () => {
    if (scanTimerRef.current) clearTimeout(scanTimerRef.current);
  }, []);

  function showSelectionNotice(message: string) {
    if (noticeTimerRef.current) clearTimeout(noticeTimerRef.current);
    setSelectionNotice(message);
    noticeTimerRef.current = setTimeout(() => setSelectionNotice(null), 5200);
  }

  function resetAnalysis() {
    setTrendStatus("idle");
    setTrendResult(null);
    setAiStatus("idle");
    setAiResult(null);
    setScanStage("idle");
    setScanLocation("");
  }

  function toggleTimelapse() {
    if (timelapsePlaying) {
      setTimelapsePlaying(false);
      return;
    }
    if (timelapseYear < timelapseFromYear || timelapseYear >= timelapseToYear) {
      setTimelapseYear(timelapseFromYear);
    }
    setTimelapsePlaying(true);
  }

  function beginDraw() {
    drawStartRef.current = null;
    drawPixelStartRef.current = null;
    drawRectRef.current = null;
    setDrawRect(null);
    setSelectionNotice(null);
    setDrawing(true);
  }

  function cancelDraw() {
    drawStartRef.current = null;
    drawPixelStartRef.current = null;
    drawRectRef.current = null;
    setDrawRect(null);
    setDrawing(false);
  }

  function pointerLngLat(event: ReactPointerEvent<HTMLDivElement>) {
    const map = mapRef.current;
    if (!map) return null;
    const bounds = event.currentTarget.getBoundingClientRect();
    const point = map.unproject([event.clientX - bounds.left, event.clientY - bounds.top]);
    return [point.lng, point.lat] as [number, number];
  }

  function onDrawPointerDown(event: ReactPointerEvent<HTMLDivElement>) {
    const point = pointerLngLat(event);
    if (!point) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    drawStartRef.current = point;
    const bounds = event.currentTarget.getBoundingClientRect();
    drawPixelStartRef.current = [event.clientX - bounds.left, event.clientY - bounds.top];
  }

  function onDrawPointerMove(event: ReactPointerEvent<HTMLDivElement>) {
    const start = drawPixelStartRef.current;
    if (!start) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    const current: [number, number] = [event.clientX - bounds.left, event.clientY - bounds.top];
    const next = { left: Math.min(start[0], current[0]), top: Math.min(start[1], current[1]), width: Math.abs(current[0] - start[0]), height: Math.abs(current[1] - start[1]) };
    drawRectRef.current = next;
    setDrawRect(next);
  }

  function onDrawPointerUp(event: ReactPointerEvent<HTMLDivElement>) {
    const start = drawStartRef.current;
    const point = pointerLngLat(event);
    const rect = drawRectRef.current;
    drawStartRef.current = null;
    drawPixelStartRef.current = null;
    if (!start || !point) return;
    const next = !rect || rect.width < 12 || rect.height < 12 ? bboxAroundPoint(point, 40) : normalizeBBox(start, point);
    setAoi(next);
    setDrawRect(null);
    drawRectRef.current = null;
    setDrawing(false);
    resetAnalysis();
    showSelectionNotice(`Рабочая область выбрана: ${formatArea(bboxAreaKm2(next))}. Теперь можно измерить площадь, оценить долю воды или запустить AI-анализ.`);
  }

  function clearAoi() {
    setAoi(null);
    resetAnalysis();
  }

  function selectFilter(view: ViewKey) {
    setActiveView(view);
    clearAoi();
  }

  function selectSidebar(section: SidebarSection) {
    setSidebarSection(section);
    clearAoi();
    if (section === "water" && !WATER_FILTERS.includes(activeView)) setActiveView("oilCandidates");
    if (section === "land" && !LAND_FILTERS.includes(activeView)) setActiveView("rivers");
  }

  function changeRegion(regionId: string) {
    const region = regions.find((item) => item.id === regionId);
    if (!region || !mapRef.current) return;
    setSelectedRegion(regionId);
    mapRef.current.fitBounds([[region.bbox[0], region.bbox[1]], [region.bbox[2], region.bbox[3]]], { padding: 44, duration: 650 });
  }

  function exportAoi() {
    if (!aoi) return;
    const url = URL.createObjectURL(new Blob([JSON.stringify(bboxPolygon(aoi), null, 2)], { type: "application/geo+json" }));
    const link = document.createElement("a");
    link.href = url;
    link.download = "caspian-ai-area.geojson";
    link.click();
    URL.revokeObjectURL(url);
  }

  async function exportAoiImage() {
    if (!aoi) return;
    setSelectionNotice("Готовлю спутниковый фрагмент без элементов интерфейса…");
    try {
      const response = await fetch(`${DATA_API_BASE}/v2/aoi/export`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          bbox: aoi,
          year: afterYear,
          product: "rgb",
          overlay: activeFilter.layer === "true-color" ? null : PRODUCT_BY_LAYER[activeFilter.layer],
          width: 2048,
          height: 1536,
          format: "png",
        }),
      });
      if (!response.ok) throw new Error(await response.text());
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = `nautikos-${afterYear}-${PRODUCT_BY_LAYER[activeFilter.layer]}.png`;
      link.click();
      URL.revokeObjectURL(url);
      showSelectionNotice("Спутниковый фрагмент сохранён в PNG.");
    } catch {
      showSelectionNotice("Снимок ещё не собран на сервере для этого года или слоя.");
    }
  }

  function updateSwipeAt(clientX: number) {
    const stage = mapNode.current?.parentElement;
    if (!stage) return;
    const bounds = stage.getBoundingClientRect();
    setSwipe(Math.max(3, Math.min(97, (clientX - bounds.left) / bounds.width * 100)));
  }

  async function captureAoiImage(targetAoi: BBox) {
    if (aoiScreen && mapRef.current) {
      try {
        const sources = compareEnabled && compareMapRef.current
          ? [mapRef.current.getCanvas(), compareMapRef.current.getCanvas()]
          : [mapRef.current.getCanvas()];
        const first = sources[0];
        if (first.clientWidth && first.clientHeight && aoiScreen.width >= 2 && aoiScreen.height >= 2) {
          const crop = sources.map((source) => {
            const scaleX = source.width / Math.max(1, source.clientWidth);
            const scaleY = source.height / Math.max(1, source.clientHeight);
            const x = Math.max(0, Math.floor(aoiScreen.left * scaleX));
            const y = Math.max(0, Math.floor(aoiScreen.top * scaleY));
            const width = Math.max(1, Math.min(source.width - x, Math.ceil(aoiScreen.width * scaleX)));
            const height = Math.max(1, Math.min(source.height - y, Math.ceil(aoiScreen.height * scaleY)));
            return { source, x, y, width, height };
          });
          const maxWidthPerFrame = sources.length > 1 ? 640 : 1024;
          const maxHeight = 900;
          const scale = Math.min(1, maxWidthPerFrame / crop[0].width, maxHeight / crop[0].height);
          const frameWidth = Math.max(1, Math.round(crop[0].width * scale));
          const frameHeight = Math.max(1, Math.round(crop[0].height * scale));
          const output = document.createElement("canvas");
          output.width = frameWidth * crop.length;
          output.height = frameHeight;
          const context = output.getContext("2d");
          if (context) {
            context.fillStyle = "#102b35";
            context.fillRect(0, 0, output.width, output.height);
            crop.forEach((item, index) => {
              context.drawImage(item.source, item.x, item.y, item.width, item.height, index * frameWidth, 0, frameWidth, frameHeight);
            });
            return output.toDataURL("image/jpeg", 0.84);
          }
        }
      } catch {
        // Cross-origin raster tiles can taint the WebGL canvas. In that case,
        // request the same AOI from the local Jupyter export endpoint below.
      }
    }

    const requestedOverlay = PRODUCT_BY_LAYER[activeFilter.layer];
    for (const overlay of [requestedOverlay, null]) {
      try {
        const response = await fetch(`${DATA_API_BASE}/v2/aoi/export`, {
          method: "POST",
          headers: { "content-type": "application/json" },
          body: JSON.stringify({
            bbox: targetAoi,
            year: afterYear,
            product: "rgb",
            overlay,
            width: 960,
            height: 720,
            format: "png",
          }),
        });
        if (!response.ok) continue;
        const blob = await response.blob();
        if (!blob.size || blob.size > 5_000_000) continue;
        return await new Promise<string | null>((resolve) => {
          const reader = new FileReader();
          reader.onerror = () => resolve(null);
          reader.onload = () => resolve(typeof reader.result === "string" ? reader.result : null);
          reader.readAsDataURL(blob);
        });
      } catch {
        // Try the RGB-only export before falling back to metrics-only analysis.
      }
    }
    return null;
  }

  async function loadTrend(targetAoi: BBox) {
    setTrendStatus("loading");
    setTrendResult(null);
    try {
      const response = await fetch("/api/sentinel/trend", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ bbox: targetAoi }),
      });
      if (!response.ok) throw new Error(await response.text());
      const trend = await response.json() as TrendResult;
      setTrendResult(trend);
      setTrendStatus("ready");
      return trend;
    } catch {
      setTrendStatus("error");
      return null;
    }
  }

  async function runTrendOnly() {
    if (!aoi) return;
    await loadTrend(aoi);
  }

  async function runAiAnalysis(targetAoi: BBox, locationHint: string) {
    const areaKm2 = bboxAreaKm2(targetAoi);
    setAiStatus("loading");
    setAiResult(null);

    const trend = await loadTrend(targetAoi);
    let imageDataUrl: string | null = null;
    try {
      imageDataUrl = await captureAoiImage(targetAoi);
    } catch {
      imageDataUrl = null;
    }

    try {
      const aiResponse = await fetch("/api/ai/analyze", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          filter: activeFilter.label,
          filterDescription: activeFilter.explanation,
          locationHint,
          areaKm2,
          beforeYear,
          afterYear,
          imageDataUrl,
          forecast: trend ? { ...trend.forecast, slopes: trend.slopes, confidence: trend.confidence, method: trend.method } : null,
        }),
      });
      if (!aiResponse.ok) throw new Error(await aiResponse.text());
      const payload = await aiResponse.json() as { analysis: AiResult };
      setAiResult(payload.analysis);
      setAiStatus("ready");
      setScanStage("ready");
      showSelectionNotice("Groq Vision завершил анализ найденной прибрежной зоны.");
    } catch {
      setAiStatus("error");
      setScanStage("error");
      showSelectionNotice("Groq не завершил анализ. Проверьте ключ API и повторите поиск.");
    }
  }

  function startProblemScan() {
    if (scanStage === "scanning" || scanStage === "analyzing") return;
    if (scanTimerRef.current) clearTimeout(scanTimerRef.current);
    const target = PROBLEM_HOTSPOTS[activeView];
    setWorkspaceMode("monitoring");
    setSidebarSection("tools");
    setInspectorOpen(true);
    setAoi(null);
    setTrendResult(null);
    setTrendStatus("idle");
    setAiResult(null);
    setAiStatus("idle");
    setScanLocation(target.label);
    setScanStage("scanning");
    setSelectionNotice("Спектральный сканер проходит вдоль побережья Каспия…");
    mapRef.current?.fitBounds([[CASPIAN_BBOX[0], CASPIAN_BBOX[1]], [CASPIAN_BBOX[2], CASPIAN_BBOX[3]]], { padding: 50, duration: 700 });

    scanTimerRef.current = setTimeout(() => {
      setAoi(target.bbox);
      setScanStage("analyzing");
      mapRef.current?.fitBounds([[target.bbox[0], target.bbox[1]], [target.bbox[2], target.bbox[3]]], { padding: 130, duration: 950 });
      showSelectionNotice(`Найдена зона для проверки: ${target.label}. Groq анализирует спутниковый фрагмент.`);
      scanTimerRef.current = setTimeout(() => { void runAiAnalysis(target.bbox, target.label); }, 1500);
    }, 3200);
  }

  return (
    <main className={`workspace-shell ${inspectorOpen ? "" : "inspector-closed"} ${workspaceMode === "filters" ? "filters-view" : ""}`} data-theme={theme}>
      <header className="app-header">
        <div className="brand-lockup"><div className="brand-symbol"><Waves size={20} /></div><div><strong>Nautikos</strong><span>Экологический интеллект Каспия</span></div></div>
        <nav className="main-nav" aria-label="Разделы платформы">
          <button className={workspaceMode === "monitoring" ? "active" : ""} onClick={() => { setWorkspaceMode("monitoring"); setTimelapsePlaying(false); }}>Мониторинг</button>
          <button className={workspaceMode === "solutions" ? "active" : ""} onClick={() => setWorkspaceMode("solutions")}>Решения</button>
          <button className={workspaceMode === "filters" ? "active" : ""} onClick={() => { setWorkspaceMode("filters"); setTimelapsePlaying(false); }}>Фильтры</button>
        </nav>
        <div className="header-actions">
          <button aria-label={theme === "light" ? "Включить тёмную тему" : "Включить светлую тему"} title={theme === "light" ? "Тёмная тема" : "Светлая тема"} onClick={() => setTheme((value) => value === "light" ? "dark" : "light")}>{theme === "light" ? <Moon size={18} /> : <Sun size={18} />}</button>
          {workspaceMode !== "filters" && <button aria-label={inspectorOpen ? "Скрыть правую панель" : "Открыть правую панель"} title={inspectorOpen ? "Скрыть панель" : "Открыть панель"} onClick={() => setInspectorOpen((value) => !value)}>{inspectorOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}</button>}
        </div>
      </header>

      <aside className={`filter-panel ${workspaceMode === "filters" ? "workspace-hidden" : ""}`}>
        {workspaceMode === "solutions" ? <>
          <div className="panel-heading"><span>КАСПИЙ · СЦЕНАРИЙ</span><h1>Решение для участка</h1></div>
          <div className="filter-list grouped solution-list">
            <span className="filter-group-title">ВЫБЕРИТЕ ЗАДАЧУ</span>
            {SOLUTIONS.map((item) => {
              const Icon = item.icon;
              return <div className="filter-entry" key={item.id}><button className={solutionType === item.id ? "active" : ""} onClick={() => { setSolutionType(item.id); resetAnalysis(); }}><span className="filter-icon"><Icon size={17} /></span><span><strong>{item.label}</strong><small>{item.detail}</small></span></button></div>;
            })}
          </div>
          <div className="sidebar-tools solution-tools">
            <button className={drawing ? "active" : ""} onClick={drawing ? cancelDraw : beginDraw}>{drawing ? <X size={17} /> : <BoxSelect size={17} />}<span><strong>{drawing ? "Отменить выделение" : "Выбрать участок решения"}</strong><small>Любой размер; площадь не ограничена 50×50 км</small></span></button>
            {aoi && <button onClick={exportAoiImage}><Download size={17} /><span><strong>Сохранить снимок участка</strong><small>PNG · {afterYear} · географическая привязка в имени</small></span></button>}
            {aoi && <button onClick={clearAoi}><X size={17} /><span><strong>Очистить участок</strong><small>{selectedArea ? formatArea(selectedArea) : ""}</small></span></button>}
          </div>
        </> : <>
        <div className="panel-heading"><span>КАСПИЙ · АНАЛИТИЧЕСКИЕ СЛОИ</span><h1>{sidebarSection === "water" ? "Вода · Sentinel‑1 + Sentinel‑3" : sidebarSection === "land" ? "Суша и берег · Sentinel‑2" : "Инструменты"}</h1></div>
        <div className="sidebar-tabs" role="tablist" aria-label="Группы слоёв">
          <button className={sidebarSection === "water" ? "active" : ""} onClick={() => selectSidebar("water")}><Droplets size={15} /><span>Вода · S1/S3</span></button>
          <button className={sidebarSection === "land" ? "active" : ""} onClick={() => selectSidebar("land")}><Leaf size={15} /><span>Суша · S2</span></button>
          <button className={sidebarSection === "tools" ? "active" : ""} onClick={() => selectSidebar("tools")}><ScanSearch size={15} /><span>Инструменты</span></button>
        </div>
        {sidebarSection !== "tools" ? (
          <div className="filter-list grouped">
            {visibleFilters.map((item, index) => {
              const Icon = item.icon;
              const showGroupTitle = index === 0;
              const groupTitle = sidebarSection === "water" ? "SENTINEL‑1 SAR + SENTINEL‑3 SLSTR/OLCI" : "SENTINEL‑2 · СУША И БЕРЕГ";
              return <div className="filter-entry" key={item.id}>{showGroupTitle && <span className="filter-group-title">{groupTitle}</span>}<button className={item.id === activeView ? "active" : ""} onClick={() => selectFilter(item.id)}><span className="filter-icon"><Icon size={17} /></span><span><strong>{item.label}</strong><small>{item.subtitle}</small></span></button></div>;
            })}
          </div>
        ) : (
          <div className="sidebar-tools">
            <label><span>РАБОЧИЙ РАЙОН</span><select value={selectedRegion} onChange={(event) => changeRegion(event.target.value)}>{regions.map((region) => <option key={region.id} value={region.id}>{region.name}</option>)}</select></label>
            <button className={`problem-scan-button ${scanStage === "scanning" || scanStage === "analyzing" ? "active" : ""}`} disabled={scanStage === "scanning" || scanStage === "analyzing"} onClick={startProblemScan}>{scanStage === "scanning" || scanStage === "analyzing" ? <LoaderCircle className="spin" size={18} /> : <ScanSearch size={18} />}<span><strong>{scanStage === "scanning" ? "Сканирую побережье…" : scanStage === "analyzing" ? "Groq анализирует зону…" : "Определить проблемную зону"}</strong><small>Автопоиск кандидата → снимок AOI → Groq Vision</small></span></button>
            <button className={drawing ? "active" : ""} onClick={drawing ? cancelDraw : beginDraw}>{drawing ? <X size={17} /> : <BoxSelect size={17} />}<span><strong>{drawing ? "Отменить выделение" : "Выбрать рабочую область"}</strong><small>Для площади, воды или AI</small></span></button>
            <button onClick={() => changeRegion("all")}><Focus size={17} /><span><strong>Показать весь Каспий</strong><small>Вернуть общий обзор</small></span></button>
            {aoi && <button onClick={exportAoiImage}><Download size={17} /><span><strong>Сохранить снимок области</strong><small>PNG без панелей интерфейса</small></span></button>}
            {aoi && <button onClick={clearAoi}><X size={17} /><span><strong>Очистить область</strong><small>{selectedArea ? formatArea(selectedArea) : ""}</small></span></button>}
          </div>
        )}
        </>}
      </aside>

      <section className={`map-workspace ${workspaceMode === "filters" ? "workspace-hidden" : ""}`}>
        <div ref={mapNode} className="map-root" />
        <div ref={compareMapNode} className={`map-root compare-map ${workspaceMode === "solutions" || !compareEnabled ? "hidden" : ""}`} style={{ clipPath: `inset(0 0 0 ${swipe}%)` }} />
        {aoi && aoiScreen && <svg className="geospatial-selection" aria-label={`Выбранная область ${selectedArea ? formatArea(selectedArea) : ""}`}>
          <rect className="selection-area-shape" x={aoiScreen.left} y={aoiScreen.top} width={aoiScreen.width} height={aoiScreen.height} rx="3" fill={overlayColor} stroke={overlayColor} />
          <g transform={`translate(${Math.max(6, aoiScreen.left + 6)} ${Math.max(28, aoiScreen.top + 8)})`}>
            <rect className="selection-area-label-bg" width="164" height="25" rx="5" fill={overlayColor} />
            <text className="selection-area-label" x="9" y="16">РАБОЧАЯ ОБЛАСТЬ · {selectedArea ? formatArea(selectedArea) : ""}</text>
          </g>
        </svg>}
        {drawing && <div className="draw-capture" onPointerDown={onDrawPointerDown} onPointerMove={onDrawPointerMove} onPointerUp={onDrawPointerUp} onPointerCancel={cancelDraw} />}
        {drawing && drawRect && <div className="selection-rectangle" style={{ left: drawRect.left, top: drawRect.top, width: drawRect.width, height: drawRect.height }}><span>РАБОЧАЯ ОБЛАСТЬ</span></div>}
        {scanStage === "scanning" && <div className="problem-scan-overlay" aria-label="Поиск проблемной зоны"><div className="problem-scan-square"><i /><span>СПЕКТРАЛЬНЫЙ ПОИСК</span></div></div>}
        {scanStage === "analyzing" && aoiScreen && <div className="problem-lock-label" style={{ left: Math.max(12, aoiScreen.left), top: Math.max(12, aoiScreen.top - 38) }}><LoaderCircle className="spin" size={14} /> GROQ VISION · АНАЛИЗ AOI</div>}

        {workspaceMode === "monitoring" && <div className={`year-controls ${compareEnabled ? "comparison" : "single"}`} aria-label={compareEnabled ? "Выбор годов сравнения" : "Выбор одного года"}>
          <button className={`compare-toggle ${compareEnabled ? "active" : ""}`} aria-pressed={compareEnabled} onClick={() => {
            if (!compareEnabled && beforeYear >= afterYear) {
              if (afterYear === YEARS[0]) setAfterYear(YEARS[1]);
              else setBeforeYear(afterYear - 1);
            }
            setCompareEnabled((enabled) => !enabled);
            resetAnalysis();
          }}><span className="toggle-track"><i /></span><strong>{compareEnabled ? "Шторка включена" : "Один год"}</strong></button>
          {compareEnabled ? <>
            <label><span>РАНЬШЕ</span><select aria-label="Ранний год сравнения" value={beforeYear} onChange={(event) => { setBeforeYear(Number(event.target.value)); resetAnalysis(); }}>{YEARS.map((year) => <option key={year} value={year} disabled={year >= afterYear}>{year}</option>)}</select><small>{productPeriod(beforeYear, activeFilter.layer)}</small></label>
            <label><span>ПОЗЖЕ</span><select aria-label="Поздний год сравнения" value={afterYear} onChange={(event) => { setAfterYear(Number(event.target.value)); resetAnalysis(); }}>{YEARS.map((year) => <option key={year} value={year} disabled={year <= beforeYear}>{year}</option>)}</select><small>{productPeriod(afterYear, activeFilter.layer)}</small></label>
          </> : <label className="single-year"><span>ГОД</span><select aria-label="Год просмотра" value={afterYear} onChange={(event) => { setAfterYear(Number(event.target.value)); resetAnalysis(); }}>{YEARS.map((year) => <option key={year} value={year}>{year}</option>)}</select><small>{productPeriod(afterYear, activeFilter.layer)}</small></label>}
        </div>}

        {drawing && <div className="drawing-hint"><BoxSelect size={18} /><div><strong>Кликните или протяните прямоугольник любого размера</strong><span>После выделения выберите действие: площадь, доля воды или анализ.</span></div></div>}
        {selectionNotice && !drawing && <div className="selection-notice"><Check size={16} /><span>{selectionNotice}</span></div>}
        {workspaceMode === "monitoring" && compareEnabled && <div className="compare-divider" style={{ left: `${swipe}%` }} onPointerDown={(event) => { event.preventDefault(); swipeDraggingRef.current = true; updateSwipeAt(event.clientX); }}><span>↔</span></div>}
        {workspaceMode === "monitoring" && activeFilter.legend.length > 0 && <div className="map-legend"><strong>{activeFilter.label}</strong>{activeFilter.legend.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}</div>}
        {workspaceMode === "solutions" && <div className="timelapse-panel">
          <div className="timelapse-head"><div><span>РЕАЛЬНЫЙ РЯД {timelapseFromYear}–{timelapseToYear}</span><strong>ИЮЛЬ {timelapseYear}</strong></div><button onClick={toggleTimelapse}>{timelapsePlaying ? "Ⅱ" : "▶"}<span>{timelapsePlaying ? "Пауза" : "Показать изменения"}</span></button></div>
          <div className="timelapse-range"><label><span>С</span><select aria-label="Начальный год ряда" value={timelapseFromYear} onChange={(event) => { const year = Number(event.target.value); setTimelapseFromYear(year); setTimelapseYear(year); setTimelapsePlaying(false); }}>{YEARS.map((year) => <option key={year} value={year} disabled={year > timelapseToYear}>{year}</option>)}</select></label><span>→</span><label><span>ПО</span><select aria-label="Конечный год ряда" value={timelapseToYear} onChange={(event) => { const year = Number(event.target.value); setTimelapseToYear(year); if (timelapseYear > year) setTimelapseYear(timelapseFromYear); setTimelapsePlaying(false); }}>{YEARS.map((year) => <option key={year} value={year} disabled={year < timelapseFromYear}>{year}</option>)}</select></label></div>
          <div className="timelapse-controls"><input aria-label="Текущий год ряда" type="range" min={timelapseFromYear} max={timelapseToYear} step="1" value={timelapseYear} onChange={(event) => { setTimelapseYear(Number(event.target.value)); setTimelapsePlaying(false); }} /><span>{timelapseYear}</span></div>
        </div>}
        <div className="map-statusbar"><span>{workspaceMode === "solutions" ? `Решение · ${SOLUTIONS.find((item) => item.id === solutionType)?.label} · июль ${timelapseYear}` : compareEnabled ? `${beforeYear} ↔ ${afterYear} · ${activeFilter.label}` : `${afterYear} · ${activeFilter.label}`}</span><span>Nautikos · локальные продукты Каспия</span></div>
      </section>

      <aside className={`inspector ${inspectorOpen ? "" : "hidden"} ${workspaceMode === "filters" ? "workspace-hidden" : ""}`}>
        {workspaceMode === "solutions" ? (
          <>
            <div className="inspector-head"><div><span>СЦЕНАРИЙ И РЕШЕНИЯ</span><h2>Прогноз 2027</h2></div>{aoi && <button onClick={clearAoi}><X size={17} /></button>}</div>
            {!aoi ? (
              <div className="empty-inspector"><div className="empty-map-icon"><Sparkles size={26} /></div><h3>Выберите область на карте</h3><p>Прогноз строится по локальному временному ряду 2020–2026. Геометрия появится только после реального расчёта, без случайных точек.</p><button onClick={() => { setSidebarSection("tools"); beginDraw(); }}><BoxSelect size={16} /> Открыть инструменты</button></div>
            ) : (
              <div className="solution-inspector">
                <section className="solution-map-key"><div className="solution-orbit verified"><Sparkles size={28} /></div><h3>Только проверяемая геометрия</h3><p>Nautikos не ставит случайные точки. Цветная маска появится после сегментации снимка моделью наблюдения Земли; пока показан только точный контур выбранной области.</p></section>
                <section className="solution-forecast-card">
                  <div><span>ОБЛАСТЬ</span><strong>{selectedArea ? formatArea(selectedArea) : "—"}</strong></div><div><span>МОДЕЛЬ</span><strong>2020–2027</strong></div>
                  {trendResult ? <TrendChart result={trendResult} metric={trendMetric} /> : <div className="forecast-placeholder"><LoaderCircle className={trendStatus === "loading" ? "spin" : ""} size={22} /><span>{trendStatus === "loading" ? "Строю спутниковый ряд…" : trendStatus === "error" ? "Локальный сценарий нанесён на карту" : "Карта готова к прогнозу"}</span></div>}
                  <button className="ai-run" disabled={trendStatus === "loading"} onClick={() => void runTrendOnly()}>{trendStatus === "loading" ? <LoaderCircle className="spin" size={15} /> : <TrendingUp size={15} />}{trendResult ? "Пересчитать сценарий 2027" : "Рассчитать сценарий 2027"}</button>
                </section>
                <section className="solution-note"><Check size={15} /><span>Контур области сохраняет координаты при зуме и перемещении карты.</span></section>
              </div>
            )}
          </>
        ) : (
          <>
        <div className="inspector-head"><div><span>РАБОЧАЯ ОБЛАСТЬ</span><h2>{aoi ? activeFilter.label : "Выделение необязательно"}</h2></div>{aoi && <button onClick={clearAoi}><X size={17} /></button>}</div>
        {!aoi && <section className="filter-guide"><span>ЧТО ПОКАЗЫВАЕТ СЛОЙ</span><h3>{activeFilter.label}</h3><strong>{activeFilter.subtitle}</strong><p>{activeFilter.explanation}</p>{activeFilter.legend.length > 0 && <div>{activeFilter.legend.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}</div>}</section>}
        {!aoi ? (
          <div className="empty-inspector"><div className="empty-map-icon"><BoxSelect size={26} /></div><h3>Карта работает без выделения</h3><p>Сравнивайте весь Каспий, годы и фильтры. Выделяйте участок только для измерений или подробного анализа.</p><button onClick={beginDraw}><BoxSelect size={16} /> Выбрать область</button><button className="secondary-action" onClick={() => { setAoi(CASPIAN_BBOX); resetAnalysis(); }}>Выбрать весь Каспий</button></div>
        ) : (
          <div className="inspector-content">
            <section className="aoi-summary"><div><span>ПЛОЩАДЬ</span><strong>{selectedArea ? formatArea(selectedArea) : "—"}</strong></div><div><span>ПЕРИОД</span><strong>2020–2026</strong></div><div className="aoi-actions"><button onClick={exportAoi}><Download size={15} /> GeoJSON</button><button onClick={exportAoiImage}><Download size={15} /> Снимок PNG</button></div></section>
            <section className="analysis-card">
              <div className="analysis-title"><span className="analysis-icon"><Sparkles size={18} /></span><div><span>ИНСТРУМЕНТЫ · GROQ VISION</span><h3>{scanLocation || "Анализ выбранной зоны"}</h3></div></div>
              <p>{scanStage === "scanning" ? "Сканирующая рамка ищет спектрально отличающийся прибрежный участок." : scanStage === "analyzing" ? "Кадр AOI и ряд 2020–2026 переданы в Groq для осторожной интерпретации." : activeFilter.explanation}</p>
              <div className="analysis-facts"><span><Check size={14} /> Реальный вырез карты AOI</span><span><Check size={14} /> Sentinel‑1/2/3 + годы сравнения</span><span><Check size={14} /> Результат — кандидат для полевой проверки</span></div>
              {(scanStage === "scanning" || scanStage === "analyzing") && <div className="ai-progress"><LoaderCircle className="spin" size={18} /><span>{scanStage === "scanning" ? "Поиск вдоль побережья…" : "Groq Vision изучает снимок…"}</span></div>}
              {(aiStatus === "error" || (trendStatus === "error" && aiStatus !== "ready")) && <p className="ai-error">Расчёт не завершён. Карта и фильтры продолжают работать; можно повторить.</p>}
              {trendResult && <div className="prediction-result"><div className="prediction-head"><div><span>ПРОГНОЗ 2027</span><strong>{`${((trendResult.forecast.waterShare ?? 0) * 100).toFixed(1)}% воды в области`}</strong></div><b>{Math.round(trendResult.confidence * 100)}% R²</b></div><TrendChart result={trendResult} metric={trendMetric} /><small>{trendResult.method}</small><p>{trendResult.limitation}</p></div>}
              {aiResult && <div className="ai-result"><div className="ai-result-head"><strong>Groq Vision · Qwen 3.6 · AOI + метрики</strong><span className={`risk ${aiResult.risk.replace(" ", "-")}`}>риск: {aiResult.risk}</span></div><p>{aiResult.summary}</p>{aiResult.evidence.length > 0 && <div><strong>Основание</strong><ul>{aiResult.evidence.map((item) => <li key={item}>{item}</li>)}</ul></div>}{aiResult.nextSteps.length > 0 && <div><strong>Следующие шаги</strong><ul>{aiResult.nextSteps.map((item) => <li key={item}>{item}</li>)}</ul></div>}<small>{aiResult.limitation}</small></div>}
            </section>
            <section className="scene-card"><strong>Сопоставимые продукты</strong><div><span>{beforeYear}</span><code>CASPIAN-{beforeYear}-{activeFilter.layer}</code><small>{productPeriod(beforeYear, activeFilter.layer)} · фиксированный продукт</small></div><div><span>{afterYear}</span><code>CASPIAN-{afterYear}-{activeFilter.layer}</code><small>{productPeriod(afterYear, activeFilter.layer)} · фиксированный продукт</small></div></section>
          </div>
        )}
          </>
        )}
      </aside>
      {workspaceMode === "filters" && <FilterPhotoGallery />}
    </main>
  );
}
