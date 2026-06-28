// constants.js — Single source of truth for all shared constants
// All values are exposed via window.SigLabUi for backward compat
// Must load before common.js

window.SigLabUi = window.SigLabUi || {};

window.SigLabUi.TRACK_LABELS = {
  trend_signals: "Directional Perps",
  yield_flows: "Systematic Carry",
};

// formatters reference window.SigLabUi.formatNumber/formatPercent
// which are set by common.js (loaded after this file)
window.SigLabUi.METRIC_META = {
  aggregate_score: {
    label: "Aggregate Score",
    formatter: (value) => (window.SigLabUi.formatNumber || ((v) => Number(v).toFixed(3)))(value, 3),
    description: "Primary selector metric, computed from the evaluator's selector windows.",
  },
  median_sharpe: {
    label: "Median Sharpe",
    formatter: (value) => (window.SigLabUi.formatNumber || ((v) => Number(v).toFixed(3)))(value, 3),
    description: "Median Sharpe across the selector windows.",
  },
  median_cagr: {
    label: "Median CAGR",
    formatter: (value) => (window.SigLabUi.formatPercent || ((v) => `${(Number(v) * 100).toFixed(2)}%`))(value),
    description: "Median annualized return across the selector windows.",
  },
  median_total_return: {
    label: "Median Return",
    formatter: (value) => (window.SigLabUi.formatPercent || ((v) => `${(Number(v) * 100).toFixed(2)}%`))(value),
    description: "Median total return across the selector windows.",
  },
  pre_audit_canonical_total_return: {
    label: "Pre-Audit Return",
    formatter: (value) => (window.SigLabUi.formatPercent || ((v) => `${(Number(v) * 100).toFixed(2)}%`))(value),
    description: "Total return measured only up to the audit boundary.",
  },
  median_calmar: {
    label: "Median Calmar",
    formatter: (value) => (window.SigLabUi.formatNumber || ((v) => Number(v).toFixed(3)))(value, 3),
    description: "Median Calmar across the selector windows.",
  },
  validation_total_return: {
    label: "Validation Return",
    formatter: (value) => (window.SigLabUi.formatPercent || ((v) => `${(Number(v) * 100).toFixed(2)}%`))(value),
    description: "Out-of-sample total return across the validation slices used during selection.",
  },
  validation_sharpe: {
    label: "Validation Sharpe",
    formatter: (value) => (window.SigLabUi.formatNumber || ((v) => Number(v).toFixed(3)))(value, 3),
    description: "Out-of-sample Sharpe across the validation slices used during selection.",
  },
  validation_cagr: {
    label: "Validation CAGR",
    formatter: (value) => (window.SigLabUi.formatPercent || ((v) => `${(Number(v) * 100).toFixed(2)}%`))(value),
    description: "Out-of-sample annualized return across the validation slices used during selection.",
  },
  audit_total_return: {
    label: "Audit Return",
    formatter: (value) => (window.SigLabUi.formatPercent || ((v) => `${(Number(v) * 100).toFixed(2)}%`))(value),
    description: "Final untouched out-of-sample total return on the audit slice.",
  },
  audit_sharpe: {
    label: "Audit Sharpe",
    formatter: (value) => (window.SigLabUi.formatNumber || ((v) => Number(v).toFixed(3)))(value, 3),
    description: "Final untouched out-of-sample Sharpe on the audit slice.",
  },
  audit_cagr: {
    label: "Audit CAGR",
    formatter: (value) => (window.SigLabUi.formatPercent || ((v) => `${(Number(v) * 100).toFixed(2)}%`))(value),
    description: "Final untouched out-of-sample annualized return on the audit slice.",
  },
};

// Track colors (was in app.js)
window.SigLabUi.TRACK_COLORS = {
  trend_signals: "#4ade80",
  yield_flows: "#f0b456",
};

// Chart series colors (was in experiment.js)
window.SigLabUi.COLORS = {
  equity: "#4ade80",
  grossExposure: "#f0b456",
  cashBalance: "#60a5fa",
  marginHeadroom: "#a3e635",
  turnover: "#a3b5a8",
};

// Action metadata (was in experiment.js)
window.SigLabUi.ACTION_META = {
  buy: { label: "Buy", color: "#4ade80", tone: "" },
  sell: { label: "Sell", color: "#9fb4a5", tone: "slate" },
  short: { label: "Short", color: "#f0b456", tone: "amber" },
  cover: { label: "Cover", color: "#60a5fa", tone: "" },
  flip_long: { label: "Flip to Long", color: "#4ade80", tone: "" },
  flip_short: { label: "Flip to Short", color: "#f0b456", tone: "amber" },
  rebalance: { label: "Rebalance", color: "#a3b5a8", tone: "slate" },
};

