"""
Signal Notifier — wysyła powiadomienia o sygnałach przez Telegram Bot API.

Używa httpx (bez dodatkowych bibliotek) — spójna architektura z parserem Gemini.

Użycie:
    from src.notifier import send_signal_notification
    await send_signal_notification(msg_id=123, ai_result={...})
"""

import httpx
from loguru import logger

from src.config import settings, PROJECT_ROOT


ADMIN_FILE = PROJECT_ROOT / ".admin_chat_id"

# Mapowanie akcji → emoji + polska nazwa
ACTION_LABELS: dict[str, tuple[str, str]] = {
    "BUY":    ("🟢", "KUPNO"),
    "ADD":    ("🟢", "DOKUPNO"),
    "SELL":   ("🔴", "SPRZEDAŻ"),
    "CLOSE":  ("🔴", "ZAMKNIĘCIE"),
    "REDUCE": ("🟡", "REDUKCJA"),
}

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ============================================================
# Helpers
# ============================================================

def _get_target_chat() -> int | None:
    """Zwraca chat_id admina (z .env lub .admin_chat_id)."""
    if settings.decision_chat_id:
        return settings.decision_chat_id
    if ADMIN_FILE.exists():
        try:
            return int(ADMIN_FILE.read_text().strip())
        except ValueError:
            pass
    return None


def _build_message(ai_result: dict) -> str:
    """Buduje sformatowany tekst wiadomości Telegram (Markdown)."""
    ts         = ai_result.get("trade_signal") or {}
    action     = ts.get("action", "UNKNOWN")
    ticker     = ts.get("ticker")
    qty        = ts.get("quantity")
    price      = ts.get("price")
    reason     = ts.get("reason", "")
    confidence = ai_result.get("confidence", 0.0)
    summary    = ai_result.get("summary", "brak opisu")

    emoji, action_pl = ACTION_LABELS.get(action, ("⚪", action))
    ticker_disp = f"`{ticker}`" if ticker else "nieznany"

    lines = [
        f"{emoji} *SYGNAŁ TRADERA: {action_pl} {ticker or ''}*",
        "",
    ]

    if ticker:
        lines.append(f"🏷  Ticker:  {ticker_disp}")
    if qty is not None:
        lines.append(f"📦 Ilość:   *{qty} szt.*")
    if price is not None:
        lines.append(f"💰 Cena:    *{price:.2f} PLN*")

    # Kalkulator pozycji na podstawie portfela
    if price and qty:
        total_val = price * qty
        portfolio = settings.my_portfolio_size
        pct       = (total_val / portfolio * 100) if portfolio else 0
        lines.append(
            f"💼 Wartość: *{total_val:,.0f} PLN*  ({pct:.1f}% portfela {portfolio:,.0f} PLN)"
        )

    lines += [
        "",
        f"🎯 Pewność AI: *{confidence * 100:.0f}%*",
        f"📝 {summary}",
    ]

    if reason:
        # Skróć uzasadnienie do 200 znaków
        short_reason = reason[:200] + ("…" if len(reason) > 200 else "")
        lines += ["", f"_Uzasadnienie: {short_reason}_"]

    return "\n".join(lines)


# ============================================================
# Główna funkcja
# ============================================================

async def send_signal_notification(msg_id: int, ai_result: dict) -> bool:
    """
    Wysyła powiadomienie o sygnale tradingowym z przyciskami ✅/❌.

    Args:
        msg_id:     ID wiadomości z Telegramu (klucz w SQLite).
        ai_result:  Wynik z analyze_message() — słownik z polami message_type, trade_signal, itp.

    Returns:
        True jeśli wiadomość wysłana pomyślnie, False w przeciwnym razie.
    """
    if not settings.bot_token:
        logger.warning("BOT_TOKEN nie ustawiony — pomijam powiadomienie decyzyjne")
        return False

    chat_id = _get_target_chat()
    if not chat_id:
        logger.warning("Brak DECISION_CHAT_ID i pliku .admin_chat_id — pomijam powiadomienie")
        return False

    ts     = ai_result.get("trade_signal") or {}
    ticker = ts.get("ticker", "?")
    action = ts.get("action", "?")

    text     = _build_message(ai_result)
    keyboard = {
        "inline_keyboard": [[
            {
                "text":          "✅ AKCEPTUJ",
                "callback_data": f"accept:{msg_id}:{ticker}:{action}",
            },
            {
                "text":          "❌ ODRZUĆ",
                "callback_data": f"reject:{msg_id}",
            },
        ]]
    }

    url = TELEGRAM_API.format(token=settings.bot_token, method="sendMessage")

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id":      chat_id,
                    "text":         text,
                    "parse_mode":   "Markdown",
                    "reply_markup": keyboard,
                },
            )

        if resp.status_code == 200:
            logger.info(
                f"📨 Powiadomienie wysłane → chat={chat_id} "
                f"msg_id={msg_id} ({action} {ticker})"
            )
            return True
        else:
            logger.error(
                f"❌ Bot API {resp.status_code}: {resp.text[:200]}"
            )
            return False

    except Exception as exc:
        logger.error(f"❌ Wyjątek w notifier: {exc}")
        return False
