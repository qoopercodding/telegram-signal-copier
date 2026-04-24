"""
Signal Notifier — wysyła powiadomienia o sygnałach przez Telegram Bot API.
"""

import asyncio
import json

import httpx
from loguru import logger

from src.config import settings, PROJECT_ROOT
from src.prices import get_share_price

ACTION_LABELS: dict[str, tuple[str, str]] = {
    "BUY":    ("🟢", "KUPNO"),
    "ADD":    ("🟢", "DOKUPNO"),
    "SELL":   ("🔴", "SPRZEDAŻ"),
    "CLOSE":  ("🔴", "ZAMKNIĘCIE"),
    "REDUCE": ("🟡", "REDUKCJA"),
}

TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"


# ============================================================
# Helpers — routing
# ============================================================

def _get_target_chat() -> int | None:
    """Zwraca chat_id docelowy z .env (DECISION_CHAT_ID lub RAW_CHANNEL_ID)."""
    if settings.decision_chat_id:
        return settings.decision_chat_id
    if settings.raw_channel_id:
        return settings.raw_channel_id
    return None


# ============================================================
# Helpers — ceny akcji
# ============================================================

async def _build_buy_list(positions: list[dict], portfolio_pln: float) -> str:
    """
    Dla listy pozycji portfela tradera oblicza ile sztuk kupić za portfolio_pln PLN.
    Zwraca sformatowany tekst Markdown.
    """
    prices: list[tuple[float | None, str]] = await asyncio.gather(
        *[asyncio.to_thread(get_share_price, p["ticker"]) for p in positions],
        return_exceptions=False,
    )

    lines = [f"📈 *CO KUPIĆ za {portfolio_pln:,.0f} PLN:*"]
    total = 0.0
    any_shares = False

    for pos, (price, source) in zip(positions, prices):
        ticker = pos["ticker"]
        pct    = pos.get("percentage") or 0.0
        target = portfolio_pln * pct / 100

        if price and price > 0:
            shares = int(target / price)
            actual = shares * price
            total += actual
            if shares > 0:
                any_shares = True
                src_tag = f" _{source}_" if source != "yfinance" else ""
                lines.append(
                    f"• *{ticker}* {pct:.1f}% → *{shares} szt.* "
                    f"@ {price:.2f} PLN = *{actual:,.0f} PLN*{src_tag}"
                )
            else:
                lines.append(
                    f"• *{ticker}* {pct:.1f}% → za mało "
                    f"_(min. {price:.2f} PLN/szt., masz {target:.0f} PLN)_"
                )
        else:
            total += target
            lines.append(
                f"• *{ticker}* {pct:.1f}% → *{target:,.0f} PLN* _(kurs niedostępny)_"
            )

    if any_shares:
        reszta = portfolio_pln - total
        lines.append(f"\n💸 Zainwestowane: *{total:,.0f} PLN* / {portfolio_pln:,.0f} PLN")
        if reszta > 0.5:
            lines.append(f"💵 Reszta na koncie: *{reszta:,.0f} PLN*")

    return "\n".join(lines)


# ============================================================
# Budowanie treści wiadomości
# ============================================================

def _build_message(ai_result: dict) -> str:
    """Buduje sformatowany tekst wiadomości Telegram (Markdown)."""
    msg_type   = ai_result.get("message_type")
    confidence = ai_result.get("confidence", 0.0)
    summary    = ai_result.get("summary", "brak opisu")

    if msg_type == "PORTFOLIO_UPDATE":
        lines = [
            "📊 *AKTUALIZACJA PORTFELA*",
            "",
            f"📝 {summary}",
            "",
            f"🎯 Pewność AI: *{confidence * 100:.0f}%*",
        ]
        return "\n".join(lines)

    # --- TRADE_ACTION ---
    ts         = ai_result.get("trade_signal") or {}
    action     = ts.get("action", "UNKNOWN")
    ticker     = ts.get("ticker")
    qty        = ts.get("quantity")
    price      = ts.get("price")
    reason     = ts.get("reason", "")

    emoji, action_pl = ACTION_LABELS.get(action, ("⚪", action))
    ticker_disp = f"`{ticker}`" if ticker else "nieznany"

    lines = [f"{emoji} *SYGNAŁ TRADERA: {action_pl} {ticker or ''}*", ""]

    if ticker:
        lines.append(f"🏷  Ticker:  {ticker_disp}")
    if qty is not None:
        lines.append(f"📦 Ilość:   *{qty} szt.*")
    if price is not None:
        lines.append(f"💰 Cena:    *{price:.2f} PLN*")

    if price and qty:
        total_val = price * qty
        portfolio = settings.my_portfolio_size
        pct       = (total_val / portfolio * 100) if portfolio else 0
        lines.append(
            f"💼 Wartość: *{total_val:,.0f} PLN*  ({pct:.1f}% portfela {portfolio:,.0f} PLN)"
        )

    lines += ["", f"🎯 Pewność AI: *{confidence * 100:.0f}%*", f"📝 {summary}"]

    if reason:
        short_reason = reason[:200] + ("…" if len(reason) > 200 else "")
        lines += ["", f"_Uzasadnienie: {short_reason}_"]

    return "\n".join(lines)


