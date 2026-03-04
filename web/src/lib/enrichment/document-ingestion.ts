import mammoth from "mammoth";
import * as XLSX from "xlsx";

export interface UploadedDocument {
  name: string;
  mimeType: string;
  contentBase64: string;
  sizeBytes: number;
}

export interface PreparedPdfDocument {
  name: string;
  contentBase64: string;
}

export interface ParsedDocument {
  name: string;
  mimeType: string;
  extractedChars: number;
}

export interface DocumentIngestionResult {
  combinedText: string;
  pdfDocuments: PreparedPdfDocument[];
  parsedDocuments: ParsedDocument[];
  warnings: string[];
}

export interface TermSheetSignals {
  valuationCap: number | null;
  discountRate: number | null;
  interestRate: number | null;
  maturityDate: string | null;
  liquidationPreferenceMultiple: number | null;
  liquidationParticipation: "non_participating" | "participating" | null;
  proRataRights: boolean | null;
  confidence: "low" | "medium" | "high";
  evidence: string[];
}

const MAX_DOCUMENTS = 3;
const MAX_BYTES_PER_DOCUMENT = 2_000_000;
const MAX_COMBINED_TEXT_CHARS = 8_000;
const MAX_ROWS_PER_SHEET = 120;
const MAX_COLS_PER_ROW = 12;

function extOf(name: string): string {
  const dot = name.lastIndexOf(".");
  if (dot < 0) return "";
  return name.slice(dot + 1).toLowerCase();
}

function isPdf(doc: UploadedDocument): boolean {
  return doc.mimeType.includes("pdf") || extOf(doc.name) === "pdf";
}

function isCsv(doc: UploadedDocument): boolean {
  return doc.mimeType.includes("csv") || extOf(doc.name) === "csv";
}

function isXlsx(doc: UploadedDocument): boolean {
  const ext = extOf(doc.name);
  return (
    doc.mimeType.includes("spreadsheetml") ||
    doc.mimeType.includes("excel") ||
    ext === "xlsx" ||
    ext === "xls"
  );
}

function isDocx(doc: UploadedDocument): boolean {
  const ext = extOf(doc.name);
  return doc.mimeType.includes("wordprocessingml") || ext === "docx";
}

function isDoc(doc: UploadedDocument): boolean {
  return doc.mimeType.includes("msword") || extOf(doc.name) === "doc";
}

function toBytes(base64: string): Uint8Array {
  return Uint8Array.from(Buffer.from(base64, "base64"));
}

function normalizeText(text: string): string {
  return text
    .replace(/\r/g, "")
    .replace(/[ \t]+/g, " ")
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}

function parseScaledNumber(raw: string): number | null {
  const match = raw.trim().match(/^([\d,.]+)\s*(k|m|b|million|billion)?$/i);
  if (!match) return null;
  const base = Number(match[1].replace(/,/g, ""));
  if (!Number.isFinite(base) || base <= 0) return null;
  const unit = (match[2] ?? "").toLowerCase();
  if (unit === "k") return base * 1_000;
  if (unit === "m" || unit === "million") return base * 1_000_000;
  if (unit === "b" || unit === "billion") return base * 1_000_000_000;
  return base;
}

function parsePercent(raw: string): number | null {
  const value = Number(raw);
  if (!Number.isFinite(value) || value <= 0) return null;
  return value > 1 ? value / 100 : value;
}

function parseDateToIso(raw: string): string | null {
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return null;
  return date.toISOString().slice(0, 10);
}

/**
 * Regex-based term-sheet key-field extraction from text.
 * Used as a fallback when structured deal terms are unavailable.
 */
