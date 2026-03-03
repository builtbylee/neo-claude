interface WatchlistCache {
  fetchedAtMs: number;
  entries: Map<string, { source: "ofac_sdn" | "uk_sanctions"; rawName: string }>;
}

export interface SanctionsMatchResult {
  checked: boolean;
  matched: boolean;
  riskLevel: "clear" | "potential_match";
  matchSource: "ofac_sdn" | "uk_sanctions" | null;
  matchName: string | null;
  reason: string;
}

const WATCHLIST_TTL_MS = 12 * 60 * 60 * 1000;
const OFAC_SDN_URL = "https://sanctionslistservice.ofac.treas.gov/api/publicationpreview/exports/sdn.csv";
const UK_SANCTIONS_URL = "https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv";

let cache: WatchlistCache | null = null;

function normalizeName(value: string): string {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function parseCsv(text: string): string[][] {
  const rows: string[][] = [];
  let field = "";
  let row: string[] = [];
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    const next = text[i + 1];

    if (ch === "\"") {
      if (inQuotes && next === "\"") {
        field += "\"";
        i += 1;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (ch === "," && !inQuotes) {
      row.push(field);
      field = "";
      continue;
    }

    if ((ch === "\n" || ch === "\r") && !inQuotes) {
      if (ch === "\r" && next === "\n") i += 1;
      row.push(field);
      rows.push(row);
      row = [];
      field = "";
      continue;
    }

    field += ch;
  }

  if (field.length > 0 || row.length > 0) {
    row.push(field);
    rows.push(row);
  }
  return rows;
}

function pickNameColumn(headers: string[]): number {
  const normalized = headers.map((h) => h.trim().toLowerCase());
  const candidates = ["name", "name 6", "entity_name", "full_name"];
  for (const c of candidates) {
    const idx = normalized.indexOf(c);
    if (idx >= 0) return idx;
  }
  for (let i = 0; i < normalized.length; i++) {
    if (normalized[i].includes("name")) return i;
  }
  return 0;
}

async function fetchWatchlist(
  url: string,
  source: "ofac_sdn" | "uk_sanctions",
): Promise<Array<{ source: "ofac_sdn" | "uk_sanctions"; name: string }>> {
  const response = await fetch(url, { method: "GET" });
  if (!response.ok) return [];
  const csvText = await response.text();
  const rows = parseCsv(csvText);
  if (rows.length < 2) return [];
  const nameColumn = pickNameColumn(rows[0]);
  const result: Array<{ source: "ofac_sdn" | "uk_sanctions"; name: string }> = [];
  for (const row of rows.slice(1)) {
    const raw = row[nameColumn]?.trim();
    if (!raw) continue;
    result.push({ source, name: raw });
  }
  return result;
}

async function ensureWatchlistLoaded(): Promise<WatchlistCache | null> {
  if (cache && Date.now() - cache.fetchedAtMs < WATCHLIST_TTL_MS) {
    return cache;
  }
  try {
    const [ofac, uk] = await Promise.all([
      fetchWatchlist(OFAC_SDN_URL, "ofac_sdn"),
      fetchWatchlist(UK_SANCTIONS_URL, "uk_sanctions"),
    ]);
    const entries = new Map<string, { source: "ofac_sdn" | "uk_sanctions"; rawName: string }>();
    for (const row of [...ofac, ...uk]) {
      const key = normalizeName(row.name);
      if (!key) continue;
      if (!entries.has(key)) {
        entries.set(key, { source: row.source, rawName: row.name });
      }
    }
    cache = { fetchedAtMs: Date.now(), entries };
    return cache;
  } catch {
    return null;
  }
}

export async function screenNameAgainstSanctions(
  candidateName: string,
): Promise<SanctionsMatchResult> {
  const normalized = normalizeName(candidateName);
  if (!normalized) {
    return {
      checked: false,
      matched: false,
      riskLevel: "clear",
      matchSource: null,
      matchName: null,
      reason: "No candidate name provided for sanctions screening.",
    };
  }

  const watchlist = await ensureWatchlistLoaded();
  if (!watchlist) {
    return {
      checked: false,
      matched: false,
      riskLevel: "clear",
      matchSource: null,
      matchName: null,
      reason: "Sanctions lists unavailable at scoring time.",
    };
  }

  const hit = watchlist.entries.get(normalized);
  if (!hit) {
    return {
      checked: true,
      matched: false,
      riskLevel: "clear",
      matchSource: null,
      matchName: null,
      reason: "No exact sanctions-list match on company name.",
    };
  }

  return {
    checked: true,
    matched: true,
    riskLevel: "potential_match",
    matchSource: hit.source,
    matchName: hit.rawName,
    reason: "Exact normalized name match found on sanctions list.",
  };
}

