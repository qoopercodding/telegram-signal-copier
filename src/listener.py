"""
signal-copier userbot — wszystkie kanały w jednym procesie Telethon.

Flow:
  DamianInwestorx (IKE/IKZE topics) → forward → test-bot-inwestor
  test-bot-inwestor           → AI Gemini → recive-bot-investor (wyniki)
  recive-bot-investor (Marcin) → AI Q&A, /fetch, zdjęcia → odpowiedź na kanale

Uruchomienie:
    python -m src.listener
"""

import asyncio
import re
import time as _time_mod
from datetime import datetime
from pathlib import Path

from loguru import logger
from telethon import TelegramClient, events
from telethon.tl.types import Message, MessageMediaPhoto, MessageMediaDocument

from src.config import settings, ensure_directories, LOGS_DIR, MEDIA_DIR
from src.storage import (
    init_db, save_raw_message, update_media_paths,
    save_ai_analysis, count_messages, save_trader_positions,
    get_latest_trader_positions,
)
from src.parser import analyze_message
from src.notifier import send_signal_notification
from src.prices import get_share_price


# ============================================================
# Logging + klient
# ============================================================

def setup_logging() -> None:
    log_file = LOGS_DIR / "listener_{time:YYYY-MM-DD}.log"
    logger.add(str(log_file), rotation="1 day", retention="7 days", level="DEBUG", encoding="utf-8")


def build_client() -> TelegramClient:
    session_path = str(LOGS_DIR.parent / settings.session_name)
    return TelegramClient(session_path, settings.telegram_api_id, settings.telegram_api_hash)


# ============================================================
# Media download
# ============================================================

async def download_media(msg: Message, client: TelegramClient) -> list[str]:
    if not msg.media:
        return []
    media_paths: list[str] = []
    ts = msg.date.strftime("%Y%m%d_%H%M%S") if msg.date else "unknown"
    base_name = f"{ts}_{msg.id}"
    try:
        if isinstance(msg.media, MessageMediaPhoto):
            file_path = MEDIA_DIR / f"{base_name}.jpg"
            await client.download_media(msg, file=str(file_path))
            media_paths.append(str(file_path))
            logger.info(f"📷 Pobrano zdjęcie → {file_path.name}")
        elif isinstance(msg.media, MessageMediaDocument):
            doc = msg.media.document
            ext = ".bin"
            if doc and doc.mime_type:
                ext = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp",
                       "application/pdf": ".pdf", "video/mp4": ".mp4"}.get(doc.mime_type, ".bin")
            file_path = MEDIA_DIR / f"{base_name}{ext}"
            await client.download_media(msg, file=str(file_path))
            media_paths.append(str(file_path))
            logger.info(f"📎 Pobrano dokument → {file_path.name}")
    except Exception as e:
        logger.error(f"❌ Błąd pobierania mediów z msg {msg.id}: {e}")
    return media_paths


# ============================================================
# Heartbeat
# ============================================================

HEARTBEAT_FILE = LOGS_DIR.parent / ".heartbeat"
FETCH_REQUEST_FILE = Path("/tmp/.fetch_request.json")

_last_message_at: str = "brak"
_start_time: float | None = None
_damian_topic_map: dict[int, str] = {}   # forwarded_msg_id → "IKE" / "IKZE"
_bot_sent_ids: set[int] = set()          # IDs wiadomości wysłanych przez bota → pomijaj w handlerach


def write_heartbeat() -> None:
    import json
    from datetime import timedelta
    global _start_time
    if _start_time is None:
        _start_time = _time_mod.time()
    uptime_delta = timedelta(seconds=int(_time_mod.time() - _start_time))
    d = uptime_delta.days
    h, rem = divmod(uptime_delta.seconds, 3600)
    m, _ = divmod(rem, 60)
    HEARTBEAT_FILE.write_text(json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "uptime": f"{d}d {h}h {m}m",
        "last_message_at": _last_message_at,
        "messages_total": count_messages(),
    }))


async def heartbeat_loop() -> None:
    while True:
        try:
            write_heartbeat()
        except Exception as e:
            logger.error(f"Heartbeat error: {e}")
        await asyncio.sleep(300)


# ============================================================
# Pipeline AI: test-bot-inwestor → Gemini → recive-bot-investor
# ============================================================

