# telegram-signal-copier — Claude Code context

System kopiowania sygnałów inwestycyjnych z Telegrama (GPW).
Właściciel: Marcin — inwestor indywidualny, 100k PLN, nie programista.
Komunikacja: po polsku, krótko i konkretnie.

## Uruchamianie

```bash
source venv/bin/activate

# Serwisy produkcyjne (systemd)
sudo systemctl status signal-copier signal-monitor
sudo journalctl -u signal-copier -n 50 --no-pager

# Restart
echo '12345678' | sudo -S systemctl restart signal-copier signal-monitor

# Testy E2E (realne wiadomości na Telegramie)
python test_pipeline.py
```

## Kluczowe pliki

| Plik | Rola |
|------|------|
| `src/listener.py` | Główny userbot — 3 handlery + 2 poll loops |
| `src/monitor_bot.py` | Bot API — tylko komendy DM admina |
| `src/parser.py` | AI Gemini — analiza wiadomości |
| `src/notifier.py` | Wysyłka wyników na recive-bot-investor |
| `src/damian_watcher.py` | Helper — IKE/IKZE topic detection |
| `src/prices.py` | Kursy GPW (stooq → yfinance) |
| `src/storage.py` | SQLite — wiadomości, AI wyniki, pozycje |
| `src/config.py` | Settings z .env (pydantic-settings) |
| `test_pipeline.py` | E2E test — wysyła realne wiadomości na TG |
| `.env` | Sekrety — API keys, channel IDs |

## Workflow

Po każdej zmianie kodu: `git commit + git push origin main`
GitHub: https://github.com/qoopercodding/telegram-signal-copier.git

## Reguły szczegółowe

- `.claude/rules/architecture.md` — pipeline, architektura, kluczowe decyzje
- `.claude/rules/channels.md` — ID kanałów, sesje, .env
- `.claude/rules/debugging.md` — znane pułapki, naprawione bugi, wzorce
- `.claude/rules/handoff.md` — co zrobiono, aktualny stan, co dalej
