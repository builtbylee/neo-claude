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
  }) => void;
  isLoading: boolean;
}

export default function ScoreForm({ onSubmit, isLoading }: ScoreFormProps) {
  const [companyName, setCompanyName] = useState("");
  const [websiteUrl, setWebsiteUrl] = useState("");
  const [sector, setSector] = useState("");
  const [revenue, setRevenue] = useState("");
  const [fundingTarget, setFundingTarget] = useState("");
  const [pitchText, setPitchText] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit({
      companyName: companyName.trim(),
      websiteUrl: websiteUrl.trim(),
      sector: sector && sector !== "Select sector..." ? sector.toLowerCase() : "",
      revenue: revenue ? parseFloat(revenue) : undefined,
      fundingTarget: fundingTarget ? parseFloat(fundingTarget) : undefined,
      pitchText: pitchText.trim(),
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

      {/* Sector + Revenue row */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
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
      </div>

      {/* Funding Target */}
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

      {/* Pitch Text */}
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
