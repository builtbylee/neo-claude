#!/usr/bin/env python3
"""Generate quarterly evidence report from rolling segment backtests and valuation cohorts."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import structlog
import typer

from startuplens.config import get_settings
from startuplens.db import execute_query, get_connection

logger = structlog.get_logger(__name__)
app = typer.Typer()

SEGMENTS = ["US_Seed", "US_EarlyGrowth", "UK_Seed", "UK_EarlyGrowth"]
HIGH_CONFIDENCE_VALUATION_MAE_MAX = 1.0
HIGH_CONFIDENCE_VALUATION_MAPE_MAX = 0.55
HIGH_CONFIDENCE_VALUATION_MIN_SAMPLE = 30


def _quarter_start(d: date) -> date:
    month = ((d.month - 1) // 3) * 3 + 1
    return date(d.year, month, 1)


def _quarter_label(d: date) -> str:
    quarter = ((d.month - 1) // 3) + 1
    return f"{d.year}-Q{quarter}"


def _json_default(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


@app.command()
def main(
    report_quarter: str | None = typer.Option(
        None,
        help="Quarter start date (YYYY-MM-DD). Defaults to current quarter.",
    ),
    holdout_window: str = typer.Option(
        "2023-2025",
        help="Holdout window expected to remain locked.",
    ),
    output_dir: str = typer.Option(
        "data/reports/evidence",
        help="Directory to write evidence report artifacts.",
    ),
) -> None:
    quarter = date.fromisoformat(report_quarter) if report_quarter else _quarter_start(date.today())
    quarter_label = _quarter_label(quarter)

    settings = get_settings()
    conn = get_connection(settings)
    try:
        segments = execute_query(
            conn,
            """
            SELECT
              segment_key,
              sample_size,
              survival_auc,
              calibration_ece,
              release_gate_open,
              last_backtest_run_id,
              last_backtest_date,
              source_coverage,
              notes
            FROM segment_model_evidence
            WHERE segment_key = ANY(%s)
            ORDER BY segment_key
            """,
            (SEGMENTS,),
        )

        windows = execute_query(
            conn,
            """
            SELECT
              segment_key,
              window_label,
              test_start,
              test_end,
              survival_auc,
              calibration_ece,
              quality_vs_random,
              failure_vs_random,
              model_uncertainty_rate,
              top_k_sector_concentration
            FROM backtest_window_results
            WHERE segment_key = ANY(%s)
            ORDER BY segment_key, test_end DESC, window_label DESC
            """,
            (SEGMENTS,),
        )

        curves = execute_query(
            conn,
            """
            SELECT
              c.segment_key,
              c.window_label,
              c.bin_index,
              c.sample_size,
              c.mean_pred,
              c.observed_rate,
              c.abs_error
            FROM backtest_calibration_curves c
            JOIN segment_model_evidence s
              ON s.last_backtest_run_id = c.backtest_run_id
             AND s.segment_key = c.segment_key
            WHERE c.segment_key = ANY(%s)
            ORDER BY c.segment_key, c.window_label, c.bin_index
            """,
            (SEGMENTS,),
        )

        valuation_rows = execute_query(
            conn,
            """
            SELECT
              cohort_quarter,
              segment_key,
              valuation_confidence,
              sample_size,
              mae,
              mape,
              coverage_ratio,
              source_tier_mix
            FROM valuation_cohort_mae
            WHERE segment_key = ANY(%s)
              AND cohort_quarter <= %s
            ORDER BY cohort_quarter DESC, segment_key, valuation_confidence
            """,
            (SEGMENTS, quarter),
        )

        holdout = execute_query(
            conn,
            """
            SELECT holdout_window, COUNT(*) AS entity_count
            FROM backtest_holdout
            WHERE holdout_window = %s
            GROUP BY holdout_window
            """,
            (holdout_window,),
        )

        window_by_segment: dict[str, list[dict]] = {s: [] for s in SEGMENTS}
        for row in windows:
            window_by_segment[row["segment_key"]].append(row)

        curve_error_by_segment: dict[str, float | None] = {s: None for s in SEGMENTS}
        for segment in SEGMENTS:
            seg_rows = [
                r
                for r in curves
                if (r["segment_key"] == segment and r["sample_size"] and r["abs_error"] is not None)
            ]
            if not seg_rows:
                continue
            weighted_abs_error = sum(
                float(r["abs_error"]) * int(r["sample_size"]) for r in seg_rows
            )
            total_samples = sum(int(r["sample_size"]) for r in seg_rows)
            if total_samples > 0:
                curve_error_by_segment[segment] = weighted_abs_error / total_samples

        valuation_by_segment: dict[str, dict[str, dict]] = {s: {} for s in SEGMENTS}
        for row in valuation_rows:
            segment = row["segment_key"]
            confidence = row["valuation_confidence"]
            if segment not in valuation_by_segment:
                continue
            valuation_by_segment[segment][confidence] = {
                "cohortQuarter": str(row["cohort_quarter"]),
                "sampleSize": row["sample_size"],
                "mae": row["mae"],
                "mape": row["mape"],
                "coverageRatio": row["coverage_ratio"],
                "sourceTierMix": row["source_tier_mix"],
            }

        holdout_count = int(holdout[0]["entity_count"]) if holdout else 0

        segment_summary = []
        for seg in segments:
            key = seg["segment_key"]
            evidence_ok = (
                int(seg.get("sample_size") or 0) >= 200
                and bool(seg.get("release_gate_open"))
                and seg.get("survival_auc") is not None
                and float(seg["survival_auc"]) >= 0.65
                and seg.get("calibration_ece") is not None
                and float(seg["calibration_ece"]) <= 0.10
            )
            valuation_latest_high = valuation_by_segment.get(key, {}).get("high")
            high_confidence_allowed = False
            if valuation_latest_high:
                mae = valuation_latest_high.get("mae")
                mape = valuation_latest_high.get("mape")
                sample = int(valuation_latest_high.get("sampleSize") or 0)
                high_confidence_allowed = (
                    sample >= HIGH_CONFIDENCE_VALUATION_MIN_SAMPLE
                    and isinstance(mae, (int, float))
                    and isinstance(mape, (int, float))
                    and float(mae) <= HIGH_CONFIDENCE_VALUATION_MAE_MAX
                    and float(mape) <= HIGH_CONFIDENCE_VALUATION_MAPE_MAX
                )
            segment_summary.append(
                {
                    "segmentKey": key,
                    "sampleSize": seg.get("sample_size"),
                    "survivalAuc": seg.get("survival_auc"),
                    "calibrationEce": seg.get("calibration_ece"),
                    "releaseGateOpen": seg.get("release_gate_open"),
                    "lastBacktestDate": (
                        str(seg.get("last_backtest_date"))
                        if seg.get("last_backtest_date")
                        else None
                    ),
                    "rollingWindows": window_by_segment.get(key, [])[:5],
                    "calibrationCurveError": curve_error_by_segment.get(key),
                    "valuationMae": valuation_by_segment.get(key, {}),
                    "valuationHighConfidenceAllowed": high_confidence_allowed,
                    "evidenceOk": evidence_ok,
                },
            )

        release_ready = (
            all(item["evidenceOk"] for item in segment_summary)
            and all(item["valuationHighConfidenceAllowed"] for item in segment_summary)
            and holdout_count > 0
        )
        summary = {
            "quarter": str(quarter),
            "quarterLabel": quarter_label,
            "generatedAt": date.today().isoformat(),
            "holdoutWindow": holdout_window,
            "holdoutEntityCount": holdout_count,
            "segments": segment_summary,
            "releaseReady": release_ready,
        }

        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        json_path = output / f"quarterly_evidence_{quarter_label}.json"
        md_path = output / f"quarterly_evidence_{quarter_label}.md"

        json_path.write_text(json.dumps(summary, indent=2, default=_json_default), encoding="utf-8")

        md_lines = [
            f"# StartupLens Quarterly Evidence Report — {quarter_label}",
            "",
            f"- Holdout window: `{holdout_window}` ({holdout_count} entities)",
            f"- Release ready: {'yes' if release_ready else 'no'}",
            "",
            (
                "| Segment | Sample | AUC | ECE | Release Gate | Curve Error | "
                "High-Conf Valuation | Evidence OK |"
            ),
            "| --- | ---: | ---: | ---: | :---: | ---: | :---: | :---: |",
        ]
        for seg in segment_summary:
            md_lines.append(
                "| {segment} | {sample} | {auc} | {ece} | {gate} | {curve} | {val} | {ok} |".format(
                    segment=seg["segmentKey"],
                    sample=seg["sampleSize"],
                    auc=seg["survivalAuc"],
                    ece=seg["calibrationEce"],
                    gate="yes" if seg["releaseGateOpen"] else "no",
                    curve=(
                        round(seg["calibrationCurveError"], 4)
                        if isinstance(seg["calibrationCurveError"], (float, int))
                        else "n/a"
                    ),
                    val="yes" if seg["valuationHighConfidenceAllowed"] else "no",
                    ok="yes" if seg["evidenceOk"] else "no",
                ),
            )
        md_lines.append("")
        md_lines.append("## Valuation MAE by Segment (latest by confidence)")
        md_lines.append("")
        for seg in segment_summary:
            md_lines.append(f"### {seg['segmentKey']}")
            valuation = seg["valuationMae"]
            if not valuation:
                md_lines.append("- No realized valuation cohort MAE available yet.")
                md_lines.append("")
                continue
            for conf in ("high", "medium", "low"):
                item = valuation.get(conf)
                if not item:
                    continue
                md_lines.append(
                    f"- {conf}: quarter {item['cohortQuarter']}, n={item['sampleSize']}, "
                    f"MAE={item['mae']}, MAPE={item['mape']}, coverage={item['coverageRatio']}"
                )
            md_lines.append("")

        md_path.write_text("\n".join(md_lines), encoding="utf-8")

        run_rows = execute_query(
            conn,
            """
            SELECT id
            FROM backtest_runs
            WHERE model_family = ANY(%s)
            ORDER BY run_date DESC
            LIMIT 20
            """,
            (SEGMENTS,),
        )
        run_ids = [r["id"] for r in run_rows]

        execute_query(
            conn,
            """
            INSERT INTO quarterly_evidence_reports (
                report_quarter,
                generated_at,
                run_ids,
                summary,
                release_readiness,
                artifact_path,
                notes
            )
            VALUES (%s, now(), %s::jsonb, %s::jsonb, %s, %s, %s)
            ON CONFLICT (report_quarter) DO UPDATE
            SET
                generated_at = now(),
                run_ids = EXCLUDED.run_ids,
                summary = EXCLUDED.summary,
                release_readiness = EXCLUDED.release_readiness,
                artifact_path = EXCLUDED.artifact_path,
                notes = EXCLUDED.notes
            """,
            (
                quarter,
                json.dumps(run_ids),
                json.dumps(summary, default=_json_default),
                release_ready,
                str(json_path),
                "Quarterly evidence generated from rolling segment backtests and valuation cohorts",
            ),
        )
        conn.commit()

        logger.info(
            "quarterly_evidence_report_generated",
            quarter=quarter_label,
            output_json=str(json_path),
            output_md=str(md_path),
            release_ready=release_ready,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    app()
