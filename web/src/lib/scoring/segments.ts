export type SegmentKey =
  | "US_Seed"
  | "US_EarlyGrowth"
  | "UK_Seed"
  | "UK_EarlyGrowth";

function normalizeCountry(country: string | null | undefined): string {
  return (country ?? "").trim().toUpperCase();
}

function normalizeStage(stageBucket: string | null | undefined): string {
  return (stageBucket ?? "").trim().toLowerCase();
}

export function deriveSegmentKey(
  country: string | null | undefined,
  stageBucket: string | null | undefined,
): SegmentKey {
  const countryNorm = normalizeCountry(country);
  const stageNorm = normalizeStage(stageBucket);
  const isUk =
    countryNorm === "UK"
    || countryNorm === "GB"
    || countryNorm === "UNITED KINGDOM";
  const isEarly =
    stageNorm.includes("early")
    || stageNorm.includes("growth")
    || stageNorm.includes("series_a")
    || stageNorm.includes("series_b")
    || stageNorm.includes("a")
    || stageNorm.includes("b");

  if (isUk) {
    return isEarly ? "UK_EarlyGrowth" : "UK_Seed";
  }
  return isEarly ? "US_EarlyGrowth" : "US_Seed";
}

