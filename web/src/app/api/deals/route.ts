import { NextRequest, NextResponse } from "next/server";

import { getSupabaseClient } from "@/lib/db/supabase";
import { getActorEmail } from "@/lib/request-user";

function getClient() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return getSupabaseClient(url, key);
}

export async function GET() {
  const supabase = getClient();
  if (!supabase) {
    return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
  }

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
  const supabase = getClient();
  if (!supabase) {
    return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
  }

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
  const actorEmail = getActorEmail(request, body.ownerEmail ?? null);

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
    owner_email: actorEmail,
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
