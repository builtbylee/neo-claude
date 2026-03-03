import { NextRequest } from "next/server";

const DEFAULT_USER_EMAIL =
  process.env.DEFAULT_USER_EMAIL ??
  process.env.NEXT_PUBLIC_DEFAULT_USER_EMAIL ??
  "owner@example.com";

export function getActorEmail(
  request: NextRequest,
  fallback?: string | null,
): string {
  const headerEmail = request.headers.get("x-user-email")?.trim();
  if (headerEmail) return headerEmail;
  if (fallback?.trim()) return fallback.trim();
  return DEFAULT_USER_EMAIL;
}

