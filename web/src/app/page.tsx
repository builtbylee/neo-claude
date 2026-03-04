"use client";

import { useEffect, useState } from "react";

import AnalystWorkbench from "@/components/AnalystWorkbench";
import ScoreForm from "@/components/ScoreForm";
import ScoreResult from "@/components/ScoreResult";
import { buildAuthHeaders } from "@/lib/auth/client-headers";
import { getSupabaseBrowserClient } from "@/lib/auth/supabase-browser";

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
    diligenceChecklist: string[];
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
    valuation_cap: number | null;
    discount_rate: number | null;
    interest_rate: number | null;
    maturity_date: string | null;
    liquidation_preference_multiple: number | null;
    liquidation_participation: string | null;
    pro_rata_rights: boolean | null;
    pro_rata_amount: number | null;
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
    valuation_cap: number | null;
    discount_rate: number | null;
    interest_rate: number | null;
    maturity_date: string | null;
    liquidation_preference_multiple: number | null;
    liquidation_participation: string | null;
    pro_rata_rights: boolean | null;
    pro_rata_amount: number | null;
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
  valuationScenario: {
    entryMultiple: number;
    cohortMedianMultiple: number | null;
    dilutionRetention: number;
    bearMoic: number;
    baseMoic: number;
    bullMoic: number;
    confidenceBand: "low" | "medium" | "high";
    auditedAgainstRealized: boolean;
    notes: string[];
  } | null;
  valuationConfidence: "low" | "medium" | "high";
  valuationConfidenceReason: string;
  segmentEvidence: {
    segmentKey: string;
    sampleSize: number;
    survivalAuc: number | null;
    calibrationEce: number | null;
    releaseGateOpen: boolean;
    evidenceOk: boolean;
    lastBacktestDate: string | null;
  } | null;
  quarterlyEvidence: {
    reportQuarter: string | null;
    generatedAt: string | null;
    releaseReadiness: boolean;
    isFresh: boolean;
  } | null;
  sanctions: {
    checked: boolean;
    matched: boolean;
    riskLevel: "clear" | "potential_match";
    matchSource: string | null;
    matchName: string | null;
    reason: string;
  };
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
      dataSource: "pricing_cohort" | "outcome_cohort";
      sampleSize: number;
      multipleType: "revenue_multiple" | "raise_proxy_multiple";
      sourceTier: "A" | "B" | "C";
    } | null;
    sourceSummary: {
      outcomeSampleSize: number;
      pricingSampleSize: number;
      pricingRevenueSampleSize: number;
      pricingProxySampleSize: number;
      pricingStageAlignedSample: number;
      pricingStageCountrySectorSample: number;
      pricingSourceBreakdown: Record<string, number>;
      pricingTierBreakdown: Record<"A" | "B" | "C", number>;
      pricingTierAShare: number;
      officialSignalCount: number;
      officialSignalTypeBreakdown: Record<string, number>;
      weightedPricingCoverage: number;
      confidencePenalty: number;
      confidencePenaltyReasons: string[];
    };
    sourceConfidence: "low" | "medium" | "high";
    valuationConfidence: "low" | "medium" | "high";
    valuationConfidenceReason: string;
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
  trust: {
    sourceTier: "A" | "B" | "C" | "unknown";
    confidencePenalty: number;
    confidencePenaltyReasons: string[];
    abstainReasons: string[];
    termFieldSources: Record<string, string>;
    termConflicts: string[];
    analystReadiness: {
      status: "ready" | "caution" | "blocked";
      passedCriteria: number;
      totalCriteria: number;
      reasons: string[];
    };
    valuationGate: {
      passed: boolean;
      reason: string;
      stageCountrySectorComps: number;
      tierAShare: number;
      coreTermCompleteness: number;
      valuationCriticalConflicts: number;
    };
    operationalWarnings: string[];
  };
};

