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
  const supabase = getClient();
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
  const { id } = await params;
  const body = (await request.json()) as { body: string; authorEmail?: string };
  if (!body.body?.trim()) {
    return NextResponse.json({ error: "body is required" }, { status: 400 });
  }
  const actorEmail = getActorEmail(request, body.authorEmail ?? null);
  const { data, error } = await supabase
    .from("deal_comments")
    .insert({
      deal_id: id,
      body: body.body.trim(),
      author_email: actorEmail,
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
