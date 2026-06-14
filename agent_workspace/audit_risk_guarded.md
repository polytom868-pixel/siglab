# Risk / Guardian Invariant Audit (Cross-Module)

Scope: every documented guard, kill switch, position-size formula, drawdown check,
and fragility detector in `siglab/risk/guardian.py`, `siglab/evaluation/{gates,score,runner}.py`,
`siglab/dashboard/{risk_utils,routes}.py`, `siglab/orchestration/optimizer_runner.py`,
`siglab/orchestration/trials.py`, `siglab/live/{paper_client,promotion,runtime}.py`.

Every claim is anchored by file:line.

---

## 1. GUARD RULES

Documented rule → implementing code.

### 1.1 Composite risk score (Risk Guardian)

- **Documented**: "composite score synthesizes Sharpe, drawdown, concentration, correlation" — `docs/module-risk-guardian.md:7`, weights table `docs/module-risk-guardian.md:78-82` (Sharpe 0.25, DD 0.30, concentration 0.25, corr 0.20).
- **Implementation**: weights `siglab/risk/guardian.py:27-32`; sub-score normalisers `guardian.py:102-156`; `compute_composite_score` `guardian.py:159-208`.

### 1.2 Sharpe sub-score normalisation

- **Documented**: clipped to [-20, +20]; 0 → 0, ≥3 → 1.0 — `docs/module-risk-guardian.md:93-98`.
- **Implementation**: `guardian.py:35-36` (bounds), `guardian.py:102-113` (`_normalize_sharpe_score`).

### 1.3 Drawdown sub-score normalisation

- **Documented**: clipped to [-1.0, 0.0]; 0 → 1.0, ≤ -0.20 → 0.0 — `docs/module-risk-guardian.md:104-109`.
- **Implementation**: `guardian.py:37-38` (bounds), `guardian.py:46` (target), `guardian.py:116-127`.

### 1.4 Concentration sub-score normalisation

- **Documented**: clipped to [0, 1.0]; 0 → 1.0, ≥ 0.20 → 0.0 — `docs/module-risk-guardian.md:114-120`.
- **Implementation**: `guardian.py:39-40`, `guardian.py:47`, `guardian.py:130-142`.

### 1.5 Correlation sub-score normalisation

- **Documented**: clipped to [0, 1.0]; 0 → 1.0, ≥ 0.70 → 0.0 — `docs/module-risk-guardian.md:127-131`.
- **Implementation**: `guardian.py:41-42`, `guardian.py:48`, `guardian.py:145-156`.

### 1.6 Concentration-limit breach check

- **Documented**: `check_concentration` returns `BreachReport`; supports `"default"` fallback — `docs/module-risk-guardian.md:208-219`.
- **Implementation**: `guardian.py:389-430`. **No caller** invokes it: `siglab/dashboard/risk_utils.py:73-201` and `siglab/dashboard/routes.py:474-509` never call `check_concentration`. The dashboard synthesises concentration from session count (HHI), not from allocation limits (see §7).

### 1.7 Risk-threshold alerts

- **Documented**: per-metric `info`/`warning`/`critical` + `direction: above|below` — `docs/module-risk-guardian.md:186-204`.
- **Implementation**: `guardian.py:433-526`. **No caller** passes a threshold config; the dashboard generates ad-hoc alerts directly from drawdown events (`risk_utils.py:172-187`).

### 1.8 Evaluation gates (VAL-EVAL-005)

- **Documented**: 10-gate table — `docs/module-evaluation.md:138-149`.
- **Implementation**: `siglab/evaluation/gates.py:18-68`.
  - `liquidation` → `gates.py:30-31` (any `liquidation_count > 0`).
  - `non_positive_median_return` → `gates.py:34-35`.
  - `non_positive_median_sharpe` → `gates.py:36-37`.
  - `non_positive_validation_return` → `gates.py:40-42`.
  - `non_positive_validation_sharpe` → `gates.py:43-44`.
  - `non_positive_pre_audit_canonical_return` → `gates.py:47-52`.
  - `invalid_canonical_series` → `gates.py:53-54`.
  - `drawdown_limit` → `gates.py:57-59` (track-conditional threshold; see §4).
  - `insufficient_breadth` → `gates.py:62-66`.

### 1.9 Promotion gates (VAL-PAPER-012)

- **Documented**: ≥ `min_trading_days` (default 10) AND ≥ `consecutive_days` (default 5) above `threshold` (default 0.65) — `docs/module-live-boundary.md:272-279`.
- **Implementation**: `siglab/live/promotion.py:147-219` (`promotion_eligible`). Thresholds at `promotion.py:35-37`. Drawdown component clamp `promotion.py:42` (`MAX_TOLERABLE_DRAWDOWN = -0.30`); sub-score `promotion.py:75-84`.

### 1.10 Live execution guard

- **Documented**: runtime refuses to execute unless real SoDEX client present, all methods present, all signing prerequisites configured — `docs/module-live-boundary.md:194-200`.
- **Implementation**: `siglab/live/runtime.py:40-47` (`update_leverage` adapter), `runtime.py:112-115` capability flags, runtime guard surface in module-live-boundary.md:375-379 (dry-run, missing-client guard, preflight).

### 1.11 Optuna search-space bounds for risk params

- **Documented**: "space inference" tunes `max_asset_weight`, `rebalance_threshold`, `max_leverage` within multiplicative bounds — `docs/module-orchestration.md:147-149`; `docs/tuning-guide.md:95-98`.
- **Implementation**: `optimizer_runner.py:624-641`. `max_asset_weight` 0.7×–1.3× (clamped 0.1–1.0); `rebalance_threshold` 0.5×–1.5× (clamped 0.005–0.10); `max_leverage` 0.75×–1.5× (clamped 0.5–4.0).

---

## 2. KILL SWITCHES

