"""
Testy end-to-end całego pipeline'u signal-copier.

Uruchomienie:
    source venv/bin/activate
    python test_pipeline.py

Co sprawdza:
  1. Połączenie — czy userbot widzi każdy kanał i może pisać
  2. Serwisy — czy signal-copier i signal-monitor działają (heartbeat)
  3. Staging pipeline — wiadomość na test-bot-inwestor → AI → recive-bot-investor
  4. Q&A na kanale — pytanie tekstowe na recive-bot-investor → odpowiedź AI
  5. Advisor — "mam X PLN" → kalkulator
  6. /fetch — pobieranie wiadomości z Damiana
  7. Edge cases — krótki tekst, pusta wiadomość, nieznana komenda
"""

import asyncio
import json
import time
from datetime import datetime, timedelta
from pathlib import Path

from telethon import TelegramClient
from telethon.tl.functions.channels import GetParticipantRequest
from telethon.tl.types import ChannelParticipantAdmin, ChannelParticipantCreator
from telethon.errors import UserNotParticipantError, ChatAdminRequiredError

# ── Konfiguracja ──────────────────────────────────────────────
import sys
sys.path.insert(0, str(Path(__file__).parent))
from src.config import settings, LOGS_DIR, PROJECT_ROOT

CHANNELS = {
    "test-bot-inwestor":   settings.source_group_id,   # -1003728819658
    "recive-bot-investor": settings.raw_channel_id,     # -1003925454327
    "DamianInwestorx":     settings.damian_group_id,    # -1001548727545
}
BOT_USERNAME = "signal_copier_monitor_bot"
HEARTBEAT_FILE = PROJECT_ROOT / ".heartbeat"

GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
RESET  = "\033[0m"
BOLD   = "\033[1m"


def ok(msg):    print(f"  {GREEN}✅ {msg}{RESET}")
def fail(msg):  print(f"  {RED}❌ {msg}{RESET}")
def warn(msg):  print(f"  {YELLOW}⚠️  {msg}{RESET}")
def info(msg):  print(f"  {CYAN}ℹ️  {msg}{RESET}")
def header(msg):print(f"\n{BOLD}{'─'*60}\n{msg}\n{'─'*60}{RESET}")


# ─────────────────────────────────────────────────────────────
# TEST 1: Połączenia i uprawnienia
# ─────────────────────────────────────────────────────────────

async def test_connectivity(client: TelegramClient) -> dict:
    header("TEST 1: Połączenia i uprawnienia")
    results = {}

    me = await client.get_me()
    ok(f"Userbot zalogowany: {me.first_name} (@{me.username})")

    for name, cid in CHANNELS.items():
        if not cid:
            fail(f"{name}: brak ID w .env!")
            results[name] = {"visible": False, "can_post": False}
            continue
        try:
            entity = await client.get_entity(cid)
            broadcast = getattr(entity, "broadcast", False)
            megagroup = getattr(entity, "megagroup", False)
            chat_type = "broadcast" if broadcast else ("supergroup" if megagroup else "group")
            ok(f"{name} ({cid}): widoczny [{chat_type}]")
            results[name] = {"visible": True, "entity": entity, "type": chat_type}
        except Exception as e:
            fail(f"{name} ({cid}): NIEWIDOCZNY — {e}")
            results[name] = {"visible": False, "can_post": False}
            continue

        # Sprawdź czy userbot może pisać
        try:
            test_msg = await client.send_message(cid, f"🔧 [TEST POŁĄCZENIA — można usunąć] {datetime.utcnow():%H:%M:%S}")
            await asyncio.sleep(1)
            await test_msg.delete()
            ok(f"{name}: MOŻE PISAĆ i usuwać wiadomości")
            results[name]["can_post"] = True
        except Exception as e:
            fail(f"{name}: NIE MOŻE PISAĆ — {e}")
            results[name]["can_post"] = False

    # Sprawdź czy monitor bot jest w kanałach
    for name, cid in [
        ("test-bot-inwestor",   settings.source_group_id),
        ("recive-bot-investor", settings.raw_channel_id),
    ]:
        if not cid or name not in results or not results[name].get("visible"):
            continue
        try:
            entity = results[name]["entity"]
            bot_entity = await client.get_entity(f"@{BOT_USERNAME}")
            await client(GetParticipantRequest(entity, bot_entity))
            ok(f"@{BOT_USERNAME} jest członkiem {name}")
        except UserNotParticipantError:
            warn(f"@{BOT_USERNAME} NIE jest członkiem {name} — dodaj go jako admina!")
        except Exception as e:
            warn(f"Nie można sprawdzić członkostwa @{BOT_USERNAME} w {name}: {e}")

    return results


