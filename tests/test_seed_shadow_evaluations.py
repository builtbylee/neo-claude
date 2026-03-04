"""Tests for scripts/run_seed_shadow_evaluations.py."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from run_seed_shadow_evaluations import main  # noqa: E402


@patch("run_seed_shadow_evaluations.typer.echo")
@patch("run_seed_shadow_evaluations._seed_missing_model_evaluations")
@patch("run_seed_shadow_evaluations._load_cycle_items")
@patch("run_seed_shadow_evaluations._load_cycle")
@patch("run_seed_shadow_evaluations.get_connection")
@patch("run_seed_shadow_evaluations.get_settings")
def test_seeds_and_reports_coverage(
    mock_settings,
    mock_conn,
    mock_load_cycle,
    mock_load_items,
    mock_seed,
    mock_echo,
) -> None:
    conn = MagicMock()
    mock_conn.return_value = conn
    mock_settings.return_value = MagicMock()
    mock_load_cycle.return_value = {"id": "cycle-1", "cycle_name": "shadow-q1"}
    mock_load_items.return_value = [
        {
            "company_name": "Alpha",
            "model_recommendation": None,
            "data_sufficiency": {"score": 80, "category_count": 4},
        },
        {
            "company_name": "Beta",
            "model_recommendation": "watch",
            "data_sufficiency": {"score": 85, "category_count": 4},
        },
    ]
    mock_seed.return_value = 1

    main(
        cycle_id=None,
        max_items=50,
        min_sufficiency_score=50.0,
        min_category_count=3,
        min_model_coverage=0.5,
    )

    mock_seed.assert_called_once()
    mock_echo.assert_called_once()
    conn.close.assert_called_once()


def test_rejects_invalid_threshold() -> None:
    with pytest.raises(Exception):
        main(min_model_coverage=1.5)