export function extractTermSheetSignals(text: string | null | undefined): TermSheetSignals {
  const input = normalizeText(text ?? "");
  if (!input) {
    return {
      valuationCap: null,
      discountRate: null,
      interestRate: null,
      maturityDate: null,
      liquidationPreferenceMultiple: null,
      liquidationParticipation: null,
      proRataRights: null,
      confidence: "low",
      evidence: [],
    };
  }

  const evidence: string[] = [];

  const valuationCapMatch = input.match(
    /valuation\s+cap(?:\s*(?:of|:|=))?\s*\$?\s*([\d,.]+\s*(?:k|m|b|million|billion)?)/i,
  );
  const valuationCap = valuationCapMatch ? parseScaledNumber(valuationCapMatch[1]) : null;
  if (valuationCapMatch?.[0]) evidence.push(valuationCapMatch[0]);

  const discountMatch =
    input.match(/discount(?:\s+rate)?(?:\s*(?:of|:|=))?\s*(\d{1,2}(?:\.\d+)?)\s*%/i)
    ?? input.match(/(\d{1,2}(?:\.\d+)?)\s*%\s*discount/i);
  const discountRate = discountMatch ? parsePercent(discountMatch[1]) : null;
  if (discountMatch?.[0]) evidence.push(discountMatch[0]);

  const interestMatch =
    input.match(/interest(?:\s+rate)?(?:\s*(?:of|:|=))?\s*(\d{1,2}(?:\.\d+)?)\s*%/i)
    ?? input.match(/(\d{1,2}(?:\.\d+)?)\s*%\s*interest/i);
  const interestRate = interestMatch ? parsePercent(interestMatch[1]) : null;
  if (interestMatch?.[0]) evidence.push(interestMatch[0]);

  const maturityMatch = input.match(
    /matur(?:ity|es?)(?:\s+date)?(?:\s*(?:on|:|=))?\s*([A-Za-z]{3,9}\s+\d{1,2},?\s+\d{4}|\d{4}-\d{2}-\d{2}|\d{1,2}\/\d{1,2}\/\d{2,4})/i,
  );
  const maturityDate = maturityMatch ? parseDateToIso(maturityMatch[1]) : null;
  if (maturityMatch?.[0]) evidence.push(maturityMatch[0]);

  const lpMatch =
    input.match(/(\d(?:\.\d+)?)x\s+liquidation\s+preference/i)
    ?? input.match(/liquidation\s+preference(?:\s*(?:of|:|=))?\s*(\d(?:\.\d+)?)x/i);
  const liquidationPreferenceMultiple = lpMatch ? Number(lpMatch[1]) : null;
  if (lpMatch?.[0]) evidence.push(lpMatch[0]);

  let liquidationParticipation: "non_participating" | "participating" | null = null;
  if (/non[-\s]?participating/i.test(input)) {
    liquidationParticipation = "non_participating";
    const m = input.match(/non[-\s]?participating/i);
    if (m?.[0]) evidence.push(m[0]);
  } else if (/participating/i.test(input)) {
    liquidationParticipation = "participating";
    const m = input.match(/participating/i);
    if (m?.[0]) evidence.push(m[0]);
  }

  let proRataRights: boolean | null = null;
  if (/no\s+pro[-\s]?rata/i.test(input)) {
    proRataRights = false;
    const m = input.match(/no\s+pro[-\s]?rata/i);
    if (m?.[0]) evidence.push(m[0]);
  } else if (/pro[-\s]?rata(?:\s+rights?|\s+participation)?/i.test(input)) {
    proRataRights = true;
    const m = input.match(/pro[-\s]?rata(?:\s+rights?|\s+participation)?/i);
    if (m?.[0]) evidence.push(m[0]);
  }

  const populated = [
    valuationCap,
    discountRate,
    interestRate,
    maturityDate,
    liquidationPreferenceMultiple,
    liquidationParticipation,
    proRataRights,
  ].filter((v) => v !== null).length;

  let confidence: "low" | "medium" | "high" = "low";
  if (populated >= 4) confidence = "high";
  else if (populated >= 2) confidence = "medium";

  return {
    valuationCap,
    discountRate,
    interestRate,
    maturityDate,
    liquidationPreferenceMultiple,
    liquidationParticipation,
    proRataRights,
    confidence,
    evidence: evidence.slice(0, 6),
  };
}

