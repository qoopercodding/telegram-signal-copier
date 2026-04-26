"""
Monitor Bot — komendy admina przez DM.

Komendy (tylko przez prywatną wiadomość do bota):
    /start   — rejestruje Twoje chat ID jako admina
    /status  — stan listenera (heartbeat, uptime, wiadomości)
    /logs    — ostatnie 15 linii logów signal-copier
    /disk    — zużycie dysku
    /health  — pełny raport
    /cleanup — usuwa media starsze niż 30 dni

Nasłuchiwanie kanałów i AI Q&A przeniesione do signal-copier (userbot).

Uruchomienie:
    python -m src.monitor_bot
"""

import asyncio
import json
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path

from loguru import logger
from telethon import TelegramClient, events

from src.config import settings, ensure_directories, LOGS_DIR, MEDIA_DIR, DB_DIR, PROJECT_ROOT
from src.storage import count_messages


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
    if not ADMIN_FILE.exists():
        return None
    try:
        return int(ADMIN_FILE.read_text().strip())
    except ValueError:
        return None


def save_admin_id(chat_id: int) -> None:
    ADMIN_FILE.write_text(str(chat_id))
    logger.info(f"Admin chat ID zapisane: {chat_id}")


def is_admin(chat_id: int) -> bool:
    admin_id = get_admin_id()
    return admin_id is not None and chat_id == admin_id


def read_heartbeat() -> dict | None:
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        return json.loads(HEARTBEAT_FILE.read_text())
    except Exception:
        return None


