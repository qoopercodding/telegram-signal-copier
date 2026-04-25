"""
Multi-provider AI abstraction — Gemini → Claude → OpenAI fallback chain.

Użycie:
    from src.ai_providers import call_ai
    text = await call_ai(prompt="...", images=[bytes_data, ...])
"""

import asyncio
from loguru import logger

from src.config import settings


# ── Provider functions ────────────────────────────────────────────────────────

async def _call_gemini(prompt: str, images: list[bytes], mime_types: list[str]) -> str:
    """Gemini 2.5-flash → 2.0-flash fallback."""
    from google import genai
    from google.genai import types

    if not settings.gemini_api_key:
        raise ValueError("GEMINI_API_KEY not set")

    client = genai.Client(api_key=settings.gemini_api_key)
    parts = [types.Part.from_text(text=prompt)]
    for img, mime in zip(images, mime_types):
        parts.append(types.Part.from_bytes(data=img, mime_type=mime))

    for model in ["gemini-2.5-flash", "gemini-2.0-flash"]:
        for attempt in range(3):
            try:
                response = await client.aio.models.generate_content(
                    model=model, contents=parts
                )
                text = (response.text or "").strip()
                if text:
                    logger.debug(f"🤖 Gemini ({model}) odpowiedział")
                    return text
            except Exception as e:
                err = str(e)
                if "429" in err or "quota" in err.lower():
                    wait = (attempt + 1) * 15
                    logger.warning(f"⏳ Gemini rate limit ({model}) — czekam {wait}s")
                    await asyncio.sleep(wait)
                else:
                    raise
        logger.warning(f"🔄 Gemini {model} wyczerpany")

    raise RuntimeError("Gemini: wszystkie modele wyczerpały limit")


async def _call_claude(prompt: str, images: list[bytes], mime_types: list[str]) -> str:
    """Anthropic Claude Haiku — fallback gdy Gemini niedostępny."""
    import anthropic

    if not settings.anthropic_api_key:
        raise ValueError("ANTHROPIC_API_KEY not set")

    content: list = []
    for img, mime in zip(images, mime_types):
        import base64
        content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": mime,
                "data": base64.b64encode(img).decode(),
            },
        })
    content.append({"type": "text", "text": prompt})

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    for attempt in range(3):
        try:
            msg = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=1024,
                messages=[{"role": "user", "content": content}],
            )
            text = msg.content[0].text.strip() if msg.content else ""
            if text:
                logger.debug("🤖 Claude Haiku odpowiedział")
                return text
        except Exception as e:
            err = str(e)
            if "529" in err or "overloaded" in err.lower() or "rate" in err.lower():
                wait = (attempt + 1) * 15
                logger.warning(f"⏳ Claude rate limit — czekam {wait}s")
                await asyncio.sleep(wait)
            else:
                raise

    raise RuntimeError("Claude: rate limit wyczerpany")


async def _call_openai(prompt: str, images: list[bytes], mime_types: list[str]) -> str:
    """OpenAI GPT-4o-mini — ostatni fallback."""
    import base64
    from openai import AsyncOpenAI

    if not settings.openai_api_key:
        raise ValueError("OPENAI_API_KEY not set")

    content: list = []
    for img, mime in zip(images, mime_types):
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(img).decode()}"},
        })
    content.append({"type": "text", "text": prompt})

    client = AsyncOpenAI(api_key=settings.openai_api_key)
    for attempt in range(3):
        try:
            resp = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": content}],
                max_tokens=1024,
            )
            text = (resp.choices[0].message.content or "").strip()
            if text:
                logger.debug("🤖 GPT-4o-mini odpowiedział")
                return text
        except Exception as e:
            err = str(e)
            if "429" in err or "quota" in err.lower():
                wait = (attempt + 1) * 15
                logger.warning(f"⏳ OpenAI rate limit — czekam {wait}s")
                await asyncio.sleep(wait)
            else:
                raise

    raise RuntimeError("OpenAI: rate limit wyczerpany")


# ── Publiczne API ─────────────────────────────────────────────────────────────

async def call_ai(
    prompt: str,
    images: list[bytes] | None = None,
    mime_types: list[str] | None = None,
) -> str:
    """
    Wysyła prompt do AI. Próbuje kolejno: Gemini → Claude → OpenAI.
    Zwraca tekst odpowiedzi lub rzuca RuntimeError gdy wszystkie padły.

    Args:
        prompt: Tekst zapytania
        images: Lista bajtów zdjęć (opcjonalne)
        mime_types: MIME type dla każdego zdjęcia (np. "image/jpeg")
    """
    imgs = images or []
    mimes = mime_types or ["image/jpeg"] * len(imgs)

    providers = []
    if settings.gemini_api_key:
        providers.append(("Gemini", _call_gemini))
    if settings.anthropic_api_key:
        providers.append(("Claude", _call_claude))
    if settings.openai_api_key:
        providers.append(("OpenAI", _call_openai))

    if not providers:
        raise RuntimeError("Brak żadnego klucza AI API w .env")

    last_error = None
    for name, fn in providers:
        try:
            return await fn(prompt, imgs, mimes)
        except ValueError:
            logger.debug(f"Pomijam {name} — brak klucza API")
        except Exception as e:
            logger.warning(f"⚠️ {name} niedostępny: {e} — próbuję następny")
            last_error = e

    raise RuntimeError(f"Wszystkie AI providers niedostępne. Ostatni błąd: {last_error}")
