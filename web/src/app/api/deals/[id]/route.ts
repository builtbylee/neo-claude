import { NextRequest, NextResponse } from "next/server";

import {
  requireAtLeastAnalyst,
  resolveRouteContext,
} from "@/lib/auth/request-context";

export async function PATCH(
  request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) {
    return context;
  }
  const roleError = requireAtLeastAnalyst(context);
  if (roleError) return roleError;
  const { supabase, actorEmail } = context;

  const { id } = await params;
  const body = (await request.json()) as {
    status?: string;
    priority?: string;
    ownerEmail?: string;
    nextActionDate?: string | null;
    convictionScore?: number | null;
    recommendationClass?: string | null;
  };
  const { data: before } = await supabase
    .from("deal_pipeline_items")
    .select("*")
    .eq("id", id)
    .single();

  const patch = {
    status: body.status,
    priority: body.priority,
    owner_email: body.ownerEmail?.trim() || actorEmail,
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
