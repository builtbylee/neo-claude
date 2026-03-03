import { NextRequest, NextResponse } from "next/server";

import { getSupabaseClient } from "@/lib/db/supabase";
import { getActorEmail } from "@/lib/request-user";

function getClient() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return getSupabaseClient(url, key);
}

export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const supabase = getClient();
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
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
  const supabase = getClient();
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
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
  const actorEmail = getActorEmail(request, body.assigneeEmail ?? null);
  const { data, error } = await supabase
    .from("diligence_tasks")
    .insert({
      deal_id: id,
      title: body.title.trim(),
      details: body.details ?? null,
      due_date: body.dueDate ?? null,
      assignee_email: actorEmail,
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
