"""Entity resolution layer for canonical company matching across data sources."""

from __future__ import annotations

from startuplens.entity_resolution.deterministic import (
    create_canonical_entity,
    link_entity,
    match_by_legal_name,
    match_by_source_id,
    normalize_name,
)
from startuplens.entity_resolution.probabilistic import (
    build_training_pairs,
    find_probable_matches,
    merge_entities,
    train_dedupe_model,
)
from startuplens.entity_resolution.resolver import (
    resolve_entity,
    run_entity_resolution,
    run_probabilistic_pass,
)
from startuplens.entity_resolution.validation import (
    compute_entity_resolution_metrics,
    generate_validation_report,
)

__all__ = [
    "build_training_pairs",
    "compute_entity_resolution_metrics",
    "create_canonical_entity",
    "find_probable_matches",
    "generate_validation_report",
    "link_entity",
    "match_by_legal_name",
    "match_by_source_id",
    "merge_entities",
    "normalize_name",
    "resolve_entity",
    "run_entity_resolution",
    "run_probabilistic_pass",
    "train_dedupe_model",
]
