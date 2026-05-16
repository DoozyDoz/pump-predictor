"""Daily batch pipeline: 5 quantitative signals + qualitative boost → alerts.

Supports staged workflow (watchlist → confirmation → entry) and legacy
immediate-alert mode controlled by LEGACY_IMMEDIATE_ALERTS config flag.
"""

import csv
import json
import os
from datetime import datetime
from src.config import ALERT_THRESHOLD, POSITION_SIZE_PCT, LEGACY_IMMEDIATE_ALERTS
from src.db import db_session, init_db
from src.universe import refresh_universe, daily_volume_check
from src.signals import (
    compute_all_funding_signals,
    compute_oi_divergence_signal, finalize_oi_divergence_signals,
    compute_ls_ratio_signal, finalize_ls_ratio_signals,
    compute_taker_ratio_signal, finalize_taker_signals,
    compute_order_book_signal, finalize_order_book_signals,
)
from src.binance import (
    TakerHistory, get_taker_ratio_history, get_24h_tickers,
    get_bulk_funding_rates,
)
from src.qualitative import (
    QualitativeTag, TokenQualitativeProfile,
    check_defillama_metrics, qualitative_override,
    profile_to_catalyst_events,
)
from src.watchlist import generate_watchlist
from src.stages import StageManager
from src.regime import detect_regime, is_suppressed
from src.risk import compute_atr, position_size as risk_position_size
from src.notify import TelegramNotifier
from src.catalysts import CatalystScorer, fetch_catalyst_data, CatalystResult

# Token → CoinGecko ID mapping (build from API, cache locally)
import os as _os
_MAPPING_PATH = _os.path.join(_os.path.dirname(__file__), "..", "data", "coingecko_map.json")


def _load_mapping() -> dict:
    if _os.path.exists(_MAPPING_PATH):
        with open(_MAPPING_PATH) as f:
            return json.load(f)
    return {}


def _save_mapping(m: dict):
    _os.makedirs(_os.path.dirname(_MAPPING_PATH), exist_ok=True)
    with open(_MAPPING_PATH, "w") as f:
        json.dump(m, f, indent=2)


def _compute_signals(symbols):
    """Run all 5 quantitative signal computations.
    Returns (funding_signals, oi_signals, ls_signals, taker_signals, book_signals,
             scan_status, scan_errors, deep_check_syms)."""
    scan_status = "FULL"
    scan_errors = []

    # S1: Funding-rate extreme (all tokens)
    try:
        funding_signals = compute_all_funding_signals(symbols)
    except Exception as e:
        print(f"SCAN FAILED: funding signal computation failed — {e}")
        return None

    # S2-S4: OI, LS, Taker on deep-check symbols
    try:
        all_funding_rates = get_bulk_funding_rates(symbols)
    except Exception as e:
        print(f"SCAN FAILED: cannot fetch funding rates — {e}")
        return None
    if not all_funding_rates:
        print("SCAN FAILED: empty funding rate response")
        return None

    funding_present = [(s, all_funding_rates[s]) for s in symbols if s in all_funding_rates]
    deep_check_syms = [s for s, _ in funding_present]

    oi_signals, ls_signals, taker_signals = [], [], []
    for sym in deep_check_syms:
        try:
            s = compute_oi_divergence_signal(sym)
            if s is not None: oi_signals.append(s)
        except Exception:
            pass
        try:
            s = compute_ls_ratio_signal(sym)
            if s is not None: ls_signals.append(s)
        except Exception:
            pass
        try:
            candles = get_taker_ratio_history(sym, period="1h", limit=500)
            if candles:
                hist = TakerHistory(candles)
                s = compute_taker_ratio_signal(sym, hist)
                if s is not None: taker_signals.append(s)
        except Exception:
            pass

    oi_signals = finalize_oi_divergence_signals(oi_signals)
    ls_signals = finalize_ls_ratio_signals(ls_signals)
    taker_signals = finalize_taker_signals(taker_signals)

    if len(oi_signals) == 0:
        scan_errors.append("OI")
        scan_status = "PARTIAL"
    if len(ls_signals) == 0:
        scan_errors.append("LS")
        scan_status = "PARTIAL"

    # S5: Order book — on tokens with >= 1 other signal firing
    book_signals = []
    fired_set = set()
    for sig_list in [oi_signals, ls_signals, taker_signals]:
        for s in sig_list:
            if s.fired:
                fired_set.add(s.symbol)
    for s in funding_signals:
        if s.fired:
            fired_set.add(s.symbol)

    book_candidates = [s for s in deep_check_syms if s in fired_set]
    if book_candidates:
        for sym in book_candidates:
            try:
                s = compute_order_book_signal(sym)
                if s is not None: book_signals.append(s)
            except Exception:
                pass
        book_signals = finalize_order_book_signals(book_signals)

    n1 = sum(1 for s in funding_signals if s.fired)
    n2 = sum(1 for s in oi_signals if s.fired)
    n3 = sum(1 for s in ls_signals if s.fired)
    n4 = sum(1 for s in taker_signals if s.fired)
    n5 = sum(1 for s in book_signals if s.fired)
    status_line = f"Fires — Fund:{n1} OI:{n2} LS:{n3} Taker:{n4} Book:{n5}"
    if scan_status == "PARTIAL":
        status_line += f" | SCAN PARTIAL (missing: {', '.join(scan_errors)})"
    print(status_line)

    return (funding_signals, oi_signals, ls_signals, taker_signals, book_signals,
            scan_status, scan_errors, deep_check_syms, all_funding_rates)