A "kill switch" is a condition that triggers termination / blocking of downstream action.

### 2.1 `BacktestConfig(enable_liquidation=True)` — forced liquidation in backtest

- **Trigger**: backtester drives equity below maintenance margin.
- **Response**: position is force-closed, `result.liquidated = True` is set.
- **Code**: config built in `runner.py:129-130, 169-170, 200-201, 1051, 1274, 1316-1321, 1351-1352`; `result.liquidated` propagated to `window_results` row at `runner.py:1602`. Gate `liquidation` (`gates.py:30-31`) fires when any window reports `liquidated=True`.

### 2.2 Live paper liquidation (kill switch on margin breach)

- **Trigger**: per-position equity < maintenance margin — `paper_client.py:1015-1016` (`if equity < mm:`).
- **Response**: liquidate at 20 bps slippage, log warning, drop position — `paper_client.py:1017-1034` (`liq_slip = mark * 0.002`, `del session.positions[pos.symbol]`). Wired to session tick at `paper_client.py:822` (`liq_events = self._check_liquidation(session, mark_prices)`).

### 2.3 Spec-level eligibility kill: `evaluate_gates` failure

- **Trigger**: any failing gate reason in `gates.py:18-68`.
- **Response**: `passed=False, gate_reasons=[...]`. Surfaced at `runner.py:370-372` (`passed, gate_reasons = evaluate_gates(...); summary["passed"]=passed; summary["gate_reasons"]=gate_reasons`).
- **Downstream killers**: `optimizer_runner.py:288` (`gate_penalty = 20.0 if not bool(summary.get("passed")) else 0.0`); `optimizer_runner.py:391-392` (skip `_stability_sweep` if not passed); `cli` deployment eligibility — `docs/module-cli.md:433`.

### 2.4 Optuna objective / deployment score kill (fragility)

- **Trigger**: `_fragility_label` returns `"fragile"` — `trials.py:854-878`.
- **Response**: `fragility_label` flows to `optimizer_runner.py:193, 236`, `fragility_penalty` (≥ 1.5 ⇒ fragile) subtracted from `aggregate_score` to produce `deployment_score` — `trials.py:284-301`, `optimizer_runner.py:296`.
- **Specific fragility triggers** (in order of evaluation, `trials.py:854-878`):
  - `active_bar_count < 72` → fragile (`trials.py:865-866`).
  - `audit_available` and `audit_total_return < -0.02` → fragile (`trials.py:867-868`).
  - `audit_alignment in {"negative", "mismatch"}` → fragile (`trials.py:869-870`).
  - `stability_pack` status ≠ `"ok"` or `passed_fraction < 1.0` → fragile (`trials.py:871-875`).
  - `fragility_penalty >= 1.5` → fragile (`trials.py:876-877`).

### 2.5 Stability-sweep kill

- **Trigger**: any neighbor payload fails evaluation — `optimizer_runner.py:493-494` (`if any(not bool(row.get("passed")) for row in results): stability_penalty += 1.0`).
- **Response**: `stability_pack.status = "fragile"` when `passed_fraction != 1.0` — `optimizer_runner.py:498`. `stability_penalty` also adds `0.5 * (central - mean)` and `0.25 * stdev` — `optimizer_runner.py:495-496`.

### 2.6 Pair-policy activity penalty (kill-by-penalty)

- **Trigger**: `active_bar_fraction < 0.005 / 0.01 / 0.02` or `regime_gate_open_fraction < 0.02 / 0.05` — `runner.py:1561-1581`.
- **Response**: penalty `+0.45 / +0.25 / +0.10` and `+0.15 / +0.05` respectively; extra `+0.05` when activity<0.01 and no time-stop / cooldown. Penalty subtracted from rank in `_pair_target_with_policy_sweep` — `runner.py:1073-1118`.

### 2.7 Pair-policy comparison "realized_winner" (deployment gate)

- **Trigger**: snapshot comparator — `runner.py:1465-1508`.
- **Response**: `realized_winner ∈ {declared, frozen, equal, mixed}`; neither side gets a hard block, but values flow to `summary["policy_sweep_*"]` for downstream promotion decisions — `runner.py:359-369`.

### 2.8 CLI runtime max-runtime stop

- **Trigger**: `--max-runtime-seconds` checked before next iteration — `docs/loop-supervision.md:31`.
- **Response**: cooperative stop (no mid-stage interrupt; requires external supervisor for hard kill).

### 2.9 Live dry-run / missing-client / preflight gates

- **Documented**: live export refuses unless `dry_run: false` AND real client AND signer AND address AND accountID — `docs/module-live-boundary.md:375-379`.
- **Implementation** (per doc, not surfaced in repo as a single line — relies on `SoDEXExecutionAdapter._require_client` raising `RuntimeError`, `SoDEXDryRunSigner` raising `SoDEXNotReadyError`, `LiveDeploymentManager._preflight_deploy_boundary` returning failure — names cited in `module-live-boundary.md:375-379`).

---

## 3. POSITION SIZING

### 3.1 `compute_position_size(risk_budget, volatility, max_size)` — Risk Guardian

- **Formula**: `size = risk_budget / volatility`, clamped to `[0, max_size]` — `guardian.py:568-569`.
- **Bounds**:
  - `volatility <= 0` → returns `0.0` — `guardian.py:563-564`.
  - `max_size < 0` → returns `0.0` — `guardian.py:565-566`.
  - `risk_budget < 0` → clamped to `0.0` — `guardian.py:561-562`.
