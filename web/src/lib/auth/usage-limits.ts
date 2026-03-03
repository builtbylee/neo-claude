import { type SupabaseClient } from "@supabase/supabase-js";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnySupabaseClient = SupabaseClient<any, any, any>;

type UsageType = "quick_score" | "batch_score" | "memo_export" | "audit_export";

interface PlanRow {
  plan_code: string;
  monthly_quick_limit: number;
  monthly_batch_limit: number;
  monthly_export_limit: number;
  hard_block_on_limit: boolean;
}

interface UserSubscriptionRow {
  email: string;
  plan_code: string;
  status: string;
}

const DEFAULT_LIMITS: Record<UsageType, number> = {
  quick_score: 300,
  batch_score: 80,
  memo_export: 80,
  audit_export: 20,
};

function currentMonthBounds(): { start: string; end: string } {
  const now = new Date();
  const start = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), 1));
  const end = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() + 1, 1));
  return {
    start: start.toISOString(),
    end: end.toISOString(),
  };
}

function limitForType(plan: PlanRow | null, usageType: UsageType): number {
  if (!plan) return DEFAULT_LIMITS[usageType];
  if (usageType === "quick_score") return plan.monthly_quick_limit ?? DEFAULT_LIMITS.quick_score;
  if (usageType === "batch_score") return plan.monthly_batch_limit ?? DEFAULT_LIMITS.batch_score;
  if (usageType === "memo_export" || usageType === "audit_export") {
    return plan.monthly_export_limit ?? DEFAULT_LIMITS.memo_export;
  }
  return DEFAULT_LIMITS[usageType];
}

async function ensureSubscription(
  supabase: AnySupabaseClient,
  actorEmail: string,
): Promise<UserSubscriptionRow> {
  const normalized = actorEmail.trim().toLowerCase();
  const existing = await supabase
    .from("user_subscriptions")
    .select("email, plan_code, status")
    .eq("email", normalized)
    .maybeSingle();

  if (existing.data) return existing.data as UserSubscriptionRow;

  await supabase.from("user_subscriptions").insert({
    email: normalized,
    plan_code: "free",
    status: "active",
  });

  return {
    email: normalized,
    plan_code: "free",
    status: "active",
  };
}

export async function canConsumeUsage(
  supabase: AnySupabaseClient,
  actorEmail: string,
  usageType: UsageType,
  units = 1,
): Promise<{ allowed: boolean; limit: number; used: number; reason: string }> {
  const subscription = await ensureSubscription(supabase, actorEmail);
  if (subscription.status !== "active") {
    return {
      allowed: false,
      limit: 0,
      used: 0,
      reason: "Subscription is not active.",
    };
  }

  const planResult = await supabase
    .from("billing_plans")
    .select(
      "plan_code, monthly_quick_limit, monthly_batch_limit, monthly_export_limit, hard_block_on_limit",
    )
    .eq("plan_code", subscription.plan_code)
    .maybeSingle();
  const plan = (planResult.data ?? null) as PlanRow | null;
  const hardBlock = plan?.hard_block_on_limit ?? true;

  const month = currentMonthBounds();
  const usageResult = await supabase
    .from("usage_events")
    .select("units")
    .eq("email", actorEmail.toLowerCase())
    .eq("usage_type", usageType)
    .gte("created_at", month.start)
    .lt("created_at", month.end);

  const used = (usageResult.data ?? []).reduce((acc, row) => {
    const n = typeof row.units === "number" ? row.units : Number(row.units ?? 0);
    return acc + (Number.isFinite(n) ? n : 0);
  }, 0);
  const limit = limitForType(plan, usageType);
  const allowed = !hardBlock || used + units <= limit;

  return {
    allowed,
    limit,
    used,
    reason: allowed
      ? "Within monthly usage limits."
      : `Monthly limit exceeded (${used + units}/${limit}) for ${usageType}.`,
  };
}

export async function recordUsageEvent(
  supabase: AnySupabaseClient,
  actorEmail: string,
  usageType: UsageType,
  units = 1,
  metadata: Record<string, unknown> = {},
): Promise<void> {
  await supabase.from("usage_events").insert({
    email: actorEmail.toLowerCase(),
    usage_type: usageType,
    units,
    metadata,
  });
}