function parseCsv(bytes: Uint8Array): string {
  const raw = new TextDecoder("utf-8", { fatal: false }).decode(bytes);
  return raw
    .split(/\r?\n/)
    .slice(0, 200)
    .map((line) => line.trim())
    .filter((line) => line.length > 0)
    .join("\n");
}

function parseXlsx(bytes: Uint8Array): string {
  const workbook = XLSX.read(bytes, { type: "array", dense: true });
  const chunks: string[] = [];

  for (const sheetName of workbook.SheetNames.slice(0, 2)) {
    const sheet = workbook.Sheets[sheetName];
    if (!sheet) continue;
    const rows = XLSX.utils.sheet_to_json<Array<string | number | boolean | null>>(
      sheet,
      { header: 1, raw: false },
    );
    if (rows.length === 0) continue;

    chunks.push(`Sheet: ${sheetName}`);
    for (const row of rows.slice(0, MAX_ROWS_PER_SHEET)) {
      const line = row
        .slice(0, MAX_COLS_PER_ROW)
        .map((cell) => (cell === null || cell === undefined ? "" : String(cell).trim()))
        .join(" | ")
        .trim();
      if (line) chunks.push(line);
    }
  }

  return chunks.join("\n");
}

async function parseDocx(bytes: Uint8Array): Promise<string> {
  const result = await mammoth.extractRawText({ buffer: Buffer.from(bytes) });
  return result.value;
}

/**
 * Prepare uploaded docs for scoring:
 * - PDF kept as native PDF blocks for Claude
 * - DOCX/CSV/XLSX converted to plain text and appended to profile
 * - Legacy DOC attempted as plain text fallback with warning
 */
export async function ingestDocuments(
  docs: UploadedDocument[] | undefined,
): Promise<DocumentIngestionResult> {
  if (!docs || docs.length === 0) {
    return {
      combinedText: "",
      pdfDocuments: [],
      parsedDocuments: [],
      warnings: [],
    };
  }

  const warnings: string[] = [];
  const parsedDocuments: ParsedDocument[] = [];
  const pdfDocuments: PreparedPdfDocument[] = [];
  const textChunks: string[] = [];

  for (const doc of docs.slice(0, MAX_DOCUMENTS)) {
    if (doc.sizeBytes > MAX_BYTES_PER_DOCUMENT) {
      warnings.push(`${doc.name}: skipped (file larger than 2MB limit)`);
      continue;
    }

    if (isPdf(doc)) {
      pdfDocuments.push({
        name: doc.name,
        contentBase64: doc.contentBase64,
      });
      parsedDocuments.push({
        name: doc.name,
        mimeType: doc.mimeType,
        extractedChars: 0,
      });
      continue;
    }

    try {
      const bytes = toBytes(doc.contentBase64);
      let extracted = "";

      if (isCsv(doc)) {
        extracted = parseCsv(bytes);
      } else if (isXlsx(doc)) {
        extracted = parseXlsx(bytes);
      } else if (isDocx(doc)) {
        extracted = await parseDocx(bytes);
      } else if (isDoc(doc)) {
        extracted = normalizeText(new TextDecoder("utf-8", { fatal: false }).decode(bytes));
        warnings.push(`${doc.name}: legacy .doc parsing may be incomplete; .docx is more reliable`);
      } else {
        warnings.push(`${doc.name}: unsupported file type`);
        continue;
      }

      const normalized = normalizeText(extracted);
      if (!normalized) {
        warnings.push(`${doc.name}: no readable text extracted`);
        continue;
      }

      parsedDocuments.push({
        name: doc.name,
        mimeType: doc.mimeType,
        extractedChars: normalized.length,
      });
      textChunks.push(`Document: ${doc.name}\n${normalized}`);
    } catch {
      warnings.push(`${doc.name}: parsing failed`);
    }
  }

  const combinedText = normalizeText(textChunks.join("\n\n")).slice(
    0,
    MAX_COMBINED_TEXT_CHARS,
  );

  return {
    combinedText,
    pdfDocuments,
    parsedDocuments,
    warnings,
  };
}
