"""
Monitor Bot — bot Telegram do monitorowania stanu systemu.

Komendy:
    /start   — rejestruje Twoje chat ID jako admina
    /status  — czy listener żyje, uptime, ile wiadomości
    /logs    — ostatnie 15 linii logów
    /disk    — ile miejsca zajmuje baza, media, logi
    /health  — pełny raport: RAM, CPU, dysk, ostatnia wiadomość
    /cleanup — usuwa media starsze niż 30 dni

Uruchomienie:
    python -m src.monitor_bot
"""

import asyncio
import json
import os
import re
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger
from telethon import TelegramClient, events
from telethon.tl.types import Message

from src.config import settings, ensure_directories, LOGS_DIR, MEDIA_DIR, DB_DIR, PROJECT_ROOT
from src.storage import get_connection, count_messages, get_latest_trader_positions
from src.prices import get_share_price


# ============================================================
# Stałe
# ============================================================

HEARTBEAT_FILE = PROJECT_ROOT / ".heartbeat"
ADMIN_FILE = PROJECT_ROOT / ".admin_chat_id"
MEDIA_RETENTION_DAYS = 30
DB_SIZE_ALERT_MB = 500
START_TIME = time.time()


# ============================================================
# Helpers
# ============================================================

def get_admin_id() -> int | None:
    """Odczytuje zapisane chat ID admina."""
    if ADMIN_FILE.exists():
        return int(ADMIN_FILE.read_text().strip())
    return None


def save_admin_id(chat_id: int) -> None:
    """Zapisuje chat ID admina."""
    ADMIN_FILE.write_text(str(chat_id))
    logger.info(f"Admin chat ID zapisane: {chat_id}")


def is_admin(chat_id: int) -> bool:
    """Sprawdza czy to admin."""
    admin_id = get_admin_id()
    return admin_id is not None and chat_id == admin_id


def get_dir_size(path: Path) -> int:
    """Rozmiar katalogu w bajtach."""
    total = 0
    if path.exists():
        for f in path.rglob("*"):
            if f.is_file():
                total += f.stat().st_size
    return total


def format_bytes(size: int) -> str:
    """Formatuje bajty do czytelnej formy."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_uptime() -> str:
    """Uptime procesu monitor bota."""
    delta = timedelta(seconds=int(time.time() - START_TIME))
    days = delta.days
    hours, remainder = divmod(delta.seconds, 3600)
    minutes, _ = divmod(remainder, 60)
    return f"{days}d {hours}h {minutes}m"


def read_heartbeat() -> dict | None:
    """Odczytuje heartbeat z listenera."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        data = json.loads(HEARTBEAT_FILE.read_text())
        return data
    except Exception:
        return None


# ============================================================
# Advisor — kalkulator pozycji
# ============================================================

_CASH_RE = re.compile(
    r'(\d[\d\s]*(?:[.,]\d+)?)\s*(k|tys\.?)?\s*pln',
    re.IGNORECASE,
)


