# Podsumowanie Projektu: Telegram Signal Copier

## 🎯 1. Zakres prac i zrealizowane komponenty

Projekt to zautomatyzowany pipeline, który nasłuchuje sygnałów inwestycyjnych z jednego kanału, przetwarza je przez AI (Google Gemini), wylicza wielkość pozycji do otwarcia na podstawie wielkości kapitału, a następnie przesyła sformatowany sygnał (z przyciskami decyzyjnymi) do kanału docelowego.

### Docelowy Przepływ (Pipeline):
1. **Źródło:** Wiadomość/Screenshot wpada do kanału **"test-bot-inwestor"**.
2. **Listener (Telethon):** Błyskawicznie przechwytuje tekst oraz pobiera załączniki (screenshoty, zdjęcia). Opcjonalnie forwarduje wiadomość surową jako backup.
3. **Analiza AI (Gemini 2.5-flash):** Moduł wysyła dane do Gemini przez REST API. AI klasyfikuje czy to sygnał, wyciąga akcję (KUP/SPRZEDAJ), ticker (np. XTB), ilość oraz cenę.
4. **Zapis do SQL:** Zapisywane są surowe wiadomości, ścieżki pobranych mediów oraz wynik analizy z AI (zabezpieczenie przed dublami).
5. **Kalkulacja Pozycji:** Na podstawie wyciągniętej ceny, ilości oraz zdefiniowanego `MY_PORTFOLIO_SIZE` bot oblicza jaką część kapitału stanowić będzie dana pozycja.
6. **Decyzja:** Bot (Bot API) wysyła sformatowaną wiadomość do kanału docelowego **"recive-bot-investor"**. Wiadomość posiada wbudowane przyciski `✅ AKCEPTUJ` oraz `❌ ODRZUĆ`. Wysłanie komendy decyzyjnej podmienia wiadomość.

### Zrealizowane Komponenty:
1.  **Szkielet i Konfiguracja:** Oparta na Pydantic (`src/config.py`, `.env`).
2.  **Listener Telethon:** Działa jako userbot, nasłuchuje źródła, pobiera media.
3.  **Baza Danych (SQLite):** Pełny zapis logów sygnałów, ścieżek mediów, analiz AI (`src/storage.py`).
4.  **AI Parser:** Refactor na bezpośrednie REST API (`httpx`) po problemach z SDK. Korzysta z szybkiego modelu `gemini-2.5-flash`. Wdrożone zostały mechanizmy omijające rate limits (retry/fallback).
5.  **Notifier / Decision Bot:** Generowanie sformatowanego alertu i obsługa przycisków za pomocą callbacków (`src/notifier.py`, `src/monitor_bot.py`). Kalkulator procentu kapitału jest integralną częścią generowanej wiadomości.
6.  **Bot Monitorujący:** Komendy diagnostyczne dla admina (`/status`, `/health`, `/disk`, `/logs`).

---

## 📍 2. Obecny Status

*   **Pipelining podstawowy:** ✅ Działa w tle na VM (usługi `systemd`). Wiadomości są łapane i przetwarzane błyskawicznie.
*   **Moduł AI:** ✅ Działa stabilnie. Dzięki nowemu kluczowi z aktywowanym billingiem w Google Cloud wyeliminowano błędy Rate Limits i włączono najwyższe modele (`gemini-2.5-flash`).
*   **Kalkulator Pozycji:** ✅ Zrealizowane. Wbudowany w powiadomienie (wylicza np. "56.40 PLN x 200 = 11280 PLN, co stanowi 11.3% portfela").
*   **Moduł Decyzyjny (Decision Bot):** ✅ Zrealizowane. System posiada przyciski, obsługę zdarzeń po kliknięciu (nadpisywanie treści po decyzji).

---

## 🛤️ 3. Następne kroki / Konfiguracja docelowa

Ostatnim etapem jest wpięcie bota w docelowe kanały:
1. **Ustawienie `SOURCE_GROUP_ID`:** ID dla kanału `"test-bot-inwestor"`.
2. **Dodanie bota na kanał docelowy:** Bot (`@signal_copier_monitor_bot`) musi zostać dodany jako Administrator do kanału `"recive-bot-investor"`, aby mógł tam wysyłać powiadomienia.
3. **Ustawienie `DECISION_CHAT_ID`:** Skonfigurowanie ID kanału `"recive-bot-investor"` w `.env`, aby Notifier wiedział, gdzie słać docelowe sygnały.
4. **Implementacja automatycznych transakcji (Opcjonalnie):** W przyszłości powiązanie przycisku "AKCEPTUJ" np. z API brokera (XTB API) do natychmiastowego egzekwowania zlecenia.
