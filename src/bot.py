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
)
from src.db import db_session, init_db

API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/price"
POLL_INTERVAL = 2       # seconds between Telegram polls
PRICE_INTERVAL = 7200   # P&L update every 2 hours (seconds)
TP_CHECK_INTERVAL = 1800  # check TP/SL every 30 minutes (seconds)


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------
def send_message(chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
    try:
        resp = requests.post(f"{API}/sendMessage", json={
            "chat_id": chat_id, "text": text,
            "parse_mode": parse_mode, "disable_web_page_preview": True,
        }, timeout=15)
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
        conn.execute("INSERT OR IGNORE INTO tokens (symbol, exchange, market) VALUES (?, 'A', 'spot')", (symbol,))
        row = conn.execute("SELECT id FROM tokens WHERE symbol = ? AND exchange = 'A'", (symbol,)).fetchone()
        tid = row[0] if row else None

        cur = conn.execute("""
            INSERT INTO paper_trades (token_id, symbol, entry_price, entry_ts,
                position_size_usd, status, tp1, tp2, stop, trail_peak, chat_id)
            VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?)
        """, (tid, symbol, entry_price, now, size_usd, tp1, tp2, stop, entry_price, chat_id))
        return cur.lastrowid


def close_position(trade_id: int, exit_price: float, reason: str = "manual") -> dict | None:
    """Close a paper trade and return summary."""
    with db_session() as conn:
        row = conn.execute("""
            SELECT symbol, entry_price, position_size_usd, status FROM paper_trades WHERE id = ?
        """, (trade_id,)).fetchone()
        if not row:
            return None
        if row["status"] == "closed":
            return {"symbol": row["symbol"], "error": "already closed"}

        pnl = ((exit_price - row["entry_price"]) / row["entry_price"]) * 100
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
            SELECT * FROM paper_trades WHERE chat_id = ? AND status IN ('active', 'tp1_hit')
            ORDER BY entry_ts
        """, (chat_id,)).fetchall()
    return [dict(r) for r in rows]


def get_price(symbol: str) -> float | None:
    """Get current price from Binance."""
    try:
        resp = requests.get(f"{BINANCE_TICKER}?symbol={symbol}USDT", timeout=10)
        if resp.status_code == 200:
            return float(resp.json()["price"])
    except Exception:
        pass
    # Try CoinAnalyze fallback — just use Binance symbol
    try:
        resp = requests.get(f"{BINANCE_TICKER}?symbol={symbol}", timeout=10)
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
        sym = p["symbol"].replace("USD.A", "")
        price = get_price(sym)
        if price is None:
            continue

        entry = p["entry_price"]
        pnl = ((price - entry) / entry) * 100
        tid = p["id"]
        status = p["status"]
        tp1 = p["tp1"]
        tp2 = p["tp2"]
        stop = p["stop"]
        peak = p["trail_peak"] or entry

        # Update trail peak
        if price > peak:
            with db_session() as conn:
                conn.execute("UPDATE paper_trades SET trail_peak = ? WHERE id = ?", (price, tid))
            peak = price

        trail_stop = peak * (1 - TRAILING_STOP_PCT)

        lines.append(f"<b>{sym}</b>: ${price:.6f} | P&L: {pnl:+.1f}% | Entry: ${entry:.6f}")

        # Check TP1
        if status == "active" and price >= tp1:
            with db_session() as conn:
                conn.execute("UPDATE paper_trades SET status = 'tp1_hit' WHERE id = ?", (tid,))
            alerts.append(f"<b>🎯 TP1 HIT — {sym}</b>\n  50% taken at +15% (${tp1:.6f})\n  Trailing -3% on remaining 50%")

        # Check TP2 (only if tp1 was hit — uses tp1_hit status)
        if status == "tp1_hit" and price >= tp2:
            close_position(tid, tp2, "tp2")
            alerts.append(f"<b>🎯 TP2 HIT — {sym}</b>\n  30% taken at +25% (${tp2:.6f})\n  20% remains trailing -3%")

        # Check trailing stop
        if status == "tp1_hit" and price <= trail_stop:
            close_position(tid, trail_stop, "trailing")
            alerts.append(f"<b>📉 TRAILING STOP — {sym}</b>\n  Closed at ${trail_stop:.6f}\n  P&L: {((trail_stop - entry) / entry) * 100:+.1f}%")

        # Check stop-loss
        if status == "active" and price <= stop:
            close_position(tid, stop, "stop_loss")
            alerts.append(f"<b>🛑 STOP LOSS — {sym}</b>\n  Closed at ${stop:.6f}\n  P&L: {(STOP_LOSS_PCT * 100):.1f}%")

    # Send position summary
    send_message(chat_id, "\n".join(lines))

    # Send alerts
    for alert in alerts:
        send_message(chat_id, alert)


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

    # "close SYMBOL" / "sell SYMBOL"
    if lower.startswith("close ") or lower.startswith("sell "):
        parts = text.split()
        if len(parts) >= 2:
            sym = parts[1].upper().replace("$", "")
            # Find active position
            positions = get_active_positions(chat_id)
            match = [p for p in positions if p["symbol"].replace("USD.A", "").upper() == sym]
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
            sym = p["symbol"].replace("USD.A", "")
            price = get_price(sym)
            if price:
                pnl = ((price - p["entry_price"]) / p["entry_price"]) * 100
                lines.append(
                    f"<b>{sym}</b>: ${price:.6f} | P&L: {pnl:+.1f}% | "
                    f"Entry: ${p['entry_price']:.6f} | Size: ${p['position_size_usd']:.0f}"
                )
                lines.append(
                    f"  TP1: ${p['tp1']:.6f} | TP2: ${p['tp2']:.6f} | "
                    f"Stop: ${p['stop']:.6f} | Status: {p['status']}"
                )
            else:
                lines.append(f"<b>{sym}</b>: price unavailable")
        send_message(chat_id, "\n".join(lines))
        return

    # "help" / "/help" / "start"
    if lower in ("help", "/help", "start", "/start", "hi", "hello"):
        send_message(chat_id,
            "<b>🤖 Alpha Bot — Paper Trading</b>\n\n"
            "<b>Commands:</b>\n"
            "• <code>buy SYMBOL at PRICE</code> — track a paper position\n"
            "• <code>close SYMBOL</code> — close a tracked position\n"
            "• <code>positions</code> — show all active positions\n"
            "• <code>help</code> — this message\n\n"
            "<b>Auto-alerts:</b>\n"
            "• P&L update every 2 hours\n"
            "• TP/SL checked every 30 minutes\n"
            "• Instant alert when TP1 (+15%), TP2 (+25%), or stop-loss (-7%) hit\n"
            "• Trailing stop (-3%) after TP1 hit\n\n"
            "<i>Example: buy COS at 0.00123</i>"
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
                tid = open_position(f"{sym}USD.A", price, chat_id, size)
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
                tid = open_position(f"{sym}USD.A", price, chat_id, size)
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
    send_message(TELEGRAM_CHAT_ID, "<b>🤖 Alpha Bot online</b>\nPaper trading ready.\nReply 'help' for commands.")

    offset = 0
    last_price_check = time.time()
    last_tp_check = time.time()

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
