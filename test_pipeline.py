"""
E2E pipeline tests — widoczne na Telegramie.

Uruchomienie:
    source venv/bin/activate
    python test_pipeline.py

Testy:
  T1  wysyła tekst na test-bot-inwestor           → widoczne na kanale
  T2  wysyła obrazek na test-bot-inwestor          → widoczne na kanale
  T3  sanity: czy T1/T2 są widoczne na kanale      → potwierdza odczyt
  T4  czeka na forward do recive-bot-investor      → widoczne na kanale
  T5  sanity: czy forward dotarł na recive-bot     → potwierdza odczyt
  T6  czeka na odpowiedź AI na recive-bot          → widoczne na kanale
  T7  sanity: czy AI odpowiedział                  → potwierdza odczyt
"""

import asyncio
import time
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from telethon import TelegramClient

sys.path.insert(0, str(Path(__file__).parent))
from src.config import settings, PROJECT_ROOT, MEDIA_DIR

STAGING  = settings.source_group_id   # test-bot-inwestor
OUTPUT   = settings.raw_channel_id    # recive-bot-investor

TAG = f"[TEST {datetime.now():%H%M%S}]"   # unikalny tag do identyfikacji wiadomości testowych

PASS = "\033[92m✅\033[0m"
FAIL = "\033[91m❌\033[0m"
WAIT = "\033[93m⏳\033[0m"
INFO = "\033[96mℹ️ \033[0m"


def log(icon, msg):
    print(f"  {icon}  {msg}", flush=True)


def separator(title):
    print(f"\n\033[1m{'─'*55}\n  {title}\n{'─'*55}\033[0m", flush=True)


# ──────────────────────────────────────────────────────────
# UTIL: pobierz testowy obrazek
# ──────────────────────────────────────────────────────────

def get_test_image() -> Path:
    img_path = MEDIA_DIR / "test_chart.jpg"
    if not img_path.exists():
        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        # pobierz prosty wykres ze stockcharts (publiczny)
        url = "https://stooq.pl/c/?s=pko&d=20260426&t=d&a=lg&b=1"
        try:
            urllib.request.urlretrieve(url, str(img_path))
        except Exception:
            # fallback: wygeneruj minimalny JPG (1x1 czerwony piksel)
            img_path.write_bytes(bytes([
                0xFF,0xD8,0xFF,0xE0,0x00,0x10,0x4A,0x46,0x49,0x46,0x00,0x01,
                0x01,0x00,0x00,0x01,0x00,0x01,0x00,0x00,0xFF,0xDB,0x00,0x43,
                0x00,0x08,0x06,0x06,0x07,0x06,0x05,0x08,0x07,0x07,0x07,0x09,
                0x09,0x08,0x0A,0x0C,0x14,0x0D,0x0C,0x0B,0x0B,0x0C,0x19,0x12,
                0x13,0x0F,0x14,0x1D,0x1A,0x1F,0x1E,0x1D,0x1A,0x1C,0x1C,0x20,
                0x24,0x2E,0x27,0x20,0x22,0x2C,0x23,0x1C,0x1C,0x28,0x37,0x29,
                0x2C,0x30,0x31,0x34,0x34,0x34,0x1F,0x27,0x39,0x3D,0x38,0x32,
                0x3C,0x2E,0x33,0x34,0x32,0xFF,0xC0,0x00,0x0B,0x08,0x00,0x01,
                0x00,0x01,0x01,0x01,0x11,0x00,0xFF,0xC4,0x00,0x1F,0x00,0x00,
                0x01,0x05,0x01,0x01,0x01,0x01,0x01,0x01,0x00,0x00,0x00,0x00,
                0x00,0x00,0x00,0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,0x08,
                0x09,0x0A,0x0B,0xFF,0xC4,0x00,0xB5,0x10,0x00,0x02,0x01,0x03,
                0x03,0x02,0x04,0x03,0x05,0x05,0x04,0x04,0x00,0x00,0x01,0x7D,
                0x01,0x02,0x03,0x00,0x04,0x11,0x05,0x12,0x21,0x31,0x41,0x06,
                0x13,0x51,0x61,0x07,0x22,0x71,0x14,0x32,0x81,0x91,0xA1,0x08,
                0x23,0x42,0xB1,0xC1,0x15,0x52,0xD1,0xF0,0x24,0x33,0x62,0x72,
                0x82,0x09,0x0A,0x16,0x17,0x18,0x19,0x1A,0x25,0x26,0x27,0x28,
                0x29,0x2A,0x34,0x35,0x36,0x37,0x38,0x39,0x3A,0x43,0x44,0x45,
                0x46,0x47,0x48,0x49,0x4A,0x53,0x54,0x55,0x56,0x57,0x58,0x59,
                0x5A,0x63,0x64,0x65,0x66,0x67,0x68,0x69,0x6A,0x73,0x74,0x75,
                0x76,0x77,0x78,0x79,0x7A,0x83,0x84,0x85,0x86,0x87,0x88,0x89,
                0x8A,0x92,0x93,0x94,0x95,0x96,0x97,0x98,0x99,0x9A,0xA2,0xA3,
                0xA4,0xA5,0xA6,0xA7,0xA8,0xA9,0xAA,0xB2,0xB3,0xB4,0xB5,0xB6,
                0xB7,0xB8,0xB9,0xBA,0xC2,0xC3,0xC4,0xC5,0xC6,0xC7,0xC8,0xC9,
                0xCA,0xD2,0xD3,0xD4,0xD5,0xD6,0xD7,0xD8,0xD9,0xDA,0xE1,0xE2,
                0xE3,0xE4,0xE5,0xE6,0xE7,0xE8,0xE9,0xEA,0xF1,0xF2,0xF3,0xF4,
                0xF5,0xF6,0xF7,0xF8,0xF9,0xFA,0xFF,0xDA,0x00,0x08,0x01,0x01,
                0x00,0x00,0x3F,0x00,0xFB,0xD4,0xFF,0xD9,
            ]))
    return img_path


