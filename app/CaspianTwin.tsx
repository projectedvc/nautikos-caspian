"use client";

import {
  AlertTriangle,
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

type BBox = [number, number, number, number];
type DatasetKey = "s2" | "s1" | "s3" | "era5" | "dem";
type ViewKey = "optical" | "waterOptical" | "shoreline" | "water" | "chlorophyll" | "suspendedMatter" | "waterTemperature" | "vegetation" | "coastMoisture" | "soil" | "erosion" | "oil";
type LayerKey = "true-color" | "olci-true-color" | "shoreline" | "water-quality" | "chlorophyll" | "suspended-matter" | "water-temperature" | "vegetation" | "coast-moisture" | "soil-stress" | "erosion-risk" | "oil-roughness";
type WorkspaceMode = "monitoring" | "solutions";
type SidebarSection = "water" | "land" | "tools";
type AoiScreen = { left: number; top: number; width: number; height: number };

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
const OVERVIEW_CACHE_VERSION = 28;
const TIMELAPSE_CACHE_VERSION = 16;
const MONTHS = ["янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"];
const WATER_FILTERS: ViewKey[] = ["optical", "waterOptical", "water", "oil", "chlorophyll", "suspendedMatter", "waterTemperature", "shoreline"];
const LAND_FILTERS: ViewKey[] = ["optical", "shoreline", "vegetation", "coastMoisture", "soil", "erosion"];

const regions: Region[] = [
  { id: "all", name: "Весь Каспий", bbox: CASPIAN_BBOX },
  { id: "north", name: "Северный Каспий", bbox: [46.2, 44.0, 53.2, 47.4] },
  { id: "middle", name: "Средний Каспий", bbox: [47.0, 40.2, 53.8, 44.5] },
  { id: "south", name: "Южный Каспий", bbox: [48.0, 36.2, 55.8, 40.8] },
  { id: "kz", name: "Побережье Казахстана", bbox: [49.0, 42.0, 55.1, 47.2] },
  { id: "az", name: "Побережье Азербайджана", bbox: [48.6, 38.2, 51.5, 42.2] },
  { id: "tm", name: "Побережье Туркменистана", bbox: [51.8, 36.8, 55.5, 42.5] },
];

const filters: FilterDefinition[] = [
  {
    id: "optical",
    label: "Снимок и граница воды",
    subtitle: "Единая RGB‑сетка · Sentinel‑2 · 2020–2026",
    dataset: "s2",
    layer: "true-color",
    icon: Satellite,
    legend: [],
    explanation: "Каждый год совмещён с одной и той же спутниковой подложкой и измеренной границей воды. Поэтому шторка показывает изменение площади и берега без смещения снимка; при детальном приближении проявляется тайловая HD‑подложка Jupyter.",
  },
  {
    id: "waterOptical",
    label: "Вода: спектральный снимок",
    subtitle: "Sentinel‑3 OLCI · цвет воды · 300 м",
    dataset: "s3",
    layer: "olci-true-color",
    icon: Satellite,
    legend: [],
    explanation: "Реальные радиансы OLCI в водных спектральных каналах. Слой показывает крупные водные массы и шлейфы по всему Каспию; разрешение 300 м, поэтому он предназначен для акватории, а не для зданий и узких береговых объектов.",
  },
  {
    id: "shoreline",
    label: "Обмеление и берег",
    subtitle: "Sentinel‑2 · MNDWI/NDWI · 10/20 м",
    dataset: "s2",
    layer: "shoreline",
    icon: Waves,
    legend: [{ color: "#0f628d", label: "вода в выбранном году" }],
    explanation: "Граница вода/суша рассчитана из MNDWI и NDWI того же фиксированного летнего Sentinel‑2-композита, который виден под слоем. Жёлтая линия показывает берег выбранного года; шторка сравнивает одинаковый сезон.",
  },
  {
    id: "water",
    label: "Шлейфы сбросов и мутность",
    subtitle: "Sentinel‑2 · NDWI + RGB · 10/20 м",
    dataset: "s2",
    layer: "water-quality",
    icon: Droplets,
    legend: [{ color: "#29b8db", label: "глубокая вода" }, { color: "#e8872f", label: "кандидат мутности" }],
    explanation: "Тёплым цветом отмечается повышенное красное отражение внутри маски воды — возможная взвесь, речной шлейф или сброс у берега. Это детальный кандидат 10/20 м, а не доказательство загрязнения: результат сверяется с TSM Sentinel‑3, направлением течения и полевой пробой.",
  },
  {
    id: "chlorophyll",
    label: "Хлорофилл / цветение",
    subtitle: "Sentinel‑3 OLCI · NDCI‑скрининг · 300 м",
    dataset: "s3",
    layer: "chlorophyll",
    icon: Waves,
    legend: [{ color: "#174ea6", label: "низкий сигнал" }, { color: "#25a56a", label: "повышенный" }, { color: "#ef5b3f", label: "высокий приоритет" }],
    explanation: "NDCI использует реальные каналы OLCI 665 и 709 нм как сравнительный сигнал хлорофилла. Красный — высокий относительный приоритет проверки цветения; это не лабораторная концентрация, не токсичность и не видовой анализ цианобактерий.",
  },
  {
    id: "suspendedMatter",
    label: "Взвесь и крупные шлейфы",
    subtitle: "Sentinel‑3 OLCI · индекс осадка · 300 м",
    dataset: "s3",
    layer: "suspended-matter",
    icon: Droplets,
    legend: [{ color: "#183b8f", label: "чистая вода" }, { color: "#f0a72f", label: "взвесь" }, { color: "#d83a2e", label: "сильная аномалия" }],
    explanation: "Отношение каналов OLCI 620 и 560 нм выделяет крупные речные шлейфы и повышенный взвешенный сигнал. Источник нельзя определить только по цвету — нужна проверка течений, предприятий и полевой пробой.",
  },
  {
    id: "waterTemperature",
    label: "Температура воды",
    subtitle: "Copernicus ERA5 · 10 суток · ~28 км",
    dataset: "era5",
    layer: "water-temperature",
    icon: Waves,
    legend: [{ color: "#2459c4", label: "холоднее" }, { color: "#f2b134", label: "теплее" }, { color: "#dc3f32", label: "тепловая аномалия" }],
    explanation: "Средняя температура поверхности за одинаковые десять дней июля по реанализу Copernicus ERA5. Слой показывает только крупномасштабную тепловую картину Каспия; локальные сбросы требуют более детального теплового сенсора и полевой проверки.",
  },
  {
    id: "vegetation",
    label: "Растительность",
    subtitle: "NDVI · влажность покрова",
    dataset: "s2",
    layer: "vegetation",
    icon: Leaf,
    legend: [{ color: "#39a96b", label: "активный покров" }, { color: "#b78a45", label: "слабый покров" }],
    explanation: "Сравнение показывает потерю или восстановление растительности и помогает выбрать место для полевой проверки или восстановления.",
  },
  {
    id: "soil",
    label: "Проблемы почвы",
    subtitle: "BSI · оголение · стресс",
    dataset: "s2",
    layer: "soil-stress",
    icon: ScanSearch,
    legend: [{ color: "#ef402d", label: "высокий приоритет" }, { color: "#f0a72f", label: "возможный стресс" }, { color: "#268a5b", label: "стабильный покров" }],
    explanation: "Красным подсвечиваются участки с сочетанием оголённого грунта и слабой растительности — кандидаты на деградацию, засоление или рекультивацию. Спектральный сигнал не заменяет анализ почвенной пробы.",
  },
  {
    id: "coastMoisture",
    label: "Влажность побережья",
    subtitle: "Sentinel‑2 · NDMI · 20 м",
    dataset: "s2",
    layer: "coast-moisture",
    icon: Droplets,
    legend: [{ color: "#d58a34", label: "сухо" }, { color: "#66a85b", label: "умеренно" }, { color: "#176ca4", label: "влажно / подтоплено" }],
    explanation: "NDMI показывает относительную влажность растительности и грунта в береговой полосе. Синий сигнал может означать заболачивание, подтопление или влажную растительность — причина уточняется по снимку и рельефу.",
  },
  {
    id: "erosion",
    label: "Рельеф, низины и сток",
    subtitle: "Copernicus DEM GLO‑30 · 30 м",
    dataset: "dem",
    layer: "erosion-risk",
    icon: ScanSearch,
    legend: [{ color: "#0d6194", label: "низина" }, { color: "#57a35d", label: "до 50 м" }, { color: "#d58b21", label: "возвышенность" }],
    explanation: "Цифровая модель рельефа 30 м показывает низины и высоты. Направление стока и уклон рассчитываются ИИ по соседним пикселям DEM; сам цвет высоты не является фактом эрозии или загрязнения.",
  },
  {
    id: "oil",
    label: "Кандидаты утечки нефти",
    subtitle: "Sentinel‑1 SAR · VV · 10 м",
    dataset: "s1",
    layer: "oil-roughness",
    icon: Radar,
    legend: [{ color: "#081722", label: "гладкая поверхность" }, { color: "#2bb9ef", label: "шероховатая вода" }],
    explanation: "SAR выделяет тёмные формации на воде при облаках и ночью. Для тревоги кандидат проверяется по ветру, AIS и повторному пролёту.",
  },
];

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

function annualOverviewUrl(year: number, layer: LayerKey, version: number) {
  return `/api/sentinel/process?year=${year}&layer=${layer}&v=${version}-${OVERVIEW_CACHE_VERSION}`;
}

function regionalBasemapTileUrl() {
  return `/api/basemap?z={z}&x={x}&y={y}&v=regional-surface-2`;
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
  return `/api/sentinel/process?year=${year}&layer=${layer}&z={z}&x={x}&y={y}&width=512&height=512&v=fixed-pyramid-${version}`;
}

function monthlyOverviewUrl(year: number, month: number, version: number) {
  return `/api/sentinel/process?year=${year}&month=${month}&layer=true-color&bbox=${CASPIAN_BBOX.join(",")}&width=640&height=800&v=timelapse-${TIMELAPSE_CACHE_VERSION}`;
}

function productPeriod(year: number, layer: LayerKey) {
  if (layer === "erosion-risk") return "статический рельеф";
  if (layer === "water-temperature") return `1—10 июля ${year}`;
  if (layer === "oil-roughness") return `июль ${year}`;
  return `1—15 июля ${year}`;
}

function nativeTileMaxZoom(layer: LayerKey) {
  if (["olci-true-color", "chlorophyll", "suspended-matter", "water-temperature"].includes(layer)) return 9;
  if (["shoreline", "water-quality", "vegetation", "coast-moisture", "soil-stress"].includes(layer)) return 10;
  return 11;
}

function updateAnnualTiles(map: MapLibreMap, year: number, layer: LayerKey, version: number) {
  const ids = ["annual-photo-overview", "annual-photo-tiles", "annual-filter-overview", "annual-filter-tiles", "monthly-frame"];
  for (const id of ids) {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  }

  const [west, south, east, north] = CASPIAN_BBOX;
  const coordinates: [[number, number], [number, number], [number, number], [number, number]] = [
    [west, north], [east, north], [east, south], [west, south],
  ];

  // Every annual product uses the same bbox and exact pixel grid.  True colour
  // carries the measured yearly water extent at basin scale and fades into the
  // same HD satellite pyramid at detailed zooms, so the image never jumps to a
  // different acquisition footprint.
  {
    map.addSource("annual-filter-overview", {
      type: "image",
      url: annualOverviewUrl(year, layer, version),
      coordinates,
    });
    map.addLayer({
      id: "annual-filter-overview",
      type: "raster",
      source: "annual-filter-overview",
      maxzoom: 24,
      paint: {
        "raster-opacity": layer === "true-color"
          ? ["interpolate", ["linear"], ["zoom"], 3, 0.96, 5.8, 0.90, 6.5, 0.34, 7, 0]
          : 0.86,
        "raster-fade-duration": 0,
        "raster-resampling": "linear",
      },
    }, "place-labels");
  }

  if (layer !== "true-color") {
    map.addSource("annual-filter-tiles", {
      type: "raster",
      tiles: [annualTileUrl(year, layer, version)],
      tileSize: 256,
      minzoom: 24,
      maxzoom: nativeTileMaxZoom(layer),
      bounds: CASPIAN_BBOX,
    });
    map.addLayer({
      id: "annual-filter-tiles",
      type: "raster",
      source: "annual-filter-tiles",
      paint: { "raster-opacity": 0.84, "raster-fade-duration": 0, "raster-resampling": "linear" },
    }, "place-labels");
  }
}

function updateMonthlyFrame(map: MapLibreMap, year: number, month: number, version: number) {
  for (const id of ["annual-photo-overview", "annual-photo-tiles", "annual-filter-overview", "annual-filter-tiles", "monthly-frame"]) {
    if (map.getLayer(id)) map.removeLayer(id);
    if (map.getSource(id)) map.removeSource(id);
  }
  const [west, south, east, north] = CASPIAN_BBOX;
  map.addSource("monthly-frame", {
    type: "image",
    url: monthlyOverviewUrl(year, month, version),
    coordinates: [[west, north], [east, north], [east, south], [west, south]],
  });
  map.addLayer({
    id: "monthly-frame",
    type: "raster",
    source: "monthly-frame",
    paint: { "raster-opacity": 1, "raster-fade-duration": 180, "raster-resampling": "linear" },
  }, "place-labels");
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
  const swipeDraggingRef = useRef(false);

  const [workspaceMode, setWorkspaceMode] = useState<WorkspaceMode>("monitoring");
  const [sidebarSection, setSidebarSection] = useState<SidebarSection>("water");
  const [activeView, setActiveView] = useState<ViewKey>("optical");
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
  const tileVersion = 25;
  const [timelapseFromYear, setTimelapseFromYear] = useState(2020);
  const [timelapseToYear, setTimelapseToYear] = useState(2026);
  const [timelapseYear, setTimelapseYear] = useState(2020);
  const [timelapseMonth, setTimelapseMonth] = useState(1);
  const [timelapsePlaying, setTimelapsePlaying] = useState(false);
  const [aoiScreen, setAoiScreen] = useState<AoiScreen | null>(null);
  const [trendStatus, setTrendStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [trendResult, setTrendResult] = useState<TrendResult | null>(null);
  const [aiStatus, setAiStatus] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [aiResult, setAiResult] = useState<AiResult | null>(null);

  const activeFilter = useMemo(() => filters.find((item) => item.id === activeView) ?? filters[0], [activeView]);
  const visibleFilters = useMemo(() => {
    const ids = sidebarSection === "land" ? LAND_FILTERS : WATER_FILTERS;
    return ids.map((id) => filters.find((item) => item.id === id)).filter((item): item is FilterDefinition => Boolean(item));
  }, [sidebarSection]);
  const selectedArea = aoi ? bboxAreaKm2(aoi) : null;
  const trendMetric = activeView === "vegetation" ? "vegetation" : ["soil", "coastMoisture", "erosion"].includes(activeView) ? "soilStress" : "waterShare";
  const overlaySlope = trendMetric === "waterShare" ? trendResult?.slopes.waterShare : trendMetric === "vegetation" ? trendResult?.slopes.vegetation : trendResult?.slopes.soilStress;
  const overlayAdverse = trendMetric === "soilStress" ? (overlaySlope ?? 0) > 0 : (overlaySlope ?? 0) < 0;
  const overlayColor = workspaceMode === "solutions" ? trendResult ? overlayAdverse ? "#ef5b4d" : "#20a46b" : "#f0a72f" : "#087dac";

  useEffect(() => {
    if (!mapNode.current || !compareMapNode.current || mapRef.current || compareMapRef.current) return;
    const options: Omit<MapOptions, "container"> = {
      center: [51.2, 41.8] as [number, number],
      // The local XYZ pyramid starts at z5. Keeping the map at or above that
      // level guarantees a real cached raster is always visible; the whole
      // Caspian still fits inside the tall monitoring viewport at z5.
      zoom: 5,
      minZoom: 5,
      maxZoom: 16,
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
      if (primary.isStyleLoaded()) updateMonthlyFrame(primary, timelapseYear, timelapseMonth, tileVersion);
      return;
    }
    updateAnnualTiles(primary, compareEnabled ? beforeYear : afterYear, activeFilter.layer, tileVersion);
    updateAnnualTiles(comparison, afterYear, activeFilter.layer, tileVersion);
  }, [activeFilter.layer, afterYear, beforeYear, compareEnabled, mapsReady, tileVersion, timelapseMonth, timelapseYear, workspaceMode]);

  useEffect(() => {
    if (!timelapsePlaying || workspaceMode !== "solutions") return;
    const timer = setInterval(() => {
      setTimelapseMonth((month) => {
        const maxMonth = timelapseYear === 2026 ? 8 : 12;
        if (month < maxMonth) return month + 1;
        if (timelapseYear < timelapseToYear) {
          setTimelapseYear((year) => year + 1);
          return 1;
        }
        setTimelapsePlaying(false);
        return month;
      });
    }, 380);
    return () => clearInterval(timer);
  }, [timelapsePlaying, timelapseToYear, timelapseYear, workspaceMode]);

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

    const slope = trendMetric === "waterShare" ? trendResult?.slopes.waterShare : trendMetric === "vegetation" ? trendResult?.slopes.vegetation : trendResult?.slopes.soilStress;
    const adverse = trendMetric === "soilStress" ? (slope ?? 0) > 0 : (slope ?? 0) < 0;
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
  }

  function toggleTimelapse() {
    if (timelapsePlaying) {
      setTimelapsePlaying(false);
      return;
    }
    const lastMonth = timelapseToYear === 2026 ? 8 : 12;
    if (timelapseYear < timelapseFromYear || timelapseYear > timelapseToYear || (timelapseYear === timelapseToYear && timelapseMonth >= lastMonth)) {
      setTimelapseYear(timelapseFromYear);
      setTimelapseMonth(1);
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
    if (section === "water" && !WATER_FILTERS.includes(activeView)) setActiveView("optical");
    if (section === "land" && !LAND_FILTERS.includes(activeView)) setActiveView("optical");
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

  function updateSwipeAt(clientX: number) {
    const stage = mapNode.current?.parentElement;
    if (!stage) return;
    const bounds = stage.getBoundingClientRect();
    setSwipe(Math.max(3, Math.min(97, (clientX - bounds.left) / bounds.width * 100)));
  }

  function captureAoiImage() {
    if (!aoiScreen || !mapRef.current) return null;
    const sources = compareEnabled && compareMapRef.current
      ? [mapRef.current.getCanvas(), compareMapRef.current.getCanvas()]
      : [mapRef.current.getCanvas()];
    const first = sources[0];
    if (!first.clientWidth || !first.clientHeight || aoiScreen.width < 2 || aoiScreen.height < 2) return null;

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
    if (!context) return null;
    context.fillStyle = "#102b35";
    context.fillRect(0, 0, output.width, output.height);
    crop.forEach((item, index) => {
      context.drawImage(item.source, item.x, item.y, item.width, item.height, index * frameWidth, 0, frameWidth, frameHeight);
    });
    return output.toDataURL("image/jpeg", 0.84);
  }

  async function runAiAnalysis() {
    if (!aoi || !selectedArea) return;
    setTrendStatus("loading");
    setAiStatus("loading");
    setTrendResult(null);
    setAiResult(null);
    try {
      const trendResponse = await fetch("/api/sentinel/trend", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ bbox: aoi }),
      });
      if (!trendResponse.ok) throw new Error(await trendResponse.text());
      const trend = await trendResponse.json() as TrendResult;
      setTrendResult(trend);
      setTrendStatus("ready");

      const aiResponse = await fetch("/api/ai/analyze", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          filter: activeFilter.label,
          filterDescription: activeFilter.explanation,
          areaKm2: selectedArea,
          beforeYear,
          afterYear,
          imageDataUrl: captureAoiImage(),
          forecast: { ...trend.forecast, slopes: trend.slopes, confidence: trend.confidence, method: trend.method },
        }),
      });
      if (!aiResponse.ok) throw new Error(await aiResponse.text());
      const payload = await aiResponse.json() as { analysis: AiResult };
      setAiResult(payload.analysis);
      setAiStatus("ready");
    } catch {
      setTrendStatus((value) => value === "ready" ? value : "error");
      setAiStatus("error");
    }
  }

  return (
    <main className={`workspace-shell ${inspectorOpen ? "" : "inspector-closed"}`} data-theme={theme}>
      <header className="app-header">
        <div className="brand-lockup"><div className="brand-symbol"><Waves size={20} /></div><div><strong>Nautikos</strong><span>Экологический интеллект Каспия</span></div></div>
        <nav className="main-nav" aria-label="Разделы платформы">
          <button className={workspaceMode === "monitoring" ? "active" : ""} onClick={() => { setWorkspaceMode("monitoring"); setTimelapsePlaying(false); }}>Мониторинг</button>
          <button className={workspaceMode === "solutions" ? "active" : ""} onClick={() => { setWorkspaceMode("solutions"); if (aoi && trendStatus === "idle") void runAiAnalysis(); }}>Решения</button>
        </nav>
        <div className="header-actions">
          <button aria-label={theme === "light" ? "Включить тёмную тему" : "Включить светлую тему"} title={theme === "light" ? "Тёмная тема" : "Светлая тема"} onClick={() => setTheme((value) => value === "light" ? "dark" : "light")}>{theme === "light" ? <Moon size={18} /> : <Sun size={18} />}</button>
          <button aria-label={inspectorOpen ? "Скрыть правую панель" : "Открыть правую панель"} title={inspectorOpen ? "Скрыть панель" : "Открыть панель"} onClick={() => setInspectorOpen((value) => !value)}>{inspectorOpen ? <PanelRightClose size={18} /> : <PanelRightOpen size={18} />}</button>
        </div>
      </header>

      <aside className="filter-panel">
        <div className="panel-heading"><span>КАСПИЙ · РАБОЧИЕ СЛОИ</span><h1>{sidebarSection === "water" ? "Вода" : sidebarSection === "land" ? "Берег и суша" : "Инструменты"}</h1></div>
        <div className="sidebar-tabs" role="tablist" aria-label="Группы слоёв">
          <button className={sidebarSection === "water" ? "active" : ""} onClick={() => selectSidebar("water")}><Droplets size={15} /><span>Вода</span></button>
          <button className={sidebarSection === "land" ? "active" : ""} onClick={() => selectSidebar("land")}><Leaf size={15} /><span>Берег</span></button>
          <button className={sidebarSection === "tools" ? "active" : ""} onClick={() => selectSidebar("tools")}><ScanSearch size={15} /><span>Инструменты</span></button>
        </div>
        {sidebarSection !== "tools" ? (
          <div className="filter-list grouped">
            {visibleFilters.map((item, index) => {
              const Icon = item.icon;
              const showGroupTitle = index === 0 || (sidebarSection === "water" && index === 4) || (sidebarSection === "land" && index === 1);
              const groupTitle = index === 0 ? (sidebarSection === "water" ? "СНИМКИ И РАДАР ВОДЫ" : "ИСХОДНЫЙ СНИМОК") : "ЭКОЛОГИЧЕСКИЕ ПОКАЗАТЕЛИ";
              return <div className="filter-entry" key={item.id}>{showGroupTitle && <span className="filter-group-title">{groupTitle}</span>}<button className={item.id === activeView ? "active" : ""} onClick={() => selectFilter(item.id)}><span className="filter-icon"><Icon size={17} /></span><span><strong>{item.label}</strong><small>{item.subtitle}</small></span></button></div>;
            })}
          </div>
        ) : (
          <div className="sidebar-tools">
            <label><span>РАБОЧИЙ РАЙОН</span><select value={selectedRegion} onChange={(event) => changeRegion(event.target.value)}>{regions.map((region) => <option key={region.id} value={region.id}>{region.name}</option>)}</select></label>
            <button className={drawing ? "active" : ""} onClick={drawing ? cancelDraw : beginDraw}>{drawing ? <X size={17} /> : <BoxSelect size={17} />}<span><strong>{drawing ? "Отменить выделение" : "Выбрать рабочую область"}</strong><small>Для площади, воды или AI</small></span></button>
            <button onClick={() => changeRegion("all")}><Focus size={17} /><span><strong>Показать весь Каспий</strong><small>Вернуть общий обзор</small></span></button>
            {aoi && <button onClick={clearAoi}><X size={17} /><span><strong>Очистить область</strong><small>{selectedArea ? formatArea(selectedArea) : ""}</small></span></button>}
          </div>
        )}
      </aside>

      <section className="map-workspace">
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
          <div className="timelapse-head"><div><span>АРХИВ {timelapseFromYear}–{timelapseToYear}</span><strong>{MONTHS[timelapseMonth - 1]} {timelapseYear}</strong></div><button onClick={toggleTimelapse}>{timelapsePlaying ? "Ⅱ" : "▶"}<span>{timelapsePlaying ? "Пауза" : "Запустить период"}</span></button></div>
          <div className="timelapse-range"><label><span>С</span><select aria-label="Начальный год таймлапса" value={timelapseFromYear} onChange={(event) => { const year = Number(event.target.value); setTimelapseFromYear(year); setTimelapseYear(year); setTimelapseMonth(1); setTimelapsePlaying(false); }}>{YEARS.map((year) => <option key={year} value={year} disabled={year > timelapseToYear}>{year}</option>)}</select></label><span>→</span><label><span>ПО</span><select aria-label="Конечный год таймлапса" value={timelapseToYear} onChange={(event) => { const year = Number(event.target.value); setTimelapseToYear(year); if (timelapseYear > year) { setTimelapseYear(timelapseFromYear); setTimelapseMonth(1); } setTimelapsePlaying(false); }}>{YEARS.map((year) => <option key={year} value={year} disabled={year < timelapseFromYear}>{year}</option>)}</select></label></div>
          <div className="timelapse-controls"><input aria-label="Месяц текущего кадра" type="range" min="1" max={timelapseYear === 2026 ? 8 : 12} value={timelapseMonth} onChange={(event) => { setTimelapseMonth(Number(event.target.value)); setTimelapsePlaying(false); }} /><span>{String(timelapseMonth).padStart(2, "0")}</span></div>
        </div>}
        <div className="map-statusbar"><span>{workspaceMode === "solutions" ? `Месячный кадр · ${MONTHS[timelapseMonth - 1]} ${timelapseYear}` : compareEnabled ? `${beforeYear} ↔ ${afterYear} · ${activeFilter.label}` : `${afterYear} · ${activeFilter.label}`}</span><span>Nautikos · локальные продукты Каспия</span></div>
      </section>

      <aside className={`inspector ${inspectorOpen ? "" : "hidden"}`}>
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
                  <button className="ai-run" disabled={trendStatus === "loading" || aiStatus === "loading"} onClick={runAiAnalysis}>{trendStatus === "loading" || aiStatus === "loading" ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}{trendResult ? "Пересчитать сценарий 2027" : "Рассчитать сценарий 2027"}</button>
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
            <section className="aoi-summary"><div><span>ПЛОЩАДЬ</span><strong>{selectedArea ? formatArea(selectedArea) : "—"}</strong></div><div><span>ПЕРИОД</span><strong>2020–2026</strong></div><div className="aoi-actions"><button onClick={exportAoi}><Download size={15} /> GeoJSON</button><button onClick={runAiAnalysis}><Droplets size={15} /> Доля воды</button><button onClick={runAiAnalysis}><Sparkles size={15} /> AI-анализ</button></div></section>
            <section className="analysis-card">
              <div className="analysis-title"><span className="analysis-icon"><Sparkles size={18} /></span><div><span>СПУТНИКОВЫЕ МЕТРИКИ + AI</span><h3>Сценарий на 2027</h3></div></div>
              <p>{activeFilter.explanation}</p>
              <div className="analysis-facts"><span><Check size={14} /> Все 7 лет, одинаковый сезон</span><span><Check size={14} /> Локальные спектральные продукты</span><span><Check size={14} /> Линейный тренд + показатель R²</span></div>
              <button className="ai-run" disabled={trendStatus === "loading" || aiStatus === "loading"} onClick={runAiAnalysis}>{trendStatus === "loading" || aiStatus === "loading" ? <LoaderCircle className="spin" size={15} /> : <TrendingUp size={15} />}{trendStatus === "loading" ? "Считаю ряд 2020–2026…" : aiStatus === "loading" ? "Groq объясняет прогноз…" : "Рассчитать прогноз 2027"}</button>
              {(trendStatus === "error" || aiStatus === "error") && <p className="ai-error">Расчёт не завершён. Карта и фильтры продолжают работать; можно повторить.</p>}
              {trendResult && <div className="prediction-result"><div className="prediction-head"><div><span>ПРОГНОЗ 2027</span><strong>{trendMetric === "waterShare" ? `${((trendResult.forecast.waterShare ?? 0) * 100).toFixed(1)}% воды в области` : trendMetric === "vegetation" ? `NDVI-показатель ${(trendResult.forecast.vegetation ?? 0).toFixed(3)}` : `стресс почвы ${((trendResult.forecast.soilStress ?? 0) * 100).toFixed(1)}%`}</strong></div><b>{Math.round(trendResult.confidence * 100)}% R²</b></div><TrendChart result={trendResult} metric={trendMetric} /><small>{trendResult.method}</small><p>{trendResult.limitation}</p></div>}
              {aiResult && <div className="ai-result"><div className="ai-result-head"><strong>Groq Vision · Qwen 3.6 · AOI + метрики</strong><span className={`risk ${aiResult.risk.replace(" ", "-")}`}>риск: {aiResult.risk}</span></div><p>{aiResult.summary}</p>{aiResult.evidence.length > 0 && <div><strong>Основание</strong><ul>{aiResult.evidence.map((item) => <li key={item}>{item}</li>)}</ul></div>}{aiResult.nextSteps.length > 0 && <div><strong>Следующие шаги</strong><ul>{aiResult.nextSteps.map((item) => <li key={item}>{item}</li>)}</ul></div>}<small>{aiResult.limitation}</small></div>}
            </section>
            <section className="scene-card"><strong>Сопоставимые продукты</strong><div><span>{beforeYear}</span><code>CASPIAN-{beforeYear}-{activeFilter.layer}</code><small>{productPeriod(beforeYear, activeFilter.layer)} · фиксированный продукт</small></div><div><span>{afterYear}</span><code>CASPIAN-{afterYear}-{activeFilter.layer}</code><small>{productPeriod(afterYear, activeFilter.layer)} · фиксированный продукт</small></div></section>
            {activeView === "oil" && <div className="honesty-card warning"><AlertTriangle size={17} /><div><strong>Это фильтр кандидатов</strong><p>Тёмная формация SAR может быть нефтью, штилем или ветровой тенью. Для тревоги нужны ветер, AIS и повторный пролёт.</p></div></div>}
          </div>
        )}
          </>
        )}
      </aside>
    </main>
  );
}
