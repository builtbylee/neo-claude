import { NextRequest, NextResponse } from "next/server";

import { getSupabaseClient } from "@/lib/db/supabase";
import { getActorEmail } from "@/lib/request-user";

function getClient() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return getSupabaseClient(url, key);
}

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const supabase = getClient();
  if (!supabase) {
    return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
  }

  const { id } = await params;
  const body = (await request.json()) as {
    status?: string;
    priority?: string;
    ownerEmail?: string;
    nextActionDate?: string | null;
    convictionScore?: number | null;
    recommendationClass?: string | null;
  };
  const actorEmail = getActorEmail(request, body.ownerEmail ?? null);

  const { data: before } = await supabase
    .from("deal_pipeline_items")
    .select("*")
    .eq("id", id)
    .single();

  const patch = {
    status: body.status,
    priority: body.priority,
    owner_email: body.ownerEmail ?? actorEmail,
    next_action_date: body.nextActionDate,
    conviction_score: body.convictionScore,
    recommendation_class: body.recommendationClass,
    updated_at: new Date().toISOString(),
  };

  const { data, error } = await supabase
    .from("deal_pipeline_items")
    .update(patch)
    .eq("id", id)
    .select("*")
    .single();
  if (error) {
    return NextResponse.json({ error: "Failed to update deal" }, { status: 500 });
  }

  await supabase.from("activity_events").insert({
    actor_email: actorEmail,
    entity_type: "deal",
    entity_id: id,
    action: "updated",
    before_state: before ?? null,
    after_state: data,
  });

  return NextResponse.json({ deal: data });
}
