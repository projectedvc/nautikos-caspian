type AnalysisRequest = {
  filter?: string;
  filterDescription?: string;
  areaKm2?: number;
  beforeYear?: number;
  afterYear?: number;
  imageDataUrl?: string;
  locationHint?: string;
  forecast?: {
    year?: number;
    waterShare?: number | null;
    vegetation?: number | null;
    soilStress?: number | null;
    confidence?: number;
    method?: string;
    slopes?: { waterShare?: number | null; vegetation?: number | null; soilStress?: number | null };
  };
};

type GroqResponse = {
  choices?: Array<{ message?: { content?: string } }>;
  model?: string;
};

const GROQ_URL = "https://api.groq.com/openai/v1/chat/completions";
const DEFAULT_VISION_MODEL = "qwen/qwen3.6-27b";
const IMAGE_DATA_URL = /^data:image\/(jpeg|png|webp);base64,/;

function extractJson(content: string) {
  const trimmed = content.trim();
  const withoutFence = trimmed
    .replace(/^```(?:json)?\s*/i, "")
    .replace(/\s*```$/, "")
    .trim();
  const start = withoutFence.indexOf("{");
  const end = withoutFence.lastIndexOf("}");
  return JSON.parse(start >= 0 && end > start ? withoutFence.slice(start, end + 1) : withoutFence);
}

export async function POST(request: Request) {
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) return Response.json({ error: "Groq API не настроен" }, { status: 503 });

  let input: AnalysisRequest;
  try {
    input = await request.json() as AnalysisRequest;
  } catch {
    return Response.json({ error: "Некорректный JSON" }, { status: 400 });
  }

  if (!input.filter || !Number.isInteger(input.beforeYear) || !Number.isInteger(input.afterYear)) {
    return Response.json({ error: "Нужны filter, beforeYear и afterYear" }, { status: 400 });
  }

  const imageDataUrl = input.imageDataUrl?.trim();
  if (imageDataUrl && (!IMAGE_DATA_URL.test(imageDataUrl) || imageDataUrl.length > 7_000_000)) {
    return Response.json({ error: "Некорректное или слишком большое изображение AOI" }, { status: 413 });
  }

  const evidence = {
    filter: input.filter.slice(0, 120),
    filterDescription: input.filterDescription?.slice(0, 600) ?? "",
    locationHint: input.locationHint?.slice(0, 160) ?? "побережье Каспийского моря",
    areaKm2: typeof input.areaKm2 === "number" ? Math.round(input.areaKm2 * 10) / 10 : null,
    comparison: {
      beforeYear: input.beforeYear,
      afterYear: input.afterYear,
      product: "локальные фиксированные продукты Sentinel-1/2/3",
      imageAttached: Boolean(imageDataUrl),
      imageLayout: imageDataUrl ? "слева период до, справа период после; при выключенной шторке один год" : null,
    },
    forecast: input.forecast ?? null,
  };

  const userText = [
    "Проведи осторожный геопространственный скрининг выбранной прибрежной зоны Каспия.",
    `Входные данные: ${JSON.stringify(evidence)}.`,
    imageDataUrl
      ? "Изображение является реальным вырезом карты. Опиши только видимые пространственные признаки и отдели наблюдение от гипотезы."
      : "Изображение недоступно: опирайся только на переданные числовые сведения.",
    "Не называй источник загрязнения доказанным. Укажи, что именно следует проверить полевой группой.",
  ].join(" ");

  const userContent = imageDataUrl
    ? [
        { type: "text", text: userText },
        { type: "image_url", image_url: { url: imageDataUrl } },
      ]
    : userText;

  const model = process.env.GROQ_VISION_MODEL ?? process.env.GROQ_MODEL ?? DEFAULT_VISION_MODEL;
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 35_000);

  try {
    const response = await fetch(GROQ_URL, {
      method: "POST",
      headers: {
        authorization: `Bearer ${apiKey}`,
        "content-type": "application/json",
      },
      signal: controller.signal,
      body: JSON.stringify({
        model,
        temperature: 0.1,
        max_completion_tokens: 900,
        response_format: { type: "json_object" },
        messages: [
          {
            role: "system",
            content: [
              "Ты геоаналитик экологического мониторинга Каспийского моря.",
              "Если приложено изображение, анализируй только видимый вырез AOI; не выдумывай координаты, объекты и источники загрязнения.",
              "areaKm2 — площадь выбранной зоны, а не площадь ущерба.",
              "Тёмная SAR-аномалия, изменение цвета воды, отступление береговой линии или стресс растительности — только кандидат для проверки.",
              "Верни строго JSON на русском: {summary: string, risk: 'низкий'|'средний'|'высокий'|'не определён', evidence: string[], nextSteps: string[], limitation: string}.",
              "summary — до 70 слов; evidence и nextSteps — по 2–3 коротких пункта.",
            ].join(" "),
          },
          { role: "user", content: userContent },
        ],
      }),
    });

    if (!response.ok) {
      const detail = (await response.text()).slice(0, 1200);
      return Response.json({ error: "Groq не выполнил анализ", detail }, { status: response.status });
    }

    const payload = await response.json() as GroqResponse;
    const content = payload.choices?.[0]?.message?.content;
    if (!content) return Response.json({ error: "Groq вернул пустой ответ" }, { status: 502 });

    try {
      return Response.json({ analysis: extractJson(content), model: payload.model ?? model, vision: Boolean(imageDataUrl) });
    } catch {
      return Response.json({
        analysis: {
          summary: content,
          risk: "не определён",
          evidence: [],
          nextSteps: ["Повторить анализ зоны", "Проверить исходный спутниковый продукт"],
          limitation: "Модель вернула неструктурированный ответ.",
        },
        model: payload.model ?? model,
        vision: Boolean(imageDataUrl),
      });
    }
  } catch (error) {
    const timedOut = error instanceof Error && error.name === "AbortError";
    return Response.json({ error: timedOut ? "Groq не ответил за 35 секунд" : "Ошибка соединения с Groq" }, { status: 504 });
  } finally {
    clearTimeout(timeout);
  }
}