export default function Home() {
  const [isLoading, setIsLoading] = useState(false);
  const [result, setResult] = useState<ScoreResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [userEmail, setUserEmail] = useState(
    process.env.NEXT_PUBLIC_DEFAULT_USER_EMAIL ?? "owner@example.com",
  );
  const [authReady, setAuthReady] = useState(false);
  const [authEnabled, setAuthEnabled] = useState(false);
  const [isAuthenticated, setIsAuthenticated] = useState(false);
  const [usageSummary, setUsageSummary] = useState<{
    plan: string;
    quickUsed: number;
    quickLimit: number;
  } | null>(null);

  useEffect(() => {
    const enableGoogleAuth =
      process.env.NEXT_PUBLIC_ENABLE_GOOGLE_AUTH === "true";
    setAuthEnabled(enableGoogleAuth);
    if (!enableGoogleAuth) {
      setAuthReady(true);
      return;
    }

    const supabase = getSupabaseBrowserClient();
    if (!supabase) {
      setAuthReady(true);
      return;
    }
    void supabase.auth.getSession().then(({ data }) => {
      const email = data.session?.user?.email;
      if (email) {
        setUserEmail(email);
        setIsAuthenticated(true);
      } else {
        setIsAuthenticated(false);
      }
      setAuthReady(true);
    });
    const { data: sub } = supabase.auth.onAuthStateChange((_event, session) => {
      const email = session?.user?.email;
      if (email) {
        setUserEmail(email);
        setIsAuthenticated(true);
      } else {
        setIsAuthenticated(false);
      }
    });
    return () => {
      sub.subscription.unsubscribe();
    };
  }, []);

  async function signInWithGoogle() {
    const supabase = getSupabaseBrowserClient();
    if (!supabase) return;
    await supabase.auth.signInWithOAuth({
      provider: "google",
      options: {
        redirectTo: window.location.origin,
      },
    });
  }

  async function signOut() {
    const supabase = getSupabaseBrowserClient();
    if (!supabase) return;
    await supabase.auth.signOut();
  }

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
    if (authEnabled && authReady && !isAuthenticated) {
      setError("Sign in with Google to run scoring.");
      return;
    }
    setIsLoading(true);
    setError(null);
    setResult(null);

    try {
      const response = await fetch("/api/score/quick", {
        method: "POST",
        headers: await buildAuthHeaders(userEmail, "json"),
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

  useEffect(() => {
    async function loadUsageSummary() {
      if (authEnabled && authReady && !isAuthenticated) {
        setUsageSummary(null);
        return;
      }
      try {
        const resp = await fetch("/api/account/usage", {
          headers: await buildAuthHeaders(userEmail),
        });
        if (!resp.ok) {
          setUsageSummary(null);
          return;
        }
        const data = (await resp.json()) as {
          subscription?: { displayName?: string };
          usageThisMonth?: { quickScore?: number };
          limits?: { quickScore?: number };
        };
        setUsageSummary({
          plan: data.subscription?.displayName ?? "Free",
          quickUsed: data.usageThisMonth?.quickScore ?? 0,
          quickLimit: data.limits?.quickScore ?? 300,
        });
      } catch {
        setUsageSummary(null);
      }
    }
    void loadUsageSummary();
  }, [authEnabled, authReady, isAuthenticated, userEmail, result]);

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
        <div className="max-w-4xl mx-auto px-6 pb-4 flex items-center justify-between text-xs text-neutral-500">
          <span>User: {userEmail}</span>
          {usageSummary && (
            <span>
              Plan: {usageSummary.plan} · Quick used {usageSummary.quickUsed}/{usageSummary.quickLimit}
            </span>
          )}
          {authEnabled && authReady ? (
            <button
              type="button"
              onClick={() =>
                isAuthenticated ? void signOut() : void signInWithGoogle()
              }
              className="rounded border border-neutral-700 px-2 py-1 text-neutral-300"
            >
              {isAuthenticated ? "Sign out" : "Sign in with Google"}
            </button>
          ) : null}
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

        <AnalystWorkbench
          userEmail={userEmail}
          authEnabled={authEnabled}
          isAuthenticated={isAuthenticated}
        />
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