# ============================================================
# Wysyłanie przez Bot API
# ============================================================

async def _send_text(client: httpx.AsyncClient, chat_id: int, text: str,
                     reply_to: int | None = None, keyboard: dict | None = None) -> int | None:
    """Wysyła wiadomość tekstową. Zwraca message_id lub None."""
    url = TELEGRAM_API.format(token=settings.bot_token, method="sendMessage")
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    if keyboard:
        payload["reply_markup"] = keyboard
    resp = await client.post(url, json=payload)
    if resp.status_code == 200:
        return resp.json().get("result", {}).get("message_id")
    logger.error(f"❌ sendMessage {resp.status_code}: {resp.text[:200]}")
    return None


async def _send_photo(client: httpx.AsyncClient, chat_id: int, photo_path: str,
                      caption: str, keyboard: dict | None = None) -> int | None:
    """Wysyła zdjęcie z podpisem. Zwraca message_id lub None."""
    url = TELEGRAM_API.format(token=settings.bot_token, method="sendPhoto")
    short_caption = caption if len(caption) <= 1024 else caption[:1020] + "..."
    data: dict = {"chat_id": chat_id, "caption": short_caption, "parse_mode": "Markdown"}
    if keyboard:
        data["reply_markup"] = json.dumps(keyboard)
    with open(photo_path, "rb") as f:
        resp = await client.post(url, data=data, files={"photo": f})
    if resp.status_code == 200:
        return resp.json().get("result", {}).get("message_id")
    logger.error(f"❌ sendPhoto {resp.status_code}: {resp.text[:200]}")
    return None


# ============================================================
# Główna funkcja
# ============================================================

async def send_signal_notification(msg_id: int, ai_result: dict, media_paths: list[str] = None) -> bool:
    """
    Wysyła powiadomienie o sygnale lub aktualizacji portfela.

    PORTFOLIO_UPDATE:
      1. zdjęcie (jeśli jest) z krótkim podpisem
      2. osobna wiadomość z tabelą CO KUPIĆ (ile sztuk @ aktualny kurs)

    TRADE_ACTION:
      zdjęcie lub tekst + przyciski AKCEPTUJ/ODRZUĆ
    """
    if not settings.bot_token:
        logger.warning("BOT_TOKEN nie ustawiony — pomijam powiadomienie")
        return False

    chat_id = _get_target_chat()
    if not chat_id:
        logger.warning("Brak DECISION_CHAT_ID i RAW_CHANNEL_ID w .env — pomijam powiadomienie")
        return False

    msg_type  = ai_result.get("message_type")
    base_text = _build_message(ai_result)

    has_photo = bool(
        media_paths and
        any(p.lower().endswith((".jpg", ".jpeg", ".png", ".webp")) for p in media_paths)
    )
    photo_path = next(
        (p for p in (media_paths or []) if p.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))),
        None,
    )

    # Klawiatura decyzyjna — tylko TRADE_ACTION
    keyboard: dict | None = None
    if msg_type == "TRADE_ACTION":
        ts     = ai_result.get("trade_signal") or {}
        ticker = ts.get("ticker", "?")
        action = ts.get("action", "?")
        keyboard = {
            "inline_keyboard": [[
                {"text": "✅ AKCEPTUJ", "callback_data": f"accept:{msg_id}:{ticker}:{action}"},
                {"text": "❌ ODRZUĆ",  "callback_data": f"reject:{msg_id}"},
            ]]
        }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:

            # ── Wyślij główną wiadomość ──────────────────────────────
            if has_photo:
                sent_id = await _send_photo(client, chat_id, photo_path, base_text, keyboard)
            else:
                sent_id = await _send_text(client, chat_id, base_text, keyboard=keyboard)

            if sent_id is None:
                return False

            logger.info(f"📨 Powiadomienie wysłane → chat={chat_id} msg_id={msg_id} sent={sent_id}")

            # ── Dla PORTFOLIO_UPDATE: osobna wiadomość z ilościami sztuk ──
            if msg_type == "PORTFOLIO_UPDATE":
                positions = ai_result.get("portfolio_positions") or []
                if positions:
                    buy_list = await _build_buy_list(positions, settings.my_portfolio_size)
                    await _send_text(client, chat_id, buy_list, reply_to=sent_id)
                    logger.info(f"📈 Buy list wysłany ({len(positions)} pozycji)")
                else:
                    logger.debug("Brak portfolio_positions w ai_result — pomijam buy list")

        return True

    except Exception as exc:
        logger.error(f"❌ Wyjątek w notifier: {exc}")
        return False