- **Inputs**: `risk_budget` (portfolio fraction at risk, e.g. 0.02), `volatility` (σ of returns, must be > 0), `max_size` (cap, e.g. 0.25).
- **Documented**: `docs/module-risk-guardian.md:230-244` (formula + bounds); `docs/module-risk-guardian.md:241-242` (returns 0.0 for invalid inputs).
- **Test citations**: `tests/test_risk_guardian.py:539-605`:
  - `test_position_sizing_zero_volatility` — `tests/test_risk_guardian.py:539-542`.
  - `test_position_sizing_negative_risk_budget` — `tests/test_risk_guardian.py:544-547`.
  - `test_basic_calculation` — `tests/test_risk_guardian.py:568-572` (0.02 / 0.10 = 0.20).
  - `test_capped_at_max_size` — `tests/test_risk_guardian.py:574-578` (1.0 clamped to 0.25).
  - `test_high_volatility_smaller_position` — `tests/test_risk_guardian.py:580-584`.
  - `test_volatility_zero_returns_zero` — `tests/test_risk_guardian.py:586-589`.
  - `test_risk_budget_zero` — `tests/test_risk_guardian.py:591-594`.
  - `test_negative_max_size_returns_zero` — `tests/test_risk_guardian.py:596-599`.
  - `test_within_limits` — `tests/test_risk_guardian.py:601-605`.

### 3.2 `_build_pair_trade_positions` (position sizing for perp pair trades)

- **Inputs (from runner.py:968-983, 1014-1029, 1120-1135)**: `gross_target`, `max_gross_target`, `max_asset_weight` (from `spec.risk.max_asset_weight`), `entry_abs_score`, `exit_abs_score`, `flip_abs_score`, `max_holding_bars`, `cooldown_bars`, `signal_leverage_scale`, `regime_gate_mask`, `exit_on_regime_break`.
- **Bounding**: every score derived by Optuna is clamped to a multiplicative envelope — `optimizer_runner.py:624-641` (see §1.11). Policy-sweep values for entry/exit/flip further bounded: `runner.py:874-876` (`exit_abs_score = max(0.0, min(entry_abs_score, entry_abs_score * exit_ratio))`, `flip_abs_score = max(entry_abs_score, min(2.5, entry_abs_score * flip_ratio))`).
- **Math source**: the `max_asset_weight` cap propagates from `spec.risk.max_asset_weight` (default 0.35 per `docs/module-evaluation.md:481-483`) into `_build_pair_trade_positions` (`runner.py:974, 1020, 1126`). The implementation file is referenced via `_build_pair_trade_positions` import (lives in `evaluation/`); the `max_asset_weight` enforcement happens inside that builder.
- **Test citation**: bounds in `optimizer_runner.py:624-641` are exercised indirectly through `_pair_policy_specs` (`runner.py:765-918`).

### 3.3 `rebalance_threshold` (lives on `spec.risk.rebalance_threshold`)

- **Input**: any per-window backtest config — `runner.py:128, 168, 199, 1050, 1273, 1320, 1351`.
- **Bounding (Optuna)**: `[max(0.005, 0.5×), min(0.10, 1.5× + 0.005)]` — `optimizer_runner.py:633-634`.
- **Documented**: `docs/tuning-guide.md:97-98` lists it as a tunable knob.

### 3.4 `max_leverage` (lives on `spec.risk.max_leverage`)

- **Inputs**: `leverage_tiers = sorted({1.0, min(2.0, spec.risk.max_leverage), spec.risk.max_leverage})` — `runner.py:70-72`.
- **Bounding (Optuna)**: `[max(0.5, 0.75×), min(4.0, 1.5×)]` — `optimizer_runner.py:639-640`.
- **Live usage**: `runtime.py:225` clamps with `max(1, int(math.ceil(_finite_float(runtime.get("live_leverage"), 1.0))))`; applied via `await adapter.update_leverage(asset_id=..., leverage=leverage, is_cross=True, ...)` at `runtime.py:232-236`.

### 3.5 `max_asset_weight`

- **Bounding (Optuna)**: `[max(0.1, 0.7×), min(1.0, 1.3× + 0.02)]` — `optimizer_runner.py:627-628`.
- **Documented default**: 0.35 — `docs/module-evaluation.md:481-483`.

---

## 4. DRAWDOWN CHECKS

### 4.1 Evaluation gate: `drawdown_limit`

- **Documented**: `worst_max_drawdown < -0.35` (track=`trend_signals`) OR `< -0.25` (other) → tag `drawdown_limit` — `docs/module-evaluation.md:147`.
- **Implementation**: `gates.py:57-59`:
  ```
  drawdown_limit = -0.35 if track == "trend_signals" else -0.25
  if float(summary.get("worst_max_drawdown", 0.0)) < drawdown_limit:
      reasons.append("drawdown_limit")
  ```
- **Breach response**: gate fail → `summary["passed"]=False, summary["gate_reasons"]` → optimizer penalty (`optimizer_runner.py:288`), deployment kill per §2.3.
- **Test**: `tests/test_gates.py:21` uses `worst_max_drawdown=-0.2` (passes); `tests/test_gates.py:41` same value (passes). No explicit `drawdown_limit` threshold test.

### 4.2 Pre-audit canonical max drawdown (computed but NOT gated)

- **Implementation**: `runner.py:257-260, 304`:
  ```
  summary_pre_audit_canonical_max_drawdown = _series_min_value(
      canonical_run.get("drawdown_curve"),
      end_idx=pre_audit_end_idx,
  )
  ...
  summary["pre_audit_canonical_max_drawdown"] = summary_pre_audit_canonical_max_drawdown
  ```
- **Consumed by**: `search/mutate.py:296, 303, 328-330`; `search/select.py:43-45`; `research/hypothesis.py:590-592`. **Not consumed by `gates.py`** — see §6.

### 4.3 Pre-audit canonical total return gate

- **Documented**: `pre_audit_canonical_total_return ≤ 0` → `non_positive_pre_audit_canonical_return` — `docs/module-evaluation.md:145`.
- **Implementation**: `gates.py:47-52`.
- **Test**: `tests/test_gates.py:20, 27` (`-0.18` fails); `tests/test_gates.py:40, 47` (`0.04` passes).