async def _process_message(msg: Message, chat_id: int, client: TelegramClient) -> None:
    """Zapis do DB + media + AI + notyfikacja. Rdzeń całego pipeline."""
    logger.info(
        f"📨 Nowa wiadomość | id={msg.id} | chat={chat_id} | "
        f"media={'📷' if msg.media else '❌'} | "
        f"tekst={repr((msg.text or '')[:60])}"
    )
    saved = save_raw_message(
        message_id=msg.id,
        chat_id=chat_id,
        timestamp=msg.date if isinstance(msg.date, datetime) else datetime.utcnow(),
        raw_text=msg.text or None,
        has_media=bool(msg.media),
        media_paths=[],
        grouped_id=msg.grouped_id,
    )
    if not saved:
        logger.warning(f"Duplikat msg {msg.id} — pomijam")
        return

    media_paths = await download_media(msg, client)
    if media_paths:
        update_media_paths(msg.id, chat_id, media_paths)

    if not settings.gemini_api_key:
        return
    try:
        ai_result = await analyze_message(text=msg.text or None, media_paths=media_paths or None)

        source_topic = _damian_topic_map.pop(msg.id, None)
        if source_topic:
            ai_result["source_topic"] = source_topic
            logger.info(f"🏷  Źródło: {source_topic}")

        save_ai_analysis(msg.id, chat_id, ai_result)

        if ai_result.get("message_type") == "PORTFOLIO_UPDATE":
            positions = ai_result.get("portfolio_positions")
            if positions and isinstance(positions, list):
                save_trader_positions(msg.id, positions)

        msg_type   = ai_result.get("message_type")
        confidence = ai_result.get("confidence", 0.0)
        logger.info(f"🤖 AI: {msg_type} | confidence={confidence:.2f} | {ai_result.get('summary','?')[:80]}")

        if msg_type in ("TRADE_ACTION", "PORTFOLIO_UPDATE", "INFORMATIONAL") and confidence >= 0.6:
            await send_signal_notification(msg.id, ai_result, media_paths, client, _track_ids=_bot_sent_ids)
        else:
            logger.debug(f"ℹ️ Bez powiadomienia: type={msg_type}, confidence={confidence:.2f}")

    except Exception as e:
        logger.error(f"❌ Błąd AI analizy msg {msg.id}: {e}")


async def handle_new_message(event: events.NewMessage.Event, client: TelegramClient) -> None:
    """Handler dla SOURCE_GROUP_ID (test-bot-inwestor)."""
    msg: Message = event.message
    source_id = settings.source_group_id
    if source_id != 0 and event.chat_id != source_id:
        return
    await _process_message(msg, event.chat_id, client)


# ============================================================
# Advisor — kalkulator pozycji (odpowiedź na recive-bot-investor)
# ============================================================

_CASH_RE = re.compile(r'(\d[\d\s]*(?:[.,]\d+)?)\s*(k|tys\.?)?\s*pln', re.IGNORECASE)


def parse_cash_amount(text: str) -> float | None:
    m = _CASH_RE.search(text)
    if not m:
        return None
    amount_str = m.group(1).replace(" ", "").replace(",", ".")
    suffix = (m.group(2) or "").lower()
    try:
        amount = float(amount_str)
        if suffix.startswith("k") or suffix.startswith("tys"):
            amount *= 1000
        return amount
    except ValueError:
        return None