// Family guide (was in app.js)
window.SigLabUi.FAMILY_GUIDE = {
  perp_multi_asset_decision: {
    track: "trend_signals",
    title: "Multi-Asset Decision",
    summary: "Independent per-asset perp decisions instead of forced relative ranking.",
    execution: "Each asset can be long, short, or flat based on its own score and threshold.",
    hedge: "Perps only; can be net long, net short, mixed, or flat.",
  },
  perp_pair_trade_unlevered: {
    track: "trend_signals",
    title: "Pair Trade 1x",
    summary: "Relative-value perp spread between two assets with gross exposure capped at 1x.",
    execution: "Can be long the first leg, short the second, reverse the spread, or sit flat.",
    hedge: "The second leg is the hedge leg; leverage stays capped.",
  },
  perp_pair_trade_levered: {
    track: "trend_signals",
    title: "Pair Trade 3x",
    summary: "Relative-value perp spread between two assets with signal-scaled gross exposure up to 3x.",
    execution: "Can be long the first leg, short the second, reverse the spread, or sit flat.",
    hedge: "The second leg is the hedge leg; stronger signals can scale gross exposure higher.",
  },
  perp_basket_neutral_unlevered: {
    track: "trend_signals",
    title: "Basket Neutral 1x",
    summary: "Cross-sectional long/short perp basket that ranks assets and holds offsetting long and short books.",
    execution: "Buys the strongest-ranked names and shorts the weakest-ranked names with unlevered gross exposure.",
    hedge: "Long and short baskets offset each other; net exposure stays near neutral.",
  },
  perp_basket_neutral_levered: {
    track: "trend_signals",
    title: "Basket Neutral 3x",
    summary: "Cross-sectional long/short perp basket with signal-scaled leverage on both sides of the book.",
    execution: "Buys the strongest-ranked names and shorts the weakest-ranked names with higher gross target capacity.",
    hedge: "Long and short baskets offset each other, but stronger signals can scale gross exposure higher.",
  },
  perp_multi_asset_carry: {
    track: "trend_signals",
    title: "Multi-Asset Carry",
    summary: "Cross-sectional perp carry rotation that buys cheap carry and shorts rich carry across a perp basket.",
    execution: "Ranks assets by funding carry state, then builds long and short books from the best and worst carry profiles.",
    hedge: "Cross-sectional long/short basket; hedge quality varies with the selected long and short mix.",
  },
  basis_spread: {
    track: "yield_flows",
    title: "Basis Spread",
    summary: "Classic spot-versus-perp funding capture.",
    execution: "Long synthetic spot and short perp on the same asset.",
    hedge: "Underlying-neutral by construction.",
  },
  stable_pt_ladder: {
    track: "yield_flows",
    title: "Stable PT Ladder",
    summary: "Fixed-income style rotation among stable PT markets.",
    execution: "Buys discounted stable PTs and rolls forward before expiry.",
    hedge: "Usually unhedged because the basis is already USD-like.",
  },
  pt_yield_rotation: {
    track: "yield_flows",
    title: "PT Yield Rotation",
    summary: "Rotates across PT markets with attractive carry and discount dynamics.",
    execution: "Buys the highest-ranked PTs and can hedge beta with perps.",
    hedge: "Optional perp hedge on the PT underlying.",
  },
  lending_carry_rotation: {
    track: "yield_flows",
    title: "Lending Carry Rotation",
    summary: "Ranks lending markets by observed carry, rewards, and liquidity.",
    execution: "Allocates to the best lending opportunities and can hedge beta.",
    hedge: "Optional perp hedge on the basis root.",
  },
};

window.SigLabUi.DEFAULT_DISPLAY_CAPITAL_USD = 100000;
window.SigLabUi.DISPLAY_CAPITAL_STORAGE_KEY = "siglab.displayCapitalUsd";
window.SigLabUi.AUTO_REFRESH_INTERVAL_MS = 30000;
window.SigLabUi.OPS_REFRESH_INTERVAL_MS = 60000;

window.SigLabUi.API = {
  RUNS: "/api/runs",
  EXPERIMENTS: "/api/experiments",
  OPS: "/api/ops",
  EXPERIMENT_SERIES: (hash) => `/api/experiments/${encodeURIComponent(hash)}/series`,
  EXPERIMENT_DEPLOY: (hash) => `/api/experiments/${encodeURIComponent(hash)}/deploy`,
  EXPERIMENT_DETAIL: (hash) => `/api/experiments/${encodeURIComponent(hash)}`,
  RUN_LINK: (id) => `/runs/${encodeURIComponent(id)}`,
  EXPERIMENT_LINK: (hash) => `/experiments/${encodeURIComponent(hash)}`,
};