def run_daily(symbols: list[str] | None = None, portfolio_usd: float = 1000.0,
              legacy: bool | None = None):
    """
    Run the daily batch pipeline.
    If legacy=True (or LEGACY_IMMEDIATE_ALERTS is True), uses the old
    immediate-alert behavior. Otherwise runs Phase 1 (watchlist generation)
    of the staged workflow.
    """
    if legacy is None:
        legacy = LEGACY_IMMEDIATE_ALERTS

    if not legacy:
        return run_phase1_watchlist(symbols, portfolio_usd)

    # ---- Legacy mode: original immediate-alert behavior ----
    init_db()
    run_ts = datetime.utcnow().isoformat()
    mapping = _load_mapping()

    if symbols is None:
        symbols = refresh_universe()
    symbols = daily_volume_check(symbols)
    sym_names = [s.replace("USDT", "") for s in symbols]
    print(f"Universe: {len(symbols)} tokens")

    try:
        all_tickers = {t["symbol"]: t for t in get_24h_tickers()}
    except Exception as e:
        print(f"SCAN FAILED: cannot fetch 24h tickers — {e}")
        return []

    result = _compute_signals(symbols)
    if result is None:
        return []
    (funding_signals, oi_signals, ls_signals, taker_signals, book_signals,
     scan_status, scan_errors, deep_check_syms, all_funding_rates) = result

    _store_snapshots(symbols, all_funding_rates, oi_signals, ls_signals,
                     taker_signals, run_ts)

    qualitative_profiles = _build_qualitative(symbols, sym_names, all_tickers, mapping)

    alerts = _build_alerts(
        funding_signals, oi_signals, ls_signals, taker_signals, book_signals,
        qualitative_profiles, run_ts, portfolio_usd, scan_status,
    )

    if alerts:
        _write_csv(alerts)
        _print_terminal(alerts, portfolio_usd)
        _send_telegram(alerts, portfolio_usd)
    else:
        _write_empty_csv(run_ts)
        print("No pump alerts today.")
        _send_telegram([], portfolio_usd)
    return alerts


# ---------------------------------------------------------------------------
# Staged workflow phases
# ---------------------------------------------------------------------------