async def build_advisor_message(cash_pln: float) -> str:
    positions = get_latest_trader_positions()
    if not positions:
        return (
            "⚠️ *Brak danych o portfelu tradera w bazie.*\n\n"
            "Poczekaj aż Damian wyśle screenshot portfela — bot go przetworzy.\n"
            "Potem napisz ponownie ile masz PLN."
        )

    source_date = (positions[0].get("created_at") or "")[:10]
    price_results: list[tuple[float | None, str]] = await asyncio.gather(
        *[asyncio.to_thread(get_share_price, p["ticker"]) for p in positions],
        return_exceptions=False,
    )

    has_pct = any((pos.get("percentage") or 0) > 0 for pos in positions)
    n = len(positions)
    equal_pct = 100.0 / n if n > 0 else 0.0

    lines = [
        f"📊 *Propozycja alokacji {cash_pln:,.0f} PLN*",
        f"_Na podstawie portfela tradera z {source_date}_",
    ]
    if not has_pct:
        lines.append(f"_⚠️ Brak % w DB — równy podział na {n} spółek ({equal_pct:.1f}% każda)_")
    lines.append("")

    total = 0.0
    any_shares = False
    for pos, (price, _src) in zip(positions, price_results):
        ticker = pos["ticker"]
        pct    = (pos.get("percentage") or 0.0) if has_pct else equal_pct
        target = cash_pln * pct / 100
        if price and price > 0:
            shares = int(target / price)
            actual = shares * price
            total += actual
            if shares > 0:
                any_shares = True
                lines.append(f"• *{ticker}* {pct:.1f}% → *{shares} szt.* @ {price:.2f} PLN = *{actual:,.0f} PLN*")
            else:
                lines.append(f"• *{ticker}* {pct:.1f}% → za mało _(masz {target:.0f} PLN, min. {price:.2f} PLN/szt.)_")
        else:
            total += target
            lines.append(f"• *{ticker}* {pct:.1f}% → *{target:,.0f} PLN* _(kurs niedostępny)_")

    if not any_shares:
        lines += ["", "⚠️ *Za mało PLN na jakikolwiek zakup przy tej alokacji.*"]
        return "\n".join(lines)

    reszta = cash_pln - total
    lines += ["", f"💸 Zainwestowane: *{total:,.0f} PLN*"]
    if reszta > 0.5:
        lines.append(f"💵 Zostaje: *{reszta:,.0f} PLN*")
    return "\n".join(lines)


async def _answer_question(text: str, client: TelegramClient) -> str:
    """Odpowiada na pytanie Marcina przez call_ai (Claude Haiku) z kontekstem portfela i logów."""
    from src.ai_providers import call_ai

    positions = get_latest_trader_positions()
    if positions:
        pos_lines = [
            f"  {p['ticker']}: {(p.get('percentage') or 0):.1f}%"
            + (f", {p['value_pln']:,.0f} PLN" if p.get("value_pln") else "")
            for p in positions
        ]
        portfolio_ctx = "Ostatnie pozycje portfela tradera w DB:\n" + "\n".join(pos_lines)
    else:
        portfolio_ctx = "Brak danych o portfelu tradera w bazie SQLite."

    log_ctx = ""
    log_files = sorted(LOGS_DIR.glob("listener_*.log"), reverse=True)
    if log_files:
        try:
            lines = log_files[0].read_text(encoding="utf-8").strip().split("\n")
            log_ctx = "Ostatnie logi signal-copier:\n" + "\n".join(lines[-15:])
        except Exception:
            pass

    prompt = (
        f"Jesteś asystentem Marcina — właściciela systemu do śledzenia sygnałów GPW.\n\n"
        f"NIE jesteś brokerem ani doradcą. "
        f"Masz dostęp do danych z bazy SQLite i logów. Odpowiadaj krótko po polsku (max 4 zdania).\n\n"
        f"{portfolio_ctx}\n\n{log_ctx}\n\n"
        f"PYTANIE: {text}"
    )

    answer, _model = await asyncio.wait_for(call_ai(prompt=prompt), timeout=60)
    return answer


async def _send_to_raw(client: TelegramClient, text: str, **kwargs) -> None:
    """Wysyła do raw_channel_id i rejestruje ID aby uniknąć pętli odpowiedzi."""
    sent = await client.send_message(settings.raw_channel_id, text, **kwargs)
    _bot_sent_ids.add(sent.id)
    if len(_bot_sent_ids) > 300:
        _bot_sent_ids.discard(min(_bot_sent_ids))


