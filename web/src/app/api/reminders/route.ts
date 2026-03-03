import { NextRequest, NextResponse } from "next/server";

import { resolveRouteContext } from "@/lib/auth/request-context";

export async function GET(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const { supabase } = context;
  const status = request.nextUrl.searchParams.get("status") ?? "pending";
  const limitParam = Number(request.nextUrl.searchParams.get("limit") ?? "100");
  const limit = Number.isFinite(limitParam)
    ? Math.max(1, Math.min(500, Math.floor(limitParam)))
    : 100;

  const { data, error } = await supabase
    .from("deal_reminders")
    .select("id, deal_id, reminder_type, due_at, priority, status, payload, created_at")
    .eq("status", status)
    .order("due_at", { ascending: true })
    .limit(limit);
  if (error) {
    return NextResponse.json(
      { error: "Failed to load reminders" },
      { status: 500 },
    );
  }
  return NextResponse.json({ reminders: data ?? [] });
}