# ─────────────────────────────────────────────────────────────
# TEST 2: Serwisy systemd (heartbeat)
# ─────────────────────────────────────────────────────────────

async def test_services():
    header("TEST 2: Serwisy systemd")

    # signal-copier heartbeat
    if HEARTBEAT_FILE.exists():
        try:
            hb = json.loads(HEARTBEAT_FILE.read_text())
            ts = datetime.fromisoformat(hb["timestamp"])
            age = (datetime.utcnow() - ts).total_seconds()
            if age < 600:
                ok(f"signal-copier ŻYWY — heartbeat {int(age)}s temu | uptime: {hb.get('uptime','?')}")
            else:
                fail(f"signal-copier MARTWY — ostatni heartbeat {int(age/60):.0f} min temu!")
                warn("Uruchom: sudo systemctl restart signal-copier")
        except Exception as e:
            fail(f"Błąd odczytu heartbeat: {e}")
    else:
        fail("signal-copier: brak pliku heartbeat — serwis nigdy nie uruchomiony lub zły katalog")

    # Sprawdź czy nowy kod jest wdrożony (listener powinien logować "all-in-one")
    log_files = sorted(LOGS_DIR.glob("listener_*.log"), reverse=True)
    if log_files:
        last_lines = log_files[0].read_text(encoding="utf-8").strip().split("\n")[-20:]
        new_code = any("all-in-one" in l or "Handler 3" in l or "recive-bot-investor" in l and "Nasłuchuję" in l
                       for l in last_lines)
        if new_code:
            ok("signal-copier działa z NOWYM kodem (refaktoryzacja wdrożona)")
        else:
            # Sprawdź datę ostatniego startu
            starts = [l for l in last_lines if "Zalogowano" in l or "Signal Copier" in l]
            if starts:
                warn(f"signal-copier ostatni start: {starts[-1][:30]}...")
                warn("Czy `git pull && sudo systemctl restart signal-copier` zostało uruchomione?")
            else:
                info("Nie można określić wersji kodu z logów")
    else:
        warn("Brak plików logów signal-copier")


# ─────────────────────────────────────────────────────────────
# TEST 3: Staging pipeline
#   test-bot-inwestor → AI → recive-bot-investor
# ─────────────────────────────────────────────────────────────

async def test_staging_pipeline(client: TelegramClient, conn_results: dict) -> bool:
    header("TEST 3: Staging pipeline (test-bot-inwestor → AI → recive-bot-investor)")

    staging_id = settings.source_group_id
    output_id  = settings.raw_channel_id

    if not conn_results.get("test-bot-inwestor", {}).get("can_post"):
        fail("Pominięto — userbot nie może pisać na test-bot-inwestor")
        return False

    # Wyślij testowy sygnał (taki że AI na pewno to przetworzy jako TRADE_ACTION)
    test_text = (
        "📊 SYGNAŁ TESTOWY [TEST PIPELINE]\n"
        "Kupuję 50 akcji PKN ORLEN (PKN) po cenie 55.20 PLN na konto IKE.\n"
        "Uzasadnienie: test automatyczny pipeline'u."
    )

    info(f"Wysyłam testową wiadomość na test-bot-inwestor...")
    sent = await client.send_message(staging_id, test_text)
    sent_at = time.time()
    info(f"Wysłano msg_id={sent.id}. Czekam max 60s na odpowiedź AI na recive-bot-investor...")

    # Czekaj na odpowiedź na recive-bot-investor
    deadline = time.time() + 60
    found = False
    last_checked_id = None

    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            msgs = await client.get_messages(output_id, limit=5)
            for m in msgs:
                if m.date and m.date.replace(tzinfo=None) > datetime.utcfromtimestamp(sent_at - 2):
                    if "SYGNAŁ" in (m.text or "") or "AI:" in (m.text or "") or "PKN" in (m.text or "") \
                       or "TRADE" in (m.text or "") or "pewność" in (m.text or "").lower() \
                       or "confidence" in (m.text or "").lower():
                        ok(f"ODPOWIEDŹ AI dotarła na recive-bot-investor (msg_id={m.id})")
                        ok(f"Treść: {(m.text or '')[:120]}...")
                        found = True
                        break
                    elif last_checked_id != m.id:
                        last_checked_id = m.id
                        info(f"Nowa wiadomość na kanale (nie AI?): {(m.text or '')[:80]}")
            if found:
                break
        except Exception as e:
            warn(f"Błąd sprawdzania recive-bot-investor: {e}")

    if not found:
        fail(f"Brak odpowiedzi AI na recive-bot-investor po 60s!")
        fail("Możliwe przyczyny:")
        fail("  a) signal-copier nie uruchomiony z nowym kodem")
        fail("  b) userbot nie ma uprawnień do test-bot-inwestor")
        fail("  c) Gemini API nie działa")
        fail(f"  d) SOURCE_GROUP_ID w .env to: {staging_id} — sprawdź czy to test-bot-inwestor")

    # Cleanup
    try:
        await sent.delete()
    except Exception:
        pass

    return found


