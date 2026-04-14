# AlgVex Code Review Rules

This file defines review-specific rules for Claude Code Review.
It complements `CLAUDE.md` (project-wide instructions) with rules specifically for automated PR review.

> **Audience**: Claude Code Review multi-agent system.
> **Scope**: Every PR to `main` branch.

---

## Critical Rules (Block Merge)

### 1. Zero Truncation Policy

Any new `[:N]` string slice on text data **must be flagged as Critical**.

Allowed exceptions (do NOT flag these):
- Array/list indexing: `list[:5]`, `df.head(10)`
- Protocol hard limits: Telegram 4096 char split via `_split_message()`
- `_validate_agent_output()` safety net with `_raw_{key}` preservation

Flag as Critical:
- `text[:100]`, `summary[:500]`, `reasoning[:1000]` without `_raw_*` backup
- Any `.truncate()` call on text fields
- Agent-to-agent data passing without `_raw_*` fields

### 2. Stop-Loss Safety

Flag as Critical:
- LONG position with SL >= entry price
- SHORT position with SL <= entry price
- Any path where `_submit_emergency_sl()` can fail without retry
- Removing or weakening emergency SL logic
- `cancel_all_orders()` in `on_stop()` (must preserve SL/TP protection)

### 3. Layer Order Integrity

Flag as Critical:
- `_layer_orders.clear()` without `_next_layer_idx = 0` reset (must always be paired)
- Modifying one layer's SL/TP when processing another layer's fill
- Missing `_order_to_layer` reverse lookup update when creating/removing layers
- Missing `_save_layer_orders()` after layer state mutation

### 4. API Key / Secret Exposure

Flag as Critical:
- Hardcoded API keys, tokens, or secrets in source code
- API keys in `configs/*.yaml` (must be in `~/.env.algvex` only)
- Logging or printing API keys/secrets (even partially)
- Committing `.env` files with real credentials

### 5. Position Sizing Bounds

Flag as Critical:
- Missing `min(position_usdt, max_usdt)` clamp on final position size
- AI `size_pct` not clamped to `<= 100`
- Missing `max_position_ratio` enforcement

---

## High Priority Rules (Should Fix)

### 6. SSoT (Single Source of Truth) Sync

If any of these SSoT files are modified, flag if dependents are not also checked/updated:

| SSoT File | Must Check |
|-----------|-----------|
| `utils/shared_logic.py` | `order_flow_processor.py`, `technical_manager.py`, verification scripts |
| `strategy/trading_logic.py` | `utils/backtest_math.py`, `web/backend/services/trade_evaluation_service.py` |
| `utils/telegram_bot.py` (`side_to_cn`) | All strategy mixin files, `telegram_command_handler.py` |
| `agents/prompt_constants.py` | `tag_validator.py`, `report_formatter.py` |

Remind: `python3 scripts/check_logic_sync.py` must pass.

### 7. Telegram Display Language

Flag if user-facing Telegram messages contain raw English direction terms:
- `LONG`, `SHORT`, `BUY`, `SELL` in f-strings or message templates
- Must use `side_to_cn()` or hardcoded Chinese equivalents (open/close/position/side)
- Correct terms: (open/close/position/side)

### 8. Mechanical Scoring Integrity (v46.0)

Flag if:
- `compute_anticipatory_scores()` confluence damping removed (single-signal saturation)
- `CONFIRMED_SELL` CVD state not handled in flow_votes
- `sr_zones` not passed to `mechanical_analyze()`
- `_quality_score` field not accessible in event_handlers
- FR hard gate blocks mechanical mode entries

### 9. R/R Ratio Guarantees

Flag if:
- `calculate_mechanical_sltp()` no longer guarantees R/R >= 1.5:1
- Counter-trend R/R multiplier (1.3x) is bypassed or reduced
- `min_rr_ratio` validation removed from ConfigManager

### 10. NautilusTrader API Usage

