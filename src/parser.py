"""
AI Parser — analiza wiadomości tradera przez Google Gemini.

Co robi:
  1. Klasyfikuje wiadomość (PORTFOLIO_UPDATE / TRADE_ACTION / COMMENT / UNKNOWN)
  2. Dla TRADE_ACTION: wyciąga action, ticker, quantity, price
  3. Dla PORTFOLIO_UPDATE: wyciąga listę pozycji z screenshota
  4. Obsługuje zarówno tekst jak i zdjęcia (vision)

Użycie:
    from src.parser import analyze_message
    result = await analyze_message(text="Kupiłem 100 XTB", media_paths=[])
"""

import json
from pathlib import Path
from typing import Optional

from google import genai
from google.genai import types
from loguru import logger

from src.config import settings
from src.models import (
    MessageType,
    TradeAction,
    ClassifiedMessage,
    TradeSignal,
)


MODELS_TO_TRY = ["gemini-2.5-flash", "gemini-2.0-flash"]

# Znane tickery GPW — szybka biała lista (bez opóźnienia sieciowego)
_GPW_KNOWN = {
    "XTB", "PKN", "KGHM", "CDR", "PKO", "PZU", "ALE", "DNP", "CCC", "LPP",
    "JSW", "PGE", "PGN", "OPL", "MBK", "SPL", "KGH", "PEO", "TPE", "ING",
    "BHW", "EUR", "PCO", "TEN", "KER", "VRG", "WPL", "ATT", "GPW", "CPS",
}


def _check_ticker_exists(ticker: str) -> bool:
    """Sprawdza czy ticker istnieje na GPW (.WA) lub globalnie przez yfinance."""
    if ticker.upper() in _GPW_KNOWN:
        return True
    try:
        import yfinance as yf
        # Najpierw GPW
        hist = yf.Ticker(f"{ticker}.WA").history(period="5d")
        if not hist.empty:
            return True
        # Potem globalnie
        hist = yf.Ticker(ticker).history(period="5d")
        return not hist.empty
    except Exception:
        return True  # Przy błędzie nie karz


async def _validate_ticker(ticker: str) -> bool:
    """Async wrapper dla _check_ticker_exists z timeoutem 10s."""
    import asyncio
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_check_ticker_exists, ticker),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.warning(f"Timeout walidacji tickera {ticker} — pomijam")
        return True


def get_client() -> genai.Client:
    """Zwraca klienta Gemini (nowe SDK google-genai)."""
    return genai.Client(api_key=settings.gemini_api_key)


# ============================================================
# Prompty
# ============================================================

def _build_classify_prompt() -> str:
    """Buduje prompt z aktualnym rozmiarem portfela użytkownika."""
    portfolio_pln = settings.my_portfolio_size
    portfolio_note = (
        f"Portfel użytkownika do skalowania: {portfolio_pln:,.0f} PLN. "
        f"Dla PORTFOLIO_UPDATE — w polu 'summary' wylicz proporcjonalne kwoty dla każdej pozycji "
        f"(format: 'TICKER X% → Y PLN z Twojego portfela').\n\n"
    )
    return portfolio_note + CLASSIFY_PROMPT


CLASSIFY_PROMPT = """Jesteś asystentem analizującym wiadomości z kanału tradera na polskiej giełdzie (GPW).

Twoim zadaniem jest:
1. Sklasyfikować wiadomość
2. Wyciągnąć sygnał tradingowy (jeśli jest)

TYPY WIADOMOŚCI:
- TRADE_ACTION — trader kupuje/sprzedaje/dodaje/redukuje pozycję
- PORTFOLIO_UPDATE — screenshot lub tekst z aktualnym stanem portfela
- TRANSACTION_HISTORY — tabela transakcji z brokera
- COMMENT — komentarz, opinia, bez akcji
- UNKNOWN — nie da się sklasyfikować

ODPOWIEDZ W FORMACIE JSON (TYLKO JSON, bez markdown):
{
    "message_type": "TRADE_ACTION | PORTFOLIO_UPDATE | COMMENT | UNKNOWN",
    "confidence": 0.0-1.0,
    "summary": "krótkie streszczenie co zrozumiałeś",
    "trade_signal": {
        "action": "BUY | SELL | CLOSE | REDUCE | ADD | null",
        "ticker": "symbol akcji lub null",
        "quantity": liczba lub null,
        "price": liczba lub null,
        "reason": "dlaczego tak interpretujesz"
    },
    "portfolio_positions": [
        {"ticker": "XTB", "percentage": 86.64, "value_pln": 1335500}
    ]
}

WAŻNE ZASADY:
- Jeśli nie jesteś pewien, ustaw confidence < 0.5
- NIE ZGADUJ — jeśli nie ma jasnego sygnału, daj null
- Tickery na GPW: np. XTB, PKN, KGHM, CDR, PKO, PZU, ALE, DNP, CCC, LPP
- Trader może pisać po polsku: "kupiłem", "dokupiłem", "sprzedałem", "zamknąłem"
- "Dobieram" = ADD, "Redukcja" = REDUCE, "Zamykam pozycję" = CLOSE
- Jeśli jest screenshot — opisz co widzisz (portfel, transakcje, chart)
- Dla PORTFOLIO_UPDATE: w portfolio_positions wypisz WSZYSTKIE pozycje z tickerem i % udziału (oraz value_pln jeśli widoczna). Dla innych typów wiadomości: portfolio_positions = []

WIADOMOŚĆ DO ANALIZY:
"""


# ============================================================
# Główna funkcja parsera
# ============================================================

