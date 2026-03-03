"use client";

import { useState } from "react";

import ScoreForm from "@/components/ScoreForm";
import ScoreResult from "@/components/ScoreResult";

type ScoreResponse = {
  score: number;
  confidenceRange: number;
  recommendation: {
    class: string;
    originalClass: string;
    label: string;
    description: string;
  };
  categories: Record<string, number>;
  dataCompleteness: number;
  textScores: Record<string, number> | null;
  extractedFacts: {
    revenue: number | null;
    fundingTarget: number | null;
    revenueGrowthYoy: number | null;
    employeeCount: number | null;
    companyAgeMonths: number | null;
  } | null;
  gates: Array<{
    name: string;
    passed: boolean;
    action: string;
    reason: string;
  }>;
  matchedCompany: string | null;
  dataSource: "user" | "document" | "website" | "ai_knowledge" | "none";
  generatedProfile: string | null;
  memo: {
    thesis: string;
    evidence: string[];
    risks: string[];
    missingData: Array<{
      field: string;
      label: string;
      impact: string;
    }>;
    verdict: string;
  } | null;
  missingFields: Array<{
    field: string;
    label: string;
    impact: string;
  }>;
  regulatoryStatus: {
    companyStatus: string | null;
    companyNumber: string | null;
  } | null;
  dealTerms: {
    instrument_type: string | null;
    round_type: string | null;
    amount_raised: number | null;
    pre_money_valuation: number | null;
    platform: string | null;
    round_date: string | null;
    overfunding_ratio: number | null;
    investor_count: number | null;
    funding_velocity_days: number | null;
    eis_seis_eligible: boolean | null;
    qsbs_eligible: boolean | null;
    qualified_institutional: boolean | null;
  } | null;
  fundingHistory: Array<{
    id: string;
    instrument_type: string | null;
    round_type: string | null;
    amount_raised: number | null;
    pre_money_valuation: number | null;
    platform: string | null;
    round_date: string | null;
    overfunding_ratio: number | null;
    investor_count: number | null;
    funding_velocity_days: number | null;
    eis_seis_eligible: boolean | null;
    qsbs_eligible: boolean | null;
    qualified_institutional: boolean | null;
  }>;
  assessmentWarning: string | null;
  documentSummary: {
    parsed: Array<{ name: string; mimeType: string; extractedChars: number }>;
    warnings: string[];
  };
  provenance: {
    newestAsOfDate: string | null;
    stale: boolean;
    fields: Array<{
      feature: string;
      source: string;
      asOfDate: string;
      stalenessDays: number;
    }>;
  } | null;
  comparables: {
    cohortStats: {
      sampleSize: number;
      failureRate: number;
      survivalRate: number;
      exitRate: number;
      medianPreMoneyValuation: number | null;
      medianRevenueMultiple: number | null;
      medianFundingTarget: number | null;
      medianRevenueAtRaise: number | null;
      medianCompanyAgeMonths: number | null;
      medianOverfundingRatio: number | null;
      pctWithInstitutional: number;
      pctPreRevenue: number;
    };
    cohortLabel: string;
    valuationContext: {
      valuationPercentile: number;
      impliedRevenueMultiple: number;
      cohortMedianMultiple: number;
      signal: "attractive" | "fair" | "aggressive";
      note: string;
    } | null;
    nearestDeals: Array<{
      name: string;
      sector: string | null;
      country: string | null;
      fundingTarget: number | null;
      revenueAtRaise: number | null;
      companyAgeMonths: number | null;
      outcome: string;
      platform: string | null;
      campaignDate: string | null;
    }>;
  } | null;
};

export default function Home() {
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<ScoreResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (data: {
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
  }) => {
    setIsLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await fetch("/api/score/quick", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });

      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err.error || `Request failed: ${response.status}`);
      }

      const scoreData = (await response.json()) as ScoreResponse;
      setResult(scoreData);
    } catch (err) {
      setError(err instanceof Error ? err.message : "An error occurred");
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-neutral-950">
      {/* Header */}
      <header className="border-b border-neutral-800">
        <div className="max-w-4xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight">
              StartupLens
            </h1>
            <p className="text-xs text-neutral-500">
              AI-Powered Investment Scoring
            </p>
          </div>
          <span className="text-xs px-2 py-1 rounded bg-neutral-800 text-neutral-400">
            Quick Score
          </span>
        </div>
      </header>

      {/* Main */}
      <main className="max-w-4xl mx-auto px-6 py-8">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
          {/* Form */}
          <div>
            <h2 className="text-lg font-semibold text-white mb-1">
              Score a Company
            </h2>
            <p className="text-sm text-neutral-500 mb-6">
              Enter company name, website, and sector for a fast analyst-style
              assessment. Expand optional inputs for deeper diligence.
            </p>
            <ScoreForm onSubmit={handleSubmit} isLoading={isLoading} />
          </div>

          {/* Results */}
          <div>
            {error && (
              <div className="rounded-xl bg-red-950/30 border border-red-800/30 p-4">
                <p className="text-sm text-red-400">{error}</p>
              </div>
            )}

            {result && <ScoreResult result={result} />}

            {!result && !error && !isLoading && (
              <div className="flex items-center justify-center h-full">
                <div className="text-center text-neutral-600 py-20">
                  <svg
                    className="w-12 h-12 mx-auto mb-3 opacity-30"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={1.5}
                      d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
                    />
                  </svg>
                  <p className="text-sm">
                    Enter a company name to get started
                  </p>
                </div>
              </div>
            )}
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="border-t border-neutral-800 mt-12">
        <div className="max-w-4xl mx-auto px-6 py-4 text-xs text-neutral-600">
          StartupLens uses ML models trained on SEC Reg CF data and Claude AI
          for text analysis. Scores are not investment advice.
          ~85% of equity crowdfunding companies fail.
        </div>
      </footer>
    </div>
  );
}
