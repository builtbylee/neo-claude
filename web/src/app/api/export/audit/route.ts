import { NextRequest, NextResponse } from "next/server";

import {
  requireAtLeastAnalyst,
  resolveRouteContext,
} from "@/lib/auth/request-context";
import { canConsumeUsage, recordUsageEvent } from "@/lib/auth/usage-limits";

type ExportType = "activity_events" | "recommendations" | "pipeline_snapshot";

function normalizeExportType(value: string | null): ExportType {
  if (value === "recommendations") return "recommendations";
  if (value === "pipeline_snapshot") return "pipeline_snapshot";
  return "activity_events";
}

export async function GET(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const roleError = requireAtLeastAnalyst(context);
  if (roleError) return roleError;

  const exportType = normalizeExportType(
    request.nextUrl.searchParams.get("type"),
  );
  const limitParam = Number(request.nextUrl.searchParams.get("limit") ?? "500");
  const limit = Number.isFinite(limitParam)
    ? Math.max(1, Math.min(5000, Math.floor(limitParam)))
    : 500;

  const usageGate = await canConsumeUsage(
    context.supabase,
    context.actorEmail,
    "audit_export",
    1,
  );
  if (!usageGate.allowed) {
    return NextResponse.json({ error: usageGate.reason }, { status: 429 });
  }

  let table = "activity_events";
  let columns = "*";
  let orderBy = "created_at";
  if (exportType === "recommendations") {
    table = "recommendation_log";
    orderBy = "created_at";
  } else if (exportType === "pipeline_snapshot") {
    table = "deal_pipeline_items";
    columns = "id, company_name, sector, country, status, priority, recommendation_class, conviction_score, owner_email, next_action_date, updated_at";
    orderBy = "updated_at";
  }

  const query = context.supabase
    .from(table)
    .select(columns)
    .order(orderBy, { ascending: false })
    .limit(limit);
  const { data, error } = await query;
  if (error) {
    return NextResponse.json(
      { error: "Failed to build audit export" },
      { status: 500 },
    );
  }

  const rows = data ?? [];
  await context.supabase.from("audit_exports").insert({
    requested_by: context.actorEmail,
    export_type: exportType,
    format: "json",
    record_count: rows.length,
    payload: {
      limit,
      table,
      generated_at: new Date().toISOString(),
    },
  });
  void recordUsageEvent(
    context.supabase,
    context.actorEmail,
    "audit_export",
    1,
    {
      exportType,
      recordCount: rows.length,
    },
  );

  return NextResponse.json({
    exportType,
    generatedAt: new Date().toISOString(),
    recordCount: rows.length,
    records: rows,
    usage: {
      planLimit: usageGate.limit,
      usedThisMonth: usageGate.used + 1,
      remaining: Math.max(0, usageGate.limit - (usageGate.used + 1)),
    },
  });
}