async def handle_channel_message(
    msg: Message,
    client: TelegramClient,
    forward_fn,
) -> None:
    """
    Handler dla wiadomości od Marcina na recive-bot-investor.
    Obsługuje: /fetch, kwoty PLN (advisor), pytania AI, zdjęcia.
    """
    if msg.id in _bot_sent_ids:
        return

    text = (msg.text or "").strip()

    # Zdjęcie → AI analiza
    if msg.media and isinstance(msg.media, (MessageMediaPhoto, MessageMediaDocument)):
        tmp_path = MEDIA_DIR / f"user_input_{msg.id}.jpg"
        try:
            await asyncio.wait_for(client.download_media(msg, file=str(tmp_path)), timeout=30)
        except Exception as e:
            logger.error(f"Błąd pobierania zdjęcia od Marcina: {e}")
            await _send_to_raw(client, "❌ Nie mogłem pobrać zdjęcia")
            return

        logger.info(f"📷 Zdjęcie od Marcina: {tmp_path.name}")
        await _send_to_raw(client, "🤔 Analizuję...")
        try:
            ai = await asyncio.wait_for(
                analyze_message(text=text or None, media_paths=[str(tmp_path)]),
                timeout=90,
            )
            msg_type   = ai.get("message_type")
            confidence = ai.get("confidence", 0.0)
            summary    = ai.get("summary", "")
            lines = [f"🔍 *Analiza zdjęcia* (pewność: {confidence*100:.0f}%)", "", f"_{summary}_", ""]

            if msg_type == "PORTFOLIO_UPDATE":
                positions = ai.get("portfolio_positions") or []
                if positions:
                    lines.append("📊 *Wykryte pozycje:*")
                    for p in positions:
                        lines.append(f"• {p['ticker']} — {(p.get('percentage') or 0):.1f}%")
                    lines += ["", "💡 _Napisz ile masz PLN, a obliczę ile sztuk kupić._"]
            elif msg_type == "TRADE_ACTION":
                ts = ai.get("trade_signal") or {}
                lines.append(f"📈 Widzę akcję: *{ts.get('action','?')} {ts.get('ticker','?')}*")
            else:
                trader_pos = get_latest_trader_positions()
                if trader_pos:
                    tickers = ", ".join(p["ticker"] for p in trader_pos[:5])
                    lines.append(f"ℹ️ Komentarz rynkowy. Portfel tradera: {tickers}...")
                else:
                    lines.append("ℹ️ Komentarz rynkowy — brak sygnału tradingowego.")

            await _send_to_raw(client, "\n".join(lines), parse_mode="markdown")
        except asyncio.TimeoutError:
            await _send_to_raw(client, "❌ Timeout analizy AI (>90s)")
        except Exception as e:
            logger.error(f"Błąd analizy zdjęcia: {e}")
            await _send_to_raw(client, f"❌ Błąd analizy: {e}")
        finally:
            tmp_path.unlink(missing_ok=True)
        return

    if not text:
        return

    # /fetch IKE N lub /fetch IKZE N
    if text.lower().startswith("/fetch"):
        from src.damian_watcher import parse_fetch_command, TOPIC_NAMES
        topic_id, count = parse_fetch_command(text)
        if not topic_id:
            await _send_to_raw(client,
                "Użycie: `/fetch IKE 5` lub `/fetch IKZE 10`", parse_mode="markdown")
            return
        topic_name = TOPIC_NAMES.get(topic_id, str(topic_id))
        await _send_to_raw(client,
            f"⏳ Pobieram *{count}* wiadomości z *{topic_name}*...", parse_mode="markdown")
        msgs_fetched = []
        async for m in client.iter_messages(entity=settings.damian_group_id, reply_to=topic_id, limit=count):
            msgs_fetched.append(m)
        if not msgs_fetched:
            await _send_to_raw(client, f"⚠️ Brak wiadomości w {topic_name}")
            return
        msgs_fetched.reverse()
        for m in msgs_fetched:
            await forward_fn(m, topic_name)
            await asyncio.sleep(0.3)
        logger.info(f"✅ /fetch {topic_name} x{len(msgs_fetched)}")
        return

    # Kwota PLN → advisor
    cash = parse_cash_amount(text)
    if cash:
        logger.info(f"💡 Advisor: {cash:,.0f} PLN")
        try:
            reply = await asyncio.wait_for(build_advisor_message(cash), timeout=30)
            await _send_to_raw(client, reply, parse_mode="markdown")
        except asyncio.TimeoutError:
            await _send_to_raw(client, "❌ Timeout pobierania kursów")
        except Exception as e:
            logger.error(f"Advisor błąd: {e}")
            await _send_to_raw(client, f"❌ Błąd kalkulatora: {e}")
        return

    # Pytanie tekstowe → AI
    if len(text) >= 10 and not text.startswith("/"):
        logger.info(f"💬 Pytanie Marcina: {text[:60]}")
        try:
            answer = await _answer_question(text, client)
            if answer:
                await _send_to_raw(client, f"💬 {answer}", parse_mode="markdown")
        except asyncio.TimeoutError:
            await _send_to_raw(client, "❌ Timeout AI (>60s)")
        except Exception as e:
            logger.error(f"Błąd odpowiedzi AI: {e}")


# ============================================================
# Główna pętla
# ============================================================