def run_phase1_watchlist(symbols: list[str] | None = None,
                         portfolio_usd: float = 1000.0) -> list[dict]:
    """Phase 1: Generate watchlist from raw signals. No buy alerts sent."""
    init_db()
    run_ts = datetime.utcnow().isoformat()
    mapping = _load_mapping()

    if symbols is None:
        symbols = refresh_universe()
    symbols = daily_volume_check(symbols)
    sym_names = [s.replace("USDT", "") for s in symbols]
    print(f"Universe: {len(symbols)} tokens")
    print("Phase 1: Watchlist generation (staged mode)")

    # 24h tickers
    try:
        all_tickers = {t["symbol"]: t for t in get_24h_tickers()}
    except Exception as e:
        print(f"SCAN FAILED: cannot fetch 24h tickers — {e}")
        return []

    # Quantitative signals
    result = _compute_signals(symbols)
    if result is None:
        return []
    (funding_signals, oi_signals, ls_signals, taker_signals, book_signals,
     scan_status, scan_errors, deep_check_syms, all_funding_rates) = result

    _store_snapshots(symbols, all_funding_rates, oi_signals, ls_signals,
                     taker_signals, run_ts)

    # Qualitative signals
    qualitative_profiles = _build_qualitative(symbols, sym_names, all_tickers, mapping)

    # Market regime filter
    regime = detect_regime()
    print(f"Market regime: {regime.value}")
    if is_suppressed(regime):
        print("Regime UNFAVORABLE: watchlist generation suppressed")
        return []

    # Catalyst scoring
    catalyst_results: dict[str, CatalystResult] = {}
    for sym in symbols:
        events = fetch_catalyst_data(sym, all_tickers, mapping)
        profile = qualitative_profiles.get(sym)
        if profile:
            events.extend(profile_to_catalyst_events(profile, all_tickers))
        price_changes = _get_price_changes(sym)
        result = CatalystScorer().aggregate(sym, events, price_changes)
        catalyst_results[sym] = result

    # Generate watchlist (pass price changes through)
    candidates = generate_watchlist(
        funding_signals, oi_signals, ls_signals, taker_signals, book_signals,
        qualitative_profiles, regime, catalyst_results=catalyst_results,
        price_changes={sym: _get_price_changes(sym) for sym in symbols},
    )

    if candidates:
        print(f"Watchlist: {len(candidates)} candidates added")
        # Use catalyst formatter when any candidate has catalyst priority
        if any(c.get("priority") == "URGENT_CATALYST" for c in candidates):
            notifier = TelegramNotifier(
                os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
            )
            msg = notifier.format_catalyst_watchlist(candidates)
            _send_telegram_stage(msg, stage="watchlist")
        else:
            msg = f"<b>Watchlist updated</b> — {len(candidates)} candidate(s) for confirmation."
            _send_telegram_stage(msg, stage="watchlist")
    else:
        print("No watchlist candidates today.")
        _send_telegram_stage("<b>Watchlist</b> — No candidates today.", stage="watchlist")

    return candidates


def run_phase2_confirmation() -> list[dict]:
    """Phase 2: Check watchlist candidates for confirmation (intraday polling)."""
    from src.confirmation import ConfirmationChecker
    from src.db import db_session

    stage_mgr = StageManager()

    # Load catalyst results for active watchlist symbols from DB
    candidates = stage_mgr.get_watchlist_candidates()
    symbols = [c["symbol"] for c in candidates]
    catalyst_results: dict[str, CatalystResult] = {}
    if symbols:
        with db_session() as conn:
            rows = conn.execute(
                """SELECT symbol, catalyst_score, catalyst_event_type, priority,
                          is_negative_catalyst, has_blocking_negative_catalyst
                   FROM watchlist WHERE symbol IN ({}) AND expired = FALSE
                   ORDER BY added_ts DESC""".format(
                    ",".join("?" * len(symbols))
                ),
                symbols,
            ).fetchall()
            for row in rows:
                sym = row["symbol"]
                # Rebuild a lightweight CatalystResult from persisted fields
                catalyst_results[sym] = CatalystResult(
                    symbol=sym,
                    score=row["catalyst_score"] or 0.0,
                    is_major_catalyst=(row["priority"] == "URGENT_CATALYST"),
                    is_negative_catalyst=bool(row["is_negative_catalyst"]),
                    has_blocking_negative_catalyst=bool(row["has_blocking_negative_catalyst"]),
                )

    checker = ConfirmationChecker(stage_mgr, catalyst_results=catalyst_results)
    results = checker.run_confirmation()

    if not results:
        return []

    confirmed = [r for r in results if r.get("confirmed")]
    denied = [r for r in results if r.get("denied")]
    promoted = [r for r in confirmed if r.get("promoted_to_entry")]

    if confirmed:
        print(f"Confirmation: {len(confirmed)} confirmed, {len(promoted)} promoted to entry")
        for c in confirmed:
            print(f"  {c['symbol']}: {c.get('reason', '')}")
    if denied:
        print(f"  {len(denied)} denied/expired")

    return results


