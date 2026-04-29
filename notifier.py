import requests


class Notifier:
    def __init__(self, telegram_token: str, chat_id: str):
        self._telegram_token = telegram_token
        self._chat_id = chat_id

    def send(self, text: str):
        try:
            url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
            requests.post(url, json={"chat_id": self._chat_id, "text": text}, timeout=10)
        except Exception:
            pass

    def format_trade(
        self,
        action: str,
        stock: str | None,
        qty: int | None = None,
        price: float | None = None,
        confidence: float | None = None,
        reasoning: str = "",
        pnl: float | None = None,
    ) -> str:
        lines = [f"*{action}* {stock or '—'}"]
        if price:
            lines.append(f"Price: ₹{price:.2f}" + (f"  Qty: {qty}" if qty else ""))
        if confidence is not None:
            lines.append(f"Confidence: {confidence:.2f}")
        if reasoning:
            lines.append(f"Reason: {reasoning}")
        if pnl is not None:
            lines.append(f"P&L: ₹{pnl:+.2f}")
        return "\n".join(lines)

    def send_eod_summary(self, pnl: float, trades: int, claude_spend: float):
        msg = (
            f"*EOD Summary*\n"
            f"Realised P&L: ₹{pnl:+.2f}\n"
            f"Trades today: {trades}\n"
            f"Claude API spend: ${claude_spend:.4f}"
        )
        self.send(msg)
