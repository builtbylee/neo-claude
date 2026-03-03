import { NextRequest, NextResponse } from "next/server";

import { resolveRouteContext } from "@/lib/auth/request-context";

type UsageType = "quick_score" | "batch_score" | "memo_export" | "audit_export";

function monthBounds(): { start: string; end: string } {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return { start: start.toISOString(), end: end.toISOString() };
}

export async function GET(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const { supabase, actorEmail } = context;

  const subResult = await supabase
    .from("user_subscriptions")
    .select("plan_code, status")
    .eq("email", actorEmail.toLowerCase())
    .maybeSingle();
  const planCode = subResult.data?.plan_code ?? "free";
  const status = subResult.data?.status ?? "active";

  const planResult = await supabase
    .from("billing_plans")
    .select("plan_code, display_name, monthly_quick_limit, monthly_batch_limit, monthly_export_limit")
    .eq("plan_code", planCode)
    .maybeSingle();

  const month = monthBounds();
  const usageResult = await supabase
    .from("usage_events")
    .select("usage_type, units")
    .eq("email", actorEmail.toLowerCase())
    .gte("created_at", month.start)
    .lt("created_at", month.end);

  const totals = new Map<UsageType, number>();
  for (const row of usageResult.data ?? []) {
    const type = row.usage_type as UsageType;
    const units = typeof row.units === "number" ? row.units : Number(row.units ?? 0);
    totals.set(type, (totals.get(type) ?? 0) + (Number.isFinite(units) ? units : 0));
  }

  return NextResponse.json({
    email: actorEmail,
    subscription: {
      planCode,
      status,
      displayName: planResult.data?.display_name ?? "Free",
    },
    usageThisMonth: {
      quickScore: totals.get("quick_score") ?? 0,
      batchScore: totals.get("batch_score") ?? 0,
      memoExport: totals.get("memo_export") ?? 0,
      auditExport: totals.get("audit_export") ?? 0,
    },
    limits: {
      quickScore: planResult.data?.monthly_quick_limit ?? 300,
      batchScore: planResult.data?.monthly_batch_limit ?? 80,
      exports: planResult.data?.monthly_export_limit ?? 80,
    },
  });
}