def run_phase3_entry(confirmed_entries: list[dict] | None = None,
                     portfolio_usd: float = 1000.0) -> list[dict]:
    """Phase 3: Final entry signal with ATR sizing for confirmed entries."""
    stage_mgr = StageManager()

    if confirmed_entries is None:
        entries = stage_mgr.get_by_stage("entry")
    else:
        entries = confirmed_entries

    if not entries:
        return []

    entry_signals = []
    for e in entries:
        sym = e["symbol"]
        atr_val = compute_atr(sym)
        if atr_val is None or atr_val <= 0:
            atr_val = 2.0  # fallback

        pos_size = risk_position_size(atr_val, portfolio_usd)
        entry_signals.append({
            "symbol": sym,
            "atr_pct": f"{atr_val:.2f}%",
            "position_size_usd": f"{pos_size:.2f}",
            "score": e.get("score", 0),
            "fired_signals": e.get("signals_fired", ""),
            "catalyst_score": e.get("catalyst_score", 0.0),
            "catalyst_event_type": e.get("catalyst_event_type", ""),
            "catalyst_title": e.get("catalyst_title", ""),
        })

    # Send entry alerts
    has_catalyst = any(s.get("catalyst_score", 0) >= 0.75 for s in entry_signals)
    if has_catalyst:
        notifier = TelegramNotifier(
            os.getenv("TELEGRAM_BOT_TOKEN", ""), os.getenv("TELEGRAM_CHAT_ID", "")
        )
        msg = notifier.format_catalyst_entry(entry_signals, portfolio_usd)
        _send_telegram_stage(msg, stage="entry")
    else:
        msg_lines = [f"<b>ENTRY SIGNALS</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}", ""]
        for s in entry_signals:
            sym = s["symbol"].replace("USDT", "")
            msg_lines.append(f"<b>BUY ${sym}</b>")
            msg_lines.append(f"  Size: ${s['position_size_usd']} | ATR: {s['atr_pct']}")
            msg_lines.append("")
        _send_telegram_stage("\n".join(msg_lines), stage="entry")

    print(f"Entry signals: {len(entry_signals)} confirmed trades")
    return entry_signals


def _get_price_changes(symbol: str) -> dict[str, float | None]:
    """Fetch 1h/4h/24h percent change from close prices.
    Returns None for a window if denominator is zero or data is missing.
    """
    import logging
    from src.binance import get_klines
    try:
        candles = get_klines(symbol, interval="1h", limit=48, market="spot")
    except Exception:
        return {"1h": None, "4h": None, "24h": None}
    if not candles or len(candles) < 24:
        return {"1h": None, "4h": None, "24h": None}
    closes = [c["c"] for c in candles if c.get("c") is not None]
    if len(closes) < 24:
        return {"1h": None, "4h": None, "24h": None}
    current = closes[-1]

    def _safe_pct(current_val, previous_val, interval: str):
        if previous_val is None or previous_val == 0:
            logging.warning(
                "Zero or missing close for %s %s window (prev=%s)",
                symbol, interval, previous_val,
            )
            return None
        return ((current_val - previous_val) / previous_val) * 100

    h1 = _safe_pct(current, closes[-2] if len(closes) >= 2 else None, "1h")
    h4 = _safe_pct(current, closes[-5] if len(closes) >= 5 else None, "4h")
    h24 = _safe_pct(current, closes[-25] if len(closes) >= 25 else None, "24h")
    return {"1h": h1, "4h": h4, "24h": h24}


def _send_telegram_stage(msg: str, stage: str = "watchlist"):
    """Send a stage-specific message via Telegram."""
    from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        ok = notifier.send(msg)
        print(f"Telegram ({stage}): {'sent' if ok else 'failed'}")
    except Exception as e:
        print(f"Telegram error ({stage}): {e}")


