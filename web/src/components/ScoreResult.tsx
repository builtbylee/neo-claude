"use client";

interface ScoreResultProps {
  result: {
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
}

const REC_COLORS: Record<string, string> = {
  invest: "bg-green-600",
  deep_diligence: "bg-blue-600",
  watch: "bg-yellow-600",
  pass: "bg-red-600",
  abstain: "bg-neutral-600",
};

const SOURCE_LABELS: Record<string, string> = {
  user: "AI analysis of your pitch text",
  document: "AI analysis of uploaded documents",
  website: "AI analysis of website content",
  ai_knowledge: "AI analysis from training knowledge",
  none: "No text analysis available",
};

function ScoreGauge({ score, range }: { score: number; range: number }) {
  const color =
    score >= 65
      ? "#22c55e"
      : score >= 50
        ? "#3b82f6"
        : score >= 40
          ? "#eab308"
          : "#ef4444";

  return (
    <div className="flex flex-col items-center">
      <div className="relative w-48 h-24 overflow-hidden">
        <svg viewBox="0 0 200 100" className="w-full h-full">
          <path
            d="M 10 100 A 90 90 0 0 1 190 100"
            fill="none"
            stroke="#374151"
            strokeWidth="12"
            strokeLinecap="round"
          />
          <path
            d="M 10 100 A 90 90 0 0 1 190 100"
            fill="none"
            stroke={color}
            strokeWidth="12"
            strokeLinecap="round"
            strokeDasharray={`${(score / 100) * 283} 283`}
          />
        </svg>
      </div>
      <div className="text-center -mt-4">
        <span className="text-4xl font-bold text-white">{score}</span>
        <span className="text-lg text-neutral-400">/100</span>
      </div>
      <div className="text-sm text-neutral-500 mt-1">
        +/- {range} points
      </div>
    </div>
  );
}

function CategoryBar({
  name,
  score,
}: {
  name: string;
  score: number;
}) {
  const color =
    score >= 60
      ? "bg-green-500"
      : score >= 45
        ? "bg-blue-500"
        : score >= 35
          ? "bg-yellow-500"
          : "bg-red-500";

  return (
    <div className="flex items-center gap-3">
      <span className="text-sm text-neutral-300 w-40 shrink-0">{name}</span>
      <div className="flex-1 bg-neutral-700 rounded-full h-2.5 overflow-hidden">
        <div
          className={`h-full rounded-full ${color} transition-all duration-500`}
          style={{ width: `${score}%` }}
        />
      </div>
      <span className="text-sm text-neutral-400 w-8 text-right">{score}</span>
    </div>
  );
}

export default function ScoreResult({ result }: ScoreResultProps) {
  const recColor = REC_COLORS[result.recommendation.class] ?? "bg-neutral-600";
  const failedGates = result.gates.filter((g) => !g.passed);
  const isAbstain = result.recommendation.class === "abstain";
  const routedFromAbstain =
    result.recommendation.originalClass === "abstain" &&
    result.recommendation.class !== "abstain";

  const extractedHeading =
    result.dataSource === "website"
      ? "Extracted from Website"
      : result.dataSource === "document"
        ? "Extracted from Uploaded Documents"
      : result.dataSource === "ai_knowledge"
        ? "Extracted from AI Knowledge"
        : "Extracted from Pitch Text";

  return (
    <div className="space-y-6">
      {/* Header: Score + Recommendation */}
      <div className="flex flex-col sm:flex-row items-center gap-6">
        <ScoreGauge score={result.score} range={result.confidenceRange} />
        <div className="text-center sm:text-left">
          <span
            className={`inline-block px-3 py-1 rounded-full text-sm font-semibold text-white ${recColor}`}
          >
            {result.recommendation.label}
          </span>
          <p className="text-sm text-neutral-400 mt-2 max-w-sm">
            {result.recommendation.description}
          </p>
          {result.matchedCompany && (
            <p className="text-xs text-neutral-500 mt-1">
              Matched: {result.matchedCompany}
            </p>
          )}
        </div>
      </div>

      {result.assessmentWarning && (
        <div className="bg-amber-950/20 rounded-xl p-5 border border-amber-800/30">
          <h3 className="text-sm font-semibold text-amber-400 mb-1">
            Confidence Warning
          </h3>
          <p className="text-sm text-neutral-300">
            {result.assessmentWarning}
          </p>
        </div>
      )}

      {/* IC Memo — shown prominently before category breakdown */}
      {result.memo && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-3">
            Investment Memo
          </h3>

          {/* Thesis */}
          <p className="text-sm text-neutral-200 mb-4 leading-relaxed">
            {result.memo.thesis}
          </p>

          {/* Evidence */}
          <div className="mb-4">
            <h4 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2">
              Evidence
            </h4>
            <ul className="space-y-1.5">
              {result.memo.evidence.map((point, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-neutral-300">
                  <span className="text-green-500 mt-0.5 shrink-0">+</span>
                  <span>{point}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Risks */}
          <div className="mb-4">
            <h4 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2">
              Key Risks
            </h4>
            <ul className="space-y-1.5">
              {result.memo.risks.map((risk, i) => (
                <li key={i} className="flex items-start gap-2 text-sm text-neutral-300">
                  <span className="text-red-400 mt-0.5 shrink-0">-</span>
                  <span>{risk}</span>
                </li>
              ))}
            </ul>
          </div>

          {/* Verdict */}
          <div className="border-t border-neutral-700/50 pt-3">
            <h4 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2">
              Verdict
            </h4>
            <p className="text-sm text-neutral-200 leading-relaxed">
              {result.memo.verdict}
            </p>
          </div>
        </div>
      )}

      {/* Actionable Abstain — show missing fields with impact */}
      {(isAbstain || routedFromAbstain) && result.missingFields.length > 0 && (
        <div className="bg-amber-950/20 rounded-xl p-5 border border-amber-800/30">
          <h3 className="text-sm font-semibold text-amber-400 mb-1">
            More Data Needed
          </h3>
          <p className="text-xs text-neutral-400 mb-3">
            Provide any of these to unlock a full assessment:
          </p>
          <div className="space-y-3">
            {result.missingFields.map((mf) => (
              <div key={mf.field} className="flex items-start gap-2">
                <span className="text-amber-500 mt-0.5 shrink-0 text-sm">?</span>
                <div>
                  <span className="text-sm text-neutral-200 font-medium">
                    {mf.label}
                  </span>
                  <p className="text-xs text-neutral-500 mt-0.5">
                    {mf.impact}
                  </p>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Category Breakdown */}
      <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
        <h3 className="text-sm font-semibold text-neutral-300 mb-4">
          Category Breakdown
        </h3>
        <div className="space-y-3">
          {Object.entries(result.categories).map(([name, score]) => (
            <CategoryBar key={name} name={name} score={score} />
          ))}
        </div>
      </div>

      {/* Deal Terms */}
      {result.dealTerms && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-3">
            Deal Terms
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
            {result.dealTerms.instrument_type && (
              <div>
                <span className="text-neutral-500">Instrument: </span>
                <span className="text-neutral-200 font-medium capitalize">
                  {result.dealTerms.instrument_type.replace(/_/g, " ")}
                </span>
              </div>
            )}
            {result.dealTerms.amount_raised !== null && (
              <div>
                <span className="text-neutral-500">Amount Raised: </span>
                <span className="text-neutral-200 font-medium">
                  ${Math.round(result.dealTerms.amount_raised).toLocaleString()}
                </span>
              </div>
            )}
            {result.dealTerms.pre_money_valuation !== null && (
              <div>
                <span className="text-neutral-500">Pre-Money: </span>
                <span className="text-neutral-200 font-medium">
                  ${Math.round(result.dealTerms.pre_money_valuation).toLocaleString()}
                </span>
              </div>
            )}
            {result.dealTerms.platform && (
              <div>
                <span className="text-neutral-500">Platform: </span>
                <span className="text-neutral-200 font-medium">
                  {result.dealTerms.platform}
                </span>
              </div>
            )}
            {result.dealTerms.overfunding_ratio !== null && (
              <div>
                <span className="text-neutral-500">Overfunding: </span>
                <span className="text-neutral-200 font-medium">
                  {result.dealTerms.overfunding_ratio.toFixed(2)}x
                </span>
              </div>
            )}
            {result.dealTerms.investor_count !== null && (
              <div>
                <span className="text-neutral-500">Investors: </span>
                <span className="text-neutral-200 font-medium">
                  {result.dealTerms.investor_count.toLocaleString()}
                </span>
              </div>
            )}
            {result.dealTerms.round_date && (
              <div>
                <span className="text-neutral-500">Round Date: </span>
                <span className="text-neutral-200 font-medium">
                  {result.dealTerms.round_date}
                </span>
              </div>
            )}
            {result.dealTerms.eis_seis_eligible !== null && (
              <div>
                <span className="text-neutral-500">EIS/SEIS: </span>
                <span className="text-neutral-200 font-medium">
                  {result.dealTerms.eis_seis_eligible ? "Eligible" : "No"}
                </span>
              </div>
            )}
            {result.dealTerms.qualified_institutional !== null && (
              <div>
                <span className="text-neutral-500">Institutional: </span>
                <span className="text-neutral-200 font-medium">
                  {result.dealTerms.qualified_institutional ? "Yes" : "No"}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Funding Trajectory */}
      {result.fundingHistory.length > 0 && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-3">
            Funding Trajectory
          </h3>
          <div className="space-y-2">
            {result.fundingHistory.map((round) => (
              <div
                key={round.id}
                className="flex items-center justify-between text-sm py-1.5 border-b border-neutral-700/30 last:border-0"
              >
                <div className="min-w-0">
                  <span className="text-neutral-200 font-medium capitalize">
                    {(round.round_type ?? round.instrument_type ?? "round").replace(/_/g, " ")}
                  </span>
                  {round.round_date && (
                    <span className="text-xs text-neutral-500 ml-2">{round.round_date}</span>
                  )}
                </div>
                <div className="flex items-center gap-3 text-xs text-neutral-400 shrink-0">
                  {round.amount_raised !== null && (
                    <span>${Math.round(round.amount_raised).toLocaleString()}</span>
                  )}
                  {round.pre_money_valuation !== null && (
                    <span>Pre: ${Math.round(round.pre_money_valuation).toLocaleString()}</span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Regulatory Status (UK Companies House) */}
      {result.regulatoryStatus && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-3">
            UK Regulatory Status
          </h3>
          <div className="grid grid-cols-2 gap-3 text-sm">
            {result.regulatoryStatus.companyStatus && (
              <div>
                <span className="text-neutral-500">Status: </span>
                <span
                  className={`font-medium capitalize ${
                    result.regulatoryStatus.companyStatus === "active"
                      ? "text-green-400"
                      : result.regulatoryStatus.companyStatus === "dissolved"
                        ? "text-red-400"
                        : "text-yellow-400"
                  }`}
                >
                  {result.regulatoryStatus.companyStatus}
                </span>
              </div>
            )}
            {result.regulatoryStatus.companyNumber && (
              <div>
                <span className="text-neutral-500">Company No: </span>
                <span className="text-neutral-200 font-medium">
                  {result.regulatoryStatus.companyNumber}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Comparables */}
      {result.comparables && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-1">
            Comparables
          </h3>
          <p className="text-xs text-neutral-500 mb-4">
            Based on {result.comparables.cohortStats.sampleSize.toLocaleString()} {result.comparables.cohortLabel} (n={result.comparables.cohortStats.sampleSize})
          </p>

          {/* Cohort base rates */}
          <div className="grid grid-cols-3 gap-4 mb-4">
            <div className="text-center">
              <div className="text-lg font-bold text-red-400">
                {Math.round(result.comparables.cohortStats.failureRate * 100)}%
              </div>
              <div className="text-xs text-neutral-500">Failure Rate</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-green-400">
                {Math.round(result.comparables.cohortStats.survivalRate * 100)}%
              </div>
              <div className="text-xs text-neutral-500">Still Trading</div>
            </div>
            <div className="text-center">
              <div className="text-lg font-bold text-blue-400">
                {Math.round(result.comparables.cohortStats.exitRate * 100)}%
              </div>
              <div className="text-xs text-neutral-500">Exited</div>
            </div>
          </div>

          {/* Cohort medians */}
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4 text-sm">
            {result.comparables.cohortStats.medianFundingTarget !== null && (
              <div>
                <span className="text-neutral-500">Median Raise: </span>
                <span className="text-neutral-200">
                  ${Math.round(result.comparables.cohortStats.medianFundingTarget).toLocaleString()}
                </span>
              </div>
            )}
            {result.comparables.cohortStats.medianPreMoneyValuation !== null && (
              <div>
                <span className="text-neutral-500">Median Pre-Money: </span>
                <span className="text-neutral-200">
                  ${Math.round(result.comparables.cohortStats.medianPreMoneyValuation).toLocaleString()}
                </span>
              </div>
            )}
            {result.comparables.cohortStats.medianRevenueAtRaise !== null && (
              <div>
                <span className="text-neutral-500">Median Revenue: </span>
                <span className="text-neutral-200">
                  ${Math.round(result.comparables.cohortStats.medianRevenueAtRaise).toLocaleString()}
                </span>
              </div>
            )}
            {result.comparables.cohortStats.medianCompanyAgeMonths !== null && (
              <div>
                <span className="text-neutral-500">Median Age: </span>
                <span className="text-neutral-200">
                  {result.comparables.cohortStats.medianCompanyAgeMonths >= 12
                    ? `${Math.round(result.comparables.cohortStats.medianCompanyAgeMonths / 12)}y`
                    : `${Math.round(result.comparables.cohortStats.medianCompanyAgeMonths)}mo`}
                </span>
              </div>
            )}
            <div>
              <span className="text-neutral-500">Pre-Revenue: </span>
              <span className="text-neutral-200">
                {Math.round(result.comparables.cohortStats.pctPreRevenue * 100)}%
              </span>
            </div>
          </div>

          {result.comparables.valuationContext && (
            <div className="rounded-lg border border-neutral-700/50 bg-neutral-900/40 p-4 mb-4">
              <h4 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2">
                Valuation Context
              </h4>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm mb-2">
                <div>
                  <span className="text-neutral-500">Implied Multiple: </span>
                  <span className="text-neutral-200">
                    {result.comparables.valuationContext.impliedRevenueMultiple.toFixed(1)}x
                  </span>
                </div>
                <div>
                  <span className="text-neutral-500">Cohort Median: </span>
                  <span className="text-neutral-200">
                    {result.comparables.valuationContext.cohortMedianMultiple.toFixed(1)}x
                  </span>
                </div>
                <div>
                  <span className="text-neutral-500">Percentile: </span>
                  <span className="text-neutral-200">
                    {result.comparables.valuationContext.valuationPercentile}th
                  </span>
                </div>
                <div>
                  <span className="text-neutral-500">Pricing Signal: </span>
                  <span
                    className={`font-medium capitalize ${
                      result.comparables.valuationContext.signal === "aggressive"
                        ? "text-red-400"
                        : result.comparables.valuationContext.signal === "attractive"
                          ? "text-green-400"
                          : "text-yellow-400"
                    }`}
                  >
                    {result.comparables.valuationContext.signal}
                  </span>
                </div>
              </div>
              <p className="text-xs text-neutral-500">
                {result.comparables.valuationContext.note}
              </p>
            </div>
          )}

          {/* Nearest deals */}
          {result.comparables.nearestDeals.length > 0 && (
            <div>
              <h4 className="text-xs font-semibold text-neutral-400 uppercase tracking-wider mb-2">
                Most Similar Deals
              </h4>
              <div className="space-y-2">
                {result.comparables.nearestDeals.map((deal, i) => (
                  <div
                    key={i}
                    className="flex items-center justify-between text-sm py-1.5 border-b border-neutral-700/30 last:border-0"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className={`w-2 h-2 rounded-full shrink-0 ${
                          deal.outcome === "trading"
                            ? "bg-green-500"
                            : deal.outcome === "exited"
                              ? "bg-blue-500"
                              : "bg-red-500"
                        }`}
                      />
                      <span className="text-neutral-200 truncate">
                        {deal.name}
                      </span>
                    </div>
                    <div className="flex items-center gap-3 shrink-0 text-xs text-neutral-500">
                      {deal.fundingTarget && (
                        <span>${Math.round(deal.fundingTarget).toLocaleString()}</span>
                      )}
                      <span className="capitalize">{deal.outcome}</span>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {(result.documentSummary.parsed.length > 0 || result.documentSummary.warnings.length > 0) && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-3">
            Document Ingestion
          </h3>
          {result.documentSummary.parsed.length > 0 && (
            <div className="space-y-1.5 mb-3">
              {result.documentSummary.parsed.map((doc) => (
                <div key={`${doc.name}-${doc.mimeType}`} className="text-xs text-neutral-400">
                  {doc.name} ({doc.mimeType}){doc.extractedChars > 0 ? ` - ${doc.extractedChars.toLocaleString()} chars` : " - analyzed as PDF"}
                </div>
              ))}
            </div>
          )}
          {result.documentSummary.warnings.length > 0 && (
            <div className="space-y-1">
              {result.documentSummary.warnings.map((warning, i) => (
                <div key={`${warning}-${i}`} className="text-xs text-amber-500">
                  {warning}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {/* AI-Generated Company Profile (knowledge mode) */}
      {result.dataSource === "ai_knowledge" && result.generatedProfile && (
        <div className="bg-amber-950/20 rounded-xl p-5 border border-amber-800/30">
          <h3 className="text-sm font-semibold text-amber-400 mb-2">
            AI-Generated Company Profile
          </h3>
          <p className="text-sm text-neutral-300 italic">
            {result.generatedProfile}
          </p>
          <p className="text-xs text-amber-600 mt-2">
            Based on AI training data — may not reflect current state
          </p>
        </div>
      )}

      {/* Claude Text Analysis */}
      {result.textScores && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-4">
            AI Text Analysis
            {result.dataSource === "document" && (
              <span className="text-xs text-neutral-500 ml-2">(from uploaded documents)</span>
            )}
            {result.dataSource === "website" && (
              <span className="text-xs text-neutral-500 ml-2">(from website)</span>
            )}
            {result.dataSource === "ai_knowledge" && (
              <span className="text-xs text-amber-500 ml-2">(from AI knowledge)</span>
            )}
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {Object.entries(result.textScores).map(([dim, score]) => (
              <div key={dim} className="text-center">
                <div className="text-lg font-bold text-white">{score}</div>
                <div className="text-xs text-neutral-500 capitalize">
                  {dim.replace(/_/g, " ")}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {result.provenance && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-3">
            Data Provenance
          </h3>
          {result.provenance.newestAsOfDate && (
            <p className="text-xs text-neutral-500 mb-3">
              Latest data timestamp: {result.provenance.newestAsOfDate}
              {result.provenance.stale ? " (stale)" : ""}
            </p>
          )}
          <div className="space-y-1.5">
            {result.provenance.fields.map((field) => (
              <div
                key={`${field.feature}-${field.source}`}
                className="flex items-center justify-between text-xs border-b border-neutral-700/30 pb-1"
              >
                <span className="text-neutral-300">{field.feature}</span>
                <span className="text-neutral-500">
                  {field.source} · {field.asOfDate} · {field.stalenessDays}d old
                </span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Extracted Facts */}
      {result.extractedFacts && Object.values(result.extractedFacts).some((v) => v !== null) && (
        <div className="bg-neutral-800/50 rounded-xl p-5 border border-neutral-700/50">
          <h3 className="text-sm font-semibold text-neutral-300 mb-3">
            {extractedHeading}
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-3 gap-3 text-sm">
            {result.extractedFacts.revenue !== null && (
              <div>
                <span className="text-neutral-500">Revenue: </span>
                <span className="text-neutral-200 font-medium">
                  ${result.extractedFacts.revenue.toLocaleString()}
                </span>
              </div>
            )}
            {result.extractedFacts.fundingTarget !== null && (
              <div>
                <span className="text-neutral-500">Funding Target: </span>
                <span className="text-neutral-200 font-medium">
                  ${result.extractedFacts.fundingTarget.toLocaleString()}
                </span>
              </div>
            )}
            {result.extractedFacts.revenueGrowthYoy !== null && (
              <div>
                <span className="text-neutral-500">YoY Growth: </span>
                <span className="text-neutral-200 font-medium">
                  {Math.round(result.extractedFacts.revenueGrowthYoy * 100)}%
                </span>
              </div>
            )}
            {result.extractedFacts.employeeCount !== null && (
              <div>
                <span className="text-neutral-500">Employees: </span>
                <span className="text-neutral-200 font-medium">
                  {result.extractedFacts.employeeCount.toLocaleString()}
                </span>
              </div>
            )}
            {result.extractedFacts.companyAgeMonths !== null && (
              <div>
                <span className="text-neutral-500">Company Age: </span>
                <span className="text-neutral-200 font-medium">
                  {result.extractedFacts.companyAgeMonths >= 12
                    ? `${Math.round(result.extractedFacts.companyAgeMonths / 12)}y`
                    : `${result.extractedFacts.companyAgeMonths}mo`}
                </span>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Gates */}
      {failedGates.length > 0 && !isAbstain && (
        <div className="bg-red-950/30 rounded-xl p-5 border border-red-800/30">
          <h3 className="text-sm font-semibold text-red-400 mb-3">
            Gate Alerts
          </h3>
          <div className="space-y-2">
            {failedGates.map((gate) => (
              <div key={gate.name} className="flex items-start gap-2 text-sm">
                <span className="text-red-400 mt-0.5">x</span>
                <div>
                  <span className="text-neutral-300 font-medium">
                    {gate.name}:
                  </span>{" "}
                  <span className="text-neutral-400">{gate.reason}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Data Completeness + Source */}
      <div className="flex items-center justify-between text-sm text-neutral-500 pt-2 border-t border-neutral-800">
        <span>Data completeness: {result.dataCompleteness}%</span>
        <span>{SOURCE_LABELS[result.dataSource] ?? "No text analysis"}</span>
      </div>
    </div>
  );
}
