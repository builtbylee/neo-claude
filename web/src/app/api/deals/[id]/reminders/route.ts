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
    .from("deal_reminders")
    .select("*")
    .eq("deal_id", id)
    .order("due_at", { ascending: true })
    .limit(100);
  if (error) {
    return NextResponse.json(
      { error: "Failed to load reminders" },
      { status: 500 },
    );
  }
  return NextResponse.json({ reminders: data ?? [] });
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
    reminderType: "next_action" | "follow_on" | "diligence_review";
    dueAt: string;
    priority?: "low" | "medium" | "high" | "critical";
    payload?: Record<string, unknown>;
  };
  if (!body.reminderType || !body.dueAt) {
    return NextResponse.json(
      { error: "reminderType and dueAt are required" },
      { status: 400 },
    );
  }
  const { data, error } = await supabase
    .from("deal_reminders")
    .insert({
      deal_id: id,
      reminder_type: body.reminderType,
      due_at: body.dueAt,
      priority: body.priority ?? "medium",
      payload: body.payload ?? {},
      created_by: actorEmail,
    })
    .select("*")
    .single();
  if (error) {
    return NextResponse.json(
      { error: "Failed to create reminder" },
      { status: 500 },
    );
  }
  await supabase.from("activity_events").insert({
    actor_email: actorEmail,
    entity_type: "reminder",
    entity_id: data.id,
    action: "created",
    after_state: data,
  });
  return NextResponse.json({ reminder: data });
}
