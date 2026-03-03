import { NextRequest, NextResponse } from "next/server";

import { getSupabaseClient } from "@/lib/db/supabase";

function getClient() {
  const url = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_ANON_KEY;
  if (!url || !key) return null;
  return getSupabaseClient(url, key);
}

export async function GET(request: NextRequest) {
  const supabase = getClient();
  if (!supabase) return NextResponse.json({ error: "Supabase not configured" }, { status: 500 });

  const sector = request.nextUrl.searchParams.get("sector");
  const { data: investments, error } = await supabase
    .from("investments")
    .select("amount_invested, company_name, entity_id");
  if (error) return NextResponse.json({ error: "Failed to load investments" }, { status: 500 });

  const rows = investments ?? [];
  const total = rows.reduce((acc, r) => acc + Number(r.amount_invested ?? 0), 0);

  let sectorCapital = 0;
  if (sector) {
    const { data: comps } = await supabase
      .from("companies")
      .select("name, sector")
      .ilike("sector", `%${sector}%`);
    const names = new Set((comps ?? []).map((c) => (c.name ?? "").toLowerCase()));
    sectorCapital = rows
      .filter((r) => names.has((r.company_name ?? "").toLowerCase()))
      .reduce((acc, r) => acc + Number(r.amount_invested ?? 0), 0);
  }

  const concentration = total > 0 ? sectorCapital / total : 0;
  return NextResponse.json({
    totalInvested: total,
    sector,
    sectorCapital,
    sectorConcentration: concentration,
    warning: concentration > 0.4 ? "Sector concentration exceeds 40%" : null,
  });
}
