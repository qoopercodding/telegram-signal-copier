# Podsumowanie Projektu: Telegram Signal Copier

## 🎯 1. Zakres prac i co już zrobiliśmy

Projekt ma na celu stworzenie zautomatyzowanego bota kopiującego sygnały inwestycyjne z kanału tradera, analizującego je przy pomocy AI oraz przesyłającego gotowe sygnały do prywatnego kanału decyzyjnego.

### Zrealizowane Komponenty:
1.  **Architektura i Konfiguracja (`src/config.py`, `.env`)**
    *   Szkielet projektu oparty na Pydantic.
    *   Konfiguracja wrażliwych danych (klucze API, ID kanałów) w ukrytym pliku `.env`.
2.  **Listener Telethon (`src/listener.py`)**
    *   Bot działa w trybie "Userbot", nasłuchując oryginalnego kanału tradera.
    *   Błyskawicznie przechwytuje tekst oraz pobiera załączniki (screenshoty, zdjęcia).
    *   Automatycznie "forwarduje" przechwyconą wiadomość na podany przez nas kanał testowy.
3.  **Lokalna Baza Danych (`src/storage.py`)**
    *   Baza SQLite (`signals.db`) śledzi każdą wiadomość (zabezpieczenie przed dublami).
    *   Archiwizuje ścieżki do pobranych mediów i docelowe ID sforwardowanych wiadomości.
    *   Przechowuje historię wniosków wyciągniętych przez AI (tabela `ai_analyses`).
4.  **Bot Monitorujący (`src/monitor_bot.py`)**
    *   Niezależny proces bota Telegramowego do zarządzania.
    *   Dostarcza komendy: `/status`, `/logs`, `/disk`, `/health`, `/cleanup`.
    *   Sprawdza plik `.heartbeat` od listenera. Jeśli listener zawiesi się na dłużej niż 30 minut, wysyła ostrzeżenie.
5.  **AI Parser (Google Gemini) (`src/parser.py`)**
    *   Omija błędy starszych bibliotek Google SDK, łącząc się bezpośrednio przez REST API (`httpx`).
    *   Skanuje pobrane zdjęcia i tekst w poszukiwaniu sygnałów (KUP/SPRZEDAJ), tickerów giełdowych (np. XTB, PKN) i cen.
6.  **Wdrożenie 24/7 (Ubuntu VM)**
    *   Serwer został skonfigurowany.
    *   Stworzono dwie usługi `systemd` (`signal-copier` oraz `signal-monitor`), które startują z systemem i samoczynnie wstają po ewentualnym crashu.

---

## 📍 2. Gdzie jesteśmy (Obecny Status)

*   **Pipelining podstawowy:** ✅ Działa idealnie w tle na VM. Wiadomości są wyłapywane w ułamku sekundy, media są pobierane, a całość leci na nasz kanał zapasowy.
*   **Moduł AI:** 🔄 W trakcie integracji. Logika jest zaimplementowana, ale napotkaliśmy przejściowe trudności z limitami platform Google (tzw. Rate Limits i problemy autoryzacyjne).
*   **Moduł Decyzyjny (Decision Bot):** ⏳ Czeka na realizację (przyciski ACCEPT / REJECT).
*   **Moduł Kalkulatora Wielkości Pozycji:** ⏳ Czeka na realizację.

---

## ⚠️ 3. Bolączki i napotkane problemy

1.  **Limity API AI (Rate Limits & Quota):**
    *   *Problem:* Nowe klucze darmowe (AI Studio) mają restrykcyjne limity ilości zapytań (szczególnie na tzw. nowych kontach), co skutkowało błędami `429 (Too Many Requests)`.
    *   *Problem:* Dodawanie klucza przez Google Cloud Console (z podpiętym billingiem) skutkowało z kolei błędami braku uprawnień (`403 API_KEY_SERVICE_BLOCKED`) przez użycie przestarzałego pakietu `google.generativeai` (SDK zmuszało do korzystania ze starego backendu).
    *   *Rozwiązanie:* Przebudowa parsera, by korzystał wprost z REST API i uwzględniał "exponential backoff" (celowe odczekanie i ponowienie zapytania po zderzeniu z limitem).
2.  **Sesje Telethona:**
    *   Uruchamianie Telethona bez uprzedniego poprawnego zamknięcia pliku `.session` może powodować problemy blokady bazy danych (SQLite database is locked). Używamy `systemd`, co łagodzi ten problem dzięki procesom w tle, ale trzeba uważać przy ręcznych restartach.

---

## 🛤️ 4. Dalsze Kroki

1.  **Zatwierdzenie stabilności AI (Iteracja 3):**
    *   Teraz, z nowym kluczem z Google Cloud z podpiętą kartą i nowym kodem omijającym SDK, chcemy sprawdzić stabilne działanie analizy screenshotów.
2.  **Budowa interaktywnego Decision Bota (Iteracja 4):**
    *   Zamiast "tępego" forwardowania wiadomości, zrobimy tak, aby bot wysyłał na Twój główny czat ładne podsumowanie:
        *"Trader kupuje 200 XTB za 55 PLN. Według Twojego kapitału powinieneś wejść za X PLN."*
    *   Pod wiadomością będą 2 przyciski: `✅ AKCEPTUJ` oraz `❌ ODRZUĆ`.
3.  **Kalkulator wielkości pozycji (Iteracja 5):**
    *   Na podstawie podanego w konfiguracji `MY_PORTFOLIO_SIZE`, bot będzie obliczał jaką wartość kapitału należy zaangażować, by odpowiednio naśladować zarządzanie ryzykiem tradera.

---

## 🔄 5. Alternatywy dla Modułu AI (jeśli Gemini nadal będzie sprawiać problemy)

Jeśli Google wciąż będzie z jakiegoś powodu odrzucać żądania (nawet z przypiętym billingiem), mamy proste alternatywy, które łatwo podpiąć pod obecną architekturę:

1.  **OpenAI (GPT-4o-mini):**
    *   *Plusy:* Bardzo stabilne API, rewelacyjna analiza obrazu (Vision), nie sypie fałszywymi błędami Rate Limit. Tanie do prostych i średnich zadań.
    *   *Minusy:* Wymaga podpięcia karty na start (minimum doładowania to np. 5$).
2.  **Mistral (Pixtral-12B):**
    *   *Plusy:* Najtańsza z płatnych opcji. Bardzo solidna do wyciągania sztywnych danych w JSON.
    *   *Minusy:* Analiza obrazów (Vision) bywa ciut słabsza niż u Google czy OpenAI na złożonych zrzutach ekranu platform giełdowych.
3.  **Anthropic (Claude 3.5 Sonnet/Haiku):**
    *   *Plusy:* Świetnie sobie radzi ze złożonymi interfejsami, tabelami giełdowymi (potrafi doskonale wyciągać dane ze screenshotów portfeli maklerskich).
    *   *Minusy:* Nieco wyższy koszt niż w przypadku Gemini 1.5 Flash.