### 4.4 Risk Guardian `max_drawdown` (computation, not gate)

- **Formula**: `peak = np.maximum.accumulate(equity_curve); dd = np.where(peak > 0, (equity - peak) / peak, 0.0); return float(np.min(dd))` — `guardian.py:235-239`.
- **Use**: feeds dashboard `/risk` and `_stream_risk_scores` — `risk_utils.py:88-89`, `routes.py:488`, `ws.py:314`.

### 4.5 Risk Guardian `current_drawdown` (computation)

- **Formula**: `(latest_val - latest_peak) / latest_peak` — `guardian.py:262-269`.
- **Use**: dashboard `risk_utils.py:90, 92, 196`.

### 4.6 Recovery time

- **Formula**: `trough_idx` is argmin of running drawdown; recovery is first index post-trough where equity ≥ pre-trough peak; `recovery_time = recovery_idx - trough_idx` — `guardian.py:272-317`.
- **Use**: dashboard `risk_utils.py:94, 197`.

### 4.7 Promotion drawdown sub-score (paper trading)

- **Documented**: `Max drawdown | 0.25 | 0% → 1.0, ≤ -30% → 0.0` — `docs/module-live-boundary.md:266-270`.
- **Implementation**: `MAX_TOLERABLE_DRAWDOWN = -0.30` (`promotion.py:42`); `_normalize_drawdown` at `promotion.py:75-84`.
- **Breach response**: sub-score enters composite (`promotion.py:113-144`); if composite < `DEFAULT_PROMOTION_THRESHOLD = 0.65` for any of last `consecutive_days=5` days, `promotion_eligible` returns `False` — `promotion.py:178-219`.
- **Test**: `tests/test_promotion.py:337` (`_normalize_drawdown(-0.50) == 0.0`); `tests/test_promotion.py:185-249` (eligibility matrix).

### 4.8 Fragility drawdown bridge: `audit_total_return < -0.02` ⇒ fragile

- **Trigger**: `trials.py:867-868`.
- **Response**: `fragility_label="fragile"`; `fragility_penalty` includes `negative_audit_penalty = max(0, -audit_total_return) * 12.0` — `trials.py:223-227, 33-34`.
- **No file:line of "live halt" — fragility only affects deployment score; live kill only via liquidation in §2.2.

### 4.9 Paper-session per-day drawdown tracking

- **Implementation**: `extract_daily_metrics` rolls daily equity and records `min(0.0, day_pnl / cumulative_equity)` as `max_drawdown` — `promotion.py:504`.
- **No hard kill**: only feeds `promotion_eligible`.

### 4.10 Live runtime drawdown tracking

- **Implementation**: `runtime.py:439-450` builds per-symbol trade plans as `target_notional = weight * account_value * leverage / mid`. **No drawdown-based kill** in `runtime.py`. Kill logic only via per-position liquidation (`paper_client.py:999-1035`).

---

## 5. FRAGILITY DETECTION

Fragility is detected exclusively in `siglab/orchestration/trials.py::summarize_generalization` + `_fragility_label` + `optimizer_runner.py::_stability_sweep`. **There is no live fragility gate** — the dashboard only emits informational alerts.

### 5.1 Penalty components (additive, all in `trials.py:222-296`)

| Component | Formula | Trigger threshold | Weight |
|---|---|---|---|
| `negative_validation_penalty` | `max(0, -validation_total_return) * w` | validation return < 0 | `w=10.0` (`trials.py:33`) |
| `audit_penalty` | `max(0, -audit_total_return) * w` (if audit available) | audit return < 0 | `w=12.0` (`trials.py:34`) |
| `generalization_gap_penalty` | `max(0, pre_audit - validation) * w` | pre-audit > validation | `w=6.0` (`trials.py:35`) |
| `audit_gap_penalty` | `max(0, validation - audit) * w` | validation > audit | `w=6.0` (`trials.py:36`) |
| `activity_penalty` | `max(0, 0.15 - active_bar_fraction) * w` | active < 0.15 | `w=8.0` (`trials.py:37`) |
| `turnover_penalty` | `max(0, turnover_mean - 0.10) * w` | turnover > 0.10 | `w=3.0` (`trials.py:38`) |
| `tx_cost_penalty` | `max(0, tx_cost_share - 0.35) * w` | fee share > 0.35 | `w=1.5` (`trials.py:39`) |
| `selector_return_std_penalty` | `max(0, return_std - 0.03) * w` | std > 0.03 | `w=8.0` (`trials.py:40`) |
| `selector_sharpe_std_penalty` | `max(0, sharpe_std - 0.75) * w` | std > 0.75 | `w=0.75` (`trials.py:41`) |
| `selector_unprofitable_share` | `max(0, 0.5 - profitable_window_pct) * w` | profitable < 0.5 | `w=2.0` (`trials.py:42`) |
| `extreme_param_penalty` | from `_extreme_param_penalty` (`trials.py:687-712`) | param at edge of Optuna space | `w=1.5` (`trials.py:43`) |
| `low_bar_penalty` | `(72 - active_bar_count)/72 * w` | active bars < 72 | `w=2.0` (`trials.py:44`) |
| `stability_penalty` | from optimizer neighbor sweep | neighbor fails or central > mean | (see `optimizer_runner.py:492-496`) |

Total → `fragility_penalty` (`trials.py:284-296`) → `deployment_score = aggregate_score - fragility_penalty` (`trials.py:297-301`).

### 5.2 Label assignment (`trials.py:854-878`)

