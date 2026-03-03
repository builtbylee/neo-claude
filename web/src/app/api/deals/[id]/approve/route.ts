import { NextRequest, NextResponse } from "next/server";

import {
  requireApprover,
  resolveRouteContext,
} from "@/lib/auth/request-context";

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const { supabase, actorEmail } = context;
  const { id } = await params;
  const body = (await request.json()) as {
    requestedBy?: string;
    approverEmail?: string;
    status?: "pending" | "approved" | "rejected";
    decisionReason?: string | null;
  };

  const status = body.status ?? "pending";

  if (status !== "pending") {
    const approverError = requireApprover(context);
    if (approverError) return approverError;
  }

  const requestedBy = actorEmail;
  const approver = body.approverEmail?.trim() || actorEmail;

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
