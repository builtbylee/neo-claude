import { NextRequest, NextResponse } from "next/server";

import { getSupabaseClient } from "@/lib/db/supabase";

export type Role = "admin" | "analyst" | "viewer";

export interface RouteContext {
  supabase: ReturnType<typeof getSupabaseClient>;
  actorEmail: string;
  role: Role;
  canApprove: boolean;
}

function defaultRole(): Role {
  const candidate = (process.env.DEFAULT_USER_ROLE ?? "admin").toLowerCase();
  if (candidate === "admin" || candidate === "analyst" || candidate === "viewer") {
    return candidate;
  }
  return "admin";
}

function defaultActorEmail(): string {
  return (
    process.env.DEFAULT_USER_EMAIL ??
    process.env.NEXT_PUBLIC_DEFAULT_USER_EMAIL ??
    "owner@example.com"
  );
}

function authRequired(): boolean {
  if (process.env.ENFORCE_AUTH === "true") return true;
  if (process.env.ENFORCE_AUTH === "false") return false;
  return process.env.NEXT_PUBLIC_ENABLE_GOOGLE_AUTH === "true";
}

function unauthorized(message: string): NextResponse {
  return NextResponse.json({ error: message }, { status: 401 });
}

function forbidden(message: string): NextResponse {
  return NextResponse.json({ error: message }, { status: 403 });
}

export async function resolveRouteContext(
  request: NextRequest,
): Promise<RouteContext | NextResponse> {
  const supabaseUrl = process.env.SUPABASE_URL;
  const supabaseKey = process.env.SUPABASE_ANON_KEY;
  if (!supabaseUrl || !supabaseKey) {
    return NextResponse.json(
      { error: "Supabase not configured" },
      { status: 500 },
    );
  }

  const authHeader = request.headers.get("authorization") ?? "";
  const token = authHeader.startsWith("Bearer ")
    ? authHeader.slice("Bearer ".length).trim()
    : "";
  const fallbackEmail = request.headers.get("x-user-email")?.trim();
  const supabase = getSupabaseClient(supabaseUrl, supabaseKey, token || undefined);

  let actorEmail = fallbackEmail || defaultActorEmail();
  let authenticated = false;

  if (token) {
    const { data, error } = await supabase.auth.getUser(token);
    if (error || !data.user?.email) {
      return unauthorized("Invalid authentication token");
    }
    actorEmail = data.user.email.toLowerCase();
    authenticated = true;
  }

  if (authRequired() && !authenticated) {
    return unauthorized("Authentication required");
  }

  if (authenticated && !actorEmail) {
    return unauthorized("Authenticated user email not available");
  }

  if (!authenticated && !authRequired()) {
    const role = defaultRole();
    return {
      supabase,
      actorEmail,
      role,
      canApprove: role === "admin",
    };
  }

  const { data: roleRow } = await supabase
    .from("user_roles")
    .select("role, can_approve, active")
    .ilike("email", actorEmail)
    .limit(1)
    .maybeSingle();

  const active = roleRow?.active ?? true;
  if (!active) {
    return forbidden("User access is disabled");
  }

  const role = (roleRow?.role as Role | null) ?? "viewer";
  const canApprove = Boolean(roleRow?.can_approve) || role === "admin";

  return {
    supabase,
    actorEmail,
    role,
    canApprove,
  };
}

export function requireAtLeastAnalyst(
  context: RouteContext,
): NextResponse | null {
  if (context.role === "viewer") {
    return forbidden("Insufficient role for write access");
  }
  return null;
}

export function requireApprover(context: RouteContext): NextResponse | null {
  if (!context.canApprove) {
    return forbidden("Approver privileges required");
  }
  return null;
}
