"""Tests for the walk-forward time-window splitter."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from startuplens.backtest.splitter import (
    TimeWindow,
    generate_walk_forward_windows,
    split_entities_by_window,
)

# ------------------------------------------------------------------
# Window generation
# ------------------------------------------------------------------


class TestGenerateWalkForwardWindows:
    """Verify the canonical five walk-forward windows."""

    def test_returns_exactly_five_windows(self):
        windows = generate_walk_forward_windows()
        assert len(windows) == 5

    def test_all_are_time_window_instances(self):
        windows = generate_walk_forward_windows()
        for w in windows:
            assert isinstance(w, TimeWindow)

    def test_window_0_boundaries(self):
        w = generate_walk_forward_windows()[0]
        assert w.train_start == date(2016, 1, 1)
        assert w.train_end == date(2018, 12, 31)
        assert w.test_start == date(2019, 1, 1)
        assert w.test_end == date(2019, 12, 31)
        assert w.label == "Train 2016-2018, Test 2019"

    def test_window_1_boundaries(self):
        w = generate_walk_forward_windows()[1]
        assert w.train_start == date(2016, 1, 1)
        assert w.train_end == date(2019, 12, 31)
        assert w.test_start == date(2020, 1, 1)
        assert w.test_end == date(2020, 12, 31)

    def test_window_2_boundaries(self):
        w = generate_walk_forward_windows()[2]
        assert w.train_end == date(2020, 12, 31)
        assert w.test_start == date(2021, 1, 1)
        assert w.test_end == date(2021, 12, 31)

    def test_window_3_boundaries(self):
        w = generate_walk_forward_windows()[3]
        assert w.train_end == date(2021, 12, 31)
        assert w.test_start == date(2022, 1, 1)
        assert w.test_end == date(2022, 12, 31)

    def test_window_4_final_holdout(self):
        w = generate_walk_forward_windows()[4]
        assert w.train_end == date(2022, 12, 31)
        assert w.test_start == date(2023, 1, 1)
        assert w.test_end == date(2025, 12, 31)
        assert w.label == "Train 2016-2022, Test 2023-2025"

    def test_train_always_starts_2016(self):
        for w in generate_walk_forward_windows():
            assert w.train_start == date(2016, 1, 1)

    def test_train_end_always_before_test_start(self):
        for w in generate_walk_forward_windows():
            assert w.train_end < w.test_start

    def test_windows_are_expanding(self):
        windows = generate_walk_forward_windows()
        for i in range(1, len(windows)):
            assert windows[i].train_end > windows[i - 1].train_end


# ------------------------------------------------------------------
# Entity splitting
# ------------------------------------------------------------------


@dataclass
class _FakeEntity:
    campaign_date: str
    name: str = ""


class TestSplitEntitiesByWindow:
    """Verify entity partitioning logic."""

    def test_entities_in_train_range(self):
        window = generate_walk_forward_windows()[0]  # train 2016-2018, test 2019
        entities = [
            _FakeEntity(campaign_date="2017-06-01", name="A"),
            _FakeEntity(campaign_date="2019-03-15", name="B"),
        ]
        train, test = split_entities_by_window(entities, window)
        assert len(train) == 1
        assert train[0].name == "A"
        assert len(test) == 1
        assert test[0].name == "B"

    def test_boundary_dates_inclusive(self):
        window = generate_walk_forward_windows()[0]
        entities = [
            _FakeEntity(campaign_date="2016-01-01", name="start_train"),
            _FakeEntity(campaign_date="2018-12-31", name="end_train"),
            _FakeEntity(campaign_date="2019-01-01", name="start_test"),
            _FakeEntity(campaign_date="2019-12-31", name="end_test"),
        ]
        train, test = split_entities_by_window(entities, window)
        assert len(train) == 2
        assert len(test) == 2

    def test_entity_outside_both_ranges_excluded(self):
        window = generate_walk_forward_windows()[0]  # train 2016-2018, test 2019
        entities = [
            _FakeEntity(campaign_date="2015-12-31", name="too_early"),
            _FakeEntity(campaign_date="2020-01-01", name="too_late"),
        ]
        train, test = split_entities_by_window(entities, window)
        assert len(train) == 0
        assert len(test) == 0

    def test_accepts_date_objects(self):
        window = generate_walk_forward_windows()[0]
        entities = [_FakeEntity(campaign_date=date(2017, 5, 10).isoformat())]
        train, test = split_entities_by_window(entities, window)
        assert len(train) == 1

    def test_empty_input(self):
        window = generate_walk_forward_windows()[0]
        train, test = split_entities_by_window([], window)
        assert train == []
        assert test == []

    def test_split_with_sample_deals(self, sample_deals):
        """Use the shared fixture to verify splitting works with ScoredDeal objects."""
        window = generate_walk_forward_windows()[0]  # train 2016-2018, test 2019
        train, test = split_entities_by_window(sample_deals, window)
        # All returned entities should have dates in the correct range
        for deal in train:
            d = date.fromisoformat(deal.campaign_date)
            assert date(2016, 1, 1) <= d <= date(2018, 12, 31)
        for deal in test:
            d = date.fromisoformat(deal.campaign_date)
            assert date(2019, 1, 1) <= d <= date(2019, 12, 31)