def _build_qualitative(symbols, sym_names, all_tickers, mapping):
    """Build qualitative profiles for all tokens using free data sources."""
    profiles = {}

    for i, sym in enumerate(symbols):
        name = sym_names[i]
        profile = TokenQualitativeProfile(symbol=sym)

        # 1. Volume + Price anomaly (Binance 24h ticker — all tokens)
        ticker = all_tickers.get(sym.upper())
        if ticker:
            try:
                vol = float(ticker.get("quoteVolume", 0) or 0)  # USDT volume
                price_chg = float(ticker.get("priceChangePercent", 0) or 0)
                trades = int(ticker.get("count", 0) or 0)
                high = float(ticker.get("highPrice", 0) or 0)
                low = float(ticker.get("lowPrice", 0) or 0)
                last = float(ticker.get("lastPrice", 0) or 0)

                # High volume + large price drop = capitulation → bounce candidate
                if vol > 10_000_000 and price_chg < -8:
                    profile.add_tag(QualitativeTag(
                        token_symbol=sym,
                        catalyst_type="capitulation_volume",
                        description=f"${vol:,.0f} vol with {price_chg:.1f}% drop — capitulation",
                        source="binance_24h",
                        confidence=0.5,
                        detected_at=datetime.utcnow().isoformat(),
                        lead_time_hours=12,
                    ))

                # High volume + positive price = momentum (confirms quant signals)
                if vol > 10_000_000 and price_chg > 5:
                    profile.add_tag(QualitativeTag(
                        token_symbol=sym,
                        catalyst_type="momentum_volume",
                        description=f"${vol:,.0f} vol with +{price_chg:.1f}% — momentum building",
                        source="binance_24h",
                        confidence=0.4,
                        detected_at=datetime.utcnow().isoformat(),
                        lead_time_hours=6,
                    ))

                # High trade count relative to typical → unusual attention
                if trades > 50_000:
                    profile.add_tag(QualitativeTag(
                        token_symbol=sym,
                        catalyst_type="trade_count_spike",
                        description=f"{trades:,} trades in 24h — elevated attention",
                        source="binance_24h",
                        confidence=0.3,
                        detected_at=datetime.utcnow().isoformat(),
                        lead_time_hours=12,
                    ))

                # Daily range — now a filter, not a boost.
                # Wide range = high noise, tight stop won't survive.
                if last > 0 and high > low:
                    daily_range = (high - low) / last * 100
                    if daily_range > 25:
                        profile.blocked = True
                        profile.block_reason = f"daily range {daily_range:.1f}% > 25% — stop too tight for this vol"
            except (ValueError, TypeError):
                pass

        # 2. DeFiLlama TVL/Revenue trends (for tokens with known protocol slugs)
        cg_id = mapping.get(name, {}).get("coingecko_id")
        if cg_id:
            # Check for DeFiLlama protocol match
            metrics = check_defillama_metrics(cg_id) or check_defillama_metrics(name.lower())
            if metrics and metrics.get("tvl"):
                if metrics.get("change_7d", 0) and metrics["change_7d"] > 10:
                    profile.add_tag(QualitativeTag(
                        token_symbol=sym,
                        catalyst_type="tvl_surge",
                        description=f"TVL +{metrics['change_7d']:.1f}% in 7d — protocol growth",
                        source="defillama",
                        confidence=0.5,
                        detected_at=datetime.utcnow().isoformat(),
                        lead_time_hours=72,
                    ))
                if metrics.get("revenue_7d", 0) and metrics["revenue_7d"] > 10000:
                    profile.add_tag(QualitativeTag(
                        token_symbol=sym,
                        catalyst_type="protocol_revenue",
                        description=f"Revenue ${metrics['revenue_7d']:,.0f} in 7d — real demand",
                        source="defillama",
                        confidence=0.5,
                        detected_at=datetime.utcnow().isoformat(),
                        lead_time_hours=72,
                    ))

        # 3. Price momentum from ticker (free signal)
        if ticker:
            try:
                pct_24h = float(ticker.get("priceChangePercent", 0) or 0)
                if pct_24h < -10:
                    profile.add_tag(QualitativeTag(
                        token_symbol=sym,
                        catalyst_type="oversold",
                        description=f"24h price {pct_24h:.1f}% — oversold bounce candidate",
                        source="binance_24h",
                        confidence=0.3,
                        detected_at=datetime.utcnow().isoformat(),
                        lead_time_hours=6,
                    ))
            except (ValueError, TypeError):
                pass

        # 4. 24h price filter — block chasing if no real catalyst
        #    (runs AFTER all tags built so catalyst_boost is fully computed)
        if ticker:
            try:
                pct_24h = float(ticker.get("priceChangePercent", 0) or 0)
                if not profile.blocked and pct_24h > 20 and profile.catalyst_boost < 0.5:
                    profile.blocked = True
                    profile.block_reason = (
                        f"24h price +{pct_24h:.1f}% with no catalyst ({profile.catalyst_boost:.2f}) — "
                        f"already pumped, not a pre-pump setup"
                    )
            except (ValueError, TypeError):
                pass

        profiles[sym] = profile

    # Count qualitative signals found
    total_tags = sum(len(p.tags) for p in profiles.values())
    cat_boosted = sum(1 for p in profiles.values() if p.catalyst_boost >= 0.5)
    blocked = sum(1 for p in profiles.values() if p.blocked)
    if total_tags > 0:
        print(f"Qualitative: {total_tags} tags, {cat_boosted} catalyst-boosted, "
              f"{blocked} blocked by filters")

    return profiles