def parse_cash_amount(text: str) -> float | None:
    """Wykrywa kwotę PLN w tekście: '120k PLN', '120 000 PLN', '120000pln'."""
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
    """Oblicza ile sztuk każdego waloru kupić za cash_pln na podstawie portfela tradera."""
    positions = get_latest_trader_positions()
    if not positions:
        return (
            "⚠️ *Brak danych o portfelu tradera w bazie.*\n\n"
            "Poczekaj aż Damian wyśle screenshot portfela — bot go przetworzy automatycznie.\n"
            "Potem napisz ponownie ile masz PLN."
        )

    source_date = (positions[0].get("created_at") or "")[:10]

    # Pobierz kursy równolegle
    price_results: list[tuple[float | None, str]] = await asyncio.gather(
        *[asyncio.to_thread(get_share_price, p["ticker"]) for p in positions],
        return_exceptions=False,
    )

    # Sprawdź czy mamy dane procentowe
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

    total_zainwestowane = 0.0
    any_shares = False

    for pos, (price, symbol) in zip(positions, price_results):
        ticker = pos["ticker"]
        pct    = (pos.get("percentage") or 0.0) if has_pct else equal_pct
        target = cash_pln * pct / 100

        if price and price > 0:
            shares = int(target / price)
            actual = shares * price
            total_zainwestowane += actual
            if shares > 0:
                any_shares = True
                lines.append(
                    f"• *{ticker}* {pct:.1f}% → *{shares} szt.* "
                    f"@ {price:.2f} PLN = *{actual:,.0f} PLN*"
                )
            else:
                lines.append(
                    f"• *{ticker}* {pct:.1f}% → za mało _(masz {target:.0f} PLN, "
                    f"min. {price:.2f} PLN/szt.)_"
                )
        else:
            total_zainwestowane += target
            lines.append(
                f"• *{ticker}* {pct:.1f}% → *{target:,.0f} PLN* _(kurs niedostępny)_"
            )

    if not any_shares:
        lines += [
            "",
            f"⚠️ *Za mało PLN na jakikolwiek zakup przy tej alokacji.*",
            f"Napisz ponownie z większą kwotą, np. `mam 120k PLN`",
        ]
        return "\n".join(lines)

    reszta = cash_pln - total_zainwestowane
    lines += [
        "",
        f"💸 Zainwestowane: *{total_zainwestowane:,.0f} PLN*",
    ]
    if reszta > 0.5:
        lines.append(f"💵 Zostaje na koncie: *{reszta:,.0f} PLN*")

    return "\n".join(lines)


_AUTH_CODE_FILE = Path("/tmp/.damian_auth_code")
_AUTH_REQUEST_FILE = Path("/tmp/.damian_auth_request")
FETCH_REQUEST_FILE = Path("/tmp/.fetch_request.json")
STAGING_MSG_FILE = Path("/tmp/.staging_msg.json")


async def cmd_advisor_channel(event: events.NewMessage.Event, client: TelegramClient) -> None:
    """
    Nasłuchuje wiadomości na kanale recive-bot-investor.
    Reaguje na:
      - /fetch IKE N / /fetch IKZE N → trigger fetchowania w signal-copier przez plik
      - 5-cyfrowy kod SMS (gdy damian_watcher czeka na logowanie)
      - Zdjęcie od Marcina → AI analiza + rekomendacja
      - '/advisor 120000' lub wolny tekst z kwotą PLN
      - Pytania tekstowe → AI odpowiada z kontekstem portfela tradera
    """
    # Ignoruj wiadomości od botów i wychodzące (własne)
    if event.message.out:
        return
    sender = await event.get_sender()
    if sender and getattr(sender, "bot", False):
        return

    text = (event.message.message or "").strip()

    # Zdjęcie od Marcina → AI analiza (przed sprawdzeniem tekstu — caption może być puste)
    if event.message.photo or event.message.document:
        await _handle_user_media(event, client, text or None)
        return

    if not text:
        return

    # /fetch IKE N — przekaż do signal-copier przez plik
    if text.lower().startswith("/fetch"):
        from src.damian_watcher import parse_fetch_command, TOPIC_NAMES
        import json, time as _time
        topic_id, count = parse_fetch_command(text)
        if topic_id:
            req = {"topic_id": topic_id, "count": count, "ts": _time.time()}
            FETCH_REQUEST_FILE.write_text(json.dumps(req))
            logger.info(f"📋 /fetch request zapisany: {TOPIC_NAMES.get(topic_id)} x{count}")
            await event.respond(
                f"⏳ Pobieram i analizuję <b>{count}</b> wiadomości z <b>{TOPIC_NAMES.get(topic_id)}</b>...",
                parse_mode="html",
            )
        else:
            await event.respond("Użycie: <code>/fetch IKE 5</code> lub <code>/fetch IKZE 10</code>", parse_mode="html")
        return

    # Relay kodu SMS
    stripped = re.sub(r"\s+", "", text)
    if re.match(r"^\d{5,6}$", stripped) and _AUTH_REQUEST_FILE.exists():
        _AUTH_CODE_FILE.write_text(stripped)
        logger.info(f"🔑 Kod SMS '{stripped}' przekazany do damian_watcher")
        return

    # /advisor lub kwota PLN
    cash: float | None = None
    if text.lower().startswith("/advisor"):
        parts = text.split(None, 1)
        if len(parts) == 2:
            raw = parts[1].lower().replace("k", "000").replace(",", ".").replace(" ", "")
            try:
                cash = float(raw)
            except ValueError:
                pass
        if not cash:
            await event.respond("Użycie: <code>/advisor 120000</code> lub napisz np. <code>mam 120k PLN</code>", parse_mode="html")
            return
    else:
        cash = parse_cash_amount(text)

    if cash:
        logger.info(f"💡 Advisor: {cash:,.0f} PLN (chat={event.chat_id})")
        try:
            reply = await build_advisor_message(cash)
            await event.respond(reply, parse_mode="markdown")
        except Exception as exc:
            logger.error(f"Advisor błąd: {exc}")
            await event.respond(f"❌ Błąd kalkulatora: {exc}")
        # Gdy tekst zawiera pytanie OBOK kwoty — odpowiedz też na nie
        if len(text) >= 20 and ("?" in text or "jaki" in text.lower() or "ile" in text.lower() or "co" in text.lower()):
            await _handle_user_question(event, text)
        return

    # Pytanie tekstowe (min. 10 znaków, nie komenda)
    if len(text) >= 10 and not text.startswith("/"):
        await _handle_user_question(event, text)