# ──────────────────────────────────────────────────────────
# UTIL: sprawdź nowe wiadomości na kanale od send_time
# ──────────────────────────────────────────────────────────

async def get_new_messages(client, channel_id, since_ts: float, limit=10):
    msgs = await client.get_messages(channel_id, limit=limit)
    cutoff = datetime.utcfromtimestamp(since_ts - 2)
    return [
        m for m in msgs
        if m.date and m.date.replace(tzinfo=None) >= cutoff
    ]


# ──────────────────────────────────────────────────────────
# T1: Wyślij TEKST na test-bot-inwestor
# ──────────────────────────────────────────────────────────

async def t1_send_text(client) -> dict:
    separator("T1 — wysyłam TEKST na test-bot-inwestor")

    text = (
        f"{TAG}\n"
        f"📊 Kupuję 100 akcji PKO BP (PKO) po 45.20 PLN na konto IKE.\n"
        f"Uzasadnienie: solidna dywidenda, niedowartościowanie względem sektora bankowego.\n"
        f"Wiadomość testowa — sprawdzam pipeline AI."
    )

    log(WAIT, f"Wysyłam na test-bot-inwestor ({STAGING})...")
    msg = await client.send_message(STAGING, text)
    log(PASS, f"Wysłano msg_id={msg.id}")
    log(INFO, f"Sprawdź kanał test-bot-inwestor — powinna być widoczna wiadomość z tagiem {TAG}")

    return {"msg_id": msg.id, "sent_at": time.time(), "text": text}


# ──────────────────────────────────────────────────────────
# T2: Wyślij OBRAZEK na test-bot-inwestor
# ──────────────────────────────────────────────────────────

async def t2_send_image(client) -> dict:
    separator("T2 — wysyłam OBRAZEK na test-bot-inwestor")

    img_path = get_test_image()
    caption = f"{TAG} 📈 Screenshot wykresu PKO — analiza techniczna. Sygnał kupna."

    log(WAIT, f"Wysyłam obrazek ({img_path.name}) na test-bot-inwestor ({STAGING})...")
    msg = await client.send_file(STAGING, str(img_path), caption=caption)
    log(PASS, f"Wysłano msg_id={msg.id} z obrazkiem")
    log(INFO, f"Sprawdź kanał test-bot-inwestor — powinna być widoczna wiadomość z obrazkiem")

    return {"msg_id": msg.id, "sent_at": time.time(), "caption": caption}


# ──────────────────────────────────────────────────────────
# T3: Sanity — czy T1/T2 są widoczne na test-bot-inwestor
# ──────────────────────────────────────────────────────────