def _build_alerts(fund, oi, ls, taker, book, qual_profiles, run_ts, portfolio_usd=1000.0,
                  scan_status: str = "FULL"):
    alerts = []
    pos_size = portfolio_usd * POSITION_SIZE_PCT
    f_map = {s.symbol: s for s in fund}
    oi_map = {s.symbol: s for s in oi}
    ls_map = {s.symbol: s for s in ls}
    t_map = {s.symbol: s for s in taker}
    b_map = {s.symbol: s for s in book}
    all_syms = set(f_map) | set(oi_map) | set(ls_map) | set(t_map) | set(b_map)

    for sym in sorted(all_syms):
        score = 0
        fired = []
        details = {}

        # Quantitative scoring
        fs = f_map.get(sym)
        if fs and fs.fired:
            score += 1; fired.append("funding_extreme")
            details.update({"fund_rate": f"{fs.current_rate:.6f}", "fund_pct": f"{fs.percentile_90d:.1f}"})

        oi_s = oi_map.get(sym)
        if oi_s and oi_s.fired:
            score += 1; fired.append("oi_divergence")
            details.update({"oi_div": f"{oi_s.divergence:.2f}"})

        ls_s = ls_map.get(sym)
        if ls_s and ls_s.fired:
            score += 1; fired.append("ls_extreme")
            details.update({"ls_ratio": f"{ls_s.current_ratio:.4f}"})

        t_s = t_map.get(sym)
        if t_s and t_s.fired:
            score += 1; fired.append("taker_extreme")
            details.update({"taker_ratio": f"{t_s.current_ratio:.4f}"})

        b_s = b_map.get(sym)
        if b_s and b_s.fired:
            score += 1; fired.append("book_imbalance")
            details.update({"bid_dom": f"{b_s.bid_dominance:.3f}"})

        # Qualitative boost (catalyst only; ticker tags are display-only)
        profile = qual_profiles.get(sym)
        if profile and profile.blocked:
            continue  # filtered out by daily range or 24h price check
        cat_boost = profile.catalyst_boost if profile else 0.0
        adjusted_score, override_reason = qualitative_override(
            score, cat_boost, ALERT_THRESHOLD,
        )

        if adjusted_score < ALERT_THRESHOLD:
            continue

        # Minimum quant signal rule: need ≥2 quant signals, or ≥1 + real catalyst
        catalyst_present = cat_boost >= 0.5
        if score < 2 and not (score >= 1 and catalyst_present):
            continue

        # Require at least one strong derivative signal: funding, OI, or LS.
        # Taker + book alone is too fragile for live entries.
        STRONG_SIGNALS = {"funding_extreme", "oi_divergence", "ls_extreme"}
        if not (set(fired) & STRONG_SIGNALS):
            continue

        # Collect qualitative tags
        qual_tags = []
        if profile and profile.tags:
            for tag in profile.tags:
                qual_tags.append(f"{tag.catalyst_type}:{tag.description[:60]}")

        alerts.append({
            "symbol": sym,
            "quant_score": f"{score}/5",
            "catalyst_boost": f"{cat_boost:+.2f}",
            "final_score": f"{adjusted_score}/5",
            "signals_fired": "|".join(fired),
            "qual_tags": " | ".join(qual_tags) if qual_tags else "",
            "override": override_reason,
            "scan_status": scan_status,
            "position_size_usd": f"{pos_size:.2f}",
            "alert_ts": run_ts,
            **details,
        })

        _persist_alert(sym, adjusted_score, fired, run_ts)

    return alerts