Flag if:
- Using `order_factory.bracket()` + `submit_order_list()` (must use step-by-step submission)
- Using `order_factory.limit()` for TP (must use `limit_if_touched()` for position-linked TP)
- Accessing `indicator_manager` from background threads (Rust/Cython not thread-safe)
- Using `nautilus_trader.core.nautilus_pyo3` indicators (must use `nautilus_trader.indicators` Cython)

---

## Medium Priority Rules (Recommend Fix)

### 11. Configuration Layer Violations

Flag if:
- Business parameters hardcoded in Python (should be in `configs/*.yaml`)
- Sensitive data in YAML (should be in `~/.env.algvex`)
- Environment-specific logic with `if/else` in code (should be in `configs/{env}.yaml`)
- Domain knowledge constants routed through YAML (Extension/Volatility thresholds are code constants)

### 12. Error Handling Patterns

Flag if:
- `sentiment_data['key']` direct access (must use `.get('key', default)`)
- Missing error handling on external API calls (Binance, Coinalyze, Telegram)
- Bare `except:` without specific exception types
- Swallowing exceptions silently (no logging)

### 13. Async / Threading Safety

Flag if:
- Calling NautilusTrader Cython objects from non-event-loop threads
- Missing `asyncio` safety in Telegram command handlers
- Blocking I/O in NautilusTrader event callbacks (`on_bar`, `on_timer`, `on_trade_tick`)

### 14. Feature Extraction Parity

Flag if:
- `extract_features()` field names don't match production data sources
- New features added to `FEATURE_SCHEMA` without `_avail_*` boolean flag consideration
- `compute_scores_from_features()` uses 0.0 default without `_avail_*` guard

---

## Nit Rules (Non-blocking)

### 15. Code Style

- Python entry point must be `main_live.py` (not `main.py`)
- Always `python3` (not `python`)
- Code comments in English
- Git commit messages in English
- User-facing text: Chinese-English mixed (Chinese primary, technical terms in English)

### 16. Occam's Razor

Flag as Nit if:
- Dead code not removed (commented-out blocks, unused imports, unreachable branches)
- `enabled: false` configuration blocks kept in YAML
- Premature abstractions for single-use patterns
- Fallback paths that cannot trigger in production

### 17. Documentation Sync

Flag as Nit if:
- CLAUDE.md references removed/renamed functions or files
- Version numbers in docs don't match code reality
- File line counts in CLAUDE.md significantly outdated (>20% drift)

---

## Files Requiring Extra Scrutiny

| File | Why | Review Focus |
|------|-----|-------------|
| `strategy/ai_strategy.py` | Core trading loop, money at risk | SL/TP logic, layer orders, emergency paths |
| `strategy/order_execution.py` | Order submission | Bracket safety, trailing stop bounds, position sizing |
| `strategy/safety_manager.py` | Emergency protection | Emergency SL retry, naked position detection |
| `strategy/event_handlers.py` | Order fill routing | Layer lookup, SL/TP pairing, position state |
| `agents/multi_agent_analyzer.py` | Mechanical decision pipeline | mechanical_analyze(), snapshot save, record_outcome |
| `agents/report_formatter.py` | Anticipatory scoring | compute_anticipatory_scores(), confluence damping, 3-dim weights |
| `utils/telegram_bot.py` | User communication | `side_to_cn()` SSoT, message splitting, dual-channel routing |
| `main_live.py` | System entry point | Environment config, adapter setup, strategy init |

---

## Validation Requirements

Every PR to `main` must pass these checks (enforced by CI):

```
smart_commit_analyzer.py    -> 0 failed rules
check_logic_sync.py         -> All logic clones in sync
analyze_dependencies.py     -> 0 circular deps, 0 missing modules
```

If the PR modifies SSoT files, the reviewer should verify that `check_logic_sync.py` covers the changed logic.

---

## What NOT to Flag

To reduce noise, do **not** flag:
- Array slicing (`list[:5]`, `df.iloc[:10]`) — only flag string truncation
- Existing code not changed by the PR (unless pre-existing Critical security issue)
- Test files using mock data or simplified logic
- Script files in `scripts/` that are diagnostic-only (not production path)
- Chinese characters in string literals (this is intentional, not encoding errors)
- Long files — AlgVex has large files by design (mixin architecture)
