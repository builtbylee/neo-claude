"""Tests for entity resolution system.

All database interactions are mocked — no real PostgreSQL needed.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from startuplens.entity_resolution.deterministic import (
    create_canonical_entity,
    link_entity,
    match_by_legal_name,
    match_by_source_id,
    normalize_name,
)
from startuplens.entity_resolution.probabilistic import (
    build_training_pairs,
    merge_entities,
)
from startuplens.entity_resolution.resolver import (
    resolve_entity,
    run_entity_resolution,
)
from startuplens.entity_resolution.validation import (
    compute_entity_resolution_metrics,
    generate_validation_report,
)

# =========================================================================
# normalize_name
# =========================================================================


class TestNormalizeName:
    """Tests for company name normalisation."""

    def test_strips_ltd_suffix(self):
        assert normalize_name("Acme Ltd") == "acme"

    def test_strips_limited_suffix(self):
        assert normalize_name("Acme Limited") == "acme"

    def test_strips_inc_suffix(self):
        assert normalize_name("Acme Inc") == "acme"

    def test_strips_llc_suffix(self):
        assert normalize_name("Acme LLC") == "acme"

    def test_strips_plc_suffix(self):
        assert normalize_name("BigCorp PLC") == "bigcorp"

    def test_strips_gmbh_suffix(self):
        assert normalize_name("TechStart GmbH") == "techstart"

    def test_unidecode_transliteration(self):
        # ü -> u, é -> e
        assert normalize_name("Über Café Ltd") == "uber cafe"

    def test_collapses_whitespace(self):
        assert normalize_name("  Acme   Corp   Ltd  ") == "acme corp"

    def test_removes_punctuation(self):
        assert normalize_name("O'Brien & Sons, Inc.") == "obrien sons"

    def test_lowercase(self):
        assert normalize_name("ACME TECHNOLOGIES") == "acme technologies"

    def test_empty_string(self):
        assert normalize_name("") == ""

    def test_name_with_numbers(self):
        assert normalize_name("Company 123 Ltd") == "company 123"

    def test_suffix_with_period(self):
        assert normalize_name("Acme Ltd.") == "acme"


# =========================================================================
# match_by_source_id
# =========================================================================


class TestMatchBySourceId:
    """Tests for source-ID based lookup."""

    def test_returns_entity_id_on_match(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.deterministic.execute_query",
            return_value=[{"entity_id": "uuid-123"}],
        ):
            result = match_by_source_id(conn, "sec_edgar", "CIK-0001")
            assert result == "uuid-123"

    def test_returns_none_on_no_match(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.deterministic.execute_query",
            return_value=[],
        ):
            result = match_by_source_id(conn, "sec_edgar", "CIK-9999")
            assert result is None


# =========================================================================
# match_by_legal_name
# =========================================================================


class TestMatchByLegalName:
    """Tests for normalised-name lookup."""

    def test_returns_entity_id_on_match(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.deterministic.execute_query",
            return_value=[{"entity_id": "uuid-456"}],
        ):
            result = match_by_legal_name(conn, "Acme Ltd", "GB")
            assert result == "uuid-456"

    def test_returns_none_on_no_match(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.deterministic.execute_query",
            return_value=[],
        ):
            result = match_by_legal_name(conn, "NoSuchCompany", "US")
            assert result is None


# =========================================================================
# create_canonical_entity
# =========================================================================


class TestCreateCanonicalEntity:
    """Tests for canonical entity creation."""

    def test_returns_uuid_string(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.deterministic.execute_query",
        ) as mock_eq:
            entity_id = create_canonical_entity(conn, "Acme Ltd", "GB")
            # Should be a valid UUID-like string
            assert len(entity_id) == 36
            assert entity_id.count("-") == 4
            # Check SQL was called with normalised values
            args = mock_eq.call_args
            assert args[0][2][1] == "acme"  # normalised name
            assert args[0][2][2] == "gb"  # lowercased country


# =========================================================================
# link_entity
# =========================================================================


class TestLinkEntity:
    """Tests for entity link creation."""

    def test_inserts_link_row(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.deterministic.execute_query",
        ) as mock_eq:
            link_entity(
                conn,
                entity_id="uuid-123",
                source="companies_house",
                source_identifier="CH-001",
                confidence=95,
                match_method="deterministic",
                source_name="Acme Limited",
            )
            mock_eq.assert_called_once()
            sql = mock_eq.call_args[0][1]
            assert "INSERT INTO entity_links" in sql
            params = mock_eq.call_args[0][2]
            assert params[1] == "uuid-123"  # entity_id
            assert params[2] == "companies_house"  # source
            assert params[3] == "CH-001"  # source_identifier
            assert params[4] == "Acme Limited"  # source_name
            assert params[5] == "deterministic"  # match_method
            assert params[6] == 95  # confidence


# =========================================================================
# resolve_entity (orchestrator)
# =========================================================================


class TestResolveEntity:
    """Tests for the deterministic resolution orchestrator."""

    def test_returns_existing_on_source_id_match(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.resolver.match_by_source_id",
            return_value="existing-uuid",
        ):
            result = resolve_entity(conn, "Acme", "GB", "sec_edgar", "CIK-001")
            assert result == "existing-uuid"

    def test_matches_by_name_when_source_id_misses(self):
        conn = MagicMock()
        with (
            patch(
                "startuplens.entity_resolution.resolver.match_by_source_id",
                return_value=None,
            ),
            patch(
                "startuplens.entity_resolution.resolver.match_by_legal_name",
                return_value="name-match-uuid",
            ),
            patch(
                "startuplens.entity_resolution.resolver.link_entity",
            ) as mock_link,
        ):
            result = resolve_entity(conn, "Acme Ltd", "GB", "sec_edgar", "CIK-002")
            assert result == "name-match-uuid"
            mock_link.assert_called_once()

    def test_creates_new_entity_when_no_match(self):
        conn = MagicMock()
        with (
            patch(
                "startuplens.entity_resolution.resolver.match_by_source_id",
                return_value=None,
            ),
            patch(
                "startuplens.entity_resolution.resolver.match_by_legal_name",
                return_value=None,
            ),
            patch(
                "startuplens.entity_resolution.resolver.create_canonical_entity",
                return_value="new-uuid",
            ) as mock_create,
            patch(
                "startuplens.entity_resolution.resolver.link_entity",
            ) as mock_link,
        ):
            result = resolve_entity(conn, "NewCo", "US", "sec_edgar", "CIK-003")
            assert result == "new-uuid"
            mock_create.assert_called_once_with(conn, "NewCo", "US")
            mock_link.assert_called_once()


# =========================================================================
# run_entity_resolution (batch)
# =========================================================================


class TestRunEntityResolution:
    """Tests for batch deterministic resolution."""

    def test_batch_stats_all_new(self):
        conn = MagicMock()
        records = [
            {"name": "Alpha", "country": "GB", "source": "ch", "source_identifier": "1"},
            {"name": "Beta", "country": "US", "source": "sec", "source_identifier": "2"},
        ]
        with (
            patch(
                "startuplens.entity_resolution.resolver.match_by_source_id",
                return_value=None,
            ),
            patch(
                "startuplens.entity_resolution.resolver.match_by_legal_name",
                return_value=None,
            ),
            patch(
                "startuplens.entity_resolution.resolver.resolve_entity",
                return_value="new-id",
            ),
        ):
            stats = run_entity_resolution(conn, records)
            assert stats["created"] == 2
            assert stats["matched"] == 0
            assert stats["total"] == 2

    def test_batch_stats_all_matched(self):
        conn = MagicMock()
        records = [
            {"name": "Alpha", "country": "GB", "source": "ch", "source_identifier": "1"},
        ]
        with (
            patch(
                "startuplens.entity_resolution.resolver.match_by_source_id",
                return_value="existing-id",
            ),
            patch(
                "startuplens.entity_resolution.resolver.resolve_entity",
                return_value="existing-id",
            ),
        ):
            stats = run_entity_resolution(conn, records)
            assert stats["matched"] == 1
            assert stats["created"] == 0
            assert stats["total"] == 1

    def test_empty_batch(self):
        conn = MagicMock()
        stats = run_entity_resolution(conn, [])
        assert stats == {"matched": 0, "created": 0, "total": 0}


# =========================================================================
# merge_entities
# =========================================================================


class TestMergeEntities:
    """Tests for entity merging."""

    def test_reassigns_links_and_deletes(self):
        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.probabilistic.execute_query",
        ) as mock_eq:
            merge_entities(conn, keep_id="keep-uuid", merge_id="merge-uuid")

            assert mock_eq.call_count == 2
            # First call: UPDATE entity_links
            update_call = mock_eq.call_args_list[0]
            assert "UPDATE entity_links" in update_call[0][1]
            assert update_call[0][2] == ("keep-uuid", "merge-uuid")

            # Second call: DELETE canonical_entities
            delete_call = mock_eq.call_args_list[1]
            assert "DELETE FROM canonical_entities" in delete_call[0][1]
            assert delete_call[0][2] == ("merge-uuid",)


# =========================================================================
# build_training_pairs
# =========================================================================


class TestBuildTrainingPairs:
    """Tests for training data extraction."""

    def test_returns_name_country_dicts(self):
        conn = MagicMock()
        rows = [
            {"entity_id": "e1", "primary_name": "acme", "country": "gb"},
            {"entity_id": "e2", "primary_name": "beta co", "country": "us"},
        ]
        with patch(
            "startuplens.entity_resolution.probabilistic.execute_query",
            return_value=rows,
        ):
            pairs = build_training_pairs(conn)
            assert len(pairs) == 2
            assert pairs[0]["name"] == "acme"
            assert pairs[0]["country"] == "gb"
            assert pairs[1]["entity_id"] == "e2"


# =========================================================================
# Validation metrics
# =========================================================================


class TestComputeMetrics:
    """Tests for precision/recall/F1 computation."""

    def _make_pair(self, src_a, id_a, src_b, id_b, same: bool) -> dict:
        return {
            "source_a": {"source": src_a, "source_identifier": id_a},
            "source_b": {"source": src_b, "source_identifier": id_b},
            "same_entity": same,
        }

    def test_perfect_resolution(self):
        """All pairs correctly resolved."""
        ground_truth = [
            self._make_pair("ch", "1", "sec", "A", same=True),
            self._make_pair("ch", "2", "sec", "B", same=False),
        ]

        def side_effect(_conn, source, source_id):
            lookup = {
                ("ch", "1"): "entity-1",
                ("sec", "A"): "entity-1",  # same entity
                ("ch", "2"): "entity-2",
                ("sec", "B"): "entity-3",  # different entity
            }
            return lookup.get((source, source_id))

        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.validation.match_by_source_id",
            side_effect=side_effect,
        ):
            metrics = compute_entity_resolution_metrics(conn, ground_truth)
            assert metrics["precision"] == 1.0
            assert metrics["recall"] == 1.0
            assert metrics["f1"] == 1.0

    def test_false_positive(self):
        """System says same entity, but they differ."""
        ground_truth = [
            self._make_pair("ch", "1", "sec", "A", same=False),
        ]

        def side_effect(_conn, source, source_id):
            # System thinks they're the same (both map to entity-1)
            return "entity-1"

        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.validation.match_by_source_id",
            side_effect=side_effect,
        ):
            metrics = compute_entity_resolution_metrics(conn, ground_truth)
            assert metrics["false_positives"] == 1
            assert metrics["precision"] == 0.0

    def test_false_negative(self):
        """System says different entities, but they're the same."""
        ground_truth = [
            self._make_pair("ch", "1", "sec", "A", same=True),
        ]

        def side_effect(_conn, source, source_id):
            lookup = {("ch", "1"): "entity-1", ("sec", "A"): "entity-2"}
            return lookup.get((source, source_id))

        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.validation.match_by_source_id",
            side_effect=side_effect,
        ):
            metrics = compute_entity_resolution_metrics(conn, ground_truth)
            assert metrics["false_negatives"] == 1
            assert metrics["recall"] == 0.0

    def test_unresolved_record_counts_as_fn_if_expected_same(self):
        """If one record is unresolved and pair should be same, it's a FN."""
        ground_truth = [
            self._make_pair("ch", "1", "sec", "A", same=True),
        ]

        def side_effect(_conn, source, source_id):
            if source == "ch":
                return "entity-1"
            return None  # sec record not resolved

        conn = MagicMock()
        with patch(
            "startuplens.entity_resolution.validation.match_by_source_id",
            side_effect=side_effect,
        ):
            metrics = compute_entity_resolution_metrics(conn, ground_truth)
            assert metrics["false_negatives"] == 1

    def test_empty_ground_truth(self):
        conn = MagicMock()
        metrics = compute_entity_resolution_metrics(conn, [])
        assert metrics["precision"] == 0.0
        assert metrics["recall"] == 0.0
        assert metrics["f1"] == 0.0
        assert metrics["total_pairs"] == 0


