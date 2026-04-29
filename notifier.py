import requests


class Notifier:
    def __init__(
        self,
        telegram_token: str,
        chat_id: str,
        twilio_sid: str | None = None,
        twilio_token: str | None = None,
        whatsapp_from: str | None = None,
        whatsapp_to: str | None = None,
    ):
        self._telegram_token = telegram_token
        self._chat_id = chat_id
        self._twilio_sid = twilio_sid
        self._twilio_token = twilio_token
        self._whatsapp_from = whatsapp_from
        self._whatsapp_to = whatsapp_to

    def send(self, text: str):
        self._send_telegram(text)
        if self._twilio_sid and self._twilio_token and self._whatsapp_from:
            self._send_whatsapp(text)

    def _send_telegram(self, text: str):
        try:
            url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
            requests.post(url, json={"chat_id": self._chat_id, "text": text}, timeout=10)
        except Exception:
            pass

    def _send_whatsapp(self, text: str):
        try:
            from twilio.rest import Client
            client = Client(self._twilio_sid, self._twilio_token)
            client.messages.create(
                body=text,
                from_=f"whatsapp:{self._whatsapp_from}",
                to=f"whatsapp:{self._whatsapp_to}",
            )
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