async def analyze_message(
    text: Optional[str] = None,
    media_paths: Optional[list[str]] = None,
) -> dict:
    """
    Analizuje wiadomość tradera — tekst i/lub zdjęcia.
    Próbuje kolejne modele jeśli rate limit.

    Returns:
        Dict z kluczami: message_type, confidence, summary, trade_signal
    """
    import asyncio

    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY nie ustawiony — pomijam analizę AI")
        return {
            "message_type": "UNKNOWN",
            "confidence": 0.0,
            "summary": "AI niedostępne — brak API key",
            "trade_signal": None,
        }

    # Buduj content_parts (nowe SDK: types.Part)
    prompt = _build_classify_prompt()
    if text:
        prompt += f"\n\nTEKST: {text}"
    else:
        prompt += "\n\nTEKST: (brak tekstu — tylko media)"

    content_parts: list = [types.Part.from_text(text=prompt)]

    # Dodaj zdjęcia (vision)
    if media_paths:
        for path_str in media_paths:
            path = Path(path_str)
            if path.exists() and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                try:
                    image_data = path.read_bytes()
                    mime_type = {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".webp": "image/webp",
                    }.get(path.suffix.lower(), "image/jpeg")
                    content_parts.append(types.Part.from_bytes(data=image_data, mime_type=mime_type))
                    logger.debug(f"📷 Dodano obraz do analizy: {path.name}")
                except Exception as e:
                    logger.error(f"Błąd ładowania obrazu {path}: {e}")

    ai_client = get_client()

    # Próbuj modele po kolei (fallback przy rate limit)
    for model_name in MODELS_TO_TRY:
        for attempt in range(3):  # Max 3 próby per model
            try:
                response = await ai_client.aio.models.generate_content(
                    model=model_name,
                    contents=content_parts,
                )
                raw_response = (response.text or "").strip()
                if not raw_response:
                    logger.warning(f"⚠️ Pusta odpowiedź od {model_name} — pomijam")
                    continue

                # Wyczyść response — Gemini czasem zwraca ```json ... ```
                if raw_response.startswith("```"):
                    raw_response = raw_response.split("\n", 1)[1]
                    raw_response = raw_response.rsplit("```", 1)[0]
                    raw_response = raw_response.strip()

                result = json.loads(raw_response)

                # Walidacja tickera — kara za zmyślone spółki
                if result.get("message_type") == "TRADE_ACTION":
                    ts = result.get("trade_signal") or {}
                    ticker = ts.get("ticker")
                    if ticker:
                        valid = await _validate_ticker(ticker)
                        if not valid:
                            logger.warning(f"⚠️ Ticker {ticker} nie znaleziony — obniżam confidence do 0.1")
                            result["confidence"] = 0.1
                            result["summary"] = f"[NIEZNANY TICKER: {ticker}] " + result.get("summary", "")

                logger.info(
                    f"🤖 AI ({model_name}): {result.get('message_type', '?')} "
                    f"(confidence={result.get('confidence', 0):.2f}) "
                    f"— {result.get('summary', '?')}"
                )
                return result

            except json.JSONDecodeError as e:
                logger.error(f"❌ AI zwrócił niepoprawny JSON: {e}\nRaw: {raw_response[:200]}")
                return {
                    "message_type": "UNKNOWN",
                    "confidence": 0.0,
                    "summary": "Błąd parsowania odpowiedzi AI",
                    "trade_signal": None,
                }
            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "quota" in error_str.lower():
                    wait = (attempt + 1) * 15  # 15s, 30s, 45s
                    logger.warning(f"⏳ Rate limit ({model_name}) — czekam {wait}s (próba {attempt+1}/3)")
                    await asyncio.sleep(wait)
                    continue
                else:
                    logger.error(f"❌ Błąd Gemini API ({model_name}): {e}")
                    return {
                        "message_type": "UNKNOWN",
                        "confidence": 0.0,
                        "summary": f"Błąd API: {str(e)[:100]}",
                        "trade_signal": None,
                    }

        logger.warning(f"🔄 Model {model_name} wyczerpany — próbuję następny")

    # Wszystkie modele wyczerpane
    logger.error("❌ Wszystkie modele Gemini zwróciły rate limit")
    return {
        "message_type": "UNKNOWN",
        "confidence": 0.0,
        "summary": "Rate limit — spróbuj za chwilę",
        "trade_signal": None,
    }


# ============================================================
# Helpery do tworzenia modeli Pydantic z wyniku AI
# ============================================================

def parse_to_classified(message_id: int, ai_result: dict) -> ClassifiedMessage:
    """Konwertuje wynik AI na ClassifiedMessage."""
    return ClassifiedMessage(
        message_id=message_id,
        message_type=MessageType(ai_result.get("message_type", "UNKNOWN")),
        confidence=ai_result.get("confidence", 0.0),
        summary=ai_result.get("summary"),
        requires_review=ai_result.get("confidence", 0.0) < 0.7,
    )


def parse_to_signal(message_id: int, ai_result: dict) -> Optional[TradeSignal]:
    """Konwertuje wynik AI na TradeSignal (jeśli jest sygnał)."""
    ts = ai_result.get("trade_signal")
    if not ts or not ts.get("action"):
        return None

    try:
        return TradeSignal(
            message_id=message_id,
            action=TradeAction(ts["action"]) if ts.get("action") else None,
            ticker=ts.get("ticker"),
            quantity=ts.get("quantity"),
            price=ts.get("price"),
            confidence=ai_result.get("confidence", 0.0),
            reason=ts.get("reason"),
            requires_review=ai_result.get("confidence", 0.0) < 0.7,
        )
    except (ValueError, KeyError) as e:
        logger.warning(f"Nie udało się sparsować sygnału: {e}")
        return None