# =========================================================================
# Validation report formatting
# =========================================================================


class TestGenerateValidationReport:
    """Tests for the formatted report output."""

    def test_excellent_report(self):
        metrics = {
            "precision": 0.98,
            "recall": 0.97,
            "f1": 0.975,
            "true_positives": 50,
            "false_positives": 1,
            "false_negatives": 2,
            "total_pairs": 53,
        }
        report = generate_validation_report(metrics)
        assert "EXCELLENT" in report
        assert "0.9750" in report
        assert "50" in report

    def test_poor_report(self):
        metrics = {
            "precision": 0.4,
            "recall": 0.3,
            "f1": 0.34,
            "true_positives": 5,
            "false_positives": 8,
            "false_negatives": 12,
            "total_pairs": 25,
        }
        report = generate_validation_report(metrics)
        assert "POOR" in report

    def test_report_contains_header(self):
        metrics = {
            "precision": 0.9,
            "recall": 0.9,
            "f1": 0.9,
            "true_positives": 10,
            "false_positives": 1,
            "false_negatives": 1,
            "total_pairs": 12,
        }
        report = generate_validation_report(metrics)
        assert "Entity Resolution Validation Report" in report
        assert "Precision:" in report
        assert "Recall:" in report
        assert "F1 Score:" in report