async def t3_sanity_staging(client, t1: dict, t2: dict) -> bool:
    separator("T3 — sanity: czy wiadomości są widoczne na test-bot-inwestor")

    await asyncio.sleep(2)
    msgs = await client.get_messages(STAGING, limit=10)
    ids_on_channel = {m.id for m in msgs}

    t1_ok = t1["msg_id"] in ids_on_channel
    t2_ok = t2["msg_id"] in ids_on_channel

    if t1_ok:
        log(PASS, f"T1 tekst (msg_id={t1['msg_id']}) widoczny na test-bot-inwestor ✓")
    else:
        log(FAIL, f"T1 tekst (msg_id={t1['msg_id']}) NIE widoczny na test-bot-inwestor!")

    if t2_ok:
        log(PASS, f"T2 obrazek (msg_id={t2['msg_id']}) widoczny na test-bot-inwestor ✓")
    else:
        log(FAIL, f"T2 obrazek (msg_id={t2['msg_id']}) NIE widoczny na test-bot-inwestor!")

    return t1_ok and t2_ok


# ──────────────────────────────────────────────────────────
# T4 + T5: Czekaj na forward do recive-bot-investor
# ──────────────────────────────────────────────────────────

async def t4_t5_forward_to_output(client, t1: dict, t2: dict) -> dict:
    separator("T4 — czekam na forward/AI do recive-bot-investor (max 75s)")
    log(INFO, "Signal-copier powinien odebrać T1/T2 ze staging i przetworzyć przez AI → recive-bot-investor")

    since = min(t1["sent_at"], t2["sent_at"]) - 3
    deadline = time.time() + 75
    found_msgs = []
    dots = 0

    while time.time() < deadline:
        await asyncio.sleep(4)
        dots += 1
        print(f"    {WAIT} czekam... {int(deadline - time.time())}s", end="\r", flush=True)

        new_msgs = await get_new_messages(client, OUTPUT, since)
        # filtruj wiadomości testowe (żeby nie liczyć naszych własnych wysłanych wcześniej)
        relevant = [
            m for m in new_msgs
            if TAG not in (m.text or "")   # nie nasze testowe
            and m.id not in {r["id"] for r in found_msgs}
        ]
        for m in relevant:
            found_msgs.append({"id": m.id, "text": (m.text or "")[:120], "ts": m.date})

        if len(found_msgs) >= 2:
            break

    print()
    separator("T5 — sanity: czy forwarded/AI wiadomości dotarły na recive-bot-investor")

    if found_msgs:
        log(PASS, f"Na recive-bot-investor pojawiło się {len(found_msgs)} nowych wiadomości:")
        for i, m in enumerate(found_msgs, 1):
            log(INFO, f"  [{i}] id={m['id']} — {m['text'][:80]}")
    else:
        log(FAIL, "Żadna wiadomość NIE dotarła na recive-bot-investor po 75s!")
        log(INFO, "Możliwe przyczyny:")
        log(INFO, "  → signal-copier nie uruchomiony z nowym kodem (git pull + restart)")
        log(INFO, "  → userbot nie nasłuchuje na test-bot-inwestor")
        log(INFO, f"  → sprawdź logi: tail -f logs/listener_{datetime.now():%Y-%m-%d}.log")

    return {"found": found_msgs, "ok": len(found_msgs) > 0}


# ──────────────────────────────────────────────────────────
# T6 + T7: Czy AI odpowiedział na wiadomości
# ──────────────────────────────────────────────────────────

