# HANDOFF — telegram-signal-copier

> Ten dokument jest przeznaczony dla AI przejmującego pracę.
> Zaktualizowany: 2026-04-25. Autor: Claude Sonnet 4.6.

---

## 1. Co to jest i po co

System kopiuje sygnały inwestycyjne od tradera **Damiana** (prywatna grupa Telegram)
do kanału właściciela. Pipeline:

```
[Damian — prywatna grupa / IKE(8951) / IKZE(8953)]
        ↓ listener.py — _damian_handler (Telethon, event)
        ↓ listener.py — _process_damian_msg()  ← bezpośrednia analiza AI
[recive-bot-investor] (output, RAW_CHANNEL_ID)
        ↓ notifier.py (bot API, z etykietą [IKE] lub [IKZE])
[Marcin dostaje powiadomienie z przyciskami AKCEPTUJ/ODRZUĆ]

Komenda /fetch IKE N (na recive-bot-investor):
[_fetch_handler] → iter_messages(Damian group) → _process_damian_msg() × N → powiadomienia
```

Użytkownik: **Marcin** — inwestor indywidualny, GPW. Portfolio: 100 000 PLN.

---

## 2. WAŻNA ZMIANA ARCHITEKTURY (2026-04-25)

**Poprzednio (STARE — nie działa):**
- `damian_watcher.py` jako osobny proces forwadował wiadomości do `test-bot-inwestor`
- `listener.py` nasłuchiwał `test-bot-inwestor` i analizował forwarded wiadomości
- **Bug**: Telethon nie odpala `NewMessage` eventów dla wiadomości forwardowanych przez ten sam klient → AI nigdy się nie uruchamiało

**Teraz (AKTUALNE — działa):**
- Cała logika Damiana **zintegrowana w `listener.py`** (jeden proces, jedna sesja)
- `_damian_handler` reaguje na live wiadomości z grupy Damiana → `_process_damian_msg()`
- `_fetch_handler` reaguje na `/fetch IKE N` → `iter_messages()` → `_process_damian_msg()`
- `_process_damian_msg()` robi: download_media → analyze_message(Gemini) → save_ai_analysis → send_signal_notification
- `damian_watcher.py` **istnieje ale nie jest uruchamiany** — służy tylko jako biblioteka (eksportuje `is_watched_topic`, `get_topic_id`, `TOPIC_NAMES`, `parse_fetch_command`)

---

## 3. Pliki — co robi każdy

| Plik | Rola |
|------|------|
| `src/config.py` | Pydantic Settings — ładuje `.env` |
| `src/listener.py` | **GŁÓWNY** — Telethon userbot, nasłuchuje test-bot-inwestor + grupę Damiana, AI pipeline |
| `src/damian_watcher.py` | Biblioteka pomocnicza — helpers (`is_watched_topic`, `parse_fetch_command`) + `login_via_channel()` |
| `src/parser.py` | Gemini AI — klasyfikuje wiadomości, waliduje tickery GPW |
| `src/notifier.py` | Bot API — wysyła powiadomienia z przyciskami, buy list (ile sztuk @ kurs) |
| `src/monitor_bot.py` | Bot Telethon — `/status`, `/portfolio`, `/advisor`, heartbeat, relay SMS kodów |
| `src/prices.py` | Kursy akcji — yfinance (.WA) + stooq.pl fallback, 40+ aliasów GPW |
| `src/storage.py` | SQLite — tabele: messages, ai_analyses, signals, trader_positions |
| `src/models.py` | Pydantic modele |

---

## 4. Klucze i ID

```dotenv
# .env (NIE commitować)
TELEGRAM_API_ID=36661880
TELEGRAM_API_HASH=f849584c847a5a892abd2f683838c76a
SOURCE_GROUP_ID=-1003728819658      # test-bot-inwestor (staging — już mniej używany)
RAW_CHANNEL_ID=-1003925454327       # recive-bot-investor (output — główny kanał)
BOT_TOKEN=8729025942:AAE...
GEMINI_API_KEY=AQ.Ab8RN6JV...
MY_PORTFOLIO_SIZE=100000

DAMIAN_GROUP_ID=-1001548727545
DAMIAN_IKE_TOPIC_ID=8951
DAMIAN_IKZE_TOPIC_ID=8953
USERBOT_PHONE=+48737132141
```

**Sesje Telethon:**
- `signal_copier.session` — `listener.py` (serwis `signal-copier`)
- `monitor_bot.session` — `monitor_bot.py` (serwis `signal-monitor`)
- `damian_watcher.session` — NIE UŻYWANA (zrezygnowano z osobnego procesu)

---

## 5. Serwisy systemd

```bash
sudo systemctl status signal-copier    # listener.py — główny pipeline
sudo systemctl status signal-monitor   # monitor_bot.py — komendy, heartbeat

# Restart po zmianach kodu:
echo '<sudo_password>' | sudo -S systemctl restart signal-copier signal-monitor
```

