"""Reviewer-assessed coverage of the handbook, tied to the audited source.

Features, standalone backtest rules and live strategy behavior are different
levels of coverage. None of these entries establish profitable performance.
"""
import ast
from pathlib import Path


STRATEGIES = [
    ("Trend following", "feature_only", "MA ratios, slopes and trend strength exist; no standalone MA trend strategy or live trend rule.", ["features/momentum.py", "features/regime.py"]),
    ("Moving-average crossover", "feature_only", "SMA fast=5/slow=20 features; no explicit crossover entry/exit baseline; EMA not implemented.", ["features/momentum.py"]),
    ("Momentum", "baseline_and_features", "Returns at 1/5/10/20/30 ticks and a ret_5 > 0 long/flat baseline. Live ML can consume features; directional behavior and added value unverified.", ["features/momentum.py", "backtest/ablation.py"]),
    ("Mean reversion", "baseline_and_features", "20-tick z-score, z-score change, reversal return; backtest long below -1.5/short above +1.5. Shared targets and spot exits implemented; paper/live short adapters use separate collateral accounting. Shorts remain disabled pending permission.", ["features/mean_reversion.py", "backtest/ablation.py"]),
    ("RSI mean reversion", "feature_only", "20-tick simple rolling RSI feature, including gains-only/losses-only/flat edge cases; no dedicated 30/70 strategy rule.", ["features/mean_reversion.py"]),
    ("Bollinger reversion", "feature_only", "20-tick, 2-standard-deviation band position; no dedicated band entry/exit rule.", ["features/mean_reversion.py"]),
    ("Breakout", "missing", "No prior-range highs/lows, channel break or breakout entry/exit baseline.", []),
    ("Volatility breakout", "missing", "Realized volatility exists as context/sizing input; no return-versus-volatility breakout trigger.", ["features/regime.py", "sizing.py"]),
    ("Volume-based", "raw_data_only", "Raw ticker trade-value fields retained, but no relative-volume, volume-change, volume-z-score or confirmation strategy. Raw fields reaching ML is not a defined volume strategy.", ["data/collector.py"]),
    ("Cross-sectional momentum", "baseline_and_features", "5-tick ranks/percentiles and relative returns; baseline thresholds 0.7/0.3. Bounded quote age and within-group ranking added; empirical basket diversification and market-neutral performance remain unverified.", ["features/cross_sectional.py", "backtest/ablation.py"]),
    ("Multi-factor / ensembles", "partial", "Averaged momentum/z-score baseline and classifier feature combination exist. Purged temporal calibration and paired component comparisons added; historical contribution and portfolio P&L diversification remain untested.", ["backtest/ablation.py", "model/train.py"]),
]

