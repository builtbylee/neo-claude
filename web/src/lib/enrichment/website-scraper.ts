/**
 * Website scraper for Cloudflare Workers.
 *
 * Fetches a URL, strips HTML via regex (no DOM available in Workers),
 * and returns cleaned text suitable for Claude scoring.
 */

export interface ScrapeResult {
  text: string;
  title: string | null;
  ok: boolean;
  error?: string;
}

const TIMEOUT_MS = 6_000;
const MAX_HTML_BYTES = 200_000;
const MIN_TEXT_LENGTH = 100;
const MAX_TEXT_LENGTH = 4_000;

const BLOCK_TAGS =
  /<(script|style|nav|footer|header|noscript|svg|iframe)[^>]*>[\s\S]*?<\/\1>/gi;

const HTML_ENTITIES: Record<string, string> = {
  "&amp;": "&",
  "&lt;": "<",
  "&gt;": ">",
  "&quot;": '"',
  "&#39;": "'",
  "&apos;": "'",
  "&nbsp;": " ",
};

function normalizeUrl(url: string): string {
  let u = url.trim();
  if (!/^https?:\/\//i.test(u)) {
    u = `https://${u}`;
  }
  return u;
}

function htmlToText(html: string): { text: string; title: string | null } {
  // Extract title
  const titleMatch = html.match(/<title[^>]*>([\s\S]*?)<\/title>/i);
  const title = titleMatch ? titleMatch[1].trim() : null;

  let text = html;

  // Remove block-level noise elements
  text = text.replace(BLOCK_TAGS, " ");

  // Remove HTML comments
  text = text.replace(/<!--[\s\S]*?-->/g, " ");

  // Remove all remaining tags
  text = text.replace(/<[^>]+>/g, " ");

  // Decode HTML entities
  text = text.replace(
    /&(?:amp|lt|gt|quot|apos|nbsp|#39);/g,
    (entity) => HTML_ENTITIES[entity] ?? entity,
  );

  // Decode numeric entities
  text = text.replace(/&#(\d+);/g, (_, code) =>
    String.fromCharCode(parseInt(code, 10)),
  );

  // Collapse whitespace
  text = text.replace(/[ \t]+/g, " ");
  text = text.replace(/\n{3,}/g, "\n\n");
  text = text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .join("\n");

  return { text: text.trim(), title };
}

export async function scrapeWebsite(url: string): Promise<ScrapeResult> {
  const normalizedUrl = normalizeUrl(url);

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), TIMEOUT_MS);

  try {
    const response = await fetch(normalizedUrl, {
      signal: controller.signal,
      headers: {
        "User-Agent": "StartupLens/1.0 (investment-scoring-bot)",
        Accept: "text/html,application/xhtml+xml",
      },
      redirect: "follow",
    });

    if (!response.ok) {
      return { text: "", title: null, ok: false, error: `http_${response.status}` };
    }

    const contentType = response.headers.get("content-type") ?? "";
    if (!contentType.includes("text/html") && !contentType.includes("application/xhtml")) {
      return { text: "", title: null, ok: false, error: "not_html" };
    }

    const rawHtml = (await response.text()).slice(0, MAX_HTML_BYTES);
    const { text, title } = htmlToText(rawHtml);

    if (text.length < MIN_TEXT_LENGTH) {
      return { text: "", title, ok: false, error: "insufficient_content" };
    }

    return {
      text: text.slice(0, MAX_TEXT_LENGTH),
      title,
      ok: true,
    };
  } catch (err) {
    const message = err instanceof Error ? err.message : "unknown";
    if (message.includes("abort")) {
      return { text: "", title: null, ok: false, error: "timeout" };
    }
    return { text: "", title: null, ok: false, error: "fetch_failed" };
  } finally {
    clearTimeout(timeout);
  }
}
