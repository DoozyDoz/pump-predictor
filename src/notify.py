"""Telegram alert notifications for pump signals."""

import requests
from datetime import datetime
from src.config import POSITION_SIZE_PCT, ALERT_THRESHOLD

TELEGRAM_BASE = "https://api.telegram.org/bot"


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str):
        self.token = token
        self.chat_id = chat_id

    def send(self, text: str, parse_mode: str = "HTML") -> bool:
        """Send a message to the configured chat."""
        try:
            resp = requests.post(
                f"{TELEGRAM_BASE}{self.token}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": text,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True,
                },
                timeout=15,
            )
            return resp.status_code == 200
        except Exception:
            return False

    def format_alerts(self, alerts: list[dict], portfolio_usd: float,
                      stage: str = "") -> str:
        """Format alerts as a Telegram message.
        Dispatches to stage-specific formatter based on stage parameter.
        Default (empty stage) uses legacy entry format for backward compatibility.
        """
        if stage == "watchlist":
            return self.format_watchlist(alerts)
        elif stage == "confirmation":
            return self.format_confirmation(alerts)
        elif stage == "entry":
            return self.format_entry(alerts, portfolio_usd)
        else:
            return self.format_entry_legacy(alerts, portfolio_usd)

    def format_watchlist(self, candidates: list[dict]) -> str:
        """Format watchlist candidates."""
        if not candidates:
            return f"<b>Watchlist</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\nNo candidates today."

        lines = [
            f"<b>Watchlist</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            f"{len(candidates)} potential setup(s) — monitoring for confirmation.\n",
        ]
        for c in candidates:
            sym = c.get("symbol", "?").replace("USDT", "")
            score = c.get("score", 0)
            signals = c.get("fired_signals", "")
            lines.append(f"<b>{sym}</b> — score {score} | {signals}")
        lines.append("\n<i>No action needed yet. Waiting for confirmation.</i>")
        return "\n".join(lines)

    def format_confirmation(self, results: list[dict]) -> str:
        """Format confirmation results."""
        if not results:
            return f"<b>Confirmation Check</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\nNo active watchlist items."

        confirmed = [r for r in results if r.get("confirmed")]
        denied = [r for r in results if r.get("denied")]
        pending = [r for r in results if not r.get("confirmed") and not r.get("denied")]

        lines = [
            f"<b>Confirmation Check</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            f"Confirmed: {len(confirmed)} | Denied: {len(denied)} | Pending: {len(pending)}\n",
        ]
        for c in confirmed:
            sym = c.get("symbol", "?").replace("USDT", "")
            lines.append(f"<b>✅ {sym}</b> — {c.get('reason', '')}")
            if c.get("promoted_to_entry"):
                lines.append(f"  ➡ Promoted to entry!")
        lines.append("\n<i>Confirmed items promoted for entry sizing.</i>")
        return "\n".join(lines)

    def format_entry(self, entries: list[dict], portfolio_usd: float) -> str:
        """Format final entry signals with ATR sizing."""
        pos_size = portfolio_usd * POSITION_SIZE_PCT

        if not entries:
            return (f"<b>Entry Check</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
                    f"\n\nNo entry signals today.")

        lines = [
            f"<b>ENTRY SIGNALS</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            f"Portfolio: ${portfolio_usd:.0f}\n",
        ]
        for e in entries:
            sym = e.get("symbol", "?").replace("USDT", "")
            atr = e.get("atr_pct", "?")
            size = e.get("position_size_usd", f"{pos_size:.2f}")
            lines.append(f"<b>BUY ${sym}</b>")
            lines.append(f"  Size: ${size} | ATR: {atr}")
            if "fired_signals" in e:
                lines.append(f"  Signals: {e['fired_signals']}")
            lines.append("")

        lines.append("<i>⚠️ Always 5-min sanity check before placing orders.</i>")
        return "\n".join(lines)

    def format_entry_legacy(self, alerts: list[dict], portfolio_usd: float) -> str:
        """Original entry alert formatting (preserved for legacy mode)."""
        pos_size = portfolio_usd * POSITION_SIZE_PCT

        if not alerts:
            return f"<b>Pump Scan</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\nNo alerts today. All quiet."

        lines = [
            f"<b>PUMP ALERTS</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            f"Portfolio: ${portfolio_usd:.0f} | Size: ${pos_size:.0f} | >= {ALERT_THRESHOLD}/5\n",
        ]
        for a in alerts:
            sym = a['symbol'].replace('USDT', '')
            score = a['quant_score']
            cat = a.get('catalyst_boost', '+0.00')
            final = a['final_score']
            signals = a.get('signals_fired', '')
            qual_tags = a.get('qual_tags', '')
            override = a.get('override', '')
            scan_status = a.get('scan_status', 'FULL')

            paper_tag = "<b>PAPER ONLY</b> — " if scan_status == "PARTIAL" else ""
            lines.append(f"<b>━━ {paper_tag}BUY ${sym} ━━</b>")
            lines.append(f"Score: {score} quant + {cat} catalyst = <b>{final}</b>")
            if override:
                lines.append(f"Override: <i>{override}</i>")
            if scan_status == "PARTIAL":
                lines.append("⚠️ <b>SCAN PARTIAL — do not execute live</b>")

            if signals:
                lines.append(f"\n<i>Quantitative:</i>")
                for sig in signals.split('|'):
                    reason = _telegram_quant_reason(sig, a)
                    lines.append(f"  ✓ {reason}")

            if qual_tags:
                lines.append(f"\n<i>Qualitative:</i>")
                for tag in qual_tags.split(' | '):
                    tag = tag.strip()
                    if ':' in tag:
                        _, desc = tag.split(':', 1)
                        lines.append(f"  • {desc.strip()}")

            lines.append(f"\n<i>Verdict:</i> {_telegram_verdict(signals, qual_tags)}")
            lines.append(f"\n<code>ACTION:</code>")
            lines.append(f"  <b>{sym}USDT</b> | Market | ${pos_size:.0f}")
            lines.append(f"  Stop: -7% | TP: +15%/+25% | Trail: -3%")
            lines.append("")

        lines.append("<i>Always 5-min sanity check before placing orders.</i>")
        return "\n".join(lines)