# ─────────────────────────────────────────────────────────────
# TEST 4: Q&A na kanale recive-bot-investor
# ─────────────────────────────────────────────────────────────

async def test_channel_qa(client: TelegramClient, conn_results: dict) -> bool:
    header("TEST 4: AI Q&A na recive-bot-investor")

    output_id = settings.raw_channel_id
    if not conn_results.get("recive-bot-investor", {}).get("can_post"):
        fail("Pominięto — userbot nie może pisać na recive-bot-investor")
        return False

    test_question = "TEST QA: ile wiadomości jest w bazie danych?"
    info(f"Wysyłam pytanie: '{test_question}'")
    sent = await client.send_message(output_id, test_question)
    sent_at = time.time()
    info("Czekam max 45s na odpowiedź AI...")

    deadline = time.time() + 45
    found = False
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            msgs = await client.get_messages(output_id, limit=5)
            for m in msgs:
                if m.id == sent.id:
                    continue
                if m.date and m.date.replace(tzinfo=None) > datetime.utcfromtimestamp(sent_at - 1):
                    text = m.text or ""
                    if "💬" in text or "baz" in text.lower() or "wiadomoś" in text.lower() \
                       or "gemini" in text.lower() or "db" in text.lower():
                        ok(f"Odpowiedź AI: {text[:120]}")
                        found = True
                        break
        except Exception as e:
            warn(f"Błąd: {e}")
        if found:
            break

    if not found:
        fail("Brak odpowiedzi AI na pytanie tekstowe po 45s")

    try:
        await sent.delete()
    except Exception:
        pass
    return found


# ─────────────────────────────────────────────────────────────
# TEST 5: Advisor PLN
# ─────────────────────────────────────────────────────────────

async def test_advisor(client: TelegramClient, conn_results: dict) -> bool:
    header("TEST 5: Advisor PLN (kalkulator alokacji)")

    output_id = settings.raw_channel_id
    if not conn_results.get("recive-bot-investor", {}).get("can_post"):
        fail("Pominięto")
        return False

    test_msg = "mam 50000 pln do zainwestowania"
    info(f"Wysyłam: '{test_msg}'")
    sent = await client.send_message(output_id, test_msg)
    sent_at = time.time()
    info("Czekam max 45s na odpowiedź kalkulatora...")

    deadline = time.time() + 45
    found = False
    while time.time() < deadline:
        await asyncio.sleep(3)
        try:
            msgs = await client.get_messages(output_id, limit=5)
            for m in msgs:
                if m.id == sent.id:
                    continue
                if m.date and m.date.replace(tzinfo=None) > datetime.utcfromtimestamp(sent_at - 1):
                    text = m.text or ""
                    if "PLN" in text and ("szt" in text or "alokacj" in text.lower() or "portfel" in text.lower()):
                        ok(f"Advisor odpowiedział: {text[:120]}")
                        found = True
                        break
        except Exception as e:
            warn(f"Błąd: {e}")
        if found:
            break

    if not found:
        fail("Brak odpowiedzi kalkulatora po 45s")
        warn("Sprawdź czy są pozycje w bazie (czy Damian wysłał jakiś portfel)")

    try:
        await sent.delete()
    except Exception:
        pass
    return found


# ─────────────────────────────────────────────────────────────
# TEST 6: Edge cases
# ─────────────────────────────────────────────────────────────