def _send_telegram(alerts, portfolio_usd):
    """Send alerts via Telegram if configured."""
    from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    from src.notify import TelegramNotifier
    try:
        notifier = TelegramNotifier(TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID)
        msg = notifier.format_alerts(alerts, portfolio_usd)
        ok = notifier.send(msg)
        print(f"Telegram: {'sent' if ok else 'failed'}")
    except Exception as e:
        print(f"Telegram error: {e}")


def _persist_alert(sym, score, fired, run_ts):
    with db_session() as conn:
        conn.execute("INSERT OR IGNORE INTO tokens (symbol, exchange, market) VALUES (?, 'B', 'spot')", (sym,))
        row = conn.execute(
            "SELECT id FROM tokens WHERE symbol = ? AND exchange = 'B' AND market = 'spot'", (sym,)
        ).fetchone()
        if row:
            conn.execute(
                "INSERT INTO alerts (token_id, pump_score, fired_signals, alert_ts) VALUES (?, ?, ?, ?)",
                (row[0], score, "|".join(fired), run_ts),
            )


CSV_FIELDNAMES = [
    "symbol", "quant_score", "catalyst_boost", "final_score",
    "signals_fired", "qual_tags", "override", "scan_status", "position_size_usd",
    "alert_ts", "fund_rate", "fund_pct", "oi_div", "ls_ratio", "taker_ratio", "bid_dom",
]


def _write_csv(alerts, path="pump_alerts.csv"):
    if not alerts: return
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(alerts)
    print(f"Alerts → {path}")


def _store_snapshots(symbols, funding_rates, oi_signals, ls_signals,
                     taker_signals, run_ts):
    """Store today's signal values to build local percentile history."""
    from src.snapshots import store_snapshots
    from src.binance import get_open_interest
    today = run_ts[:10]  # YYYY-MM-DD

    snapshots = []
    for sym in symbols:
        # Funding rate
        rate = funding_rates.get(sym)
        if rate is not None:
            snapshots.append({"symbol": sym, "signal_type": "funding_rate",
                              "value": rate, "snapshot_ts": today})

    # OI — fetch current raw open interest from Binance (OI signals store divergence)
    oi_symbols = {s.symbol for s in oi_signals}
    for sym in oi_symbols:
        try:
            oi = get_open_interest(sym)
            if oi is not None:
                snapshots.append({"symbol": sym, "signal_type": "oi_value",
                                  "value": oi, "snapshot_ts": today})
        except Exception:
            pass

    # LS ratio values
    for s in ls_signals:
        snapshots.append({"symbol": s.symbol, "signal_type": "ls_ratio",
                          "value": s.current_ratio, "snapshot_ts": today})

    # Taker ratio values
    for s in taker_signals:
        snapshots.append({"symbol": s.symbol, "signal_type": "taker_ratio",
                          "value": s.current_ratio, "snapshot_ts": today})

    if snapshots:
        store_snapshots(snapshots)


def _write_empty_csv(run_ts, path="pump_alerts.csv"):
    with open(path, "w", newline="") as f:
        f.write(f"# No alerts — {run_ts}\n")
    print(f"Empty alerts → {path}")


