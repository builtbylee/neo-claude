"""Backtest infrastructure: walk-forward validation, baselines, portfolio simulation."""

from startuplens.backtest.baselines import (
    ScoredDeal,
    heuristic_baseline,
    random_baseline,
    sector_momentum_baseline,
)
from startuplens.backtest.holdout import (
    filter_training_entities,
    get_holdout_entity_ids,
    get_holdout_summary,
    is_entity_held_out,
    quarantine_holdout,
)
from startuplens.backtest.metrics import (
    MetricResult,
    all_must_pass_met,
    compute_ece,
    evaluate_backtest,
)
from startuplens.backtest.provenance import (
    compare_runs,
    get_backtest_run,
    get_latest_runs,
    get_passing_runs,
    log_backtest_run,
)
from startuplens.backtest.simulator import (
    InvestorPolicy,
    SimulatedPortfolio,
    simulate_portfolio,
    simulate_walk_forward,
)
from startuplens.backtest.splitter import (
    TimeWindow,
    generate_walk_forward_windows,
    split_entities_by_window,
)

__all__ = [
    # splitter
    "TimeWindow",
    "generate_walk_forward_windows",
    "split_entities_by_window",
    # metrics
    "MetricResult",
    "compute_ece",
    "evaluate_backtest",
    "all_must_pass_met",
    # baselines
    "ScoredDeal",
    "random_baseline",
    "heuristic_baseline",
    "sector_momentum_baseline",
    # simulator
    "InvestorPolicy",
    "SimulatedPortfolio",
    "simulate_portfolio",
    "simulate_walk_forward",
    # holdout
    "quarantine_holdout",
    "get_holdout_entity_ids",
    "is_entity_held_out",
    "filter_training_entities",
    "get_holdout_summary",
    # provenance
    "log_backtest_run",
    "get_backtest_run",
    "get_latest_runs",
    "get_passing_runs",
    "compare_runs",
]
