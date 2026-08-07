type AnalysisRequest = {
  filter?: string;
  filterDescription?: string;
  areaKm2?: number;
  beforeYear?: number;
  afterYear?: number;
  imageDataUrl?: string;
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
const IMAGE_DATA_URL = /^data:image\/(jpeg|png|webp);base64,/;

export async function POST(request: Request) {
  const apiKey = process.env.GROQ_API_KEY;
  if (!apiKey) return Response.json({ error: "Groq is not configured" }, { status: 503 });

  let input: AnalysisRequest;
  try {
    input = await request.json() as AnalysisRequest;
  } catch {
    return Response.json({ error: "Invalid JSON" }, { status: 400 });
  }

  if (!input.filter || !Number.isInteger(input.beforeYear) || !Number.isInteger(input.afterYear)) {
    return Response.json({ error: "filter, beforeYear and afterYear are required" }, { status: 400 });
  }
  const imageDataUrl = input.imageDataUrl?.trim();
  if (imageDataUrl && (!IMAGE_DATA_URL.test(imageDataUrl) || imageDataUrl.length > 7_000_000)) {
    return Response.json({ error: "Invalid or oversized AOI image" }, { status: 413 });
  }

  const evidence = {
    filter: input.filter.slice(0, 120),
    filterDescription: input.filterDescription?.slice(0, 600) ?? "",
    areaKm2: typeof input.areaKm2 === "number" ? Math.round(input.areaKm2 * 10) / 10 : null,
    comparison: {
      beforeYear: input.beforeYear,
      afterYear: input.afterYear,
      season: "1–15 июля",
      product: "локальные фиксированные продукты Sentinel-2/Sentinel-1/OLCI",
      imageAttached: Boolean(imageDataUrl),
      imageLayout: imageDataUrl ? "до слева, после справа (либо один выбранный год)" : null,
    },
    forecast: input.forecast ?? null,
  };

  const userText = `Дай проверяемое заключение для интерфейса Nautikos. Данные: ${JSON.stringify(evidence)}. ${imageDataUrl ? "Изображение — реальный вырез выбранной на карте области. Опиши только явно видимые пространственные признаки и сопоставь их с числовым рядом." : "Изображение недоступно: опирайся только на числовые метрики."}`;
  const userContent = imageDataUrl
    ? [
        { type: "text", text: userText },
        { type: "image_url", image_url: { url: imageDataUrl } },
      ]
    : userText;

  const groqPayload = {
    model: process.env.GROQ_MODEL ?? "llama-3.3-70b-versatile",
    temperature: 0.15,
    max_completion_tokens: 850,
    messages: [
      {
        role: "system",
        content: "Ты геоаналитик экологического мониторинга Каспия. Если приложено изображение, ты действительно видишь вырез выбранной AOI: слева период «до», справа «после», либо один кадр. Отделяй визуальное наблюдение от числовой метрики. areaKm2 — площадь выбранной области, не площадь ущерба. Не придумывай дельты, объекты, источники загрязнения или координаты. Нефтяная плёнка, сброс отходов, заболевание растений и деградация почвы — только кандидаты для проверки, не доказанный диагноз. При confidence ниже 0.5 риск указывай как «не определён». Прогноз 2027 называй сценарной экстраполяцией, не будущим снимком. Верни JSON на русском: {summary: string, risk: 'низкий'|'средний'|'высокий'|'не определён', evidence: string[], nextSteps: string[], limitation: string}. summary до 65 слов, списки по 2–3 коротких пункта.",
      },
      { role: "user", content: userContent },
    ],
  };

  const callGroq = (strictJson: boolean) => fetch(GROQ_URL, {
    method: "POST",
    headers: {
      authorization: `Bearer ${apiKey}`,
      "content-type": "application/json",
    },
    body: JSON.stringify({
      ...groqPayload,
      ...(strictJson ? { response_format: { type: "json_object" } } : {}),
    }),
  });

  let response = await callGroq(true);

  if (!response.ok) {
    const detail = await response.text();
    if (response.status === 400 && detail.includes("json_validate_failed")) {
      response = await callGroq(false);
    } else {
      return Response.json({ error: "Groq analysis failed", detail }, { status: response.status });
    }
  }
  if (!response.ok) {
    return Response.json({ error: "Groq analysis failed", detail: await response.text() }, { status: response.status });
  }

  const payload = await response.json() as GroqResponse;
  const content = payload.choices?.[0]?.message?.content;
  if (!content) return Response.json({ error: "Groq returned an empty response" }, { status: 502 });

  try {
    return Response.json({ analysis: JSON.parse(content), model: payload.model ?? "llama-3.3-70b-versatile", vision: Boolean(imageDataUrl) });
  } catch {
    return Response.json({
      analysis: {
        summary: content,
        risk: "не определён",
        evidence: [],
        nextSteps: [],
        limitation: "Ответ модели не был структурирован.",
      },
      model: payload.model ?? "llama-3.3-70b-versatile",
      vision: Boolean(imageDataUrl),
    });
  }
}