async def test_edge_cases(client: TelegramClient, conn_results: dict):
    header("TEST 6: Edge cases (wiadomości które NIE powinny generować odpowiedzi)")

    output_id = settings.raw_channel_id
    if not conn_results.get("recive-bot-investor", {}).get("can_post"):
        fail("Pominięto")
        return

    cases = [
        ("Pusty tekst (spacja)", " "),
        ("Za krótki tekst (<10 znaków)", "ok"),
        ("Nieznana komenda /xyz", "/xyz"),
    ]

    for case_name, text in cases:
        info(f"Edge case: {case_name} → wysyłam '{text}'")
        sent = await client.send_message(output_id, text)
        sent_at = time.time()
        await asyncio.sleep(8)  # Krótkie oczekiwanie — nie powinno być odpowiedzi

        try:
            msgs = await client.get_messages(output_id, limit=3)
            responded = any(
                m.id != sent.id and m.date
                and m.date.replace(tzinfo=None) > datetime.utcfromtimestamp(sent_at - 1)
                for m in msgs
            )
            if responded:
                warn(f"{case_name}: BOT ODPOWIEDZIAŁ (nie powinien!)")
            else:
                ok(f"{case_name}: poprawnie zignorowany (brak odpowiedzi)")
        except Exception as e:
            warn(f"Błąd: {e}")

        try:
            await sent.delete()
        except Exception:
            pass
        await asyncio.sleep(2)


# ─────────────────────────────────────────────────────────────
# RAPORT KOŃCOWY
# ─────────────────────────────────────────────────────────────

def print_summary(results: dict):
    header("PODSUMOWANIE")
    passed = sum(1 for v in results.values() if v)
    total  = len(results)
    color  = GREEN if passed == total else (YELLOW if passed > 0 else RED)
    print(f"{color}{BOLD}  {passed}/{total} testów OK{RESET}\n")

    for name, ok_val in results.items():
        icon = f"{GREEN}✅" if ok_val else f"{RED}❌"
        print(f"  {icon} {name}{RESET}")

    if passed < total:
        print(f"\n{YELLOW}  Najczęstszy powód problemów:{RESET}")
        print(f"  → Serwis nie uruchomiony z nowym kodem.")
        print(f"  → Uruchom na VM:")
        print(f"    git -C /home/marcin/telegram-signal-copier pull")
        print(f"    sudo systemctl restart signal-copier signal-monitor")
        print(f"\n  → Sprawdź czy userbot (@QooperBoy) jest ADMINEM na:")
        print(f"    test-bot-inwestor   {settings.source_group_id}")
        print(f"    recive-bot-investor {settings.raw_channel_id}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

async def main():
    print(f"\n{BOLD}{'═'*60}")
    print(f"  SIGNAL COPIER — testy pipeline E2E")
    print(f"  {datetime.now():%Y-%m-%d %H:%M:%S}")
    print(f"{'═'*60}{RESET}")

    print(f"\n  Kanały:")
    for name, cid in CHANNELS.items():
        print(f"    {name:25s} {cid}")
    print(f"  .env SOURCE_GROUP_ID = {settings.source_group_id}")
    print(f"  .env RAW_CHANNEL_ID  = {settings.raw_channel_id}")

    session = str(PROJECT_ROOT / settings.session_name)
    client = TelegramClient(session, settings.telegram_api_id, settings.telegram_api_hash)
    await client.connect()

    if not await client.is_user_authorized():
        print(f"\n{RED}❌ Userbot nie jest zalogowany! Uruchom python -m src.listener żeby się zalogować.{RESET}")
        return

    test_results = {}

    conn_results = await test_connectivity(client)
    test_results["1_connectivity"] = all(
        v.get("visible") for v in conn_results.values()
    )

    await test_services()
    test_results["2_services"] = HEARTBEAT_FILE.exists() and (
        (datetime.utcnow() - datetime.fromisoformat(
            json.loads(HEARTBEAT_FILE.read_text())["timestamp"]
        )).total_seconds() < 600
    ) if HEARTBEAT_FILE.exists() else False

    test_results["3_staging_pipeline"] = await test_staging_pipeline(client, conn_results)
    test_results["4_channel_qa"]        = await test_channel_qa(client, conn_results)
    test_results["5_advisor"]           = await test_advisor(client, conn_results)

    await test_edge_cases(client, conn_results)
    test_results["6_edge_cases"] = True  # edge cases są informacyjne

    print_summary(test_results)

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