async def main() -> None:
    global _last_message_at, _start_time
    _start_time = _time_mod.time()

    ensure_directories()
    setup_logging()
    init_db()

    logger.info("🚀 Signal Copier — userbot all-in-one")
    logger.info(f"   Staging:   {settings.source_group_id}  (test-bot-inwestor)")
    logger.info(f"   Output:    {settings.raw_channel_id}   (recive-bot-investor)")
    logger.info(f"   Damian:    {settings.damian_group_id}")
    logger.info(f"   Wiadomości w bazie: {count_messages()}")

    client = build_client()

    # Handler 1 — test-bot-inwestor → AI → recive-bot-investor
    if settings.source_group_id:
        @client.on(events.NewMessage(chats=settings.source_group_id))
        async def _staging_handler(event: events.NewMessage.Event) -> None:
            global _last_message_at
            _last_message_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            try:
                await handle_new_message(event, client)
            except Exception as e:
                logger.error(f"_staging_handler error: {e}")

    # Handler 2 — DamianInwestorx IKE/IKZE → forward do test-bot-inwestor
    if settings.damian_group_id:
        from src.damian_watcher import is_watched_topic, get_topic_id, TOPIC_NAMES

        async def _forward_to_staging(msg: Message, topic_name: str) -> None:
            saved = save_raw_message(
                message_id=msg.id,
                chat_id=settings.damian_group_id,
                timestamp=msg.date if isinstance(msg.date, datetime) else datetime.utcnow(),
                raw_text=msg.text or None,
                has_media=bool(msg.media),
                media_paths=[],
                grouped_id=msg.grouped_id,
            )
            if not saved:
                logger.debug(f"[{topic_name}] Duplikat msg {msg.id} — pomijam")
                return
            try:
                fwd = await client.forward_messages(
                    entity=settings.source_group_id,
                    messages=msg.id,
                    from_peer=settings.damian_group_id,
                )
                fwd_id = fwd[0].id if isinstance(fwd, list) else fwd.id
                _damian_topic_map[fwd_id] = topic_name
                logger.info(f"📤 [{topic_name}] msg {msg.id} → staging {fwd_id}")
            except Exception as e:
                if "protected" in str(e).lower() or "ChatForwardsRestrictedError" in type(e).__name__:
                    # Grupa Damiana ma "Restrict saving content" — pobieramy i re-wysyłamy
                    logger.info(f"🔒 Protected chat — re-wysyłam [{topic_name}] msg {msg.id}")
                    await _resend_to_staging(msg, topic_name)
                else:
                    logger.error(f"❌ Forward [{topic_name}] msg {msg.id}: {e}")

        async def _resend_to_staging(msg: Message, topic_name: str) -> None:
            """Fallback gdy forward zablokowany przez protected chat — pobieramy + re-wysyłamy."""
            text = msg.text or ""
            caption = f"[{topic_name}] {text}" if text else f"[{topic_name}]"
            tmp_path = None
            try:
                if msg.media:
                    tmp_path = MEDIA_DIR / f"damian_resend_{msg.id}.jpg"
                    await asyncio.wait_for(
                        client.download_media(msg, file=str(tmp_path)), timeout=30
                    )
                    sent = await client.send_file(
                        settings.source_group_id,
                        str(tmp_path),
                        caption=caption,
                    )
                else:
                    sent = await client.send_message(settings.source_group_id, caption)
                fwd_id = sent.id
                _damian_topic_map[fwd_id] = topic_name
                logger.info(f"📤 [{topic_name}] msg {msg.id} → staging {fwd_id} (re-send)")
            except Exception as e:
                logger.error(f"❌ Re-send [{topic_name}] msg {msg.id}: {e}")
            finally:
                if tmp_path:
                    tmp_path.unlink(missing_ok=True)

        @client.on(events.NewMessage(chats=settings.damian_group_id))
        async def _damian_handler(event: events.NewMessage.Event) -> None:
            msg = event.message
            if not is_watched_topic(msg):
                return
            topic_name = TOPIC_NAMES.get(get_topic_id(msg), "?")
            logger.info(f"📩 Damian [{topic_name}] id={msg.id}")
            await _forward_to_staging(msg, topic_name)

    # Handler 3 — usunięty: _output_poll_loop co 15s obsługuje recive-bot-investor
    # events.NewMessage + poll loop = podwójne odpowiedzi dla tego samego msg

    async with client:
        me = await client.get_me()
        logger.info(f"✅ Zalogowano jako: {me.first_name} (@{me.username})")

        # Warm up entity cache — required for broadcast channel update tracking
        for cid in filter(None, [settings.source_group_id, settings.raw_channel_id, settings.damian_group_id]):
            try:
                await client.get_entity(cid)
                logger.info(f"✅ Entity cached: {cid}")
            except Exception as e:
                logger.warning(f"⚠️ Entity {cid}: {e}")

        logger.info("👂 Nasłuchuję...")

        write_heartbeat()
        asyncio.create_task(heartbeat_loop())

        if settings.source_group_id:
            # Polling fallback — events.NewMessage may miss updates on broadcast channels
            async def _staging_poll_loop():
                last_id = 0
                try:
                    seed = await client.get_messages(settings.source_group_id, limit=1)
                    if seed:
                        last_id = seed[0].id
                        logger.info(f"🔄 Poll init: last_id={last_id}")
                except Exception as e:
                    logger.warning(f"Poll init error: {e}")

                while True:
                    await asyncio.sleep(15)
                    try:
                        new_msgs = await client.get_messages(
                            settings.source_group_id, min_id=last_id, limit=20
                        )
                        for msg in reversed(new_msgs):
                            if msg.id > last_id:
                                last_id = msg.id
                                logger.info(f"🔄 Poll: nowa wiadomość id={msg.id} na staging")
                                await _process_message(msg, settings.source_group_id, client)
                    except Exception as e:
                        logger.error(f"Staging poll error: {e}")

            asyncio.create_task(_staging_poll_loop())

        if settings.raw_channel_id:
            # Polling fallback dla recive-bot-investor (Marcin pisze na kanale)
            async def _output_poll_loop():
                last_id = 0
                try:
                    seed = await client.get_messages(settings.raw_channel_id, limit=1)
                    if seed:
                        last_id = seed[0].id
                        logger.info(f"🔄 Output poll init: last_id={last_id}")
                except Exception as e:
                    logger.warning(f"Output poll init error: {e}")

                while True:
                    await asyncio.sleep(15)
                    try:
                        new_msgs = await client.get_messages(
                            settings.raw_channel_id, min_id=last_id, limit=20
                        )
                        for msg in reversed(new_msgs):
                            if msg.id > last_id:
                                last_id = msg.id
                                if msg.id in _bot_sent_ids:
                                    continue
                                logger.info(f"🔄 Output poll: wiadomość Marcina id={msg.id}: {repr((msg.text or '')[:40])}")
                                try:
                                    await asyncio.wait_for(
                                        handle_channel_message(msg, client, _forward_to_staging),
                                        timeout=120,
                                    )
                                except asyncio.TimeoutError:
                                    logger.error(f"Output poll timeout msg {msg.id}")
                                except Exception as e:
                                    logger.error(f"Output poll handler error: {e}")
                    except Exception as e:
                        logger.error(f"Output poll error: {e}")

            asyncio.create_task(_output_poll_loop())

        if settings.damian_group_id:
            # Fallback /fetch przez IPC (monitor_bot nadal może pisać plik)
            async def _fetch_loop():
                import json
                while True:
                    await asyncio.sleep(5)
                    if not FETCH_REQUEST_FILE.exists():
                        continue
                    try:
                        req = json.loads(FETCH_REQUEST_FILE.read_text())
                        FETCH_REQUEST_FILE.unlink(missing_ok=True)
                        topic_id = req.get("topic_id")
                        count_req = int(req.get("count", 5))
                        req_ts = req.get("ts", 0)
                        if _time_mod.time() - req_ts > 120 or not topic_id:
                            continue
                        from src.damian_watcher import TOPIC_NAMES
                        topic_name = TOPIC_NAMES.get(topic_id, str(topic_id))
                        logger.info(f"📋 IPC /fetch: {topic_name} x{count_req}")
                        fetched = []
                        async for m in client.iter_messages(
                            entity=settings.damian_group_id, reply_to=topic_id, limit=count_req
                        ):
                            fetched.append(m)
                        fetched.reverse()
                        for m in fetched:
                            await _forward_to_staging(m, topic_name)
                            await asyncio.sleep(0.3)
                    except Exception as e:
                        logger.error(f"fetch_loop error: {e}")
                        FETCH_REQUEST_FILE.unlink(missing_ok=True)

            asyncio.create_task(_fetch_loop())

        await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