---

## 6. Stan — co działa, co nie

### ✅ Działa
- `listener.py` — pełny pipeline: test-bot-inwestor + Damian IKE/IKZE → AI → powiadomienia
- `_damian_handler` — live watch na grupę Damiana (IKE/IKZE), bezpośrednia analiza AI
- `_fetch_handler` — `/fetch IKE N` na recive-bot-investor → analiza N wiadomości → powiadomienia z `[IKE]`/`[IKZE]` etykietą
- `parser.py` — Gemini 2.5 Flash, walidacja tickerów, portfel 100k PLN w prompcie
- `notifier.py` — zdjęcia + tekst, buy list (sztuki × kurs), przyciski AKCEPTUJ/ODRZUĆ
- `prices.py` — yfinance + stooq fallback, aliasy GPW
- `monitor_bot.py` — `/status`, `/portfolio`, `/advisor`, relay SMS kodów

### ⚠️ Znane ograniczenia
- Gemini 2.5 Flash bywa wolny (~10-30s/wiadomość) — `/fetch IKE 10` zajmuje ~2-5 min
- yfinance bywa niedostępny dla małych spółek GPW → stooq.pl fallback
- Callback AKCEPTUJ/ODRZUĆ loguje ale nie egzekwuje zlecenia (brak brokera API)

### ❌ TODO / Nie zrobione
- Integracja z brokerem (XTB, eMakler) — automatyczna realizacja sygnałów
- `/fetch` z datą (np. `/fetch IKE od 2026-04-01`) — teraz tylko ostatnie N
- Web dashboard (Streamlit / Grafana) — podgląd portfela i historii sygnałów
- Testy jednostkowe dla `parser.py` i `prices.py`

---

## 7. Architektura decyzyjna

### Routing powiadomień
- `DECISION_CHAT_ID` ustawiony → powiadomienia tam
- fallback: `RAW_CHANNEL_ID` (recive-bot-investor)

### Ticker normalizacja
- AI zwraca długie nazwy (CDPROJEKT, CYFRPLSAT) → `prices.py._GPW_MAP` normalizuje do symboli (CDR, CPS)
- `parser.py._GPW_KNOWN` = szybka whitelist bez yfinance
- Nieznany ticker → confidence obniżone do 0.1

### Relay kodów SMS (jednorazowe — dla nowej sesji)
- `monitor_bot.py` obserwuje `recive-bot-investor` przez Telethon
- Widzi wiadomość z 5-6 cyframi → zapisuje do `/tmp/.damian_auth_code`
- `login_via_channel()` w `damian_watcher.py` czyta ten plik
- Kod musi być wysłany **ze spacjami** (`4 8 4 2 7`) — Telegram auto-unieważnia zwarte 5-cyfry

---

## 8. Git push

Token zapisany w remote URL — `git push origin main` działa bez hasła.
Remote: `https://github.com/qoopercodding/telegram-signal-copier.git`
Token ważny ~90 dni od 2026-04-24.

---

## 9. Jak testować

```bash
cd /home/marcin/telegram-signal-copier
source venv/bin/activate

# Import check
python -c "from src.listener import main; print('OK')"

# Test parsefera komend
python -c "from src.damian_watcher import parse_fetch_command; print(parse_fetch_command('/fetch IKE 5'))"

# Logi live
sudo journalctl -u signal-copier -f
sudo journalctl -u signal-monitor -f
```

**Test end-to-end:** Wyślij `/fetch IKE 3` na kanale `recive-bot-investor`.
Oczekiwany flow:
1. Bot odpowiada "⏳ Pobieram i analizuję 3 wiadomości z IKE..."
2. Gemini analizuje każdą wiadomość (~10-30s każda)
3. Dla `TRADE_ACTION`/`PORTFOLIO_UPDATE` z confidence ≥ 0.6 → powiadomienie z `[IKE]`
4. Bot odpowiada "✅ Przeanalizowano 3 wiadomości z IKE"

---

## 10. Znane problemy i obejścia

| Problem | Rozwiązanie |
|---------|-------------|
| Telethon nie odpala eventów dla własnych wiadomości outgoing | Przetwarzaj bezpośrednio, nie przez forward pipeline |
| Gemini rate limit 429 | `parser.py` retry 3× z backoffem 15/30/45s, fallback na `gemini-2.0-flash-001` |
| Kurs GPW niedostępny w yfinance | `prices.py` stooq.pl fallback |
| Kod SMS auto-unieważniany przez Telegram | Wysyłaj ze spacjami: `4 8 4 2 7` |
| `sqlite3.OperationalError: database is locked` | Nie uruchamiaj dwóch procesów z tą samą sesją `.session` |
