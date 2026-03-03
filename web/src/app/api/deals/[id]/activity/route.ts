import { NextRequest, NextResponse } from "next/server";

import { getSupabaseClient } from "@/lib/db/supabase";

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
  if (!supabase) {
    return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });
  }
  const { id } = await params;
  const { data: dealEvents, error: dealError } = await supabase
    .from("activity_events")
    .select("*")
    .eq("entity_type", "deal")
    .eq("entity_id", id)
    .order("created_at", { ascending: false })
    .limit(100);

  if (dealError) {
    return NextResponse.json(
      { error: "Failed to load activity history" },
      { status: 500 },
    );
  }
  const { data: tasks, error: tasksError } = await supabase
    .from("diligence_tasks")
    .select("id")
    .eq("deal_id", id);
  const { data: comments, error: commentsError } = await supabase
    .from("deal_comments")
    .select("id")
    .eq("deal_id", id);
  const { data: approvals, error: approvalsError } = await supabase
    .from("approval_requests")
    .select("id")
    .eq("deal_id", id);
  if (tasksError || commentsError || approvalsError) {
    return NextResponse.json(
      { error: "Failed to load activity references" },
      { status: 500 },
    );
  }

  const taskIds = (tasks ?? []).map((t) => t.id);
  const commentIds = (comments ?? []).map((c) => c.id);
  const approvalIds = (approvals ?? []).map((a) => a.id);

  const auxQueries = [];
  if (taskIds.length > 0) {
    auxQueries.push(
      supabase
        .from("activity_events")
        .select("*")
        .eq("entity_type", "task")
        .in("entity_id", taskIds)
        .order("created_at", { ascending: false })
        .limit(100),
    );
  }
  if (commentIds.length > 0) {
    auxQueries.push(
      supabase
        .from("activity_events")
        .select("*")
        .eq("entity_type", "comment")
        .in("entity_id", commentIds)
        .order("created_at", { ascending: false })
        .limit(100),
    );
  }
  if (approvalIds.length > 0) {
    auxQueries.push(
      supabase
        .from("activity_events")
        .select("*")
        .eq("entity_type", "approval")
        .in("entity_id", approvalIds)
        .order("created_at", { ascending: false })
        .limit(100),
    );
  }

  const auxResults = await Promise.all(auxQueries);
  const auxErrors = auxResults.find((r) => r.error);
  if (auxErrors?.error) {
    return NextResponse.json(
      { error: "Failed to load related activity history" },
      { status: 500 },
    );
  }
  const events = [
    ...(dealEvents ?? []),
    ...auxResults.flatMap((r) => r.data ?? []),
  ];
  events.sort((a, b) => {
    const aTs = new Date(a.created_at).getTime();
    const bTs = new Date(b.created_at).getTime();
    return bTs - aTs;
  });
  return NextResponse.json({ events: events.slice(0, 200) });
}
