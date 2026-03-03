import { NextRequest, NextResponse } from "next/server";

import { resolveRouteContext } from "@/lib/auth/request-context";

type JsonObject = Record<string, unknown>;

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

export async function GET(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const { supabase } = context;

  const [{ data: runs }, { data: versions }, { data: calibrations }] = await Promise.all([
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
  });
}

