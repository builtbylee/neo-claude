"""Tests for holdout quarantine manager."""

from unittest.mock import MagicMock, patch

from startuplens.backtest.holdout import (
    filter_training_entities,
    get_holdout_entity_ids,
    get_holdout_summary,
    is_entity_held_out,
    quarantine_holdout,
)


class TestQuarantineHoldout:
    def test_inserts_correct_number_of_rows(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_many", return_value=3) as mock:
            result = quarantine_holdout(
                conn,
                entity_ids=["e1", "e2", "e3"],
                window_label="2023-2025",
            )
        assert result == 3
        mock.assert_called_once()
        args = mock.call_args
        assert len(args[0][2]) == 3  # 3 param tuples

    def test_sets_company_ids_to_none_when_omitted(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_many", return_value=2) as mock:
            quarantine_holdout(conn, ["e1", "e2"], "2023-2025")
        rows = mock.call_args[0][2]
        assert all(row[2] is None for row in rows)

    def test_uses_provided_company_ids(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_many", return_value=2) as mock:
            quarantine_holdout(conn, ["e1", "e2"], "2023-2025", company_ids=["c1", "c2"])
        rows = mock.call_args[0][2]
        assert rows[0][2] == "c1"
        assert rows[1][2] == "c2"

    def test_empty_entity_ids_returns_zero(self):
        conn = MagicMock()
        result = quarantine_holdout(conn, [], "2023-2025")
        assert result == 0

    def test_window_label_in_all_rows(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_many", return_value=2) as mock:
            quarantine_holdout(conn, ["e1", "e2"], "2023-2025")
        rows = mock.call_args[0][2]
        assert all(row[3] == "2023-2025" for row in rows)

    def test_each_row_has_unique_uuid(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_many", return_value=3) as mock:
            quarantine_holdout(conn, ["e1", "e2", "e3"], "2023-2025")
        rows = mock.call_args[0][2]
        uuids = [row[0] for row in rows]
        assert len(set(uuids)) == 3


class TestGetHoldoutEntityIds:
    def test_returns_entity_ids(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.holdout.execute_query",
            return_value=[{"entity_id": "e1"}, {"entity_id": "e2"}],
        ):
            result = get_holdout_entity_ids(conn, "2023-2025")
        assert result == ["e1", "e2"]

    def test_empty_result(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_query", return_value=[]):
            result = get_holdout_entity_ids(conn, "2023-2025")
        assert result == []


class TestIsEntityHeldOut:
    def test_returns_true_when_found(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.holdout.execute_query",
            return_value=[{"?column?": 1}],
        ):
            assert is_entity_held_out(conn, "e1", "2023-2025") is True

    def test_returns_false_when_not_found(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_query", return_value=[]):
            assert is_entity_held_out(conn, "e1", "2023-2025") is False


class TestFilterTrainingEntities:
    def test_removes_holdout_entities(self):
        conn = MagicMock()
        with patch(
            "startuplens.backtest.holdout.execute_query",
            return_value=[{"entity_id": "e2"}, {"entity_id": "e4"}],
        ):
            result = filter_training_entities(
                conn, ["e1", "e2", "e3", "e4", "e5"], "2023-2025"
            )
        assert result == ["e1", "e3", "e5"]

    def test_returns_all_when_no_holdout(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_query", return_value=[]):
            result = filter_training_entities(conn, ["e1", "e2"], "2023-2025")
        assert result == ["e1", "e2"]

    def test_empty_input(self):
        conn = MagicMock()
        with patch("startuplens.backtest.holdout.execute_query", return_value=[]):
            result = filter_training_entities(conn, [], "2023-2025")
        assert result == []


class TestGetHoldoutSummary:
    def test_returns_summary_rows(self):
        conn = MagicMock()
        expected = [
            {"holdout_window": "2019", "entity_count": 50, "created_at": "2025-01-01"},
            {"holdout_window": "2023-2025", "entity_count": 120, "created_at": "2025-01-01"},
        ]
        with patch("startuplens.backtest.holdout.execute_query", return_value=expected):
            result = get_holdout_summary(conn)
        assert len(result) == 2
        assert result[0]["holdout_window"] == "2019"
