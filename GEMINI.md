# Projekt: telegram-signal-copier

## Kim jesteś
Jesteś asystentem deweloperskim Marcina — inwestora indywidualnego na GPW.
Odpowiadaj po polsku. Bądź konkretny i zwięzły.

## Projekt
System kopiowania sygnałów inwestycyjnych od tradera Damiana (prywatna grupa Telegram)
do bota decyzyjnego Marcina. Stack: Python 3.12, Telethon, python-telegram-bot, SQLite,
google-genai, systemd.

**Ścieżka projektu:** `/home/marcin/telegram-signal-copier`
**Venv:** `venv/bin/python` | `source venv/bin/activate`

## Architektura (działające serwisy systemd)
- `signal-copier` → `src/listener.py` — Telethon userbot (@QooperBoy)
  - nasłuchuje: test-bot-inwestor (`-1003728819658`) + grupy Damiana (IKE/IKZE)
  - pipeline: odbiór → AI parser → powiadomienie bota decyzyjnego
- `signal-monitor` → `src/monitor_bot.py` — bot /status, /advisor, heartbeat
- `gemini-bot` → `src/gemini_bot.py` — Gemini 2.5 Pro bot Telegram (@GeminiTelegramixbot)

## ID Telegram
| Zasób | ID |
|---|---|
| test-bot-inwestor (staging) | `-1003728819658` |
| recive-bot-investor (output) | `-1003925454327` |
| Damian — grupa (IKE temat 8951, IKZE temat 8953) | `-1001548727545` |

## Git
- Remote: `https://github.com/qoopercodding/telegram-signal-copier.git`
- Branch: `main`
- `git push origin main` działa bez hasła (token w URL remote)
- Konwencja commitów: `feat:` / `fix:` / `docs:` / `refactor:`

## Sekrety (.env — NIE commituj!)
- `GEMINI_API_KEY` — Google AI Studio
- `TELEGRAM_BOT_TOKEN` — @GeminiTelegramixbot
- Pozostałe klucze: Telethon API, Anthropic, OpenAI — w `.env`

## Zasady pracy
1. Po każdej zmianie: `git add <pliki> && git commit -m "..." && git push origin main`
2. Nigdy nie commituj: `.env`, `*.session`, `db/`, `logs/`, `media/`
3. Używaj venv: `venv/bin/python`, nie systemowego Pythona
4. Serwisy sprawdzaj: `systemctl --user status <nazwa>`
5. Logi: `journalctl --user -u <nazwa> -f`

## Sterowanie przez Telegram (@GeminiTelegramixbot)

Bot Telegram działa jako zdalny terminal do tej VM — wystarczy napisać do bota.

**Jak uruchomić sesję:**
```
# Na VM — odpal Gemini CLI w katalogu projektu:
cd /home/marcin/telegram-signal-copier
gemini
```
Gemini CLI automatycznie wczyta ten plik (GEMINI.md) jako kontekst projektu.

**Komendy które bot Telegram obsługuje (przez Gemini 2.5 Pro):**
- Pisz normalnie po polsku — bot rozumie i wykonuje
- `uruchom: <komenda>` — wykona komendę w terminalu i pokaże wynik
- `edytuj plik: <ścieżka>` — pokaże zawartość i pozwoli edytować
- `git push` / `git status` — operacje git na projekcie
- `status serwisów` — sprawdzi signal-copier, signal-monitor, gemini-bot
- `logi <serwis>` — ostatnie logi danego serwisu

**Ważne adresy:**
- Bot Telegram: @GeminiTelegramixbot
- GitHub: https://github.com/qoopercodding/telegram-signal-copier
- VM: marcin@Ubuntu-2404-noble-amd64-base (SSH jeśli potrzebne)

**Żeby bot mógł wykonywać komendy w terminalu** — zaimplementowano w `src/gemini_bot.py`
przez Function Calling (narzędzie `run_terminal_command`). Bot ma dostęp do powłoki VM 
i może wykonywać komendy typu git, systemctl, ls itp. (tylko dla autoryzowanego Admina).


## TODO (priorytet)
1. Integracja z brokerem XTB API — automatyczna realizacja sygnałów
2. `/fetch IKE od 2026-04-01` — fetch z datą
3. Web dashboard (Streamlit) — historia sygnałów
4. Testy dla `parser.py` i `prices.py`
