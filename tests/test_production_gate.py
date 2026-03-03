"""Tests for production_gate.py safety behavior."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Allow importing scripts module
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from production_gate import main  # noqa: E402


def _run_result(returncode: int, stderr: str = "") -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stderr = stderr
    return result


def _base_run() -> dict:
    return {
        "id": 10,
        "run_date": "2026-03-03T10:00:00Z",
        "metrics": {"survival_auc": 0.7},
        "pass_fail": {"survival_auc": {"value": 0.7, "threshold": 0.65, "passed": True}},
        "all_passed": True,
        "notes": "ok",
    }


@patch("production_gate.get_settings")
@patch("production_gate.get_connection")
@patch("production_gate.refresh_matview")
@patch("production_gate._load_latest_backtest_run")
@patch("production_gate.subprocess.run")
def test_fails_when_backtest_does_not_create_fresh_run(
    mock_subprocess: MagicMock,
    mock_load_latest: MagicMock,
    mock_refresh: MagicMock,
    mock_get_connection: MagicMock,
    mock_get_settings: MagicMock,
):
    conn = MagicMock()
    mock_get_connection.return_value = conn
    mock_get_settings.return_value = MagicMock()
    mock_subprocess.return_value = _run_result(returncode=0)

    first = _base_run()
    second = _base_run()
    second["id"] = first["id"]  # stale (no new run)
    mock_load_latest.side_effect = [first, second]

    with pytest.raises(SystemExit) as exc:
        main(skip_backtest=False, skip_export=True)

    assert exc.value.code == 2
    mock_refresh.assert_called_once()
    conn.close.assert_called_once()


@patch("production_gate.get_settings")
@patch("production_gate.get_connection")
@patch("production_gate.refresh_matview")
@patch("production_gate._load_latest_backtest_run")
@patch("production_gate.subprocess.run")
def test_fails_when_model_export_fails(
    mock_subprocess: MagicMock,
    mock_load_latest: MagicMock,
    mock_refresh: MagicMock,
    mock_get_connection: MagicMock,
    mock_get_settings: MagicMock,
):
    conn = MagicMock()
    mock_get_connection.return_value = conn
    mock_get_settings.return_value = MagicMock()
    mock_load_latest.return_value = _base_run()
    mock_subprocess.return_value = _run_result(returncode=1, stderr="export fail")

    with pytest.raises(SystemExit) as exc:
        main(skip_backtest=True, skip_export=False, output="web/public/model/model.json")

    assert exc.value.code == 2
    mock_refresh.assert_called_once()
    conn.close.assert_called_once()
