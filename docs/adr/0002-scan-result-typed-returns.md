# ADR 0002: ScanResult Typed Returns for Staged Pipeline

## Status
Accepted

## Context
The staged pipeline (Phase 1 watchlist, Phase 2 confirmation) returned `[]` for every "empty" outcome — whether no tokens met thresholds, the market regime suppressed alerts, or a data source failed. The Telegram bot at `src/bot.py` could only check `if candidates:` and had no way to distinguish these cases. Users running `/scan` received "Running scan..." → "Scan complete" with nothing in between, creating confusion about whether the bot was broken, the market was quiet, or data sources were down.

## Decision
Introduce a typed `ScanResult` dataclass that replaces `list[dict]` as the return type for `run_phase1_watchlist()` and `run_phase2_confirmation()`. `ScanResult` carries:
- `status`: one of `no_setups`, `suppressed`, `api_failure`, `no_watchlist`, `no_confirmations`, `error`
- `alerts`: the list of Pump Alerts (empty for non-success statuses)
- `detail`: human-readable reason string
- `candidate_symbols`: tokens that scored ≥2/3 but were blocked (used for suppressed reporting)

## Consequences

### Positive
- The bot can now send precise "why empty" messages ("No Pump Alerts today", "Alerts suppressed: high volatility", "Binance API timeout").
- Background polling stores status to SQLite and only sends Telegram notifications on state changes, eliminating noise while preserving observability.
- Phase 1 still computes candidates when suppressed, so the human trader sees what would have fired — preserving the feedback loop for tuning suppression rules.

### Negative
- Touches `pipeline.py`, `confirmation.py`, `bot.py`, and `notify.py`.
- Callers must be updated to iterate `result.alerts` instead of the result directly.
- Adds a new `scan_status` SQLite table.

## Alternatives Considered
- **Exceptions for empty states** — rejected because empty scans are expected, not exceptional.
- **Side-channel log variable** — rejected because it couples the pipeline and bot through global state.
- **Raw list with sentinel tuples** — rejected because it obscures the contract and is error-prone.

## Related
- `CONTEXT.md` — term **Scan Report**
- `src/pipeline.py` — `run_phase1_watchlist`, `run_phase2_confirmation`
- `src/bot.py` — `handle_message`, background polling loop
- `src/db.py` — `scan_status` table