async def _handle_user_media(
    event: events.NewMessage.Event,
    client: TelegramClient,
    caption: str | None,
) -> None:
    """Pobiera zdjęcie od Marcina, analizuje AI i odpowiada rekomendacją."""
    from src.parser import analyze_message
    from src.storage import get_latest_trader_positions

    tmp_path = MEDIA_DIR / f"user_input_{event.message.id}.jpg"
    try:
        await client.download_media(event.message, file=str(tmp_path))
        logger.info(f"📷 Pobrano zdjęcie od Marcina: {tmp_path.name}")
    except Exception as e:
        logger.error(f"Błąd pobierania zdjęcia: {e}")
        await event.reply("❌ Nie mogłem pobrać zdjęcia")
        return

    await event.reply("🤔 Analizuję...")

    try:
        ai = await analyze_message(text=caption, media_paths=[str(tmp_path)])
        msg_type = ai.get("message_type")
        confidence = ai.get("confidence", 0.0)
        summary = ai.get("summary", "")

        lines = [f"🔍 *Analiza Twojego zdjęcia* (pewność: {confidence*100:.0f}%)", "", f"_{summary}_", ""]

        if msg_type == "PORTFOLIO_UPDATE":
            positions = ai.get("portfolio_positions") or []
            if positions:
                lines.append("📊 *Wykryte pozycje:*")
                for p in positions:
                    pct = p.get("percentage") or 0
                    lines.append(f"• {p['ticker']} — {pct:.1f}%")
                lines += ["", "💡 _Napisz ile masz PLN do zainwestowania, a obliczę ile sztuk kupić._"]
            else:
                lines.append("Nie rozpoznałem konkretnych pozycji na tym screenshocie.")

        elif msg_type == "TRADE_ACTION":
            ts = ai.get("trade_signal") or {}
            action = ts.get("action", "?")
            ticker = ts.get("ticker", "?")
            lines.append(f"📈 Widzę akcję: *{action} {ticker}*")
            lines.append("Chcesz żebym to wykonał? (AKCEPTUJ/ODRZUĆ)")

        elif msg_type in ("INFORMATIONAL", "COMMENT"):
            trader_pos = get_latest_trader_positions()
            if trader_pos:
                tickers = ", ".join(p["ticker"] for p in trader_pos[:5])
                lines.append(f"ℹ️ Komentarz rynkowy. Aktualny portfel tradera: {tickers}...")
            else:
                lines.append("ℹ️ Komentarz rynkowy — brak sygnału tradingowego.")

        else:
            lines.append(f"ℹ️ Typ wiadomości: {msg_type}")

        await event.reply("\n".join(lines), parse_mode="markdown")

    except Exception as e:
        logger.error(f"Błąd analizy zdjęcia od Marcina: {e}")
        await event.reply(f"❌ Błąd analizy: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)


async def _handle_user_question(event: events.NewMessage.Event, text: str) -> None:
    """Odpowiada na pytania tekstowe Marcina z kontekstem portfela i logów."""
    from src.parser import get_client as get_ai_client
    from src.storage import get_latest_trader_positions
    from google.genai import types as gtypes

    AI_MODEL = "gemini-2.5-flash"

    # Kontekst: pozycje portfela tradera
    positions = get_latest_trader_positions()
    if positions:
        pos_lines = []
        for p in positions:
            pct = p.get("percentage") or 0
            val = p.get("value_pln")
            pos_lines.append(
                f"  {p['ticker']}: {pct:.1f}%" + (f", {val:,.0f} PLN" if val else "")
            )
        portfolio_ctx = "Ostatnie pozycje portfela tradera w DB:\n" + "\n".join(pos_lines)
    else:
        portfolio_ctx = "Brak danych o portfelu tradera w bazie SQLite."

    # Kontekst: ostatnie logi (15 linii)
    log_ctx = ""
    log_files = sorted(LOGS_DIR.glob("listener_*.log"), reverse=True)
    if log_files:
        try:
            lines = log_files[0].read_text(encoding="utf-8").strip().split("\n")
            log_ctx = "Ostatnie logi signal-copier (listener):\n" + "\n".join(lines[-15:])
        except Exception:
            pass

    prompt = f"""Jesteś asystentem Marcina — właściciela systemu do śledzenia sygnałów giełdowych GPW.

KIM JESTEŚ (odpowiedz szczerze gdy ktoś pyta):
- Jesteś modelem {AI_MODEL} (Google Gemini)
- NIE jesteś brokerem, doradcą finansowym ani systemem tradingowym
- Jesteś papugą lingwistyczną z dostępem do danych z bazy SQLite i logów procesu
- Twoje obliczenia to arytmetyka na danych z DB — nie prognozujesz, nie masz kryształowej kuli
- Gdy pytają "jakim modelem jesteś" — powiedz wprost: {AI_MODEL}

DANE KONTEKSTOWE:
{portfolio_ctx}

{log_ctx}

ZASADY:
- Odpowiadaj krótko i konkretnie po polsku (max 4 zdania)
- Nie udawaj eksperta — mów co widzisz w danych, nie więcej
- Jeśli pytanie dotyczy logów — przytoczyć fragment z powyższego kontekstu
- Jeśli pytasz o coś czego nie ma w danych — powiedz że nie wiesz

PYTANIE MARCINA: {text}"""

    try:
        ai_client = get_ai_client()
        response = await ai_client.aio.models.generate_content(
            model=AI_MODEL,
            contents=[gtypes.Part.from_text(text=prompt)],
        )
        answer = (response.text or "").strip()
        if answer:
            await event.reply(f"💬 {answer}", parse_mode="markdown")
    except Exception as e:
        logger.error(f"Błąd odpowiedzi na pytanie: {e}")


# ============================================================
# Komendy bota
# ============================================================

async def cmd_start(event: events.NewMessage.Event) -> None:
    """Rejestruje admina."""
    save_admin_id(event.chat_id)
    await event.respond(
        "✅ **Zarejestrowano jako admin!**\n\n"
        "Dostępne komendy:\n"
        "  /status — stan systemu\n"
        "  /logs — ostatnie logi\n"
        "  /disk — zużycie dysku\n"
        "  /health — pełny raport\n"
        "  /cleanup — wyczyść stare media\n\n"
        "**Na kanale recive-bot-investor:**\n"
        "  /advisor 120000 — propozycja alokacji X PLN\n"
        "  lub napisz np. `mam 120k PLN wolnej gotówki`"
    )


async def cmd_status(event: events.NewMessage.Event) -> None:
    """Status listenera."""
    if not is_admin(event.chat_id):
        return

    hb = read_heartbeat()
    msg_count = count_messages()

    if hb:
        last_hb = datetime.fromisoformat(hb["timestamp"])
        age_seconds = (datetime.utcnow() - last_hb).total_seconds()
        listener_status = "🟢 ŻYWY" if age_seconds < 600 else f"🔴 MARTWY (ostatni heartbeat {int(age_seconds)}s temu)"
        listener_uptime = hb.get("uptime", "?")
        last_msg = hb.get("last_message_at", "brak")
    else:
        listener_status = "⚪ BRAK DANYCH (listener nie wysłał heartbeatu)"
        listener_uptime = "?"
        last_msg = "?"

    text = (
        f"📊 **Status systemu**\n\n"
        f"**Listener:** {listener_status}\n"
        f"**Listener uptime:** {listener_uptime}\n"
        f"**Monitor uptime:** {get_uptime()}\n"
        f"**Wiadomości w bazie:** {msg_count}\n"
        f"**Ostatnia wiadomość:** {last_msg}\n"
    )
    await event.respond(text)


async def cmd_logs(event: events.NewMessage.Event) -> None:
    """Ostatnie logi."""
    if not is_admin(event.chat_id):
        return

    # Znajdź najnowszy plik logów
    log_files = sorted(LOGS_DIR.glob("listener_*.log"), reverse=True)
    if not log_files:
        await event.respond("📄 Brak plików logów")
        return

    latest = log_files[0]
    try:
        lines = latest.read_text(encoding="utf-8").strip().split("\n")
        last_lines = lines[-15:]  # Ostatnie 15 linii
        text = f"📄 **Ostatnie logi** ({latest.name}):\n\n```\n" + "\n".join(last_lines) + "\n```"
        # Telegram max 4096 znaków
        if len(text) > 4000:
            text = text[:4000] + "\n...```"
        await event.respond(text)
    except Exception as e:
        await event.respond(f"❌ Błąd odczytu logów: {e}")


async def cmd_disk(event: events.NewMessage.Event) -> None:
    """Zużycie dysku."""
    if not is_admin(event.chat_id):
        return

    db_size = get_dir_size(DB_DIR)
    media_size = get_dir_size(MEDIA_DIR)
    logs_size = get_dir_size(LOGS_DIR)
    total = db_size + media_size + logs_size

    # Dysk systemowy
    disk = shutil.disk_usage("/")
    disk_free_pct = (disk.free / disk.total) * 100

    # Ile plików media
    media_count = sum(1 for f in MEDIA_DIR.rglob("*") if f.is_file()) if MEDIA_DIR.exists() else 0

    # Alert jeśli baza > limit
    db_alert = " ⚠️" if db_size > DB_SIZE_ALERT_MB * 1024 * 1024 else ""

    text = (
        f"💾 **Zużycie dysku**\n\n"
        f"**Baza SQLite:** {format_bytes(db_size)}{db_alert}\n"
        f"**Media ({media_count} plików):** {format_bytes(media_size)}\n"
        f"**Logi:** {format_bytes(logs_size)}\n"
        f"**Razem projekt:** {format_bytes(total)}\n"
        f"---\n"
        f"**Dysk wolny:** {format_bytes(disk.free)} ({disk_free_pct:.0f}%)\n"
    )
    if disk_free_pct < 10:
        text += "\n🔴 **UWAGA: Mało miejsca na dysku!**"

    await event.respond(text)


async def cmd_health(event: events.NewMessage.Event) -> None:
    """Pełny raport zdrowia."""
    if not is_admin(event.chat_id):
        return

    # RAM
    try:
        import resource
        # Linux only
        mem_usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024  # MB
        ram_text = f"{mem_usage:.0f} MB"
    except Exception:
        # Fallback — odczyt z /proc
        try:
            with open("/proc/self/status") as f:
                for line in f:
                    if line.startswith("VmRSS:"):
                        ram_kb = int(line.split()[1])
                        ram_text = f"{ram_kb / 1024:.0f} MB"
                        break
                else:
                    ram_text = "?"
        except Exception:
            ram_text = "? (nie Linux)"

    # Load average
    try:
        load1, load5, load15 = os.getloadavg()
        load_text = f"{load1:.2f} / {load5:.2f} / {load15:.2f}"
    except Exception:
        load_text = "?"

    # Listener heartbeat
    hb = read_heartbeat()
    if hb:
        last_hb = datetime.fromisoformat(hb["timestamp"])
        age = (datetime.utcnow() - last_hb).total_seconds()
        hb_text = f"{'🟢' if age < 600 else '🔴'} {int(age)}s temu"
    else:
        hb_text = "⚪ brak"

    # Disk
    disk = shutil.disk_usage("/")
    disk_free_pct = (disk.free / disk.total) * 100

    text = (
        f"🏥 **Raport zdrowia**\n\n"
        f"**Heartbeat listenera:** {hb_text}\n"
        f"**Wiadomości w bazie:** {count_messages()}\n"
        f"**Monitor uptime:** {get_uptime()}\n"
        f"**RAM (monitor):** {ram_text}\n"
        f"**Load avg:** {load_text}\n"
        f"**Dysk wolny:** {format_bytes(disk.free)} ({disk_free_pct:.0f}%)\n"
    )
    await event.respond(text)


async def cmd_cleanup(event: events.NewMessage.Event) -> None:
    """Czyści stare media."""
    if not is_admin(event.chat_id):
        return

    cutoff = datetime.utcnow() - timedelta(days=MEDIA_RETENTION_DAYS)
    removed = 0
    freed = 0

    if MEDIA_DIR.exists():
        for f in MEDIA_DIR.iterdir():
            if f.is_file():
                mtime = datetime.utcfromtimestamp(f.stat().st_mtime)
                if mtime < cutoff:
                    size = f.stat().st_size
                    f.unlink()
                    removed += 1
                    freed += size

    if removed > 0:
        await event.respond(
            f"🧹 **Cleanup zakończony**\n\n"
            f"Usunięto: {removed} plików\n"
            f"Zwolniono: {format_bytes(freed)}"
        )
    else:
        await event.respond(f"✨ Nic do wyczyszczenia (brak plików starszych niż {MEDIA_RETENTION_DAYS} dni)")


# ============================================================
# Handler przycisków AKCEPTUJ / ODRÓĆ (Decision Bot)
# ============================================================

async def cmd_callback(event: events.CallbackQuery.Event) -> None:
    """
    Obsługuje kliknięcia przycisków inline:
      accept:{msg_id}:{ticker}:{action}
      reject:{msg_id}
    """
    data = event.data.decode("utf-8")
    now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    if data.startswith("accept:"):
        parts  = data.split(":")
        msg_id = parts[1] if len(parts) > 1 else "?"
        ticker = parts[2] if len(parts) > 2 else "?"
        action = parts[3] if len(parts) > 3 else "?"

        logger.info(f"✅ Admin zaakceptował sygnał msg_id={msg_id}: {action} {ticker}")
        await event.edit(
            f"✅ *ZAAKCEPTOWANO*\n\n"
            f"Sygnał `{action} {ticker}` (msg #{msg_id}) zatwierdzony.\n"
            f"_⏰ {now}_"
        )

    elif data.startswith("reject:"):
        parts  = data.split(":")
        msg_id = parts[1] if len(parts) > 1 else "?"

        logger.info(f"❌ Admin odrzucił sygnał msg_id={msg_id}")
        await event.edit(
            f"❌ *ODRZUCONO*\n\n"
            f"Sygnał (msg #{msg_id}) odrzucony.\n"
            f"_⏰ {now}_"
        )

    else:
        logger.warning(f"Nieznany callback: {data}")
        await event.answer("Nieznana akcja")
        return

    await event.answer()  # Ukryj "zegarek" w Telegramie


# ============================================================
# Heartbeat checker (działa w tle)
# ============================================================

async def heartbeat_checker(client: TelegramClient) -> None:
    """
    Co 10 minut sprawdza heartbeat listenera.
    Jeśli brak > 30 min → wysyła alert do admina (raz na 60 min żeby nie spamować).
    """
    _last_alert_at: float = 0.0

    while True:
        await asyncio.sleep(600)  # Co 10 minut

        admin_id = get_admin_id()
        if not admin_id:
            continue

        hb = read_heartbeat()
        if hb is None:
            continue

        last_hb = datetime.fromisoformat(hb["timestamp"])
        age = (datetime.utcnow() - last_hb).total_seconds()

        if age > 1800:  # 30 minut
            logger.warning(f"🔴 Listener martwy! Ostatni heartbeat {int(age)}s temu")
            now = time.time()
            if now - _last_alert_at > 3600:  # Alert max raz na godzinę
                _last_alert_at = now
                try:
                    await client.send_message(
                        admin_id,
                        f"🔴 **ALERT: Listener nie żyje!**\n\n"
                        f"Ostatni heartbeat: {int(age / 60)} min temu\n"
                        f"_Uruchom: `sudo systemctl restart signal-copier`_",
                    )
                except Exception as e:
                    logger.error(f"Błąd wysyłania alertu heartbeat: {e}")


# ============================================================
# Main
# ============================================================

async def main() -> None:
    ensure_directories()

    log_file = LOGS_DIR / "monitor_{time:YYYY-MM-DD}.log"
    logger.add(str(log_file), rotation="1 day", retention="7 days", level="DEBUG", encoding="utf-8")

    logger.info("🤖 Monitor Bot — start")

    client = TelegramClient(
        str(PROJECT_ROOT / "monitor_bot"),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

    # Rejestruj handlery komend
    @client.on(events.NewMessage(pattern="/start"))
    async def _start(event):
        await cmd_start(event)

    @client.on(events.NewMessage(pattern="/status"))
    async def _status(event):
        await cmd_status(event)

    @client.on(events.NewMessage(pattern="/logs"))
    async def _logs(event):
        await cmd_logs(event)

    @client.on(events.NewMessage(pattern="/disk"))
    async def _disk(event):
        await cmd_disk(event)

    @client.on(events.NewMessage(pattern="/health"))
    async def _health(event):
        await cmd_health(event)

    @client.on(events.NewMessage(pattern="/cleanup"))
    async def _cleanup(event):
        await cmd_cleanup(event)

    @client.on(events.CallbackQuery())
    async def _callback(event):
        await cmd_callback(event)

    # Nasłuch wiadomości na kanale recive-bot-investor (advisor + zdjęcia + pytania)
    if settings.raw_channel_id:
        @client.on(events.NewMessage(chats=settings.raw_channel_id))
        async def _channel_msg(event):
            await cmd_advisor_channel(event, client)
        logger.info(f"📡 Nasłuchuję kanał {settings.raw_channel_id} (advisor)")

    # Nasłuch wiadomości na kanale test-bot-inwestor (staging → IPC → signal-copier AI)
    if settings.source_group_id:
        @client.on(events.NewMessage(chats=settings.source_group_id))
        async def _staging_msg(event):
            msg = event.message
            if msg.out:
                return
            req = {"msg_id": msg.id, "chat_id": settings.source_group_id, "ts": time.time()}
            STAGING_MSG_FILE.write_text(json.dumps(req))
            logger.info(f"📨 staging IPC: msg_id={msg.id} → signal-copier")
        logger.info(f"📡 Nasłuchuję staging {settings.source_group_id} (test-bot-inwestor)")

    await client.start(bot_token=settings.bot_token)
    me = await client.get_me()
    logger.info(f"✅ Monitor Bot zalogowany: @{me.username}")

    # Uruchom heartbeat checker w tle
    asyncio.create_task(heartbeat_checker(client))

    logger.info("👂 Czekam na komendy...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
