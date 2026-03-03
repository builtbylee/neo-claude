import { getSupabaseBrowserClient } from "@/lib/auth/supabase-browser";

export async function buildAuthHeaders(
  userEmail: string,
  contentType: "json" | "none" = "none",
): Promise<HeadersInit> {
  const headers: Record<string, string> = {
    "x-user-email": userEmail,
  };
  if (contentType === "json") {
    headers["Content-Type"] = "application/json";
  }

  const supabase = getSupabaseBrowserClient();
  if (supabase) {
    const { data } = await supabase.auth.getSession();
    const accessToken = data.session?.access_token;
    if (accessToken) {
      headers.Authorization = `Bearer ${accessToken}`;
    }
  }

  return headers;
}

