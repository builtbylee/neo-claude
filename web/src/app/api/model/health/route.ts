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
    },
  });
}