# Handbook sections include explanatory concepts, alternatives and optional
# techniques; these statuses are not a compliance percentage or an adoption list.
PARTS = [
    (1, 'partial', 'Shared signal-to-target rules and cash/quantity order deltas added; historical hypothesis evidence remains missing.'),
    (2, 'partial', 'Spot and collateral-based short API conventions checked against official documentation; account permissions and spot fees still require verification.'),
    (3, 'partial', 'Exact duplicates removed, conflicts rejected, writes atomic, stale quotes/history gaps rejected and groups separated. Sampling cadence/outlier policy remains a research input.'),
    (4, 'partial', 'Signed training-only conditional payoffs replace probability-as-return. Broader statistical uncertainty and trade-level research remain missing.'),
    (5, 'missing_research_evidence', 'No uncertainty intervals, trial accounting or multiple-testing safeguards.'),
    (6, 'partial', 'Rolling returns and volatility exist; no stationarity or autocorrelation research.'),
    (7, 'partial', 'Returns/SMA/z-score/RSI/Bollinger/ranks/volatility implemented; no EMA, ATR, MACD, stochastic, VWAP or derived volume features.'),
    (8, 'partial', 'Shared rule and model target interfaces with long/flat exits and optional signed targets; three rule families, not every handbook example.'),
    (9, 'implemented_offline', 'Finite confidence/volatility sizing, per-asset/gross caps, cash reserves and rounded quantity conversion tested with synthetic fixtures.'),
    (10, 'implemented_offline', 'Spot sells reduce holdings; separate collateral-based paper/live short adapters support opening/closing. Shorts disabled by default; actual account behavior untested.'),
    (11, 'implemented_offline', 'Sequential delayed-fill ledger, explicit terminal liquidation and initial-equity peaks; multi-asset runner accounting tested. Real venue parity unverified.'),
    (12, 'implemented_offline', 'Unknown labels masked; actual label-availability times checked at purged boundaries. Feature builders pass future-shock prefix invariance; arbitrary feature provenance cannot be inferred from values.'),
    (13, 'partial', 'Purged rolling validation and internal chronological probability calibration added. Final historical holdout not yet frozen or evaluated.'),
    (14, 'partial', 'Training/validation scores recorded; no parameter stability or held-out evidence.'),
    (15, 'partial', 'Offline failure fixtures cover stale quotes, gaps, duplicates, repeated orders, drawdown/rate limits, partial fills and uncertain responses. Historical parameter/cost/period stress grid not run.'),
    (16, 'partial', 'Terminal fees and initial-equity drawdown fixed; finite RMS-downside convention and explicit undefined annualization handling. Closed-trade expectancy/profit factor and organizer metric parity remain pending.'),
    (17, 'partial', 'Training-only expected-return gate, live spread allowance and paper bid/ask/slippage/fees. Maker resting-fill model explicitly unsupported; measured venue costs not available.'),
    (18, 'partial', 'Bid/ask used for paper fills and runner spread gate; depth, impact and actual execution calibration remain unverified.'),
    (19, 'partial', 'Per-asset/gross caps, drawdown halt, persisted hourly reservations and shared request budget integrated and tested. Daily loss and correlated concentration policies remain research choices.'),
    (20, 'partial', 'Signed gross exposure helper and runner portfolio sizing integrated; correlated cluster limits and empirical multi-asset diversification not established.'),
    (21, 'partial', 'Three shared explicit rule families plus model feature combination. Missing breakout/volume examples remain optional research, not silently claimed coverage.'),
    (22, 'partial', 'Same-model/common-OOS regime/cost/sizing comparisons added; component switches respected. Empirical diversification and economic contribution still untested.'),
    (23, 'partial', 'Training-only scaler, purged temporal probability calibration, conditional payoffs, fixed schema, regression support and source/config model artifacts. Real prediction stability and held-out economic value remain untested.'),
    (24, 'optional_not_implemented', 'Reinforcement learning absent; handbook describes it as generally unnecessary for this beginner setting.'),
    (25, 'implemented_offline', 'Editable packaging fixes module startup. Explicit model loading, stateful paper ledger, live reconciliation adapter, delta planning and pre-submission uncertainty persistence added; API behavior only mocked.'),
    (26, 'partial', 'Public/signed transport, shared rate budget, no mutation retries and fail-closed partial/pending/unknown states tested. Real partial fills, outages and recovery require paper/sandbox evidence.'),
    (27, 'implemented_offline', 'Paper cash, quantities, collateral, bid/ask/slippage fills, fees and persisted restart state implemented; sustained online paper run not yet performed.'),
    (28, 'partial', 'Structured confirmed-fill events with quantity/price/fees/target/equity and persisted risk state. Production monitoring and venue slippage calibration remain future work.'),
    (29, 'partial', 'Editable package, configuration, seed, checksummed model/schema manifests and deterministic offline regression suite. Historical data/experiment manifests still needed.'),
    (30, 'partial', 'Paired/common-OOS one-factor comparison scaffold repaired. Freeze data/parameters and record economic experiments before selection.'),
    (31, 'missing_research_evidence', 'No frozen hypothesis/data/config/cost records or rejected-experiment log.'),
    (32, 'partial', 'Original reproducible defects repaired and covered by regression fixtures. Remaining market/operational risks require evidence beyond fixtures.'),
    (33, 'not_code_requirement', 'Team responsibilities, staged research and fallback operation are process choices; deployment instructions alone do not demonstrate readiness.'),
    (34, 'partial', 'Some essential formulas implemented; final trust questions remain unresolved without out-of-sample/robustness and venue evidence.'),
]


