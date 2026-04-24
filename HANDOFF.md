# HANDOFF — telegram-signal-copier

> Ten dokument jest przeznaczony dla AI przejmującego pracę.
> Zaktualizowany: 2026-04-24. Autor: Claude Sonnet 4.6.

---

## 1. Co to jest i po co

System kopiuje sygnały inwestycyjne od tradera **Damiana** (prywatna grupa Telegram)
do kanału właściciela. Pipeline:

```
[Damian — prywatna grupa / IKE / IKZE]
        ↓ damian_watcher.py (NOWY — forward live)
[test-bot-inwestor] (staging, SOURCE_GROUP_ID)
        ↓ listener.py (AI analiza, SQLite)
[recive-bot-investor] (output, RAW_CHANNEL_ID)
        ↓ notifier.py (bot API)
[Użytkownik (Marcin) dostaje powiadomienie]
```

Użytkownik: **Marcin** — inwestor indywidualny, GPW (giełda polska). Portfolio: 100 000 PLN.

---

## 2. Pliki — co robi każdy

| Plik | Rola |
|------|------|
| `src/config.py` | Pydantic Settings — ładuje `.env` |
| `src/listener.py` | Telethon userbot — nasłuchuje test-bot-inwestor, wywołuje AI, notifier |
| `src/damian_watcher.py` | **NOWY** Telethon userbot — nasłuchuje IKE/IKZE, forwarduje do test-bot-inwestor |
| `src/parser.py` | Gemini AI — klasyfikuje wiadomości (TRADE_ACTION / PORTFOLIO_UPDATE / OTHER) |
| `src/notifier.py` | Bot API — wysyła powiadomienia na recive-bot-investor (z przyciskami AKCEPTUJ/ODRZUĆ) |
| `src/monitor_bot.py` | Bot API — heartbeat, /status, /portfolio, /advisor |
| `src/prices.py` | Pobieranie kursów GPW — yfinance (.WA) + stooq.pl fallback |
| `src/storage.py` | SQLite — wiadomości, AI analizy, pozycje tradera |
| `src/models.py` | Pydantic modele (Signal, itp.) |

---

## 3. Klucze i ID — co gdzie

```dotenv
# .env (NIE commitować)
TELEGRAM_API_ID=36661880
TELEGRAM_API_HASH=f849584c847a5a892abd2f683838c76a
SOURCE_GROUP_ID=-1003728819658      # test-bot-inwestor (staging)
RAW_CHANNEL_ID=-1003925454327       # recive-bot-investor (output)
BOT_TOKEN=8729025942:AAE...         # bot Decision / Monitor
GEMINI_API_KEY=AQ.Ab8RN6JV...
MY_PORTFOLIO_SIZE=100000

# Damian's group
DAMIAN_GROUP_ID=-1001548727545
DAMIAN_IKE_TOPIC_ID=8951
DAMIAN_IKZE_TOPIC_ID=8953
DAMIAN_SESSION_NAME=damian_watcher
```

**Kanały Telegram:**
- `test-bot-inwestor`: `-1003728819658` — staging, tu trafiają forwarded wiadomości
- `recive-bot-investor`: `-1003925454327` — output, tu Marcin widzi analizy AI

**Sesje Telethon:**
- `signal_copier.session` — używana przez `listener.py`
- `damian_watcher.session` — używana przez `damian_watcher.py` (OSOBNA — muszą być rozdzielone!)

---

## 4. Serwisy systemd (na VM)

```bash
sudo systemctl status signal-copier    # listener.py
sudo systemctl status signal-monitor   # monitor_bot.py
# damian-watcher.service — JESZCZE NIE WDROŻONY (TODO)
```

Restart po zmianach:
```bash
echo '<sudo_password>' | sudo -S systemctl restart signal-copier signal-monitor
```

---

## 5. Stan implementacji — co jest gotowe, co nie

