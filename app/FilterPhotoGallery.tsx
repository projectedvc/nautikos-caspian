"use client";

import {
  Droplets,
  Gauge,
  Leaf,
  ScanLine,
  ThermometerSun,
  Waves,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  type LucideIcon,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import "./filter-photo-gallery.css";

const YEARS = [2020, 2021, 2022, 2023, 2024, 2025, 2026] as const;

type FrameState = "loading" | "ready" | "missing";

type FilterProduct = {
  id: string;
  title: string;
  shortTitle: string;
  description: string;
  sensor: string;
  method: string;
  resolution: string;
  limitation: string;
  icon: LucideIcon;
  palette: readonly { color: string; label: string }[];
};

const PRODUCTS: readonly FilterProduct[] = [
  {
    id: "rivers",
    title: "Реки и водотоки",
    shortTitle: "Водотоки",
    description: "Выделяет воду, влажные русла и места соединения рек с Каспием.",
    sensor: "Sentinel-2 MSI L2A",
    method: "MNDWI + NIR/SWIR false colour",
    resolution: "10–20 м",
    limitation: "Индекс показывает спектральный сигнал воды, а не лабораторный состав стока.",
    icon: Droplets,
    palette: [
      { color: "#172a63", label: "глубокая вода" },
      { color: "#20b7d6", label: "мелководье / русло" },
      { color: "#e34b43", label: "растительный покров" },
    ],
  },
  {
    id: "shoreline",
    title: "Обмеление берега",
    shortTitle: "Обмеление",
    description: "Показывает положение границы воды и зоны, вышедшие из воды между годами.",
    sensor: "Sentinel-2 MSI L2A",
    method: "MNDWI shoreline classification",
    resolution: "10–20 м",
    limitation: "На границу влияют сезон, ветер и уровень воды; сравниваются одинаковые месяцы.",
    icon: Waves,
    palette: [
      { color: "#176fa2", label: "вода" },
      { color: "#f3bd2f", label: "граница берега" },
      { color: "#e7513c", label: "осушенная зона" },
    ],
  },
  {
    id: "coastal-vegetation",
    title: "Растительность берегов",
    shortTitle: "Растительность",
    description: "Сравнивает состояние растительного покрова в прибрежной полосе и дельтах.",
    sensor: "Sentinel-2 MSI L2A",
    method: "NDVI + red-edge composite",
    resolution: "10–20 м",
    limitation: "NDVI отражает активность растительности, но сам по себе не определяет её вид.",
    icon: Leaf,
    palette: [
      { color: "#6f2e91", label: "слабый покров" },
      { color: "#d6db3d", label: "умеренный" },
      { color: "#20a45a", label: "активный покров" },
    ],
  },
  {
    id: "oil-slicks",
    title: "Плёнки и отходы на воде",
    shortTitle: "Плёнки на воде",
    description: "Ищет участки аномально гладкой водной поверхности для приоритетной проверки.",
    sensor: "Sentinel-1 SAR GRD",
    method: "VV/VH backscatter anomaly",
    resolution: "10 м",
    limitation: "Это кандидаты: штиль, водоросли и природные плёнки могут выглядеть похоже на нефть.",
    icon: ScanLine,
    palette: [
      { color: "#162840", label: "обычная вода" },
      { color: "#f0ae2c", label: "аномалия" },
      { color: "#e43d38", label: "приоритет проверки" },
    ],
  },
  {
    id: "water-temperature",
    title: "Температура поверхности воды",
    shortTitle: "Температура",
    description: "Показывает крупномасштабные температурные аномалии поверхности Каспия.",
    sensor: "Sentinel-3 SLSTR L2 WST",
    method: "Water Surface Temperature",
    resolution: "≈ 1 км",
    limitation: "Тепловой продукт не предназначен для детализации отдельных объектов у берега.",
    icon: ThermometerSun,
    palette: [
      { color: "#2448b7", label: "холоднее" },
      { color: "#43d2c5", label: "средняя" },
      { color: "#ed4b35", label: "теплее" },
    ],
  },
  {
    id: "water-colour",
    title: "Цвет и качество воды",
    shortTitle: "Цвет воды",
    description: "Визуализирует оптические различия воды, связанные со взвесью и цветением.",
    sensor: "Sentinel-3 OLCI L2 WFR",
    method: "True colour + TSM/CHL indicators",
    resolution: "300 м",
    limitation: "Спутниковый цвет воды — индикатор; тип загрязнителя подтверждается пробой.",
    icon: Gauge,
    palette: [
      { color: "#2249a1", label: "чистая / глубокая" },
      { color: "#13c7ae", label: "повышенный сигнал" },
      { color: "#f1b92b", label: "сильная аномалия" },
    ],
  },
] as const;

const FRAME_FILES: Record<string, string> = {
  rivers: "vegetation.webp",
  shoreline: "true-color.webp",
  "coastal-vegetation": "vegetation.webp",
  "oil-slicks": "oil-roughness.webp",
  "water-temperature": "water-temperature.webp",
  "water-colour": "olci-true-color.webp",
};

function frameUrl(product: string, year: number) {
  return `/overviews/annual/${year}/${FRAME_FILES[product] ?? "true-color.webp"}`;
}

function FrameFallback({ side, year, product }: { side: "before" | "after"; year: number; product: string }) {
  return (
    <div className={`filter-gallery__frame-fallback filter-gallery__frame-fallback--${side}`}>
      <div>
        <ScanLine aria-hidden="true" size={28} />
        <strong>Кадр {year} ещё не опубликован</strong>
        <span>{frameUrl(product, year)}</span>
      </div>
    </div>
  );
}

export default function FilterPhotoGallery() {
  const [productId, setProductId] = useState(PRODUCTS[0].id);
  const [beforeYear, setBeforeYear] = useState<number>(2020);
  const [afterYear, setAfterYear] = useState<number>(2026);
  const [swipe, setSwipe] = useState(50);
  const [frameStates, setFrameStates] = useState<Record<string, FrameState>>({});
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const viewportRef = useRef<HTMLDivElement>(null);
  const panRef = useRef<{ pointerId: number; startX: number; startY: number; originX: number; originY: number } | null>(null);

  const product = useMemo(
    () => PRODUCTS.find((item) => item.id === productId) ?? PRODUCTS[0],
    [productId],
  );
  const beforeUrl = frameUrl(product.id, beforeYear);
  const afterUrl = frameUrl(product.id, afterYear);
  const beforeState = frameStates[beforeUrl] ?? "loading";
  const afterState = frameStates[afterUrl] ?? "loading";

  useEffect(() => {
    setFrameStates((current) => ({ ...current, [beforeUrl]: "loading", [afterUrl]: "loading" }));
    setZoom(1);
    setPan({ x: 0, y: 0 });
  }, [beforeUrl, afterUrl]);

  const setFrameState = (url: string, state: FrameState) => {
    setFrameStates((current) => (current[url] === state ? current : { ...current, [url]: state }));
  };

  const updateSwipe = (clientX: number) => {
    const bounds = viewportRef.current?.getBoundingClientRect();
    if (!bounds?.width) return;
    setSwipe(Math.min(96, Math.max(4, ((clientX - bounds.left) / bounds.width) * 100)));
  };

  const startSwipe = (event: React.PointerEvent<HTMLDivElement>) => {
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    updateSwipe(event.clientX);
  };

  const moveSwipe = (event: React.PointerEvent<HTMLDivElement>) => {
    event.stopPropagation();
    if (event.currentTarget.hasPointerCapture(event.pointerId)) updateSwipe(event.clientX);
  };

  const startPan = (event: React.PointerEvent<HTMLDivElement>) => {
    if ((event.target as HTMLElement).closest("button, select, input, .filter-gallery__divider")) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    panRef.current = { pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, originX: pan.x, originY: pan.y };
  };

  const movePan = (event: React.PointerEvent<HTMLDivElement>) => {
    const drag = panRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !event.currentTarget.hasPointerCapture(event.pointerId)) return;
    setPan({ x: drag.originX + event.clientX - drag.startX, y: drag.originY + event.clientY - drag.startY });
  };

  const stopPan = (event: React.PointerEvent<HTMLDivElement>) => {
    if (panRef.current?.pointerId === event.pointerId) panRef.current = null;
    if (event.currentTarget.hasPointerCapture(event.pointerId)) event.currentTarget.releasePointerCapture(event.pointerId);
  };

  const changeZoom = (next: number) => {
    const value = Math.min(4, Math.max(1, next));
    setZoom(value);
    if (value === 1) setPan({ x: 0, y: 0 });
  };

  const handleWheel = (event: React.WheelEvent<HTMLDivElement>) => {
    event.preventDefault();
    changeZoom(zoom + (event.deltaY < 0 ? 0.25 : -0.25));
  };

  const imageTransform = { transform: `translate3d(${pan.x}px, ${pan.y}px, 0) scale(${zoom})` };

  const handleSwipeKey = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.key === "ArrowLeft") {
      event.preventDefault();
      setSwipe((value) => Math.max(4, value - 2));
    }
    if (event.key === "ArrowRight") {
      event.preventDefault();
      setSwipe((value) => Math.min(96, value + 2));
    }
    if (event.key === "Home") setSwipe(4);
    if (event.key === "End") setSwipe(96);
  };

  return (
    <section className="filter-photo-gallery" aria-label="Архивные фильтры Каспия">
      <aside className="filter-gallery__catalog">
        <div className="filter-gallery__catalog-head">
          <span>Локальный архив 2020–2026</span>
          <h2>Фильтры Каспия</h2>
          <p>Готовые кадры одного охвата без обращения к спутниковому API при просмотре.</p>
        </div>

        <nav className="filter-gallery__products" aria-label="Выбор фильтра">
          {PRODUCTS.map((item) => {
            const Icon = item.icon;
            return (
              <button
                key={item.id}
                type="button"
                className={item.id === product.id ? "active" : ""}
                aria-pressed={item.id === product.id}
                onClick={() => setProductId(item.id)}
              >
                <span className="filter-gallery__product-icon"><Icon size={19} aria-hidden="true" /></span>
                <span><strong>{item.shortTitle}</strong><small>{item.sensor}</small></span>
              </button>
            );
          })}
        </nav>

        <div className="filter-gallery__source-note">
          <strong>Что показано</strong>
          <span>{product.method}</span>
          <span>Номинальное разрешение: {product.resolution}</span>
        </div>
      </aside>

      <div className="filter-gallery__stage">
        <header className="filter-gallery__toolbar">
          <div className="filter-gallery__title">
            <span>{product.sensor}</span>
            <h1>{product.title}</h1>
            <p>{product.description}</p>
          </div>
          <div className="filter-gallery__year-controls" aria-label="Годы сравнения">
            <label>
              <span>Раньше</span>
              <select value={beforeYear} onChange={(event) => setBeforeYear(Number(event.target.value))}>
                {YEARS.map((year) => <option key={year} value={year} disabled={year >= afterYear}>{year}</option>)}
              </select>
            </label>
            <span className="filter-gallery__year-arrow" aria-hidden="true">→</span>
            <label>
              <span>Позже</span>
              <select value={afterYear} onChange={(event) => setAfterYear(Number(event.target.value))}>
                {YEARS.map((year) => <option key={year} value={year} disabled={year <= beforeYear}>{year}</option>)}
              </select>
            </label>
          </div>
        </header>

        <div
          ref={viewportRef}
          className="filter-gallery__viewport"
          onPointerDown={startPan}
          onPointerMove={movePan}
          onPointerUp={stopPan}
          onPointerCancel={stopPan}
          onWheel={handleWheel}
        >
          <div className="filter-gallery__frame filter-gallery__frame--before">
            {beforeState !== "missing" && (
              <img
                key={beforeUrl}
                src={beforeUrl}
                alt={`${product.title}, ${beforeYear}`}
                draggable={false}
                style={imageTransform}
                onLoad={() => setFrameState(beforeUrl, "ready")}
                onError={() => setFrameState(beforeUrl, "missing")}
              />
            )}
            {beforeState === "missing" && <FrameFallback side="before" year={beforeYear} product={product.id} />}
          </div>

          <div className="filter-gallery__frame filter-gallery__frame--after" style={{ clipPath: `inset(0 0 0 ${swipe}%)` }}>
            {afterState !== "missing" && (
              <img
                key={afterUrl}
                src={afterUrl}
                alt={`${product.title}, ${afterYear}`}
                draggable={false}
                style={imageTransform}
                onLoad={() => setFrameState(afterUrl, "ready")}
                onError={() => setFrameState(afterUrl, "missing")}
              />
            )}
            {afterState === "missing" && <FrameFallback side="after" year={afterYear} product={product.id} />}
          </div>

          {(beforeState === "loading" || afterState === "loading") && (
            <div className="filter-gallery__loading" role="status">
              <span aria-hidden="true" />
              Открываю локальные кадры…
            </div>
          )}

          <div className="filter-gallery__frame-label filter-gallery__frame-label--before">
            <span>Раньше</span><strong>{beforeYear}</strong>
          </div>
          <div className="filter-gallery__frame-label filter-gallery__frame-label--after">
            <span>Позже</span><strong>{afterYear}</strong>
          </div>

          <div
            className="filter-gallery__divider"
            style={{ left: `${swipe}%` }}
            role="slider"
            tabIndex={0}
            aria-label="Положение шторки сравнения"
            aria-valuemin={4}
            aria-valuemax={96}
            aria-valuenow={Math.round(swipe)}
            onPointerDown={startSwipe}
            onPointerMove={moveSwipe}
            onKeyDown={handleSwipeKey}
          >
            <span aria-hidden="true">↔</span>
          </div>

          <div className="filter-gallery__map-controls" aria-label="Масштаб изображения">
            <button type="button" aria-label="Увеличить" title="Увеличить" onClick={() => changeZoom(zoom + 0.25)}><ZoomIn size={17} /></button>
            <button type="button" aria-label="Уменьшить" title="Уменьшить" disabled={zoom <= 1} onClick={() => changeZoom(zoom - 0.25)}><ZoomOut size={17} /></button>
            <button type="button" aria-label="Сбросить положение" title="Сбросить положение" disabled={zoom === 1 && pan.x === 0 && pan.y === 0} onClick={() => { setZoom(1); setPan({ x: 0, y: 0 }); }}><RotateCcw size={16} /></button>
            <span>{Math.round(zoom * 100)}%</span>
          </div>

          <div className="filter-gallery__legend">
            <strong>{product.shortTitle}</strong>
            <div>{product.palette.map((item) => <span key={item.label}><i style={{ background: item.color }} />{item.label}</span>)}</div>
          </div>
        </div>

        <footer className="filter-gallery__footer">
          <div className="filter-gallery__range">
            <span>{beforeYear}</span>
            <input
              type="range"
              min={4}
              max={96}
              value={swipe}
              aria-label="Шторка сравнения"
              onChange={(event) => setSwipe(Number(event.target.value))}
            />
            <span>{afterYear}</span>
          </div>
          <p><strong>Важно:</strong> {product.limitation}</p>
        </footer>
      </div>
    </section>
  );
}