def _telegram_quant_reason(signal_name: str, alert: dict) -> str:
    if signal_name == 'funding_extreme':
        rate = alert.get('fund_rate', '?')
        pct = alert.get('fund_pct', '?')
        return f"Funding extreme: {rate} at {pct}th pct — shorts overcrowded"
    if signal_name == 'oi_divergence':
        div = alert.get('oi_div', '?')
        return f"OI/Price divergence: {div} — accumulation detected"
    if signal_name == 'ls_extreme':
        ratio = alert.get('ls_ratio', '?')
        return f"L/S ratio extreme: {ratio} — sentiment too bearish"
    if signal_name == 'taker_extreme':
        ratio = alert.get('taker_ratio', '?')
        return f"Taker ratio extreme: {ratio} — sellers exhausted"
    if signal_name == 'book_imbalance':
        dom = alert.get('bid_dom', '?')
        return f"Order book bid dominance: {dom} — buy wall support"
    return signal_name


def _telegram_verdict(signals: str, qual_tags: str) -> str:
    if 'funding_extreme' in signals and 'volume' in qual_tags.lower():
        return "Funding extreme + volume spike — short squeeze setup. <b>High conviction.</b>"
    if 'funding_extreme' in signals:
        return "Funding extreme — shorts overcrowded. Monitor for volume."
    if 'capitulation' in qual_tags.lower():
        return "Capitulation volume — potential bounce. <b>High risk.</b>"
    if 'momentum' in qual_tags.lower():
        return "Momentum breakout — trend active. Trail stops tightly."
    return "Multiple signals converging — edge confirmed."
