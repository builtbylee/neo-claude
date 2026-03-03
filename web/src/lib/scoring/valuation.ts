export interface ValuationScenario {
  entryMultiple: number;
  cohortMedianMultiple: number | null;
  dilutionRetention: number;
  bearMoic: number;
  baseMoic: number;
  bullMoic: number;
  notes: string[];
}

interface ScenarioInput {
  stageBucket: string | null;
  preMoneyValuation: number | null;
  revenue: number | null;
  valuationSignal: "attractive" | "fair" | "aggressive" | null;
  cohortMedianMultiple: number | null;
}

function clamp(value: number, min: number, max: number): number {
  return Math.max(min, Math.min(max, value));
}

function round2(value: number): number {
  return Math.round(value * 100) / 100;
}

export function computeValuationScenario(
  input: ScenarioInput,
): ValuationScenario | null {
  if (
    !input.preMoneyValuation
    || !input.revenue
    || input.preMoneyValuation <= 0
    || input.revenue <= 0
  ) {
    return null;
  }

  const entryMultiple = input.preMoneyValuation / input.revenue;
  if (!Number.isFinite(entryMultiple) || entryMultiple <= 0) return null;

  const stage = (input.stageBucket ?? "").toLowerCase();
  const dilutionRetention = stage.includes("seed")
    ? 0.35
    : stage.includes("early")
      ? 0.55
      : 0.45;

  const cohortMedian = input.cohortMedianMultiple && input.cohortMedianMultiple > 0
    ? input.cohortMedianMultiple
    : null;
  const baselineExitMultiple = cohortMedian ?? 8;
  let pricingAdjustment = 1;
  if (input.valuationSignal === "aggressive") pricingAdjustment = 0.75;
  if (input.valuationSignal === "attractive") pricingAdjustment = 1.15;

  const bearExit = Math.max(1, baselineExitMultiple * 0.6 * pricingAdjustment);
  const baseExit = Math.max(1.5, baselineExitMultiple * 1.1 * pricingAdjustment);
  const bullExit = Math.max(3, baselineExitMultiple * 2.2 * pricingAdjustment);

  const bearMoic = clamp((bearExit / entryMultiple) * dilutionRetention, 0, 25);
  const baseMoic = clamp((baseExit / entryMultiple) * dilutionRetention, 0, 25);
  const bullMoic = clamp((bullExit / entryMultiple) * dilutionRetention, 0, 25);

  const notes: string[] = [
    `Dilution retention assumes ${Math.round(dilutionRetention * 100)}% ownership retained at exit.`,
    `Scenario exits are benchmarked to ${cohortMedian ? "cohort median multiples" : "default market multiple priors"}.`,
  ];
  if (input.valuationSignal === "aggressive") {
    notes.push("Aggressive entry pricing penalty applied.");
  }
  if (input.valuationSignal === "attractive") {
    notes.push("Attractive entry pricing uplift applied.");
  }

  return {
    entryMultiple: round2(entryMultiple),
    cohortMedianMultiple: cohortMedian ? round2(cohortMedian) : null,
    dilutionRetention: round2(dilutionRetention),
    bearMoic: round2(bearMoic),
    baseMoic: round2(baseMoic),
    bullMoic: round2(bullMoic),
    notes,
  };
}