Order of evaluation (short-circuit):
1. `not audit_available and not stability_pack` → `"untested"` — `trials.py:863-864`.
2. `active_bar_count < 72` → `"fragile"` — `trials.py:865-866`.
3. `audit_total_return < -0.02` → `"fragile"` — `trials.py:867-868`.
4. `audit_alignment in {"negative", "mismatch"}` → `"fragile"` — `trials.py:869-870`.
5. `stability_pack.status != "ok"` OR `passed_fraction < 1.0` → `"fragile"` — `trials.py:871-875`.
6. `fragility_penalty >= 1.5` → `"fragile"` — `trials.py:876-877`.
7. otherwise → `"stable"`.

### 5.3 Consequence

- `fragility_label` flows to `optimizer_runner.py:193, 236`; to `writer_runner.py` per `docs/module-orchestration.md:184-189` (per doc; code reference omitted in scope).
- `fragility_penalty` subtracted from `aggregate_score` to form `objective` in `optimizer_runner.py:296`.
- `stability_penalty` flows back into `summarize_generalization` via `trials.py:282`, into the same `fragility_penalty` total at `trials.py:295`.
- **No live system stops** on fragility — purely a deployment score (per `docs/module-orchestration.md:86-89`).

### 5.4 Audit-alignment label (`trials.py:828-851`)

- `audit_available == False` → `"not_run"`.
- `audit_total_return < 0` → `"negative"`.
- validation and audit both zero / both positive / both negative AND `|validation - audit| ≤ 0.03` → `"aligned"`.
- otherwise → `"mismatch"`.

### 5.5 Pair-policy fragility (`runner.py:1465-1508`)

- `metric_directions` map per-metric expected sign — `runner.py:1471-1479`.
- `realized_winner` is `declared` / `frozen` / `equal` / `mixed` — `runner.py:1496-1507`.
- Flows to `summary["policy_sweep_realized_winner"]` — `runner.py:367`. No hard kill; only disclosure.

---

## 6. INVARIANT VIOLATIONS

A path that returns PASS / ELIGIBLE / SAFE when a documented guard should block.

### 6.1 `pre_audit_canonical_max_drawdown` is never gated

