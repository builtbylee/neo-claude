import { NextRequest, NextResponse } from "next/server";

import { getSupabaseClient } from "@/lib/db/supabase";
import { getActorEmail } from "@/lib/request-user";

function getClient() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return getSupabaseClient(url, key);
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const supabase = getClient();
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
  const { id } = await params;
  const body = (await request.json()) as {
    requestedBy?: string;
    approverEmail?: string;
    status?: "pending" | "approved" | "rejected";
    decisionReason?: string | null;
  };

  const status = body.status ?? "pending";
  const actorEmail = getActorEmail(request, body.requestedBy ?? null);
  const requestedBy = actorEmail;
  const approver = body.approverEmail ?? actorEmail;

  const { data, error } = await supabase
    .from("approval_requests")
    .insert({
      deal_id: id,
      requested_by: requestedBy,
      approver_email: approver,
      status,
      decision_reason: body.decisionReason ?? null,
      decided_at: status === "approved" || status === "rejected" ? new Date().toISOString() : null,
    })
    .select("*")
    .single();
  if (error) return NextResponse.json({ error: "Failed to record approval" }, { status: 500 });

  await supabase.from("activity_events").insert({
    actor_email: requestedBy,
    entity_type: "approval",
    entity_id: data.id,
    action: status === "pending" ? "requested" : status,
    after_state: data,
  });

  return NextResponse.json({ approval: data });
}
