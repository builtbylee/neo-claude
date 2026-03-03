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