def format_bytes(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def get_dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def get_uptime() -> str:
    delta = timedelta(seconds=int(time.time() - START_TIME))
    d = delta.days
    h, rem = divmod(delta.seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{d}d {h}h {m}m"


# ============================================================
# Komendy
# ============================================================

async def cmd_start(event: events.NewMessage.Event) -> None:
    save_admin_id(event.chat_id)
    await event.respond(
        "✅ **Zarejestrowano jako admin!**\n\n"
        "Komendy (przez DM do bota):\n"
        "  /status — stan systemu\n"
        "  /logs   — ostatnie logi\n"
        "  /disk   — zużycie dysku\n"
        "  /health — pełny raport\n"
        "  /cleanup — wyczyść stare media\n\n"
        "Pytania AI, /fetch i advisor — pisz bezpośrednio na kanale recive-bot-investor."
    )


async def cmd_status(event: events.NewMessage.Event) -> None:
    if not is_admin(event.chat_id):
        return
    hb = read_heartbeat()
    msg_count = count_messages()
    if hb:
        last_hb = datetime.fromisoformat(hb["timestamp"])
        age = (datetime.utcnow() - last_hb).total_seconds()
        listener_status = "🟢 ŻYWY" if age < 600 else f"🔴 MARTWY ({int(age)}s temu)"
        listener_uptime = hb.get("uptime", "?")
        last_msg = hb.get("last_message_at", "brak")
    else:
        listener_status = "⚪ BRAK DANYCH"
        listener_uptime = "?"
        last_msg = "?"
    await event.respond(
        f"📊 **Status systemu**\n\n"
        f"**Listener:** {listener_status}\n"
        f"**Listener uptime:** {listener_uptime}\n"
        f"**Monitor uptime:** {get_uptime()}\n"
        f"**Wiadomości w bazie:** {msg_count}\n"
        f"**Ostatnia wiadomość:** {last_msg}\n"
    )


async def cmd_logs(event: events.NewMessage.Event) -> None:
    if not is_admin(event.chat_id):
        return
    log_files = sorted(LOGS_DIR.glob("listener_*.log"), reverse=True)
    if not log_files:
        await event.respond("📄 Brak plików logów")
        return
    try:
        lines = log_files[0].read_text(encoding="utf-8").strip().split("\n")
        text = f"📄 **Logi** ({log_files[0].name}):\n\n```\n" + "\n".join(lines[-15:]) + "\n```"
        if len(text) > 4000:
            text = text[:4000] + "\n...```"
        await event.respond(text)
    except Exception as e:
        await event.respond(f"❌ Błąd odczytu logów: {e}")


async def cmd_disk(event: events.NewMessage.Event) -> None:
    if not is_admin(event.chat_id):
        return
    db_size    = get_dir_size(DB_DIR)
    media_size = get_dir_size(MEDIA_DIR)
    logs_size  = get_dir_size(LOGS_DIR)
    disk = shutil.disk_usage("/")
    media_count = sum(1 for f in MEDIA_DIR.rglob("*") if f.is_file()) if MEDIA_DIR.exists() else 0
    db_alert = " ⚠️" if db_size > DB_SIZE_ALERT_MB * 1024 * 1024 else ""
    text = (
        f"💾 **Zużycie dysku**\n\n"
        f"**Baza SQLite:** {format_bytes(db_size)}{db_alert}\n"
        f"**Media ({media_count} plików):** {format_bytes(media_size)}\n"
        f"**Logi:** {format_bytes(logs_size)}\n"
        f"**Dysk wolny:** {format_bytes(disk.free)} ({disk.free/disk.total*100:.0f}%)\n"
    )
    await event.respond(text)


async def cmd_health(event: events.NewMessage.Event) -> None:
    if not is_admin(event.chat_id):
        return
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    ram_text = f"{int(line.split()[1]) / 1024:.0f} MB"
                    break
            else:
                ram_text = "?"
    except Exception:
        ram_text = "?"
    try:
        load1, load5, _ = os.getloadavg()
        load_text = f"{load1:.2f} / {load5:.2f}"
    except Exception:
        load_text = "?"
    hb = read_heartbeat()
    if hb:
        age = (datetime.utcnow() - datetime.fromisoformat(hb["timestamp"])).total_seconds()
        hb_text = f"{'🟢' if age < 600 else '🔴'} {int(age)}s temu"
    else:
        hb_text = "⚪ brak"
    disk = shutil.disk_usage("/")
    await event.respond(
        f"🏥 **Raport zdrowia**\n\n"
        f"**Heartbeat listenera:** {hb_text}\n"
        f"**Wiadomości w bazie:** {count_messages()}\n"
        f"**Monitor uptime:** {get_uptime()}\n"
        f"**RAM (monitor):** {ram_text}\n"
        f"**Load avg:** {load_text}\n"
        f"**Dysk wolny:** {format_bytes(disk.free)} ({disk.free/disk.total*100:.0f}%)\n"
    )


async def cmd_cleanup(event: events.NewMessage.Event) -> None:
    if not is_admin(event.chat_id):
        return
    cutoff = datetime.utcnow() - timedelta(days=MEDIA_RETENTION_DAYS)
    removed = 0
    freed = 0
    if MEDIA_DIR.exists():
        for f in MEDIA_DIR.iterdir():
            if f.is_file() and datetime.utcfromtimestamp(f.stat().st_mtime) < cutoff:
                freed += f.stat().st_size
                f.unlink()
                removed += 1
    if removed > 0:
        await event.respond(f"🧹 **Cleanup:** usunięto {removed} plików, zwolniono {format_bytes(freed)}")
    else:
        await event.respond(f"✨ Nic do wyczyszczenia (brak plików starszych niż {MEDIA_RETENTION_DAYS} dni)")


# ============================================================
# Heartbeat checker (alert gdy listener martwy)
# ============================================================

async def heartbeat_checker(client: TelegramClient) -> None:
    _last_alert_at: float = 0.0
    while True:
        await asyncio.sleep(600)
        admin_id = get_admin_id()
        if not admin_id:
            continue
        hb = read_heartbeat()
        if hb is None:
            continue
        age = (datetime.utcnow() - datetime.fromisoformat(hb["timestamp"])).total_seconds()
        if age > 1800:
            logger.warning(f"🔴 Listener martwy! {int(age)}s temu")
            if time.time() - _last_alert_at > 3600:
                _last_alert_at = time.time()
                try:
                    await client.send_message(
                        admin_id,
                        f"🔴 **ALERT: Listener nie żyje!**\n\n"
                        f"Ostatni heartbeat: {int(age/60)} min temu\n"
                        f"Uruchom: `sudo systemctl restart signal-copier`",
                    )
                except Exception as e:
                    logger.error(f"Błąd alertu heartbeat: {e}")


# ============================================================
# Main
# ============================================================

async def main() -> None:
    ensure_directories()

    log_file = LOGS_DIR / "monitor_{time:YYYY-MM-DD}.log"
    logger.add(str(log_file), rotation="1 day", retention="7 days", level="DEBUG", encoding="utf-8")

    logger.info("🤖 Monitor Bot — start (tylko komendy DM)")

    client = TelegramClient(
        str(PROJECT_ROOT / "monitor_bot"),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )

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

    await client.start(bot_token=settings.bot_token)
    me = await client.get_me()
    logger.info(f"✅ Monitor Bot zalogowany: @{me.username}")

    asyncio.create_task(heartbeat_checker(client))

    logger.info("👂 Czekam na komendy admina...")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
