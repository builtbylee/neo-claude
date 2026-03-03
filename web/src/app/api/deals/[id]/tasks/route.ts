import { NextRequest, NextResponse } from "next/server";

import {
  requireAtLeastAnalyst,
  resolveRouteContext,
} from "@/lib/auth/request-context";

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const { supabase } = context;
  const { id } = await params;
  const { data, error } = await supabase
    .from("diligence_tasks")
    .select("*")
    .eq("deal_id", id)
    .order("created_at", { ascending: false });
  if (error) return NextResponse.json({ error: "Failed to load tasks" }, { status: 500 });
  return NextResponse.json({ tasks: data ?? [] });
}

export async function POST(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) return context;
  const roleError = requireAtLeastAnalyst(context);
  if (roleError) return roleError;
  const { supabase, actorEmail } = context;
  const { id } = await params;
  const body = (await request.json()) as {
    title: string;
    details?: string | null;
    dueDate?: string | null;
    assigneeEmail?: string;
    evidenceRequired?: boolean;
  };
  if (!body.title?.trim()) {
    return NextResponse.json({ error: "title is required" }, { status: 400 });
  }
  const { data, error } = await supabase
    .from("diligence_tasks")
    .insert({
      deal_id: id,
      title: body.title.trim(),
      details: body.details ?? null,
      due_date: body.dueDate ?? null,
      assignee_email: body.assigneeEmail?.trim() || actorEmail,
      evidence_required: body.evidenceRequired ?? false,
    })
    .select("*")
    .single();
  if (error) return NextResponse.json({ error: "Failed to create task" }, { status: 500 });
  await supabase.from("activity_events").insert({
    actor_email: actorEmail,
    entity_type: "task",
    entity_id: data.id,
    action: "created",
    after_state: data,
  });
  return NextResponse.json({ task: data });
}
