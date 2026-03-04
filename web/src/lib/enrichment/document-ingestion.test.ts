import assert from "node:assert/strict";
import test from "node:test";

import * as XLSX from "xlsx";

import { extractTermSheetSignals, ingestDocuments } from "./document-ingestion";

function toB64(input: string | Uint8Array): string {
  if (typeof input === "string") {
    return Buffer.from(input, "utf-8").toString("base64");
  }
  return Buffer.from(input).toString("base64");
}

test("ingestDocuments returns empty payload when no docs provided", async () => {
  const result = await ingestDocuments(undefined);
  assert.equal(result.combinedText, "");
  assert.equal(result.pdfDocuments.length, 0);
  assert.equal(result.parsedDocuments.length, 0);
  assert.equal(result.warnings.length, 0);
});

test("ingestDocuments parses csv into combined text", async () => {
  const csv = "month,revenue\njan,1000\nfeb,1200\n";
  const result = await ingestDocuments([
    {
      name: "metrics.csv",
      mimeType: "text/csv",
      contentBase64: toB64(csv),
      sizeBytes: csv.length,
    },
  ]);

  assert.match(result.combinedText, /metrics\.csv/i);
  assert.match(result.combinedText, /month,revenue/);
  assert.equal(result.pdfDocuments.length, 0);
  assert.equal(result.parsedDocuments.length, 1);
  assert.equal(result.warnings.length, 0);
});

test("ingestDocuments parses xlsx into combined text", async () => {
  const wb = XLSX.utils.book_new();
  const ws = XLSX.utils.aoa_to_sheet([
    ["metric", "value"],
    ["revenue", 250000],
  ]);
  XLSX.utils.book_append_sheet(wb, ws, "Sheet1");
  const bytes = XLSX.write(wb, { bookType: "xlsx", type: "array" }) as Uint8Array;

  const result = await ingestDocuments([
    {
      name: "kpi.xlsx",
      mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      contentBase64: toB64(bytes),
      sizeBytes: bytes.byteLength,
    },
  ]);

  assert.match(result.combinedText, /Sheet: Sheet1/);
  assert.match(result.combinedText, /revenue \| 250000/);
  assert.equal(result.parsedDocuments.length, 1);
});

test("ingestDocuments keeps pdf docs for native Claude processing", async () => {
  const result = await ingestDocuments([
    {
      name: "deck.pdf",
      mimeType: "application/pdf",
      contentBase64: toB64("%PDF-1.4"),
      sizeBytes: 1024,
    },
  ]);

  assert.equal(result.pdfDocuments.length, 1);
  assert.equal(result.pdfDocuments[0].name, "deck.pdf");
  assert.equal(result.parsedDocuments[0].extractedChars, 0);
});

test("extractTermSheetSignals parses core convertible term fields", () => {
  const text = `
    Instrument: SAFE with valuation cap $8m and 20% discount.
    Liquidation preference: 1x non-participating.
    Includes pro rata rights for major investors.
  `;
  const parsed = extractTermSheetSignals(text);
  assert.equal(parsed.valuationCap, 8_000_000);
  assert.equal(parsed.discountRate, 0.2);
  assert.equal(parsed.liquidationPreferenceMultiple, 1);
  assert.equal(parsed.liquidationParticipation, "non_participating");
  assert.equal(parsed.proRataRights, true);
  assert.equal(parsed.confidence, "high");
});

test("extractTermSheetSignals handles sparse text safely", () => {
  const parsed = extractTermSheetSignals("General company overview without legal terms.");
  assert.equal(parsed.valuationCap, null);
  assert.equal(parsed.discountRate, null);
  assert.equal(parsed.confidence, "low");
});