- **Code path**: `summary["pre_audit_canonical_max_drawdown"]` is computed in `runner.py:257-260, 304`; the canonical-run pre-audit drawdown **is the most audit-relevant drawdown** (it's the pre-audit-window worst drawdown), but `gates.py:18-68` does not reference it.
- **Result**: a spec can pass `evaluate_gates` while `pre_audit_canonical_max_drawdown < -0.35` (would block) — `gates.py:30-66` checks only `worst_max_drawdown` (full curve, not pre-audit slice). If the post-audit window is recovered, `worst_max_drawdown` may pass while pre-audit still violates.
- **Why this is an invariant violation**: the doc (`docs/module-evaluation.md:145-147`) lists both pre-audit-return and drawdown-limit as gates, but drawdown-limit is applied to `worst_max_drawdown` (full curve), not to the pre-audit slice. The pre-audit max drawdown value is computed (`runner.py:304`) yet never consumed by `gates.py`.

### 6.2 `pre_audit_end_idx` falls back to full curve when no audit_holdout

- **Code path**: `_pre_audit_end_idx` returns `len(values)` if no `kind=="audit_holdout"` range exists — `runner.py:3572-3580`. The "in_sample_only" branch in `_evaluation_plan` produces no audit range — `runner.py:521-548`.
- **Result**: when `strict_holdout=False`, the "pre-audit" values equal full-curve values; `gates.py:48-52` then duplicates the role of `worst_max_drawdown` for `non_positive_pre_audit_canonical_return` (always coincident with `median_total_return` check, not a stricter pre-audit gate).

### 6.3 `drawdown_limit` uses `worst_max_drawdown`, not the per-track pre-audit drawdown

- **Code path**: `gates.py:57-58` reads `summary.get("worst_max_drawdown")` (full curve, see `score.py:109` — `_safe_nanmin(drawdown)` over all window arrays).
- **Result**: a spec with full-curve worst-DD = -0.30 (passes gate at -0.35 trend_signals) but whose pre-audit slice hit -0.50 still PASSes. The doc (`module-evaluation.md:147`) does not state that this is intended; it is a fact of the implementation.

### 6.4 Dashboard `concentration` contradicts documented default

- **Documented**: "In the current dashboard integration, concentration is set to 0.0 (no limit configuration) until concentration limits are configured in the session." — `docs/module-risk-guardian.md:121-122`.
- **Actual code**: `risk_utils.py:150-152` computes `hhi = 1.0/n; concentration = 1.0 - hhi` and feeds it into `_normalize_concentration_score`. With 1 strategy, `concentration=0.0` (matches doc). With 2+ strategies, `concentration > 0` is fed to the score, **but the doc says the value is 0.0**. The doc is wrong; the score receives a strategy-count-derived value that has no relationship to allocation limits.

### 6.5 `_normalize_concentration_score` semantics flipped in dashboard

- **Documented**: "0.0 = at or under limit, 1.0 = 100 % over limit" — `docs/module-risk-guardian.md:114-117`.
- **Code semantics**: `clipped <= 0.0` → 1.0 (best); `clipped >= 0.20` → 0.0 (worst) — `guardian.py:138-142`.
- **Dashboard feeds**: `concentration = 1 - 1/n` ∈ (0, 1]. For 5 strategies, `concentration=0.8` → sub-score = 0.0. The doc's framing (deviation from limit) is contradicted; the dashboard treats it as a strategy-diversification penalty unrelated to allocation.

### 6.6 `_stream_risk_scores` returns last 20 events without re-checking thresholds

- **Code path**: `risk_utils.py:172-187` — ad-hoc severity assignment: `sev = "warning" if abs(event.max_drawdown_pct) < 0.15 else "critical"`. No use of `check_risk_thresholds` (`guardian.py:433-526`). The alert thresholds declared in the doc (`module-risk-guardian.md:186-204`) are not enforced — every drawdown event is auto-classified into warning / critical, so the configurable `info/warning/critical` semantics are bypassed.

### 6.7 Dashboard composite score absent from any guard

- **Code path**: `compute_composite_score` (`guardian.py:159-208`) returns a number that the dashboard displays (`risk_utils.py:189-190`). It is **not consumed by any gate, kill switch, or fragility detector**. The composite is purely a display value.
- **Result**: a portfolio with `composite_score=0.05` displays a red gauge but nothing blocks new deployments or stops trading.

### 6.8 Live runtime lacks drawdown-based kill

- **Code path**: `runtime.py:439-450` builds trade plans; no `max_drawdown`, `current_drawdown`, or threshold check. `_check_liquidation` (`paper_client.py:999-1035`) is the only per-position kill switch in live code. A strategy with cumulative drawdown -0.99 (sub-score 0.0) continues to receive new trades.

### 6.9 Optuna hard bounds not enforced on tuned params

- **Code path**: `optimizer_runner.py:624-641` declares the *suggestion* bounds for Optuna; the suggested value is then passed to the evaluator with no project-side clamp. If Optuna's internal search ever exceeded the declared bounds, the suggestion would propagate un-clamped into `spec.params`. The pair-policy sweep also internally clamps (`runner.py:874-876, 1229-1233`) but only for the *internal ranking*; the spec params stored at `runner.py:1137-1145` use the same unclamped value.

---

## 7. CROSS-MODULE INCONSISTENCY

### 7.1 Drawdown cap for sub-score vs promotion vs gate

- **`score.py:93`**: `_bounded(_safe_nanmedian(drawdown), lower=-1.0, upper=0.0)`. Component cap.
- **`guardian.py:46`**: `DRAWDOWN_TARGET = -0.20` for composite sub-score (zero sub-score threshold).
- **`promotion.py:42`**: `MAX_TOLERABLE_DRAWDOWN = -0.30` for promotion sub-score (zero sub-score threshold).
- **`gates.py:57-58`**: `drawdown_limit = -0.35 if trend_signals else -0.25` (pass/fail threshold on `worst_max_drawdown`).

**Inconsistency**: a drawdown worse than `-0.20` zeroes the guardian sub-score but does not block evaluation. A drawdown worse than `-0.30` zeroes the promotion sub-score but the spec may still promote if the other three sub-scores are ≥ 0.815. A drawdown worse than `-0.35` (trend_signals) or `-0.25` (other) blocks evaluation. **Three different "zero" thresholds for drawdown across the system** with no documented rationale (`docs/module-risk-guardian.md:104-109`; `docs/module-live-boundary.md:266-270`; `docs/module-evaluation.md:147`).

### 7.2 Drawdown comparison is per-track in gates but not in score

- `gates.py:57-58` branches on `track == "trend_signals"`. `score.py:90-100` does not. `promotion.py:75-84` does not. **The track-conditional drawdown limit is unique to gates.**

### 7.3 Sub-score components in `score.py` vs `trials.py`

- `score.py:9-16` documents `SCORE_COMPONENT_WEIGHTS = (median_sharpe 1.0, median_total_return 4.0, median_calmar 0.5, asset_breadth 0.1, profitable_window_pct 0.25, worst_max_drawdown 1.5)` — these weights are in `trials.py:9-16` (the doc claim is in `module-evaluation.md` which mirrors this list). The score assembly is at `score.py:94-101`.
- `trials.py:32-45` `DEFAULT_GENERALIZATION_WEIGHTS` lists separate weights for *fragility* penalties (different magnitude class: 1.5–12.0), not score assembly.
- **Inconsistency**: `worst_max_drawdown * 1.5` in score assembly (`score.py:100`) but `trials.py:9-16` and the doc both call this component `worst_max_drawdown`. The value fed in is `_safe_nanmin(drawdown)` (`score.py:109`) — a *negative* number, and adding `1.5 × negative` *reduces* the aggregate. The math is consistent with a "drawdown penalty" but the doc describes it as a positive contribution. Net: a deeper drawdown strictly reduces `aggregate_score` by 1.5 per unit drawdown; an aggregate of 0 vs -0.5 differs by 0.75.

### 7.4 Sharpe normalisation target differs

- `guardian.py:45`: `SHARPE_TARGET = 3.0` → sub-score saturates at 1.0.
- `promotion.py:41`: `MAX_SHARPE = 3.0` → sub-score saturates at 1.0.
- **Consistent at 3.0**, but documentation cross-link is implicit.

### 7.5 Concentration semantics differ

- `guardian.py:130-142` `concentration` = "deviation fraction over limit" (0..1).
- `risk_utils.py:150-152` `concentration` = `1 - 1/n` (diversification index, 0..1).
- **Different quantity, same field name, same downstream normalizer** — see §6.4–6.5.

### 7.6 Pre-audit end-idx fallback

- `runner.py:3572-3580`: when no `audit_holdout` range, `_pre_audit_end_idx` returns the full curve length. This collapses `pre_audit_canonical_total_return` and `worst_max_drawdown` for the "in_sample_only" branch (and "validation_holdout" branch — `runner.py:557-562`, no `audit_holdout` range there either). The gate `drawdown_limit` and the summary value `pre_audit_canonical_max_drawdown` then refer to the same series under `selector_scope="in_sample_only"`. There is no `audit_holdout` range in two of the three evaluation-plan branches — `runner.py:521-548, 555-592`. Only the third branch (the full plan, `runner.py:601-639`) creates one.

### 7.7 `passed` defined per gate, not per fragility dimension

- `optimizer_runner.py:288`: `gate_penalty = 20.0 if not passed else 0.0`.
- `trials.py:284-296` defines `fragility_penalty` independently.
- **Inconsistency**: a spec with `passed=True` (all gates green) but `fragility_penalty > 20.0` is still a "passing spec" by gate semantics; the deployment score is heavily negative, but no `passed=False` is recorded. The `fragility_label="fragile"` is the only flag, and it does not flip `passed`.

### 7.8 `check_concentration` and `check_risk_thresholds` are dead code

- `guardian.py:389-430` and `guardian.py:433-526` — no callers in `dashboard/`, `evaluation/`, `orchestration/`, `live/`. Search confirms zero invocations.
- **Inconsistency**: docs (`module-risk-guardian.md:208-219, 186-204`) describe these as production features. They are not used.

### 7.9 `compute_position_size` is dead code at runtime

- `guardian.py:534-569` is exported (`siglab/risk/__init__.py:17`) and tested (`tests/test_risk_guardian.py:539-605`) but no runtime caller exists (live sizing uses `weight * account_value * leverage / mid` in `runtime.py:449`).
- **Inconsistency**: the position-sizing story in the doc (`module-risk-guardian.md:230-244`) is not the position-sizing story of the live runtime.

### 7.10 Drawdown alerting: doc vs dashboard

- `docs/module-risk-guardian.md:222-225` says "Events with `|max_drawdown_pct| < 15%` → WARNING, ≥ 15% → CRITICAL".
- `risk_utils.py:176`: `sev = "warning" if abs(event.max_drawdown_pct) < 0.15 else "critical"`. Consistent with doc.
- `risk_utils.py:182`: `threshold=0.0` is hard-coded in the alert payload — alerts always report the event's drawdown vs a 0.0 baseline, never against a configurable threshold. This contradicts the documented `info/warning/critical` schema.

### 7.11 Sharpe floor in score vs runner

- `score.py:90`: `_bounded(_safe_nanmedian(sharpe), lower=-20.0, upper=20.0)`.
- `guardian.py:35-36`: `SHARPE_MIN=-20.0, SHARPE_MAX=20.0`. Consistent.
- `trials.py` does not bound Sharpe. No internal inconsistency but `median_sharpe` propagates into `trials.py:9-16` `SCORE_COMPONENT_WEIGHTS` and into `_selector_window_variation` (`trials.py:637-674`) without a cap.

### 7.12 Optuna bound for `risk.max_leverage` upper vs default

- `optimizer_runner.py:639-640`: `high = min(4.0, max_leverage * 1.5)`.
- `runtime.py:225`: `max(1, int(math.ceil(live_leverage)))`. Live leverage must be integer ≥ 1.
- **Inconsistency**: a tuned `max_leverage=3.5` survives Optuna but the live runtime rounds it to int (4 or 3 depending on ceil). The doc does not state that `max_leverage` is continuous in research but integer in live.

### 7.13 Stability sweep `passed_fraction == 1.0` test

- `optimizer_runner.py:498`: `"status": "ok" if passed_fraction == 1.0 else "fragile"`.
- `trials.py:871-875`: `fragility_label = "fragile"` if `stability_pack.get("status") != "ok" or passed_fraction < 1.0`. Both call `passed_fraction < 1.0` "fragile". Consistent. But `optimizer_runner.py:489` uses `passed_fraction = sum(...)/len(results)` (int division semantics for small len) — `1/2 = 0.5`, never `1.0`, so two neighbors always produce a fragile status. **Three neighbors is the minimum count that can reach 1.0** — but `_stability_sweep` only generates two neighbors (`optimizer_runner.py:424-428`). So `passed_fraction == 1.0` is *unreachable by construction* with the current neighbor-generator — `status` is always `"fragile"` (or one of the skip statuses) when neighbor_count == 2. This is a structural bug; every "ok" status requires neighbor_count == 0 or ≥ 3.

---

## 8. EVIDENCE

Line-level evidence for every claim above, in compact form.

### Guardian (`siglab/risk/guardian.py`)

- `SHARPE_MIN/MAX = -20.0/20.0` — `guardian.py:35-36`.
- `SHARPE_TARGET = 3.0` — `guardian.py:45`.
- `DRAWDOWN_MIN/MAX = -1.0/0.0` — `guardian.py:37-38`.
- `DRAWDOWN_TARGET = -0.20` — `guardian.py:46`.
- `CONCENTRATION_MIN/MAX = 0.0/1.0` — `guardian.py:39-40`.
- `CONCENTRATION_TARGET = 0.20` — `guardian.py:47`.
- `CORRELATION_MIN/MAX = 0.0/1.0` — `guardian.py:41-42`.
- `CORRELATION_TARGET = 0.70` — `guardian.py:48`.
- `_normalize_sharpe_score` — `guardian.py:102-113`.
- `_normalize_drawdown_score` — `guardian.py:116-127`.
- `_normalize_concentration_score` — `guardian.py:130-142`.
- `_normalize_correlation_score` — `guardian.py:145-156`.
- `compute_composite_score` — `guardian.py:159-208`.
- `max_drawdown` — `guardian.py:216-239`.
- `current_drawdown` — `guardian.py:242-269`.
- `recovery_time` — `guardian.py:272-317`.
- `correlation_matrix` — `guardian.py:325-381`.
- `check_concentration` — `guardian.py:389-430`.
- `check_risk_thresholds` — `guardian.py:433-526`.
- `compute_position_size` — `guardian.py:534-569`.
- `track_drawdown_events` — `guardian.py:577-642`.

### Gates (`siglab/evaluation/gates.py`)

- `liquidation` — `gates.py:30-31`.
- `non_positive_median_return` — `gates.py:34-35`.
- `non_positive_median_sharpe` — `gates.py:36-37`.
- `non_positive_validation_return` — `gates.py:40-42`.
- `non_positive_validation_sharpe` — `gates.py:43-44`.
- `non_positive_pre_audit_canonical_return` — `gates.py:47-52`.
- `invalid_canonical_series` — `gates.py:53-54`.
- `drawdown_limit` (track-conditional) — `gates.py:57-59`.
- `insufficient_breadth` — `gates.py:62-66`.

### Score (`siglab/evaluation/score.py`)

- `serialize_stats` — `score.py:17-29`.
- `_safe_nanmedian` / `_safe_nanmin` — `score.py:32-51`.
- `_bounded` — `score.py:54-63`.
- `summarize_window_results` — `score.py:66-120`. Component caps at `score.py:90-93`; aggregate at `score.py:94-101`; `worst_max_drawdown` at `score.py:109`.

### Runner (`siglab/evaluation/runner.py`)

- `liquidation_count` propagation — `score.py:86`, `runner.py:1602`.
- `BacktestConfig(enable_liquidation=True)` — `runner.py:129-130, 169-170, 200-201, 1051, 1274, 1316-1321, 1351-1352`.
- `_pre_audit_end_idx` — `runner.py:3572-3580`.
- `summary_pre_audit_canonical_*` — `runner.py:249-260, 302-304`.
- `_pre_audit_drawdown_pack` — `runner.py:2574-2817`.
- `_pair_target_with_policy_sweep` — `runner.py:920-1234`.
- `_pair_policy_specs` bounds — `runner.py:765-918`.
- `_pair_policy_activity_penalty` — `runner.py:1547-1581`.
- `_pair_policy_compare_snapshots` — `runner.py:1465-1508`.
- `evaluate_gates` invocation — `runner.py:370-372`.

### Dashboard (`siglab/dashboard/`)

- `risk_utils.load_equity_curves` — `risk_utils.py:30-53`.
- `STALE_THRESHOLD_SECONDS = 7d` — `risk_utils.py:27`.
- `empty_risk_response` — `risk_utils.py:56-70`.
- `compute_risk_metrics` (HHi concentration) — `risk_utils.py:73-201`; concentration at `risk_utils.py:150-152`; alerts at `risk_utils.py:172-187`.
- `_compute_risk_metrics` route — `routes.py:474-493`.
- `GET /risk` — `routes.py:496-509`.
- WebSocket risk stream — `ws.py:290-332`.

### Promotion (`siglab/live/promotion.py`)

- `DEFAULT_WEIGHTS` (4 sub-scores at 0.25) — `promotion.py:28-33`.
- `DEFAULT_PROMOTION_THRESHOLD = 0.65` — `promotion.py:35`.
- `DEFAULT_CONSECUTIVE_DAYS = 5` — `promotion.py:36`.
- `DEFAULT_MIN_TRADING_DAYS = 10` — `promotion.py:37`.
- `TARGET_ANNUAL_RETURN = 0.30` — `promotion.py:40`.
- `MAX_SHARPE = 3.0` — `promotion.py:41`.
- `MAX_TOLERABLE_DRAWDOWN = -0.30` — `promotion.py:42`.
- `_normalize_drawdown` — `promotion.py:75-84`.
- `compute_sub_scores` — `promotion.py:91-110`.
- `compute_composite_score` — `promotion.py:113-144`.
- `promotion_eligible` — `promotion.py:147-219`.
- `extract_session_metrics` — `promotion.py:270-409`.
- `extract_daily_metrics` — `promotion.py:412-509`.

### Paper client (`siglab/live/paper_client.py`)

- Liquidation check trigger — `paper_client.py:1015-1016`.
- Liquidation response — `paper_client.py:1017-1034`.
- Liquidation wiring — `paper_client.py:822` (`liq_events = self._check_liquidation(session, mark_prices)`).
- 20 bps slippage constant — `paper_client.py:1018`.

### Live runtime (`siglab/live/runtime.py`)

- `update_leverage` adapter — `runtime.py:40-47`.
- Capability flags — `runtime.py:112-115`.
- Live leverage rounding — `runtime.py:225`.
- Per-symbol trade plan — `runtime.py:439-450`.

### Optimizer (`siglab/orchestration/optimizer_runner.py`)

- `_objective_details` (gate penalty) — `optimizer_runner.py:278-301`.
- `_stability_sweep` (neighbors, passed_fraction) — `optimizer_runner.py:383-506`. `passed_fraction` formula at `optimizer_runner.py:489`; status at `optimizer_runner.py:498`; penalty at `optimizer_runner.py:492-496`.
- `infer_optuna_space` bounds — `optimizer_runner.py:624-641`.
- `_threshold_bounds` — `optimizer_runner.py:741-750`.

### Trials / fragility (`siglab/orchestration/trials.py`)

- `SCORE_COMPONENT_WEIGHTS` — `trials.py:9-16`.
- `DEFAULT_GENERALIZATION_WEIGHTS` — `trials.py:32-45`.
- `summarize_generalization` — `trials.py:176-351`.
- `_audit_alignment_label` — `trials.py:828-851`.
- `_fragility_label` — `trials.py:854-878`.
- `deployment_rank` — `trials.py:354-365`.

### Tests cited

- `tests/test_risk_guardian.py:539-605` (position sizing 8 tests).
- `tests/test_gates.py:9-47` (positive / negative pre-audit canonical return).
- `tests/test_promotion.py:185-249` (eligibility); `tests/test_promotion.py:337` (`_normalize_drawdown(-0.50) == 0.0`).

### Docs cited

- `docs/module-risk-guardian.md:7-244, 273-313` (composite, sub-scores, drawdown, position sizing, dashboard).
- `docs/module-evaluation.md:120-149` (gates table).
- `docs/module-live-boundary.md:194-200, 255-279, 375-379` (execution guard, promotion, preflight).
- `docs/module-orchestration.md:85-89, 147-149, 184-189` (fragility penalty, search space, penalization rationale).
- `docs/tuning-guide.md:95-98` (tunable knobs).
- `docs/loop-supervision.md:31` (max-runtime stop).