def build_coverage(root: Path, config):
    handbook = root / "Crypto_Algorithmic_Trading_Hackathon_Handbook.md"
    headings = {}
    for line_no, line in enumerate(handbook.read_text().splitlines(), 1):
        if line.startswith("# PART "):
            part = int(line.split()[2])
            headings[part] = {"title": line.lstrip("# "), "source": {"path": handbook.name, "line": line_no}}
    uses = {}
    for path in sorted(root.rglob("*.py")):
        if ".venv" in path.parts or "analysis" in path.parts:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "config":
                uses.setdefault(node.attr, []).append({"path": str(path.relative_to(root)), "line": node.lineno})
    config_lines = {}
    for node in ast.parse((root / "config.py").read_text()).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    config_lines[target.id] = node.lineno
    special = {
        "INITIAL_CAPITAL": "Used as paper/backtest initial capital; live sizing uses reconciled equity.",
        "COMPETITION_DURATION_DAYS": "Declared only; no duration-driven shutdown or evaluation window.",
        "NO_LEVERAGE": "Runner gross/per-asset caps and collateral/cash reservations enforce no leverage; flag itself is declarative.",
        "NO_HFT": "Declarative flag; slow polling is configured separately.",
        "MAX_PORTFOLIO_EXPOSURE": "Signed gross cap integrated with per-asset caps into runner order planning.",
        "MAX_TRADES_PER_HOUR": "Hourly attempt reservations enforced and persisted across restarts.",
        "MAX_DRAWDOWN_KILL_SWITCH": "High-water equity halt enforced and latched in persisted runner state.",
        "TARGET_MODE": "Threshold/direction calibrated classifiers and explicit ridge/random-forest regression supported.",
        "TARGET_HORIZON": "Used in labels and purging; runner re-evaluates targets each fresh quote rather than promising a fixed holding duration.",
        "COMPONENTS": "Feature/model/cost/sizing switches respected; hard risk guards always remain active.",
        "MAKER_FEE": "Cost helper supports maker setting; live order call uses MARKET, no resting/fill behavior tested.",
        "TAKER_FEE": "Used in costs; venue fee assumption has not been freshly verified.",
        "SLIPPAGE_ESTIMATE": "Paper fills split this assumed round-trip slippage by side; spread uses observed bid/ask. Actual fill calibration remains pending.",
        "BARS_PER_YEAR": "Research annualization assumes the configured observation cadence; runner rejects large recent gaps. Sampling regularity must be verified in historical inputs.",
        "WALKFORWARD_TRAIN_BARS": "Used by offline trainer; explicit model artifacts loaded at runner startup, no autonomous retraining.",
        "WALKFORWARD_VAL_BARS": "Used by purged offline validation; economic evaluation separately uses paired common timestamps.",
        "WALKFORWARD_STEP_BARS": "Used by purged rolling validation; final historical holdout remains to be frozen.",
        "UNIVERSE_GROUPS": "Grouping enforced during feature construction, including expanded universes.",
    }
    parameter_rows = []
    for name, line in config_lines.items():
        if name in {"API_KEY", "SECRET"}:
            continue  # never export environment-derived secrets
        parameter_rows.append({"parameter": name, "value": getattr(config, name),
                               "source": {"path": "config.py", "line": line},
                               "references": uses.get(name, []),
                               "assessment": special.get(name, "Referenced by the repaired implementation; historical effectiveness and tuning remain unvalidated." if uses.get(name) else "Configuration/catalog value; see dependent settings, not an independent live guard.")})
    return {
        "method": "Current source review against all 34 parts and all 11 Part 21 strategy families, plus AST parameter-use inventory. Original findings preserved separately in the baseline audit. Feature availability is not proof of learned strategy behavior or economic value.",
        "strategies": [{"family": family, "status": status, "assessment": note,
                        "files": files, "handbook_source": headings[21]["source"]} for family, status, note, files in STRATEGIES],
        "handbook_sections": [{"part": part, **headings[part], "status": status, "assessment": note} for part, status, note in PARTS],
        "parameters": parameter_rows,
        "strategy_rule_parameters": [
            {"parameter": "cross-sectional return horizon", "value": 5, "file": "config.py"},
            {"parameter": "momentum baseline sign threshold", "value": 0.0, "file": "strategy.py"},
            {"parameter": "mean-reversion baseline z threshold", "value": 1.5, "file": "config.py"},
            {"parameter": "cross-sectional long/short percentiles", "value": [.7, .3], "file": "config.py"},
            {"parameter": "reference volatility for sizing", "value": .01, "file": "config.py"},
        ],
        "priorities": [
            "Freeze chronological historical train/validation/final-test datasets; label masking and purging now covered by fixtures.",
            "Reconcile repaired research metric conventions and cost assumptions with organizer/account sources.",
            "Exercise shared targets and reconciled delta execution against sustained paper/sandbox observations.",
            "Select/test additional concentration and daily-loss policies if required; integrated hard caps are fixture-tested.",
            "Measure probability/conditional-payoff stability and out-of-sample economic performance on real data.",
            "Run repaired paired component comparisons with frozen settings and realistic measured/stressed costs.",
            "Run historical multi-regime, multi-asset parameter/cost/delay stress tests and paper accounting before deciding which 2–4 strategy families to retain.",
        ],
        "unverified": ["Historical profitability: no market dataset, saved trained model or backtest results committed.",
                       "Venue fees, minimum order sizes, symbols, short mechanics, rate limits and exact competition scoring conventions.",
                       "Actual venue behavior of the new live adapter; all current execution evidence is mocked or paper."],
    }
