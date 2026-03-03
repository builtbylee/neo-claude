"use client";

import { useState } from "react";

const SECTORS = [
  "Select sector...",
  "Agriculture",
  "Banking and Financial Services",
  "Biotechnology",
  "Business Services",
  "Commercial",
  "Computers",
  "Construction",
  "Energy",
  "Health Care",
  "Insurance",
  "Investing",
  "Manufacturing",
  "Pharmaceuticals",
  "Real Estate",
  "Restaurants",
  "Retailing",
  "Technology",
  "Telecommunications",
  "Travel",
  "Other",
];

interface ScoreFormProps {
  onSubmit: (data: {
    companyName: string;
    websiteUrl: string;
    sector: string;
    revenue: number | undefined;
    fundingTarget: number | undefined;
    pitchText: string;
    documents: Array<{
      name: string;
      mimeType: string;
      contentBase64: string;
      sizeBytes: number;
    }>;
  }) => void;
  isLoading: boolean;
}

const MAX_DOCS = 3;
const MAX_BYTES_PER_DOC = 2_000_000;

function bytesToBase64(bytes: Uint8Array): string {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    const slice = bytes.subarray(i, i + chunk);
    binary += String.fromCharCode(...slice);
  }
  return btoa(binary);
}

export default function ScoreForm({ onSubmit, isLoading }: ScoreFormProps) {
  const [companyName, setCompanyName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [sector, setSector] = useState("");
  const [revenue, setRevenue] = useState("");
  const [fundingTarget, setFundingTarget] = useState("");
  const [pitchText, setPitchText] = useState("");
  const [documents, setDocuments] = useState<File[]>([]);
  const [docError, setDocError] = useState<string | null>(null);
  const [showAdvanced, setShowAdvanced] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setDocError(null);

    const encodedDocs: Array<{
      name: string;
      mimeType: string;
      contentBase64: string;
      sizeBytes: number;
    }> = [];

    for (const file of documents.slice(0, MAX_DOCS)) {
      if (file.size > MAX_BYTES_PER_DOC) {
        setDocError(`${file.name} is larger than 2MB and was skipped.`);
        continue;
      }
      const buf = await file.arrayBuffer();
      encodedDocs.push({
        name: file.name,
        mimeType: file.type || "application/octet-stream",
        contentBase64: bytesToBase64(new Uint8Array(buf)),
        sizeBytes: file.size,
      });
    }

    onSubmit({
      companyName: companyName.trim(),
      websiteUrl: websiteUrl.trim(),
      sector: sector && sector !== "Select sector..." ? sector.toLowerCase() : "",
      revenue: revenue ? parseFloat(revenue) : undefined,
      fundingTarget: fundingTarget ? parseFloat(fundingTarget) : undefined,
      pitchText: pitchText.trim(),
      documents: encodedDocs,
    });
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {/* Company Name */}
      <div>
        <label
          htmlFor="companyName"
          className="block text-sm font-medium text-neutral-300 mb-1.5"
        >
          Company Name *
        </label>
        <input
          id="companyName"
          type="text"
          required
          value={companyName}
          onChange={(e) => setCompanyName(e.target.value)}
          placeholder="e.g. Acme Robotics"
          className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-neutral-100 placeholder-neutral-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
      </div>

      {/* Website URL */}
      <div>
        <label
          htmlFor="websiteUrl"
          className="block text-sm font-medium text-neutral-300 mb-1.5"
        >
          Website URL
        </label>
        <input
          id="websiteUrl"
          type="url"
          value={websiteUrl}
          onChange={(e) => setWebsiteUrl(e.target.value)}
          placeholder="https://example.com"
          className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-neutral-100 placeholder-neutral-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        />
        <p className="text-xs text-neutral-500 mt-1">
          Used for auto-analysis when no pitch text is provided
        </p>
      </div>

      {/* Sector */}
      <div>
        <label
          htmlFor="sector"
          className="block text-sm font-medium text-neutral-300 mb-1.5"
        >
          Sector
        </label>
        <select
          id="sector"
          value={sector}
          onChange={(e) => setSector(e.target.value)}
          className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-neutral-100 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
        >
          {SECTORS.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
      </div>

      <button
        type="button"
        onClick={() => setShowAdvanced((v) => !v)}
        className="text-xs text-neutral-400 hover:text-neutral-200 transition-colors"
      >
        {showAdvanced ? "Hide advanced inputs" : "Add optional advanced inputs"}
      </button>

      {showAdvanced && (
        <>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div>
              <label
                htmlFor="revenue"
                className="block text-sm font-medium text-neutral-300 mb-1.5"
              >
                Annual Revenue ($)
              </label>
              <input
                id="revenue"
                type="number"
                min="0"
                step="1000"
                value={revenue}
                onChange={(e) => setRevenue(e.target.value)}
                placeholder="e.g. 500000"
                className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-neutral-100 placeholder-neutral-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
            <div>
              <label
                htmlFor="fundingTarget"
                className="block text-sm font-medium text-neutral-300 mb-1.5"
              >
                Funding Target ($)
              </label>
              <input
                id="fundingTarget"
                type="number"
                min="0"
                step="1000"
                value={fundingTarget}
                onChange={(e) => setFundingTarget(e.target.value)}
                placeholder="e.g. 250000"
                className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-neutral-100 placeholder-neutral-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500"
              />
            </div>
          </div>

          <div>
            <label
              htmlFor="pitchText"
              className="block text-sm font-medium text-neutral-300 mb-1.5"
            >
              Pitch Text{" "}
              <span className="text-neutral-500">(optional — or provide a website URL for auto-analysis)</span>
            </label>
            <textarea
              id="pitchText"
              rows={5}
              value={pitchText}
              onChange={(e) => setPitchText(e.target.value)}
              placeholder="Paste the company's pitch or description here for Claude AI analysis..."
              className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-neutral-100 placeholder-neutral-500 focus:border-blue-500 focus:outline-none focus:ring-1 focus:ring-blue-500 resize-y"
            />
          </div>

          <div>
            <label
              htmlFor="documents"
              className="block text-sm font-medium text-neutral-300 mb-1.5"
            >
              Supporting Documents
              <span className="text-neutral-500"> (optional: PDF, DOC/DOCX, CSV/XLSX)</span>
            </label>
            <input
              id="documents"
              type="file"
              multiple
              accept=".pdf,.doc,.docx,.csv,.xlsx,.xls"
              onChange={(e) => setDocuments(Array.from(e.target.files ?? []).slice(0, MAX_DOCS))}
              className="w-full rounded-lg border border-neutral-700 bg-neutral-800 px-4 py-2.5 text-neutral-100 file:mr-3 file:rounded-md file:border-0 file:bg-neutral-700 file:px-3 file:py-1.5 file:text-xs file:font-medium file:text-neutral-200"
            />
            <p className="text-xs text-neutral-500 mt-1">
              Up to {MAX_DOCS} files, 2MB each. Files are used only for this analysis request.
            </p>
            {documents.length > 0 && (
              <div className="mt-2 space-y-1">
                {documents.map((doc) => (
                  <div key={`${doc.name}-${doc.size}`} className="text-xs text-neutral-400">
                    {doc.name} ({Math.round(doc.size / 1024)} KB)
                  </div>
                ))}
              </div>
            )}
            {docError && (
              <p className="text-xs text-amber-500 mt-2">{docError}</p>
            )}
          </div>
        </>
      )}

      {/* Submit */}
      <button
        type="submit"
        disabled={isLoading || !companyName.trim()}
        className="w-full rounded-lg bg-blue-600 px-6 py-3 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
      >
        {isLoading ? (
          <span className="flex items-center justify-center gap-2">
            <svg
              className="animate-spin h-4 w-4"
              xmlns="http://www.w3.org/2000/svg"
              fill="none"
              viewBox="0 0 24 24"
            >
              <circle
                className="opacity-25"
                cx="12"
                cy="12"
                r="10"
                stroke="currentColor"
                strokeWidth="4"
              />
              <path
                className="opacity-75"
                fill="currentColor"
                d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"
              />
            </svg>
            Scoring...
          </span>
        ) : (
          "Score Company"
        )}
      </button>
    </form>
  );
}
