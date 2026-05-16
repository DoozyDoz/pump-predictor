"""
Telegram paper trading bot — receives buy signals, tracks P&L, alerts on TP/SL.
Runs as a long-lived polling daemon.
"""

import time
import json
import requests
from datetime import datetime, timedelta
from src.config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    STOP_LOSS_PCT, TAKE_PROFIT_1_PCT, TAKE_PROFIT_2_PCT,
    TAKE_PROFIT_1_PCT_SHARE, TAKE_PROFIT_2_PCT_SHARE,
    TRAILING_STOP_PCT, POSITION_SIZE_PCT,
    CONFIRMATION_POLL_MINUTES, LEGACY_IMMEDIATE_ALERTS,
)
from src.db import db_session, init_db, get_last_scan_status, write_scan_status
from src.scan_result import ScanResult

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price"
POLL_INTERVAL = 2       # seconds between Telegram polls
PRICE_INTERVAL = 7200   # P&L update every 2 hours (seconds)
TP_CHECK_INTERVAL = 1800  # check TP/SL every 30 minutes (seconds)

# Emoji-prefixed button text that appears in the reply keyboard.
# When a user taps a button, Telegram sends the exact button text as a message.
MENU_BUTTONS = {
    "🔍 Scan": "scan",
    "📊 Positions": "positions",
    "👁 Watchlist": "watchlist",
    "❓ Help": "help",
}


