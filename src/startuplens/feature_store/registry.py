"""Canonical feature definitions for the as-of feature store.

The registry is the single source of truth for all feature names, families,
and types. It drives the materialized view SQL generation and validates
feature writes.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FeatureDefinition:
    name: str
    family: str
    dtype: str  # numeric, boolean, categorical
    description: str
    required_for_training: bool = True


# Every feature used in the system must be registered here.
FEATURE_REGISTRY: list[FeatureDefinition] = [
    # --- Campaign features ---
    FeatureDefinition("funding_target", "campaign", "numeric", "Funding target amount"),
    FeatureDefinition("amount_raised", "campaign", "numeric", "Amount raised"),
    FeatureDefinition("overfunding_ratio", "campaign", "numeric", "amount_raised / funding_target"),
    FeatureDefinition("equity_offered_pct", "campaign", "numeric", "Equity offered %"),
    FeatureDefinition("pre_money_valuation", "campaign", "numeric", "Pre-money valuation"),
    FeatureDefinition("investor_count", "campaign", "numeric", "Number of investors"),
    FeatureDefinition(
        "funding_velocity_days", "campaign", "numeric", "Days to reach funding target"
    ),
    FeatureDefinition("eis_seis_eligible", "campaign", "boolean", "EIS/SEIS eligibility"),
    FeatureDefinition("platform", "campaign", "categorical", "Crowdfunding platform"),
    # --- Company features ---
    FeatureDefinition("company_age_months", "company", "numeric", "Age at raise in months"),
    FeatureDefinition("employee_count", "company", "numeric", "Employee count at raise"),
    FeatureDefinition("revenue_at_raise", "company", "numeric", "Revenue at raise"),
    FeatureDefinition("pre_revenue", "company", "boolean", "Is pre-revenue"),
    FeatureDefinition("revenue_growth_rate", "company", "numeric", "YoY revenue growth"),
    FeatureDefinition("total_prior_funding", "company", "numeric", "Total prior funding raised"),
    FeatureDefinition("prior_vc_backing", "company", "boolean", "Has prior VC/angel backing"),
    FeatureDefinition("sector", "company", "categorical", "Business sector"),
    FeatureDefinition(
        "revenue_model_type", "company", "categorical",
        "Revenue model: recurring, transactional, project",
    ),
    FeatureDefinition("country", "company", "categorical", "Country"),
    # --- Team features ---
    FeatureDefinition("founder_count", "team", "numeric", "Number of founders"),
    FeatureDefinition(
        "domain_experience_years", "team", "numeric", "Founder domain experience (years)"
    ),
    FeatureDefinition("prior_exits", "team", "boolean", "Founders have prior exits"),
    FeatureDefinition("accelerator_alumni", "team", "boolean", "Accelerator alumni"),
    # --- Financial features ---
    FeatureDefinition("total_assets", "financial", "numeric", "Total assets"),
    FeatureDefinition("total_debt", "financial", "numeric", "Total debt"),
    FeatureDefinition("debt_to_asset_ratio", "financial", "numeric", "Debt-to-asset ratio"),
    FeatureDefinition("cash_position", "financial", "numeric", "Cash and equivalents"),
    FeatureDefinition("burn_rate_monthly", "financial", "numeric", "Estimated monthly burn rate"),
    FeatureDefinition("gross_margin", "financial", "numeric", "Gross margin %"),
    # --- Terms/pricing features ---
    FeatureDefinition(
        "instrument_type", "terms", "categorical", "equity / safe / convertible_note / asa"
    ),
    FeatureDefinition("valuation_cap", "terms", "numeric", "SAFE/convertible valuation cap"),
    FeatureDefinition("discount_rate", "terms", "numeric", "SAFE/convertible discount rate"),
    FeatureDefinition("mfn_clause", "terms", "boolean", "Most favoured nation clause"),
    FeatureDefinition(
        "liquidation_pref_multiple", "terms", "numeric", "Liquidation preference multiple"
    ),
    FeatureDefinition(
        "liquidation_participation", "terms", "categorical",
        "non_participating / participating / capped_participating",
    ),
    FeatureDefinition("seniority_position", "terms", "numeric", "Seniority in preference stack"),
    FeatureDefinition("pro_rata_rights", "terms", "boolean", "Has pro-rata rights"),
    FeatureDefinition(
        "qualified_institutional", "terms", "boolean", "Has institutional co-investor"
    ),
    # --- Regulatory features ---
    FeatureDefinition(
        "company_status", "regulatory", "categorical", "Companies House status"
    ),
    FeatureDefinition("accounts_overdue", "regulatory", "boolean", "Accounts overdue"),
    FeatureDefinition("charges_count", "regulatory", "numeric", "Number of charges"),
    FeatureDefinition(
        "director_disqualifications", "regulatory", "numeric",
        "Count of disqualified directors",
    ),
    # --- Market regime features ---
    FeatureDefinition(
        "interest_rate_regime", "market_regime", "categorical", "rising / stable / falling"
    ),
    FeatureDefinition(
        "equity_market_regime", "market_regime", "categorical", "bull / neutral / bear"
    ),
    FeatureDefinition(
        "ecf_quarterly_volume", "market_regime", "numeric",
        "Reg CF filings in prior quarter",
    ),
    # --- Evidence quality features ---
    FeatureDefinition("data_source_count", "evidence", "numeric", "Number of data sources"),
    FeatureDefinition(
        "field_completeness_ratio", "evidence", "numeric", "Fraction of fields present"
    ),
]

# Lookup helpers

_BY_NAME: dict[str, FeatureDefinition] = {f.name: f for f in FEATURE_REGISTRY}
_BY_FAMILY: dict[str, list[FeatureDefinition]] = {}
for _f in FEATURE_REGISTRY:
    _BY_FAMILY.setdefault(_f.family, []).append(_f)

FAMILIES = sorted(_BY_FAMILY.keys())


def get_feature(name: str) -> FeatureDefinition:
    """Look up a feature definition by name. Raises KeyError if unknown."""
    return _BY_NAME[name]


def get_features_by_family(family: str) -> list[FeatureDefinition]:
    """Return all feature definitions for a given family."""
    return list(_BY_FAMILY.get(family, []))


def get_all_feature_names() -> list[str]:
    """Return all feature names in registry order."""
    return [f.name for f in FEATURE_REGISTRY]


def get_training_feature_names() -> list[str]:
    """Return only features marked as required_for_training."""
    return [f.name for f in FEATURE_REGISTRY if f.required_for_training]


def is_valid_feature(name: str) -> bool:
    """Check if a feature name is registered."""
    return name in _BY_NAME


def generate_materialized_view_sql() -> str:
    """Generate the SQL for training_features_wide based on the registry.

    Each feature becomes a column via MAX(CASE WHEN ...) pivot.
    """
    columns = []
    for feat in FEATURE_REGISTRY:
        if feat.dtype == "numeric":
            cast = "::numeric"
        elif feat.dtype == "boolean":
            cast = "::boolean"
        else:
            cast = "::text"
        col = (
            f"  MAX(CASE WHEN feature_name = '{feat.name}' "
            f"THEN (feature_value->>'value'){cast} END) AS {feat.name}"
        )
        columns.append(col)

    cols_sql = ",\n".join(columns)

    return f"""CREATE MATERIALIZED VIEW IF NOT EXISTS training_features_wide AS
SELECT
  entity_id,
  as_of_date,
{cols_sql},
  MAX(label_quality_tier) AS worst_label_tier
FROM feature_store
WHERE label_quality_tier <= 2
GROUP BY entity_id, as_of_date;

CREATE UNIQUE INDEX IF NOT EXISTS idx_training_wide_entity
  ON training_features_wide(entity_id, as_of_date);
"""
