import { NextRequest, NextResponse } from "next/server";

import { resolveRouteContext } from "@/lib/auth/request-context";

type JsonObject = Record<string, unknown>;

function asNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

function currentQuarterStartIso(): string {
  const now = new Date();
  const quarterMonth = Math.floor(now.getUTCMonth() / 3) * 3;
  return new Date(Date.UTC(now.getUTCFullYear(), quarterMonth, 1))
    .toISOString()
    .slice(0, 10);
}

export async function GET(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const { supabase } = context;

  const [
    { data: runs },
    { data: versions },
    { data: calibrations },
    { data: segments },
    { data: valuationAudits },
    { data: valuationCohorts },
    { data: evidenceReports },
  ] = await Promise.all([
    supabase
      .from("backtest_runs")
      .select("id, run_date, metrics, pass_fail, all_passed")
      .order("run_date", { ascending: false })
      .limit(1),
    supabase
      .from("model_versions")
      .select("id, family, version, trained_at, release_status, validation_auc, calibration_ece")
      .order("trained_at", { ascending: false })
      .limit(1),
    supabase
      .from("calibration_log")
      .select("checked_at, ece, status")
      .order("checked_at", { ascending: false })
      .limit(1),
    supabase
      .from("segment_model_evidence")
      .select("segment_key, sample_size, survival_auc, calibration_ece, release_gate_open, last_backtest_date")
      .order("segment_key", { ascending: true }),
    supabase
      .from("valuation_scenario_audits")
      .select("base_moic, realized_moic")
      .not("base_moic", "is", null)
      .not("realized_moic", "is", null)
      .order("created_at", { ascending: false })
      .limit(1000),
    supabase
      .from("valuation_cohort_mae")
      .select("cohort_quarter, segment_key, valuation_confidence, sample_size, mae, mape")
      .order("cohort_quarter", { ascending: false })
      .limit(200),
    supabase
      .from("quarterly_evidence_reports")
      .select("report_quarter, generated_at, release_readiness, artifact_path, summary")
      .order("report_quarter", { ascending: false })
      .limit(1),
  ]);

  const latestRun = runs?.[0] ?? null;
  const latestModel = versions?.[0] ?? null;
  const latestCalibration = calibrations?.[0] ?? null;
  const nowMs = Date.now();
  const trainedAtMs = latestModel?.trained_at
    ? new Date(latestModel.trained_at).getTime()
    : null;
  const daysSinceTrain =
    trainedAtMs && Number.isFinite(trainedAtMs)
      ? Math.floor((nowMs - trainedAtMs) / (24 * 60 * 60 * 1000))
      : null;

  const metrics = (latestRun?.metrics ?? {}) as JsonObject;
  const survivalAuc = asNumber(metrics.survival_auc);
  const calibrationEce = asNumber(metrics.calibration_ece);
  const qualityVsRandom = asNumber(metrics.portfolio_quality_vs_random);
  const calibrationHealthy =
    latestCalibration?.status === "healthy"
    || (calibrationEce !== null && calibrationEce <= 0.08);
  const releaseGateOpen = Boolean(latestRun?.all_passed);
  const retrainRecommended =
    (daysSinceTrain !== null && daysSinceTrain > 90)
    || latestCalibration?.status === "critical"
    || (calibrationEce !== null && calibrationEce > 0.1);

  let valuationAuditCoverage = 0;
  let valuationMeanAbsError: number | null = null;
  if (valuationAudits && valuationAudits.length > 0) {
    valuationAuditCoverage = valuationAudits.length;
    const absErrors = valuationAudits
      .map((row) => {
        const predicted = asNumber((row as JsonObject).base_moic);
        const realized = asNumber((row as JsonObject).realized_moic);
        if (predicted === null || realized === null) return null;
        return Math.abs(predicted - realized);
      })
      .filter((v): v is number => v !== null);
    if (absErrors.length > 0) {
      valuationMeanAbsError =
        Math.round((absErrors.reduce((a, b) => a + b, 0) / absErrors.length) * 100) / 100;
    }
  }

  const latestEvidence = evidenceReports?.[0] ?? null;
  const latestValuationCohort = valuationCohorts?.[0] ?? null;
  const evidenceQuarter = (latestEvidence?.report_quarter as string | null) ?? null;
  const evidenceFresh = Boolean(
    evidenceQuarter && evidenceQuarter.slice(0, 10) >= currentQuarterStartIso(),
  );

  return NextResponse.json({
    latestBacktest: latestRun
      ? {
          runDate: latestRun.run_date as string | null,
          allPassed: Boolean(latestRun.all_passed),
          survivalAuc,
          calibrationEce,
          qualityVsRandom,
        }
      : null,
    latestModel: latestModel
      ? {
          family: latestModel.family as string | null,
          version: latestModel.version as string | null,
          trainedAt: latestModel.trained_at as string | null,
          releaseStatus: latestModel.release_status as string | null,
          daysSinceTrain,
        }
      : null,
    checks: {
      calibrationHealthy,
      releaseGateOpen,
      retrainRecommended,
    },
    segmentEvidence: (segments ?? []).map((row) => {
      const segment = row as JsonObject;
      const sampleSize = asNumber(segment.sample_size) ?? 0;
      const survival = asNumber(segment.survival_auc);
      const ece = asNumber(segment.calibration_ece);
      const release = Boolean(segment.release_gate_open);
      const evidenceOk =
        sampleSize >= 200
        && release
        && survival !== null
        && survival >= 0.65
        && ece !== null
        && ece <= 0.10;
      return {
        segmentKey: String(segment.segment_key ?? ""),
        sampleSize,
        survivalAuc: survival,
        calibrationEce: ece,
        releaseGateOpen: release,
        lastBacktestDate: (segment.last_backtest_date as string | null) ?? null,
        evidenceOk,
      };
    }),
    valuationAudit: {
      realizedCoverage: valuationAuditCoverage,
      meanAbsoluteError: valuationMeanAbsError,
      latestCohort: latestValuationCohort
        ? {
            quarter: (latestValuationCohort.cohort_quarter as string | null) ?? null,
            segmentKey: (latestValuationCohort.segment_key as string | null) ?? null,
            confidence: (latestValuationCohort.valuation_confidence as string | null) ?? null,
            sampleSize: asNumber(latestValuationCohort.sample_size),
            mae: asNumber(latestValuationCohort.mae),
            mape: asNumber(latestValuationCohort.mape),
          }
        : null,
    },
    quarterlyEvidence: latestEvidence
      ? {
          reportQuarter: (latestEvidence.report_quarter as string | null) ?? null,
          generatedAt: (latestEvidence.generated_at as string | null) ?? null,
          releaseReadiness: Boolean(latestEvidence.release_readiness),
          isFresh: evidenceFresh,
          artifactPath: (latestEvidence.artifact_path as string | null) ?? null,
        }
      : null,
  });
}
