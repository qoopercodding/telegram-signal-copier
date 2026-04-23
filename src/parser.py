"""
AI Parser — analiza wiadomości tradera przez Google Gemini REST API.

Co robi:
  1. Klasyfikuje wiadomość (PORTFOLIO_UPDATE / TRADE_ACTION / COMMENT / UNKNOWN)
  2. Dla TRADE_ACTION: wyciąga action, ticker, quantity, price
  3. Dla PORTFOLIO_UPDATE: wyciąga listę pozycji z screenshota
  4. Obsługuje zarówno tekst jak i zdjęcia (vision)

Użycie:
    from src.parser import analyze_message
    result = await analyze_message(text="Kupiłem 100 XTB", media_paths=[])
"""

import asyncio
import json
import base64
from pathlib import Path
from typing import Optional

import httpx
from loguru import logger

from src.config import settings
from src.models import (
    MessageType,
    TradeAction,
    ClassifiedMessage,
    TradeSignal,
)


# ============================================================
# Konfiguracja — REST API (bez deprecated SDK)
# ============================================================

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MODELS_TO_TRY = ["gemini-2.0-flash-lite", "gemini-1.5-flash"]


# ============================================================
# Prompty
# ============================================================

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
    }
}

WAŻNE ZASADY:
- Jeśli nie jesteś pewien, ustaw confidence < 0.5
- NIE ZGADUJ — jeśli nie ma jasnego sygnału, daj null
- Tickery na GPW: np. XTB, PKN, KGHM, CDR, PKO, PZU, ALE, DNP, CCC, LPP
- Trader może pisać po polsku: "kupiłem", "dokupiłem", "sprzedałem", "zamknąłem"
- "Dobieram" = ADD, "Redukcja" = REDUCE, "Zamykam pozycję" = CLOSE
- Jeśli jest screenshot — opisz co widzisz (portfel, transakcje, chart)

WIADOMOŚĆ DO ANALIZY:
"""


# ============================================================
# REST API call
# ============================================================

async def call_gemini_rest(
    model_name: str,
    api_key: str,
    parts: list[dict],
) -> dict:
    """Wywołuje Gemini API przez REST (httpx) zamiast deprecated gRPC SDK."""
    url = GEMINI_API_URL.format(model=model_name)

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 1024,
        },
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            url,
            params={"key": api_key},
            json=payload,
        )

    if response.status_code == 429:
        raise Exception(f"429 Rate limit exceeded")
    elif response.status_code == 403:
        raise Exception(f"403 API blocked: {response.text[:200]}")
    elif response.status_code != 200:
        raise Exception(f"{response.status_code} {response.text[:200]}")

    data = response.json()
    text = data["candidates"][0]["content"]["parts"][0]["text"]
    return {"text": text}


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
    Używa REST API (httpx) zamiast deprecated gRPC SDK.
    """
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY nie ustawiony — pomijam analizę AI")
        return {
            "message_type": "UNKNOWN",
            "confidence": 0.0,
            "summary": "AI niedostępne — brak API key",
            "trade_signal": None,
        }

    # Buduj parts dla REST API
    parts = []

    prompt = CLASSIFY_PROMPT
    if text:
        prompt += f"\n\nTEKST: {text}"
    else:
        prompt += "\n\nTEKST: (brak tekstu — tylko media)"

    parts.append({"text": prompt})

    # Dodaj zdjęcia (vision) jako base64
    if media_paths:
        for path_str in media_paths:
            path = Path(path_str)
            if path.exists() and path.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
                try:
                    image_data = base64.b64encode(path.read_bytes()).decode("utf-8")
                    mime_type = {
                        ".jpg": "image/jpeg",
                        ".jpeg": "image/jpeg",
                        ".png": "image/png",
                        ".webp": "image/webp",
                    }.get(path.suffix.lower(), "image/jpeg")

                    parts.append({
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_data,
                        }
                    })
                    logger.debug(f"📷 Dodano obraz do analizy: {path.name}")
                except Exception as e:
                    logger.error(f"Błąd ładowania obrazu {path}: {e}")

    # Próbuj modele po kolei (fallback przy rate limit)
    for model_name in MODELS_TO_TRY:
        for attempt in range(3):
            try:
                result = await call_gemini_rest(model_name, settings.gemini_api_key, parts)
                raw_response = result["text"].strip()

                # Wyczyść response — Gemini czasem zwraca ```json ... ```
                if raw_response.startswith("```"):
                    raw_response = raw_response.split("\n", 1)[1]
                    raw_response = raw_response.rsplit("```", 1)[0]
                    raw_response = raw_response.strip()

                parsed = json.loads(raw_response)
                logger.info(
                    f"🤖 AI ({model_name}): {parsed.get('message_type', '?')} "
                    f"(confidence={parsed.get('confidence', 0):.2f}) "
                    f"— {parsed.get('summary', '?')}"
                )
                return parsed

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
                if "429" in error_str:
                    wait = (attempt + 1) * 15
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