def _print_terminal(alerts, portfolio_usd):
    pos_size = portfolio_usd * POSITION_SIZE_PCT
    print()
    print("╔" + "═" * 70 + "╗")
    print(f"║  🚀  PUMP ALERTS — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}".ljust(72) + "║")
    print(f"║  Portfolio: ${portfolio_usd:.0f} | Position size: ${pos_size:.0f} | ≥{ALERT_THRESHOLD}/5 signals".ljust(72) + "║")
    print("╚" + "═" * 70 + "╝")

    for a in alerts:
        sym = a['symbol'].replace('USDT', '')
        score = a['quant_score']
        cat = a.get('catalyst_boost', '+0.00')
        final = a['final_score']
        signals = a.get('signals_fired', '')
        qual_tags = a.get('qual_tags', '')
        override = a.get('override', '')
        scan_status = a.get('scan_status', 'FULL')

        # Header
        paper_tag = "PAPER ONLY — " if scan_status == "PARTIAL" else ""
        print(f"\n  {'█'*60}")
        print(f"  █  {paper_tag}BUY ${sym}")
        print(f"  █  Position: ${pos_size:.0f} | Stop: -7% | TP: +15%/+25%/trail")
        print(f"  █  Score: {score} quant + {cat} catalyst = {final} (≥{ALERT_THRESHOLD} triggers)")
        if scan_status == "PARTIAL":
            print("  █  ⚠️  SCAN PARTIAL — do not execute live")
        print(f"  {'█'*60}")

        # Quantitative reasoning
        print("\n  📊  QUANTITATIVE SIGNALS:")
        if signals:
            fired_list = signals.split('|')
            for sig in fired_list:
                reason = _quant_reason(sig, a)
                print(f"      ✓  {reason}")
        else:
            print("      (none fired — alert from qualitative boost only)")

        # Qualitative reasoning
        if qual_tags:
            print("\n  📰  QUALITATIVE SIGNALS:")
            for tag in qual_tags.split(' | '):
                tag = tag.strip()
                if ':' in tag:
                    tag_type, desc = tag.split(':', 1)
                    print(f"      •  {desc.strip()}")

        # Verdict
        print("\n  ⚡  VERDICT: ", end="")
        if 'funding_extreme' in signals and ('volume' in qual_tags.lower() or 'momentum' in qual_tags.lower()):
            print("Funding extreme + volume spike — short squeeze setup. High conviction.")
        elif 'funding_extreme' in signals:
            print("Funding extreme detected — shorts overcrowded. Monitor for volume confirmation.")
        elif 'capitulation' in qual_tags.lower():
            print("Capitulation volume — potential bounce. High risk, size accordingly.")
        elif 'momentum' in qual_tags.lower():
            print("Momentum breakout — trend is active. Trail stops tightly.")
        elif override:
            print(f"Qualitative override — {override}")
        else:
            print("Multiple signals converging — edge confirmed.")

        # Action
        print("\n  🎯  ACTION: Place OCO on Binance Spot")
        print(f"      Symbol:    {sym}USDT")
        print("      Entry:     Market")
        print(f"      Size:      ${pos_size:.0f}")
        print("      Stop-loss: -7%")
        print("      TP1:       +15% (50%)")
        print("      TP2:       +25% (30%)")
        print("      Trailing:  -3% from peak (20%)")
        print("      ⚠️  5-min sanity check: scan for hacks, delistings, regulatory news")

    print(f"\n{'─'*70}")
    print(f"  {len(alerts)} alert(s) | Next run: 08:07 UTC | Good luck 🍀")
    print(f"{'─'*70}\n")


def _quant_reason(signal_name: str, alert: dict) -> str:
    """Build human-readable reason for a quantitative signal."""
    if signal_name == 'funding_extreme':
        rate = alert.get('fund_rate', '?')
        pct = alert.get('fund_pct', '?')
        return f"Funding rate extreme: {rate} at {pct}th percentile — shorts are overcrowded (contrarian bullish)"
    if signal_name == 'oi_divergence':
        div = alert.get('oi_div', '?')
        return f"OI/Price divergence: {div} — rising open interest without price increase (accumulation)"
    if signal_name == 'ls_extreme':
        ratio = alert.get('ls_ratio', '?')
        return f"Long/Short ratio extreme: {ratio} — too many shorts, sentiment too bearish (contrarian bullish)"
    if signal_name == 'taker_extreme':
        ratio = alert.get('taker_ratio', '?')
        return f"Taker ratio extreme: {ratio} — too many market sells, sellers exhausted (contrarian bullish)"
    if signal_name == 'book_imbalance':
        dom = alert.get('bid_dom', '?')
        return f"Order book bid dominance: {dom} — strong buy wall supporting price"
    return f"{signal_name}"
