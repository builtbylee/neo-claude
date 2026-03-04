export type SourceTier = "A" | "B" | "C" | "unknown";

export type TermFieldSource =
  | "db_structured"
  | "derived_text"
  | "db_and_derived"
  | "conflict"
  | "none";

export interface TermSignalInput {
  valuationCap: number | null;
  discountRate: number | null;
  interestRate: number | null;
  maturityDate: string | null;
  liquidationPreferenceMultiple: number | null;
  liquidationParticipation: "non_participating" | "participating" | null;
  proRataRights: boolean | null;
  confidence: "low" | "medium" | "high";
}

export interface DealTermInput {
  valuation_cap?: number | null;
  discount_rate?: number | null;
  interest_rate?: number | null;
  maturity_date?: string | null;
  liquidation_preference_multiple?: number | null;
  liquidation_participation?: string | null;
  pro_rata_rights?: boolean | null;
}

export interface TermAuditResult {
  fieldSources: Record<string, TermFieldSource>;
  conflicts: string[];
  confidencePenalty: number;
  confidencePenaltyReasons: string[];
}

export interface AnalystReadinessResult {
  status: "ready" | "caution" | "blocked";
  passedCriteria: number;
  totalCriteria: number;
  reasons: string[];
}

interface AnalystReadinessInput {
  dataCompleteness: number;
  failedGateCount: number;
  valuationConfidence: "low" | "medium" | "high";
  segmentEvidenceOk: boolean;
  quarterlyEvidenceOk: boolean;
  sourceTier: SourceTier;
  pricingSampleSize: number;
  stageCountrySectorComps: number;
  tierAShare: number;
  coreTermCompleteness: number;
  valuationCriticalConflictCount: number;
  operationalWarningCount: number;
}

const TERM_FIELDS = [
  "valuation_cap",
  "discount_rate",
  "interest_rate",
  "maturity_date",
  "liquidation_preference_multiple",
  "liquidation_participation",
  "pro_rata_rights",
] as const;

function isPresent(value: unknown): boolean {
  return value !== null && value !== undefined;
}

function numericConflict(a: number, b: number): boolean {
  const absDiff = Math.abs(a - b);
  const scale = Math.max(Math.abs(a), Math.abs(b), 1);
  const relDiff = absDiff / scale;
  return absDiff > 0.01 && relDiff > 0.025;
}

function hasConflict(dbValue: unknown, derivedValue: unknown): boolean {
  if (!isPresent(dbValue) || !isPresent(derivedValue)) return false;
  if (typeof dbValue === "number" && typeof derivedValue === "number") {
    return numericConflict(dbValue, derivedValue);
  }
  return String(dbValue).toLowerCase() !== String(derivedValue).toLowerCase();
}

export function auditTermSignals(
  dbTerms: DealTermInput | null,
  derivedTerms: TermSignalInput,
): TermAuditResult {
  const fieldSources: Record<string, TermFieldSource> = {};
  const conflicts: string[] = [];

  const derivedMap: Record<(typeof TERM_FIELDS)[number], unknown> = {
    valuation_cap: derivedTerms.valuationCap,
    discount_rate: derivedTerms.discountRate,
    interest_rate: derivedTerms.interestRate,
    maturity_date: derivedTerms.maturityDate,
    liquidation_preference_multiple: derivedTerms.liquidationPreferenceMultiple,
    liquidation_participation: derivedTerms.liquidationParticipation,
    pro_rata_rights: derivedTerms.proRataRights,
  };

  for (const field of TERM_FIELDS) {
    const dbValue = dbTerms?.[field] ?? null;
    const derivedValue = derivedMap[field];
    if (isPresent(dbValue) && isPresent(derivedValue)) {
      if (hasConflict(dbValue, derivedValue)) {
        fieldSources[field] = "conflict";
        conflicts.push(field);
      } else {
        fieldSources[field] = "db_and_derived";
      }
      continue;
    }
    if (isPresent(dbValue)) {
      fieldSources[field] = "db_structured";
      continue;
    }
    if (isPresent(derivedValue)) {
      fieldSources[field] = "derived_text";
      continue;
    }
    fieldSources[field] = "none";
  }

  const confidencePenaltyReasons: string[] = [];
  let confidencePenalty = 0;

  if (conflicts.length >= 2) {
    confidencePenalty += 2;
    confidencePenaltyReasons.push(
      `Conflicting term fields detected: ${conflicts.join(", ")}.`,
    );
  } else if (conflicts.length === 1) {
    confidencePenalty += 1;
    confidencePenaltyReasons.push(
      `One term field is inconsistent across sources: ${conflicts[0]}.`,
    );
  }

  const dbCoverage = TERM_FIELDS.filter(
    (field) => fieldSources[field] === "db_structured" || fieldSources[field] === "db_and_derived",
  ).length;
  if (dbCoverage === 0 && derivedTerms.confidence !== "high") {
    confidencePenalty += 1;
    confidencePenaltyReasons.push(
      "Deal terms rely on low/medium-confidence text extraction without structured backing.",
    );
  }

  return {
    fieldSources,
    conflicts,
    confidencePenalty,
    confidencePenaltyReasons,
  };
}

export function computeAnalystReadiness(
  input: AnalystReadinessInput,
): AnalystReadinessResult {
  const checks: Array<{ passed: boolean; reason: string }> = [
    {
      passed: input.dataCompleteness >= 0.60,
      reason: "Data completeness is below 60%.",
    },
    {
      passed: input.failedGateCount === 0,
      reason: "One or more reliability gates failed.",
    },
    {
      passed: input.segmentEvidenceOk,
      reason: "Segment backtest evidence is insufficient.",
    },
    {
      passed: input.quarterlyEvidenceOk,
      reason: "Quarterly evidence report is stale or not release-ready.",
    },
    {
      passed: input.valuationConfidence !== "low",
      reason: "Valuation confidence is low.",
    },
    {
      passed: input.stageCountrySectorComps >= 80,
      reason: "Stage-country-sector comparable sample is below 80.",
    },
    {
      passed: input.sourceTier !== "C" && input.sourceTier !== "unknown" && input.tierAShare >= 0.30,
      reason: "Tier-A pricing evidence share is below 30% or source tier is too weak.",
    },
    {
      passed: input.coreTermCompleteness >= 0.85,
      reason: "Core term completeness is below 85%.",
    },
    {
      passed: input.valuationCriticalConflictCount === 0,
      reason: "Valuation-critical term conflicts were detected across sources.",
    },
    {
      passed: input.operationalWarningCount === 0,
      reason: "Operational data fetch warnings were encountered.",
    },
  ];

  const failed = checks.filter((c) => !c.passed).map((c) => c.reason);
  const passedCriteria = checks.length - failed.length;
  const ratio = checks.length > 0 ? passedCriteria / checks.length : 0;

  let status: AnalystReadinessResult["status"] = "blocked";
  if (failed.length === 0) {
    status = "ready";
  } else if (ratio >= 0.6) {
    status = "caution";
  }

  return {
    status,
    passedCriteria,
    totalCriteria: checks.length,
    reasons: failed,
  };
}
