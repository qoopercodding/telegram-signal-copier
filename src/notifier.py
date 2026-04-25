"""
Signal Notifier — wysyła powiadomienia przez Telethon (MTProto userbot).

Routing: zawsze RAW_CHANNEL_ID (recive-bot-investor).
Format: HTML, osobne wiadomości — najpierw oryginalne zdjęcie, potem analiza.
"""

import asyncio
from loguru import logger

from src.config import settings
from src.prices import get_share_price

ACTION_LABELS: dict[str, tuple[str, str]] = {
    "BUY":    ("🟢", "KUPNO"),
    "ADD":    ("🟢", "DOKUPNO"),
    "SELL":   ("🔴", "SPRZEDAŻ"),
    "CLOSE":  ("🔴", "ZAMKNIĘCIE"),
    "REDUCE": ("🟡", "REDUKCJA"),
}


# ============================================================
# Helpers — ceny i lista zakupów
# ============================================================

async def _build_buy_list(positions: list[dict], portfolio_pln: float) -> str:
    """Dla listy pozycji portfela tradera oblicza ile sztuk kupić za portfolio_pln PLN."""
    prices: list[tuple[float | None, str]] = await asyncio.gather(
        *[asyncio.to_thread(get_share_price, p["ticker"]) for p in positions],
        return_exceptions=False,
    )

    lines = [f"📈 <b>CO KUPIĆ za {portfolio_pln:,.0f} PLN:</b>"]
    total = 0.0
    any_shares = False

    for pos, (price, source) in zip(positions, prices):
        ticker = pos["ticker"]
        pct = pos.get("percentage") or 0.0
        target = portfolio_pln * pct / 100

        if price and price > 0:
            shares = int(target / price)
            actual = shares * price
            total += actual
            if shares > 0:
                any_shares = True
                src_tag = f" <i>{source}</i>" if source else ""
                lines.append(
                    f"• <b>{ticker}</b> {pct:.1f}% → <b>{shares} szt.</b>"
                    f" @ {price:.2f} PLN = <b>{actual:,.0f} PLN</b>{src_tag}"
                )
            else:
                lines.append(
                    f"• <b>{ticker}</b> {pct:.1f}% → za mało"
                    f" <i>(min. {price:.2f} PLN/szt., masz {target:.0f} PLN)</i>"
                )
        else:
            total += target
            lines.append(
                f"• <b>{ticker}</b> {pct:.1f}% → <b>{target:,.0f} PLN</b> <i>(kurs niedostępny)</i>"
            )

    if any_shares:
        reszta = portfolio_pln - total
        lines.append(f"\n💸 Zainwestowane: <b>{total:,.0f} PLN</b> / {portfolio_pln:,.0f} PLN")
        if reszta > 0.5:
            lines.append(f"💵 Reszta: <b>{reszta:,.0f} PLN</b>")

    return "\n".join(lines)


# ============================================================
# Budowanie treści wiadomości
# ============================================================

def _build_message(ai_result: dict) -> str:
    """Buduje sformatowany tekst wiadomości (HTML)."""
    msg_type = ai_result.get("message_type")
    confidence = ai_result.get("confidence", 0.0)
    summary = ai_result.get("summary", "brak opisu")
    source_topic = ai_result.get("source_topic")
    ai_model = ai_result.get("ai_model", "")

    topic_label = f" <code>[{source_topic}]</code>" if source_topic else ""
    model_line = f"\n🤖 Model: <i>{ai_model}</i>" if ai_model else ""

    if msg_type == "PORTFOLIO_UPDATE":
        positions = ai_result.get("portfolio_positions") or []
        pos_lines = ""
        if positions:
            pos_lines = "\n" + "\n".join(
                f"• <b>{p['ticker']}</b> {p.get('percentage', 0):.1f}%"
                + (f" — {p['value_pln']:,.0f} PLN" if p.get("value_pln") else "")
                for p in positions
            )
        return "\n".join(filter(None, [
            f"📊 <b>AKTUALIZACJA PORTFELA{topic_label}</b>",
            "",
            pos_lines,
            "",
            f"📝 {summary}",
            f"🎯 Pewność AI: <b>{confidence * 100:.0f}%</b>{model_line}",
        ]))

    if msg_type == "INFORMATIONAL":
        return "\n".join([
            f"ℹ️ <b>NOTATKA{topic_label}</b>",
            "",
            f"📝 {summary}",
            f"🎯 Pewność AI: <b>{confidence * 100:.0f}%</b>{model_line}",
        ])

    # TRADE_ACTION
    ts = ai_result.get("trade_signal") or {}
    action = ts.get("action", "UNKNOWN")
    ticker = ts.get("ticker")
    qty = ts.get("quantity")
    price = ts.get("price")
    reason = ts.get("reason", "")

    emoji, action_pl = ACTION_LABELS.get(action, ("⚪", action))
    ticker_disp = f"<code>{ticker}</code>" if ticker else "nieznany"

    lines = [f"{emoji} <b>SYGNAŁ: {action_pl} {ticker or ''}{topic_label}</b>", ""]

    if ticker:
        lines.append(f"🏷  Ticker:  {ticker_disp}")
    if qty is not None:
        lines.append(f"📦 Ilość:   <b>{qty} szt.</b>")
    if price is not None:
        lines.append(f"💰 Cena:    <b>{price:.2f} PLN</b>")
    if price and qty:
        total_val = price * qty
        portfolio = settings.my_portfolio_size
        pct = (total_val / portfolio * 100) if portfolio else 0
        lines.append(
            f"💼 Wartość: <b>{total_val:,.0f} PLN</b>  ({pct:.1f}% portfela {portfolio:,.0f} PLN)"
        )

    lines += [
        "",
        f"📝 {summary}",
        f"🎯 Pewność AI: <b>{confidence * 100:.0f}%</b>{model_line}",
    ]

    if reason:
        short_reason = reason[:200] + ("…" if len(reason) > 200 else "")
        lines += ["", f"<i>Uzasadnienie: {short_reason}</i>"]

    return "\n".join(lines)