async def t6_t7_ai_response(client, forward_result: dict, since_ts: float) -> bool:
    separator("T6 — czekam na odpowiedź AI na recive-bot-investor (max 60s)")

    if not forward_result["ok"]:
        log(FAIL, "Pominięto — brak forwardowanych wiadomości z T4/T5")
        return False

    log(INFO, "AI powinien odpowiedzieć analizą sygnału (TRADE_ACTION / PORTFOLIO_UPDATE)")

    deadline = time.time() + 60
    ai_msgs = []

    while time.time() < deadline:
        await asyncio.sleep(4)
        print(f"    {WAIT} czekam na AI... {int(deadline - time.time())}s", end="\r", flush=True)

        new_msgs = await get_new_messages(client, OUTPUT, since_ts)
        for m in new_msgs:
            text = m.text or ""
            # AI odpowiedź zawiera charakterystyczne słowa
            is_ai = any(kw in text for kw in [
                "SYGNAŁ", "KUPNO", "SPRZEDAŻ", "pewność", "Pewność",
                "confidence", "TRADE", "PORTFEL", "AKTUALIZACJA",
                "🟢", "🔴", "🎯", "📊", "NOTATKA", "ℹ️"
            ])
            already = m.id in {r["id"] for r in ai_msgs}
            if is_ai and not already:
                ai_msgs.append({"id": m.id, "text": text[:150]})

        if ai_msgs:
            break

    print()
    separator("T7 — sanity: czy AI odpowiedział na recive-bot-investor")

    if ai_msgs:
        log(PASS, f"AI odpowiedział {len(ai_msgs)} wiadomością(ami):")
        for m in ai_msgs:
            log(INFO, f"  id={m['id']}: {m['text'][:100]}")
        return True
    else:
        log(FAIL, "AI NIE odpowiedział po 60s!")
        log(INFO, "Możliwe przyczyny:")
        log(INFO, "  → signal-copier nie działa z nowym kodem")
        log(INFO, "  → Gemini API nie odpowiada (sprawdź GEMINI_API_KEY)")
        log(INFO, "  → confidence < 0.6 (AI sklasyfikował jako nieistotne)")
        log(INFO, f"  → sprawdź: tail -20 logs/listener_{datetime.now():%Y-%m-%d}.log")
        return False


# ──────────────────────────────────────────────────────────
# PODSUMOWANIE
# ──────────────────────────────────────────────────────────

def print_summary(results: list[tuple[str, bool]]):
    separator("PODSUMOWANIE")
    for name, ok in results:
        icon = PASS if ok else FAIL
        print(f"  {icon}  {name}")
    passed = sum(1 for _, ok in results if ok)
    total = len(results)
    color = "\033[92m" if passed == total else ("\033[93m" if passed > 0 else "\033[91m")
    print(f"\n  {color}\033[1m{passed}/{total} OK\033[0m\n")

    if passed < total:
        print("  \033[93mAby naprawić:\033[0m")
        print("    git -C /home/marcin/telegram-signal-copier pull")
        print("    sudo systemctl restart signal-copier signal-monitor")
        print(f"    tail -f /home/marcin/telegram-signal-copier/logs/listener_{datetime.now():%Y-%m-%d}.log")


# ──────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────

async def main():
    print(f"\n\033[1m{'═'*55}")
    print(f"  SIGNAL COPIER — E2E test (widoczny na Telegramie)")
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}   TAG={TAG}")
    print(f"  staging:  test-bot-inwestor   {STAGING}")
    print(f"  output:   recive-bot-investor {OUTPUT}")
    print(f"{'═'*55}\033[0m")

    # Używamy kopii sesji żeby nie kolidować z działającym signal-copier
    import shutil, tempfile
    orig = str(PROJECT_ROOT / settings.session_name) + ".session"
    tmp_session = "/tmp/test_pipeline_session"
    shutil.copy2(orig, tmp_session + ".session")
    client = TelegramClient(tmp_session, settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print(f"\n\033[91m❌ Userbot nie zalogowany!\033[0m")
        await client.disconnect()
        return

    me = await client.get_me()
    log(PASS, f"Userbot: {me.first_name} @{me.username}")

    results = []
    test_start = time.time()

    # T1
    t1 = await t1_send_text(client)
    results.append(("T1 tekst wysłany na test-bot-inwestor", True))

    # T2
    t2 = await t2_send_image(client)
    results.append(("T2 obrazek wysłany na test-bot-inwestor", True))

    # T3
    t3_ok = await t3_sanity_staging(client, t1, t2)
    results.append(("T3 sanity: T1+T2 widoczne na test-bot-inwestor", t3_ok))

    # T4 + T5
    fwd = await t4_t5_forward_to_output(client, t1, t2)
    results.append(("T4/T5 forward: wiadomości dotarły na recive-bot-investor", fwd["ok"]))

    # T6 + T7
    ai_ok = await t6_t7_ai_response(client, fwd, test_start)
    results.append(("T6/T7 AI odpowiedział na recive-bot-investor", ai_ok))

    print_summary(results)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
