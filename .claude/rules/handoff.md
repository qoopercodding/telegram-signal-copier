# Handoff — aktualny stan projektu

Ostatnia aktualizacja: 2026-04-27

## Co zostało zrobione (sesja 2026-04-26/27)

### Refaktor architektury (commit 94b68ef)
- Przeniesiono CAŁĄ logikę kanałów z monitor_bot do listener.py (userbot)
- monitor_bot = tylko komendy DM admina
- listener.py = 3 handlery + 2 poll loops + IPC fetch loop

### Fix: broadcast channel events (commit 049f4af)
- Dodano `get_entity()` dla wszystkich kanałów przy starcie (ciepły PTS cache)
- Dodano `_staging_poll_loop()` co 15s dla test-bot-inwestor

### Fix: wiadomości Marcina ignorowane (commit e6674fa)
- `msg.out = True` blokował pytania Marcina (ten sam konto co userbot)
- Zastąpiono `_bot_sent_ids` set + helper `_send_to_raw()`
- Dodano `_output_poll_loop()` co 15s dla recive-bot-investor

### Testy E2E (commit 55135a6 + 06bcd6b)
- `test_pipeline.py` — realne wiadomości na Telegramie, widoczne w aplikacji
- 5/5 testów przechodzi: T1(tekst staging) T2(obraz staging) T3(sanity) T4/T5(AI forward) T6/T7(AI Q&A)

## Aktualny stan (2026-04-27)

**Działa:**
- Pipeline: Damian IKE/IKZE → test-bot-inwestor → AI → recive-bot-investor ✅
- AI Q&A na recive-bot-investor (pytania Marcina) ✅
- Analiza zdjęć na recive-bot-investor ✅
- /fetch IKE N / /fetch IKZE N ✅
- Advisor: "X PLN" → ile sztuk kupić ✅
- Testy 5/5 ✅

**Co dalej (TODO):**
1. Debugging aktywny — Marcin zgłosił problemy (patrz niżej)
2. Portfolio snapshot history (tabela portfolio_snapshots)
3. Web dashboard (Streamlit) — historia sygnałów
4. Testy jednostkowe parser.py i prices.py
5. Bankier.pl jako 3. źródło cen

## Aktywne problemy do debugowania

*Uzupełnij przed nową sesją debugowania:*
- [ ] Opisać konkretny objaw
- [ ] Dodać log snippet z błędem
- [ ] Wskazać plik:linia gdzie szukać

## Jak szybko sprawdzić stan

```bash
# Logi na żywo
sudo journalctl -u signal-copier -f

# Testy E2E
source venv/bin/activate && python test_pipeline.py

# Status serwisów
sudo systemctl status signal-copier signal-monitor
```
