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

    def format_alerts(self, alerts: list[dict], portfolio_usd: float) -> str:
        """Format alerts as a Telegram message."""
        pos_size = portfolio_usd * POSITION_SIZE_PCT

        if not alerts:
            return f"<b>🔍 Pump Scan</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}\n\nNo alerts today. All quiet."

        lines = [
            f"<b>🚀 PUMP ALERTS</b> — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}",
            f"Portfolio: ${portfolio_usd:.0f} | Size: ${pos_size:.0f} | ≥{ALERT_THRESHOLD}/5\n",
        ]

        for a in alerts:
            sym = a['symbol'].replace('USD.A', '')
            score = a['quant_score']
            boost = a['qual_boost']
            final = a['final_score']
            signals = a.get('signals_fired', '')
            qual_tags = a.get('qual_tags', '')

            lines.append(f"<b>━━ BUY ${sym} ━━</b>")
            lines.append(f"Score: {score} quant + {boost} qual = <b>{final}</b>")

            # Quantitative reasoning
            if signals:
                lines.append(f"\n<i>📊 Quantitative:</i>")
                for sig in signals.split('|'):
                    reason = _telegram_quant_reason(sig, a)
                    lines.append(f"  ✓ {reason}")

            # Qualitative reasoning
            if qual_tags:
                lines.append(f"\n<i>📰 Qualitative:</i>")
                for tag in qual_tags.split(' | '):
                    tag = tag.strip()
                    if ':' in tag:
                        _, desc = tag.split(':', 1)
                        lines.append(f"  • {desc.strip()}")

            # Verdict
            lines.append(f"\n<i>⚡ Verdict:</i> {_telegram_verdict(signals, qual_tags)}")

            # Action block
            lines.append(f"\n<code>🎯 ACTION:</code>")
            lines.append(f"  <b>{sym}USDT</b> | Market | ${pos_size:.0f}")
            lines.append(f"  Stop: -7% | TP: +15%/+25% | Trail: -3%")
            lines.append("")

        lines.append("<i>⚠️ Always 5-min sanity check before placing orders.</i>")
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