def build_main_menu() -> dict:
    """Return a persistent ReplyKeyboardMarkup with common commands."""
    return {
        "keyboard": [
            ["🔍 Scan", "📊 Positions"],
            ["👁 Watchlist", "❓ Help"],
        ],
        "resize_keyboard": True,
        "persistent": True,
    }


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def send_message(chat_id: str, text: str, parse_mode: str = "HTML",
                 reply_markup: dict | None = None) -> bool:
    payload = {
        "chat_id": chat_id, "text": text,
        "parse_mode": parse_mode, "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        resp = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
        return resp.status_code == 200
    except Exception:
        return False


def get_updates(offset: int = 0) -> list[dict]:
    try:
        resp = requests.get(f"{API}/getUpdates", params={
            "offset": offset, "timeout": 30, "allowed_updates": ["message"],
        }, timeout=35)
        if resp.status_code == 200:
            return resp.json().get("result", [])
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Position management
# ---------------------------------------------------------------------------
def open_position(symbol: str, entry_price: float, chat_id: str, size_usd: float = 100.0) -> int | None:
    """Create a paper trade position. Returns trade ID or None."""
    tp1 = entry_price * (1 + TAKE_PROFIT_1_PCT)
    tp2 = entry_price * (1 + TAKE_PROFIT_2_PCT)
    stop = entry_price * (1 + STOP_LOSS_PCT)
    now = datetime.utcnow().isoformat()

    with db_session() as conn:
        conn.execute("INSERT OR IGNORE INTO tokens (symbol, exchange, market) VALUES (?, 'B', 'spot')", (symbol,))
        row = conn.execute("SELECT id FROM tokens WHERE symbol = ? AND exchange = 'B'", (symbol,)).fetchone()
        tid = row[0] if row else None

        cur = conn.execute("""
            INSERT INTO paper_trades (token_id, symbol, entry_price, entry_ts,
                position_size_usd, status, tp1, tp2, stop, trail_peak, chat_id)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
        """, (tid, symbol, entry_price, now, size_usd, tp1, tp2, stop, entry_price, chat_id))
        return cur.lastrowid


def close_position(trade_id: int, exit_price: float, reason: str = "manual") -> dict | None:
    """Close a paper trade and return summary. Accounts for TP1 partial fills."""
    with db_session() as conn:
        row = conn.execute("""
            SELECT symbol, entry_price, position_size_usd, status,
                   COALESCE(tp1_filled, 0) as tp1_filled,
                   COALESCE(realized_pnl, 0) as realized_pnl
            FROM paper_trades WHERE id = ?
        """, (trade_id,)).fetchone()
        if not row:
            return None
        if row["status"] == "closed":
            return {"symbol": row["symbol"], "error": "already closed"}

        remaining = 1.0 - row["tp1_filled"]
        pnl = row["realized_pnl"] + remaining * ((exit_price - row["entry_price"]) / row["entry_price"]) * 100
        now = datetime.utcnow().isoformat()
        conn.execute("""
            UPDATE paper_trades SET exit_price = ?, exit_ts = ?, exit_reason = ?,
                pnl_pct = ?, status = 'closed'
            WHERE id = ?
        """, (exit_price, now, reason, pnl, trade_id))
        return {
            "symbol": row["symbol"],
            "entry": row["entry_price"],
            "exit": exit_price,
            "pnl_pct": pnl,
            "reason": reason,
        }


def get_active_positions(chat_id: str) -> list[dict]:
    with db_session() as conn:
        rows = conn.execute("""
            SELECT *, COALESCE(tp1_filled, 0) as tp1_filled,
                   COALESCE(realized_pnl, 0) as realized_pnl
            FROM paper_trades WHERE chat_id = ? AND status IN ('active', 'tp1_hit')
            ORDER BY entry_ts
        """, (chat_id,)).fetchall()
    return [dict(r) for r in rows]


def get_price(symbol: str) -> float | None:
    """Get current price from Binance. Accepts base (COS) or full symbol (COSUSDT)."""
    sym = symbol if symbol.upper().endswith("USDT") else f"{symbol}USDT"
    try:
        resp = requests.get(f"{BINANCE_TICKER}?symbol={sym.upper()}", timeout=10)
        if resp.status_code == 200:
            return float(resp.json()["price"])
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Price checker — called on timer
# ---------------------------------------------------------------------------
def check_positions(chat_id: str):
    """Check all active positions: send hourly P&L, alert on TP/SL hits."""
    positions = get_active_positions(chat_id)
    if not positions:
        return

    lines = ["<b>📊 POSITION UPDATE</b>\n"]
    alerts = []

    for p in positions:
        sym = p["symbol"].replace("USDT", "")
        price = get_price(sym)
        if price is None:
            continue

        entry = p["entry_price"]
        tid = p["id"]
        status = p["status"]
        tp1 = p["tp1"]
        tp2 = p["tp2"]
        stop = p["stop"]
        peak = p["trail_peak"] or entry
        tp1_filled = p.get("tp1_filled") or 0
        realized = p.get("realized_pnl") or 0

        # Blended P&L: realized from partial fills + remaining position
        remaining = 1.0 - tp1_filled
        unrealized = remaining * ((price - entry) / entry) * 100
        pnl = realized + unrealized

        # Update trail peak
        if price > peak:
            with db_session() as conn:
                conn.execute("UPDATE paper_trades SET trail_peak = ? WHERE id = ?", (price, tid))
            peak = price

        trail_stop = peak * (1 - TRAILING_STOP_PCT)

        lines.append(f"<b>{sym}</b>: ${price:.6f} | P&L: {pnl:+.1f}% | Entry: ${entry:.6f}")

        # Check TP1 — record partial fill
        if status == "active" and price >= tp1:
            with db_session() as conn:
                conn.execute("""
                    UPDATE paper_trades SET status = 'tp1_hit',
                    tp1_filled = 0.5, realized_pnl = 0.5 * ?
                    WHERE id = ?
                """, (TAKE_PROFIT_1_PCT * 100, tid))
            alerts.append(f"<b>🎯 TP1 HIT — {sym}</b>\n  50% taken at +15% (${tp1:.6f})\n  Trailing -3% on remaining 50%")

        # Check TP2 (only if tp1 was hit)
        if status == "tp1_hit" and price >= tp2:
            close_position(tid, tp2, "tp2")
            alerts.append(f"<b>🎯 TP2 HIT — {sym}</b>\n  Remaining 50% closed at +25% (${tp2:.6f})")

        # Check trailing stop
        if status == "tp1_hit" and price <= trail_stop:
            close_position(tid, trail_stop, "trailing")
            alerts.append(f"<b>📉 TRAILING STOP — {sym}</b>\n  Closed at ${trail_stop:.6f}")

        # Check stop-loss
        if status == "active" and price <= stop:
            close_position(tid, stop, "stop_loss")
            alerts.append(f"<b>🛑 STOP LOSS — {sym}</b>\n  Closed at ${stop:.6f}")

    # Send position summary
    send_message(chat_id, "\n".join(lines))

    # Send alerts
    for alert in alerts:
        send_message(chat_id, alert)


# ---------------------------------------------------------------------------
# Scan report formatting
# ---------------------------------------------------------------------------
def _format_scan_report(result: ScanResult, label: str) -> str:
    """Format a ScanResult into a Telegram HTML message."""
    if result.status == "alerts_found":
        return f"<b>✅ {label} — {len(result.alerts)} alert(s)</b>"
    if result.status == "no_setups":
        return f"<b>{label} — No Pump Alerts today</b>\n0 tokens scored ≥2/3."
    if result.status == "suppressed":
        syms = ", ".join(result.candidate_symbols) if result.candidate_symbols else "N/A"
        detail = result.detail or "regime filter"
        return (
            f"<b>⚠️ {label} — Suppressed</b>\n"
            f"{len(result.candidate_symbols)} token(s) scored ≥2/3 but alerts are suppressed: {detail}.\n"
            f"Symbols: {syms}"
        )
    if result.status == "api_failure":
        detail = result.detail or "data source unavailable"
        return f"<b>❌ {label} — API failure</b>\n{detail}"
    if result.status == "no_watchlist":
        return f"<b>{label} — No watchlist candidates</b>\nPhase 1 produced no candidates to confirm."
    if result.status == "no_confirmations":
        syms = ", ".join(result.candidate_symbols) if result.candidate_symbols else "N/A"
        return (
            f"<b>{label} — No confirmations</b>\n"
            f"Watchlist: {syms}.\n"
            f"Phase 2 confirmation: none passed the 4h candle check."
        )
    if result.status == "error":
        detail = result.detail or "unexpected error"
        return f"<b>❌ {label} — Error</b>\n{detail}"
    return f"<b>{label}</b> — {result.status}"


def _handle_poller_result(result: ScanResult, chat_id: str):
    """Persist scan status and send Telegram only on state change."""
    from src.db import write_scan_status, get_last_scan_status

    phase = result.phase or "phase2"

    # Read previous status BEFORE writing the new one
    prev = get_last_scan_status(phase)
    prev_status = prev["status"] if prev else None

    write_scan_status(
        phase=phase,
        status=result.status,
        detail=result.detail,
        candidate_symbols=result.candidate_symbols,
        alert_count=len(result.alerts),
    )

    state_changed = prev_status is not None and prev_status != result.status

    if result:
        # Always send when there are actionable alerts
        for alert in result.alerts:
            sym = alert.get("symbol", "?").replace("USDT", "")
            send_message(chat_id,
                f"<b>✅ Confirmed — {sym}</b>\n{alert.get('reason', '')}")
    elif state_changed:
        # Empty result but state changed — notify why
        send_message(chat_id, _format_scan_report(result, "Phase 2"))
    # If no alerts and no state change, stay silent (log only)


# ---------------------------------------------------------------------------
# Command handler
# ---------------------------------------------------------------------------
def handle_message(msg: dict):
    """Process a Telegram message as a command."""
    text = msg.get("text", "").strip()
    chat_id = str(msg.get("chat", {}).get("id", ""))
    if not text or not chat_id:
        return

    lower = text.lower()

    # Normalize menu button responses: "🔍 scan" -> "scan"
    if lower in (k.lower() for k in MENU_BUTTONS):
        for btn_text, cmd in MENU_BUTTONS.items():
            if lower == btn_text.lower():
                lower = cmd
                break

    # "close SYMBOL" / "sell SYMBOL"
    if lower.startswith("close ") or lower.startswith("sell "):
        parts = text.split()
        if len(parts) >= 2:
            sym = parts[1].upper().replace("$", "")
            # Find active position
            positions = get_active_positions(chat_id)
            match = [p for p in positions if p["symbol"].replace("USDT", "").upper() == sym]
            if match:
                price = get_price(sym)
                if price:
                    result = close_position(match[0]["id"], price, "manual")
                    if result:
                        send_message(chat_id,
                            f"<b>🔒 Closed {sym}</b>\n"
                            f"Entry: ${result['entry']:.6f}\n"
                            f"Exit: ${result['exit']:.6f}\n"
                            f"P&L: {result['pnl_pct']:+.1f}%")
                else:
                    send_message(chat_id, f"❌ Could not get price for {sym}")
            else:
                send_message(chat_id, f"❌ No active position for {sym}")
        return

    # "positions" / "status"
    if lower in ("positions", "status", "/status", "/positions"):
        positions = get_active_positions(chat_id)
        if not positions:
            send_message(chat_id, "No active positions.")
            return
        lines = ["<b>📊 ACTIVE POSITIONS</b>\n"]
        for p in positions:
            sym = p["symbol"].replace("USDT", "")
            price = get_price(sym)
            if price:
                tp1_filled = p.get("tp1_filled") or 0
                realized = p.get("realized_pnl") or 0
                remaining = 1.0 - tp1_filled
                pnl = realized + remaining * ((price - p["entry_price"]) / p["entry_price"]) * 100
                lines.append(
                    f"<b>{sym}</b>: ${price:.6f} | P&L: {pnl:+.1f}% | "
                    f"Entry: ${p['entry_price']:.6f} | Size: ${p['position_size_usd']:.0f}"
                )
                status_extra = " (50% filled)" if tp1_filled > 0 else ""
                lines.append(
                    f"  TP1: ${p['tp1']:.6f} | TP2: ${p['tp2']:.6f} | "
                    f"Stop: ${p['stop']:.6f} | Status: {p['status']}{status_extra}"
                )
            else:
                lines.append(f"<b>{sym}</b>: price unavailable")
        send_message(chat_id, "\n".join(lines))
        return

    # "scan" / "/scan" — on-demand pipeline run
    if lower in ("scan", "/scan"):
        send_message(chat_id, "<b>🔍 Running scan...</b>")
        try:
            if LEGACY_IMMEDIATE_ALERTS:
                from src.pipeline import run_daily
                alerts = run_daily()
                if alerts:
                    send_message(chat_id, f"<b>✅ Scan complete</b> — {len(alerts)} alert(s) sent above")
                else:
                    send_message(chat_id, "<b>✅ Scan complete</b> — no alerts today")
            else:
                from src.pipeline import run_phase1_watchlist, run_phase2_confirmation
                p1 = run_phase1_watchlist()
                send_message(chat_id, _format_scan_report(p1, "Phase 1"))

                if p1:
                    p2 = run_phase2_confirmation()
                    send_message(chat_id, _format_scan_report(p2, "Phase 2"))
                else:
                    p2 = ScanResult(status="no_watchlist", phase="phase2")
                    send_message(chat_id, _format_scan_report(p2, "Phase 2"))
        except Exception as e:
            send_message(chat_id, f"<b>❌ Scan failed:</b> {str(e)[:200]}")
        return

    # "watchlist" / "/watchlist" — show current watchlist candidates
    if lower in ("watchlist", "/watchlist"):
        try:
            from src.stages import StageManager
            mgr = StageManager()
            candidates = mgr.get_watchlist_candidates()
            if not candidates:
                send_message(chat_id, "<b>Watchlist</b> — No active candidates.")
                return
            lines = [f"<b>Watchlist ({len(candidates)} candidates)</b>\n"]
            for c in candidates:
                sym = c["symbol"].replace("USDT", "")
                lines.append(f"• {sym} — score: {c['score']}")
            send_message(chat_id, "\n".join(lines[:20]))
        except Exception as e:
            send_message(chat_id, f"<b>❌ Watchlist error:</b> {str(e)[:200]}")
        return

    # "menu" / "/menu" — re-show the keyboard
    if lower in ("menu", "/menu"):
        send_message(chat_id,
            "<b>Menu shown</b> — tap a button below or type a command.",
            reply_markup=build_main_menu())
        return

    # "help" / "/help" / "start"
    if lower in ("help", "/help", "start", "/start", "hi", "hello"):
        send_message(chat_id,
            "<b>🤖 Alpha Bot — Paper Trading</b>\n\n"
            "<b>Commands:</b>\n"
            "• <code>buy SYMBOL at PRICE</code> — track a paper position\n"
            "• <code>close SYMBOL</code> — close a tracked position\n"
            "• <code>scan</code> — run on-demand pump scan\n"
            "• <code>watchlist</code> — show current watchlist candidates\n"
            "• <code>positions</code> — show all active positions\n"
            "• <code>menu</code> — show command buttons\n"
            "• <code>help</code> — this message\n\n"
            "<b>Auto-alerts:</b>\n"
            "• P&L update every 2 hours\n"
            "• TP/SL checked every 30 minutes\n"
            "• Instant alert when TP1 (+15%), TP2 (+25%), or stop-loss (-7%) hit\n"
            "• Trailing stop (-3%) after TP1 hit\n\n"
            "<i>Example: buy COS at 0.00123</i>",
            reply_markup=build_main_menu()
        )
        return

    # "buy SYMBOL at PRICE" or just "SYMBOL PRICE"
    parts = text.split()
    if len(parts) >= 2:
        # Try "buy COS at 0.00123" format
        if lower.startswith("buy "):
            sym = parts[1].upper().replace("$", "")
            # Find price: "at PRICE" or just the next number
            price = None
            for i, p in enumerate(parts):
                if p.lower() == "at" and i + 1 < len(parts):
                    try:
                        price = float(parts[i + 1].replace("$", "").replace(",", ""))
                    except ValueError:
                        pass
                    break
                try:
                    price = float(p.replace("$", "").replace(",", ""))
                    break
                except ValueError:
                    continue

            if price and price > 0:
                size = 100.0  # default $100 test position
                tid = open_position(f"{sym}USDT", price, chat_id, size)
                if tid:
                    tp1 = price * (1 + TAKE_PROFIT_1_PCT)
                    tp2 = price * (1 + TAKE_PROFIT_2_PCT)
                    stop = price * (1 + STOP_LOSS_PCT)
                    send_message(chat_id,
                        f"<b>✅ Tracking {sym}</b>\n"
                        f"Entry: ${price:.6f}\n"
                        f"Size: ${size:.0f}\n"
                        f"TP1: ${tp1:.6f} (+15%) | TP2: ${tp2:.6f} (+25%)\n"
                        f"Stop: ${stop:.6f} (-7%) | Trail: -3% after TP1\n"
                        f"<i>Updates every 2 hours. Reply 'close {sym}' to stop.</i>"
                    )
            else:
                send_message(chat_id, f"❌ Could not parse price. Use: <code>buy {sym} at 0.0012</code>")
            return

        # "COS 0.00123" shorthand
        sym = parts[0].upper().replace("$", "")
        try:
            price = float(parts[1].replace("$", "").replace(",", ""))
            if price > 0:
                size = 100.0
                tid = open_position(f"{sym}USDT", price, chat_id, size)
                if tid:
                    tp1 = price * (1 + TAKE_PROFIT_1_PCT)
                    stop = price * (1 + STOP_LOSS_PCT)
                    send_message(chat_id,
                        f"<b>✅ Tracking {sym}</b>\n"
                        f"Entry: ${price:.6f} | TP1: ${tp1:.6f} | Stop: ${stop:.6f}"
                    )
        except ValueError:
            pass


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------
def run_bot():
    init_db()
    print(f"Bot starting... Chat ID: {TELEGRAM_CHAT_ID}")
    send_message(TELEGRAM_CHAT_ID,
        "<b>🤖 Alpha Bot online</b>\nPaper trading ready.\nTap a button below or type 'help' for commands.",
        reply_markup=build_main_menu())

    offset = 0
    last_price_check = time.time()
    last_tp_check = time.time()
    last_confirmation_check = time.time()

    while True:
        try:
            # Poll Telegram
            updates = get_updates(offset)
            for u in updates:
                offset = u["update_id"] + 1
                msg = u.get("message", {})
                if msg:
                    handle_message(msg)

            # Hourly P&L update
            now = time.time()
            if now - last_price_check >= PRICE_INTERVAL:
                check_positions(TELEGRAM_CHAT_ID)
                last_price_check = now
                last_tp_check = now  # also checks TP on hourly update

            # TP/SL check every 5 min (only if there are active positions)
            if now - last_tp_check >= TP_CHECK_INTERVAL:
                positions = get_active_positions(TELEGRAM_CHAT_ID)
                if positions:
                    check_positions(TELEGRAM_CHAT_ID)
                last_tp_check = now

            # Confirmation polling (staged mode only)
            if not LEGACY_IMMEDIATE_ALERTS:
                CONFIRMATION_CHECK_INTERVAL = CONFIRMATION_POLL_MINUTES * 60
                if now - last_confirmation_check >= CONFIRMATION_CHECK_INTERVAL:
                    from src.pipeline import run_phase2_confirmation
                    result = run_phase2_confirmation()
                    # Persist status and notify only on state change
                    _handle_poller_result(result, TELEGRAM_CHAT_ID)
                    last_confirmation_check = now

        except KeyboardInterrupt:
            print("Shutting down...")
            send_message(TELEGRAM_CHAT_ID, "<b>⚠️ Bot shutting down.</b>")
            break
        except Exception as e:
            print(f"Error: {e}")
            time.sleep(5)

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run_bot()
