"""Precision / recall measurement for entity resolution quality.

Compares resolution outputs against a ground-truth set to compute
standard information-retrieval metrics.
"""

from __future__ import annotations

import psycopg

from startuplens.entity_resolution.deterministic import match_by_source_id


def compute_entity_resolution_metrics(
    conn: psycopg.Connection,
    ground_truth: list[dict],
) -> dict[str, float]:
    """Compute precision, recall, and F1 for entity resolution.

    Parameters
    ----------
    conn:
        Database connection.
    ground_truth:
        List of dicts each containing:
          - ``source_a``: ``{"source": str, "source_identifier": str}``
          - ``source_b``: ``{"source": str, "source_identifier": str}``
          - ``same_entity``: bool — whether these two records refer to the
            same real-world entity.

    The function looks up the resolved ``entity_id`` for each source
    record and checks whether the system agrees with the ground truth.

    Returns
    -------
    dict
        ``{"precision": float, "recall": float, "f1": float,
          "true_positives": int, "false_positives": int,
          "false_negatives": int, "total_pairs": int}``
    """
    true_positives = 0
    false_positives = 0
    false_negatives = 0

    for pair in ground_truth:
        src_a = pair["source_a"]
        src_b = pair["source_b"]
        expected_same = pair["same_entity"]

        id_a = match_by_source_id(conn, src_a["source"], src_a["source_identifier"])
        id_b = match_by_source_id(conn, src_b["source"], src_b["source_identifier"])

        # If either record is unresolved, we can't make a determination
        if id_a is None or id_b is None:
            if expected_same:
                false_negatives += 1
            continue

        predicted_same = id_a == id_b

        if predicted_same and expected_same:
            true_positives += 1
        elif predicted_same and not expected_same:
            false_positives += 1
        elif not predicted_same and expected_same:
            false_negatives += 1
        # True negatives are not tracked (not useful for P/R/F1).

    precision = (
        true_positives / (true_positives + false_positives)
        if (true_positives + false_positives) > 0
        else 0.0
    )
    recall = (
        true_positives / (true_positives + false_negatives)
        if (true_positives + false_negatives) > 0
        else 0.0
    )
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "total_pairs": len(ground_truth),
    }


def generate_validation_report(metrics: dict[str, float]) -> str:
    """Format entity resolution metrics into a human-readable report.

    Parameters
    ----------
    metrics:
        Dict as returned by :func:`compute_entity_resolution_metrics`.

    Returns
    -------
    str
        Multi-line formatted report.
    """
    lines = [
        "Entity Resolution Validation Report",
        "=" * 40,
        "",
        f"Total pairs evaluated:  {metrics.get('total_pairs', 0):.0f}",
        f"True positives:         {metrics.get('true_positives', 0):.0f}",
        f"False positives:        {metrics.get('false_positives', 0):.0f}",
        f"False negatives:        {metrics.get('false_negatives', 0):.0f}",
        "",
        f"Precision:  {metrics.get('precision', 0.0):.4f}",
        f"Recall:     {metrics.get('recall', 0.0):.4f}",
        f"F1 Score:   {metrics.get('f1', 0.0):.4f}",
    ]

    # Add a quality assessment
    f1 = metrics.get("f1", 0.0)
    if f1 >= 0.95:
        lines.append("\nAssessment: EXCELLENT — production-ready")
    elif f1 >= 0.85:
        lines.append("\nAssessment: GOOD — acceptable for production with monitoring")
    elif f1 >= 0.70:
        lines.append("\nAssessment: FAIR — consider improving probabilistic matching")
    else:
        lines.append("\nAssessment: POOR — significant entity resolution errors")

    return "\n".join(lines)
