import assert from "node:assert/strict";
import test from "node:test";

import { canConsumeUsage, recordUsageEvent } from "./usage-limits";

type TableName = "user_subscriptions" | "billing_plans" | "usage_events";

type MockState = {
  subscription: { email: string; plan_code: string; status: string } | null;
  plan: {
    plan_code: string;
    monthly_quick_limit: number;
    monthly_batch_limit: number;
    monthly_export_limit: number;
    hard_block_on_limit: boolean;
  } | null;
  usageRows: Array<{ usage_type: string; units: number; email: string }>;
  insertedUsage: Array<Record<string, unknown>>;
  insertedSubscriptions: Array<Record<string, unknown>>;
};

function createMockSupabase(state: MockState) {
  return {
    from(table: TableName) {
      const filters = new Map<string, unknown>();
      return {
        select() {
          return this;
        },
        eq(column: string, value: unknown) {
          filters.set(column, value);
          return this;
        },
        gte() {
          return this;
        },
        lt() {
          return this;
        },
        async maybeSingle() {
          if (table === "user_subscriptions") {
            return { data: state.subscription, error: null };
          }
          if (table === "billing_plans") {
            return { data: state.plan, error: null };
          }
          return { data: null, error: null };
        },
        async insert(payload: Record<string, unknown>) {
          if (table === "usage_events") {
            state.insertedUsage.push(payload);
          }
          if (table === "user_subscriptions") {
            state.insertedSubscriptions.push(payload);
            state.subscription = {
              email: String(payload.email ?? ""),
              plan_code: String(payload.plan_code ?? "free"),
              status: String(payload.status ?? "active"),
            };
          }
          return { data: null, error: null };
        },
        async then(
          resolve: (value: { data: Array<{ units: number }>; error: null }) => void,
        ) {
          if (table !== "usage_events") {
            resolve({ data: [], error: null });
            return;
          }
          const email = String(filters.get("email") ?? "");
          const usageType = String(filters.get("usage_type") ?? "");
          const rows = state.usageRows
            .filter((row) => row.email === email && row.usage_type === usageType)
            .map((row) => ({ units: row.units }));
          resolve({ data: rows, error: null });
        },
      };
    },
  };
}

test("canConsumeUsage blocks when monthly limit is exceeded", async () => {
  const state: MockState = {
    subscription: { email: "a@example.com", plan_code: "free", status: "active" },
    plan: {
      plan_code: "free",
      monthly_quick_limit: 5,
      monthly_batch_limit: 2,
      monthly_export_limit: 2,
      hard_block_on_limit: true,
    },
    usageRows: [
      { email: "a@example.com", usage_type: "quick_score", units: 4 },
      { email: "a@example.com", usage_type: "quick_score", units: 1 },
    ],
    insertedUsage: [],
    insertedSubscriptions: [],
  };

  const supabase = createMockSupabase(state);
  const result = await canConsumeUsage(
    supabase as never,
    "a@example.com",
    "quick_score",
    1,
  );

  assert.equal(result.allowed, false);
  assert.equal(result.limit, 5);
  assert.equal(result.used, 5);
});

test("canConsumeUsage auto-creates default free subscription when missing", async () => {
  const state: MockState = {
    subscription: null,
    plan: {
      plan_code: "free",
      monthly_quick_limit: 300,
      monthly_batch_limit: 80,
      monthly_export_limit: 80,
      hard_block_on_limit: true,
    },
    usageRows: [],
    insertedUsage: [],
    insertedSubscriptions: [],
  };
  const supabase = createMockSupabase(state);
  const result = await canConsumeUsage(
    supabase as never,
    "new@example.com",
    "quick_score",
    1,
  );

  assert.equal(result.allowed, true);
  assert.equal(state.insertedSubscriptions.length, 1);
});

test("recordUsageEvent writes usage row", async () => {
  const state: MockState = {
    subscription: null,
    plan: null,
    usageRows: [],
    insertedUsage: [],
    insertedSubscriptions: [],
  };
  const supabase = createMockSupabase(state);
  await recordUsageEvent(supabase as never, "u@example.com", "memo_export", 2, {
    foo: "bar",
  });
  assert.equal(state.insertedUsage.length, 1);
  assert.equal(state.insertedUsage[0].usage_type, "memo_export");
  assert.equal(state.insertedUsage[0].units, 2);
});