### ✅ Gotowe i działające
- `listener.py` — pełny pipeline (Telethon → AI → SQLite → notifier)
- `parser.py` — Gemini AI klasyfikacja, walidacja tickerów GPW, `portfolio_positions` w JSON
- `notifier.py` — wysyłanie foto + tekstu, buy list (ile sztuk @ kurs), przyciski AKCEPTUJ/ODRZUĆ
- `prices.py` — 40+ aliasów GPW, yfinance + stooq.pl fallback
- `storage.py` — tabele: messages, ai_analyses, signals, trader_positions
- `monitor_bot.py` — `/status`, `/portfolio`, `/advisor <kwota>`, heartbeat
- `damian_watcher.py` — **napisany, nie uruchomiony jeszcze**

### ❌ TODO — jeszcze nie zrobione
1. **Pierwsze uruchomienie `damian_watcher.py`** na VM:
   ```bash
   source venv/bin/activate
   python -m src.damian_watcher
   # Poprosi o tel + SMS → stworzy damian_watcher.session
   ```
2. **Systemd service dla damian_watcher** — plik w repozytorium nie istnieje, wzór w `planTelethon.md` sekcja 7
3. **Test komendy `/fetch IKE 5`** — wpisać na recive-bot-investor, sprawdzić czy działa
4. **Callback AKCEPTUJ/ODRZUĆ** w `monitor_bot.py` — czy faktycznie coś robi po kliknięciu?

---

## 6. Architektura decyzyjna (ważne!)

### Routing wiadomości
- Jeśli `DECISION_CHAT_ID` ustawiony → powiadomienia idą tam
- Jeśli nie → fallback na `RAW_CHANNEL_ID` (recive-bot-investor)
- **Nie używać `.admin_chat_id`** — to był bug, już naprawiony

### Duplikaty wiadomości — UWAGA
- `listener.py` **NIE forwarduje** surowych wiadomości (był bug — usunięto)
- Jedynym źródłem wiadomości na recive-bot-investor jest `notifier.py`
- `damian_watcher.py` forwarduje tylko do **test-bot-inwestor** (staging), nie do output

### Gemini AI model
- Używa: `gemini-2.5-flash` (REST API przez httpx, nie SDK)
- Endpoint: `https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent`
- Poprzedni model `gemini-2.0-flash-001` już nie działa

### Ticker normalizacja
- AI zwraca długie nazwy (CDPROJEKT, CYFRPLSAT, SYNEKTIK)
- `prices.py` ma mapę `_GPW_MAP` → normalizuje do symboli GPW (CDR, CPS, SNT)
- yfinance szuka `{symbol}.WA` (giełda warszawska)

---

## 7. Jak git push

Credentials zapisane w remote URL (token GitHub):
```bash
git push origin main
```
Działa bez pytania o hasło. Token ważny ~90 dni od 2026-04-24.

Remote: `https://github.com/qoopercodding/telegram-signal-copier.git`

---

## 8. Jak testować lokalnie

```bash
cd /home/marcin/telegram-signal-copier
source venv/bin/activate

# Sprawdź import
python -c "from src.damian_watcher import parse_fetch_command; print('OK')"

# Uruchom listener (wymaga sesji signal_copier.session)
python -m src.listener

# Uruchom watcher Damiana (wymaga sesji damian_watcher.session)
python -m src.damian_watcher
```

---

## 9. Znane problemy i obejścia

| Problem | Rozwiązanie |
|---------|-------------|
| Kurs akcji niedostępny w yfinance | `prices.py` ma fallback stooq.pl |
| AI zwraca zmyśloną spółkę | `parser.py` waliduje ticker przez yfinance, obniża confidence do 0.1 |
| Dwie sesje Telethon na tym samym pliku .session | `damian_watcher` używa `damian_watcher.session` |
| `gemini-2.0-flash-001` niedostępny | Zmieniono na `gemini-2.5-flash` |
| Portfolio buy list pokazuje "0 szt." | Naprawiono — przy zerze pokazuje min. kwotę |

---

## 10. Następne kroki (priorytet)

1. Uruchomić `damian_watcher.py` na VM (pierwsze logowanie sesja SMS)
2. Test: `/fetch IKE 5` na recive-bot-investor
3. Stworzyć `damian-watcher.service` i wdrożyć systemd
4. Sprawdzić działanie callback przycisków AKCEPTUJ/ODRZUĆ