# ============================================================
# Główna funkcja
# ============================================================

async def send_signal_notification(
    msg_id: int,
    ai_result: dict,
    media_paths: list[str] = None,
    client=None,
) -> bool:
    """
    Wysyła powiadomienie przez Telethon (MTProto) do RAW_CHANNEL_ID.

    Kolejność:
      1. Oryginalne zdjęcie (jeśli jest) z analizą jako podpis (caption ≤ 1024 znaków)
         lub pełna analiza jako osobna wiadomość tekstowa gdy caption za długi/brak zdjęcia
      2. Dla PORTFOLIO_UPDATE: osobna wiadomość z tabelą CO KUPIĆ (ilości sztuk)

    Args:
        client: Telethon TelegramClient (wymagany)
    """
    if client is None:
        logger.warning("Brak klienta Telethon — pomijam powiadomienie")
        return False

    chat_id = settings.raw_channel_id
    if not chat_id:
        logger.warning("Brak RAW_CHANNEL_ID w .env — pomijam powiadomienie")
        return False

    if not settings.my_portfolio_size:
        try:
            await client.send_message(
                chat_id,
                "⚠️ <b>Ustaw MY_PORTFOLIO_SIZE w .env</b> — ile PLN chcesz zainwestować?\n"
                "<i>(Bez tego nie mogę wyliczyć ile sztuk kupić)</i>",
                parse_mode="html",
            )
        except Exception as e:
            logger.error(f"send portfolio prompt error: {e}")
        return False

    msg_type = ai_result.get("message_type")
    text = _build_message(ai_result)

    photo_path = next(
        (p for p in (media_paths or []) if p.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))),
        None,
    )

    try:
        sent_id = None

        if photo_path:
            # Caption limit w Telegramie: 1024 znaki
            if len(text) <= 1024:
                sent_msg = await client.send_file(
                    chat_id,
                    photo_path,
                    caption=text,
                    parse_mode="html",
                )
                sent_id = sent_msg.id
            else:
                # Wyślij zdjęcie bez podpisu, potem tekst osobno
                photo_msg = await client.send_file(chat_id, photo_path)
                sent_msg = await client.send_message(
                    chat_id, text, parse_mode="html", reply_to=photo_msg.id
                )
                sent_id = sent_msg.id
        else:
            sent_msg = await client.send_message(chat_id, text, parse_mode="html")
            sent_id = sent_msg.id

        logger.info(f"📨 Powiadomienie wysłane → chat={chat_id} msg_id={msg_id} sent={sent_id}")

        # Dla PORTFOLIO_UPDATE: osobna wiadomość z ilościami sztuk
        if msg_type == "PORTFOLIO_UPDATE":
            positions = ai_result.get("portfolio_positions") or []
            if positions:
                buy_list = await _build_buy_list(positions, settings.my_portfolio_size)
                await client.send_message(chat_id, buy_list, parse_mode="html", reply_to=sent_id)
                logger.info(f"📈 Buy list wysłany ({len(positions)} pozycji)")
            else:
                logger.debug("Brak portfolio_positions — pomijam buy list")

        return True

    except Exception as exc:
        logger.error(f"❌ Wyjątek w notifier: {exc}")
        return False
