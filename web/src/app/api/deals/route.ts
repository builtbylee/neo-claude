import { NextRequest, NextResponse } from "next/server";

import {
  requireAtLeastAnalyst,
  resolveRouteContext,
} from "@/lib/auth/request-context";

export async function GET(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) {
    return context;
  }
  const { supabase } = context;

  const { data, error } = await supabase
    .from("deal_pipeline_items")
    .select("*")
    .order("updated_at", { ascending: false })
    .limit(100);
  if (error) {
    return NextResponse.json({ error: "Failed to load pipeline" }, { status: 500 });
  }
  return NextResponse.json({ deals: data ?? [] });
}

export async function POST(request: NextRequest) {
  const context = await resolveRouteContext(request);
  if (context instanceof NextResponse) {
    return context;
  }
  const roleError = requireAtLeastAnalyst(context);
  if (roleError) return roleError;
  const { supabase, actorEmail } = context;

  const body = (await request.json()) as {
    evaluationId?: string | null;
    entityId?: string | null;
    companyName: string;
    sector?: string | null;
    country?: string | null;
    stageBucket?: string | null;
    recommendationClass?: string | null;
    convictionScore?: number | null;
    status?: string;
    priority?: string;
    ownerEmail?: string;
    nextActionDate?: string | null;
  };

  if (!body.companyName?.trim()) {
    return NextResponse.json({ error: "companyName is required" }, { status: 400 });
  }
  const payload = {
    evaluation_id: body.evaluationId ?? null,
    entity_id: body.entityId ?? null,
    company_name: body.companyName.trim(),
    sector: body.sector ?? null,
    country: body.country ?? null,
    stage_bucket: body.stageBucket ?? null,
    recommendation_class: body.recommendationClass ?? null,
    conviction_score: body.convictionScore ?? null,
    status: body.status ?? "new",
    priority: body.priority ?? "medium",
    owner_email: body.ownerEmail?.trim() || actorEmail,
    next_action_date: body.nextActionDate ?? null,
  };

  const { data, error } = await supabase
    .from("deal_pipeline_items")
    .insert(payload)
    .select("*")
    .single();

  if (error) {
    return NextResponse.json({ error: "Failed to create deal" }, { status: 500 });
  }

  await supabase.from("activity_events").insert({
    actor_email: actorEmail,
    entity_type: "deal",
    entity_id: data.id,
    action: "created",
    after_state: data,
  });

  return NextResponse.json({ deal: data });
}
