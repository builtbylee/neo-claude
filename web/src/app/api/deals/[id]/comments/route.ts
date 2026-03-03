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
    .from("deal_comments")
    .select("*")
    .eq("deal_id", id)
    .order("created_at", { ascending: false });
  if (error) return NextResponse.json({ error: "Failed to load comments" }, { status: 500 });
  return NextResponse.json({ comments: data ?? [] });
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
  const body = (await request.json()) as { body: string; authorEmail?: string };
  if (!body.body?.trim()) {
    return NextResponse.json({ error: "body is required" }, { status: 400 });
  }
  const { data, error } = await supabase
    .from("deal_comments")
    .insert({
      deal_id: id,
      body: body.body.trim(),
      author_email: body.authorEmail?.trim() || actorEmail,
    })
    .select("*")
    .single();
  if (error) return NextResponse.json({ error: "Failed to add comment" }, { status: 500 });
  await supabase.from("activity_events").insert({
    actor_email: actorEmail,
    entity_type: "comment",
    entity_id: data.id,
    action: "created",
    after_state: data,
  });
  return NextResponse.json({ comment: data });
}
