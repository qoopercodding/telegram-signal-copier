"""
Gemini 2.5 Pro Telegram bot — developer assistant z code execution.
Kontekst rozmowy: ostatnie 10 wymian per użytkownik.
"""
import asyncio
import logging
import os
from collections import defaultdict, deque
from pathlib import Path

from dotenv import load_dotenv
from google import genai
from google.genai import types
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv(Path(__file__).parent.parent / ".env")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]

MODEL_NAME = "gemini-2.5-pro"
MAX_HISTORY = 10  # wymian per użytkownik (user+model = 2 wpisy)

SYSTEM_INSTRUCTION = """Jesteś zaawansowanym asystentem deweloperskim i analitycznym.

Specjalizacje:
- Python, asyncio, Telegram bots (Telethon, python-telegram-bot)
- Inwestowanie: analiza sygnałów giełdowych, GPW, portfele
- Systemy automatyczne: kopiowanie sygnałów, boty tradingowe
- SQLite, REST API, systemd, Linux

Zasady:
- Odpowiadaj po polsku, chyba że użytkownik pisze po angielsku
- Kod formatuj w blokach ```python lub odpowiednim języku
- Gdy piszesz kod — uruchamiaj go i pokaż wynik (masz interpreter Pythona)
- Bądź konkretny i zwięzły — nie lej wody
- Przy analizie danych finansowych zawsze podawaj zastrzeżenie o ryzyku"""

THINKING_BUDGET = 8000  # tokeny myślenia (0 = wyłączone, max 24000)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)

GENERATE_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_INSTRUCTION,
    thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
    tools=[types.Tool(code_execution=types.ToolCodeExecution())],
)

# Per-user historia: deque of types.Content
histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY * 2))


# --- helpers ---

def _build_contents(history: deque, new_text: str) -> list[types.Content]:
    contents = list(history)
    contents.append(types.Content(role="user", parts=[types.Part(text=new_text)]))
    return contents


def _call_gemini(contents: list[types.Content]) -> str:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
        config=GENERATE_CONFIG,
    )
    # Zbierz tekst ze wszystkich parts (model może zwrócić kilka bloków)
    parts_text = []
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            parts_text.append(part.text)
        elif hasattr(part, "executable_code") and part.executable_code:
            parts_text.append(f"```python\n{part.executable_code.code}\n```")
        elif hasattr(part, "code_execution_result") and part.code_execution_result:
            outcome = part.code_execution_result.outcome.name
            output = part.code_execution_result.output or ""
            parts_text.append(f"**Wynik ({outcome}):**\n```\n{output}\n```")
    return "\n\n".join(parts_text) if parts_text else response.text


# --- command handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "Hej"
    await update.message.reply_text(
        f"Cześć, {name}! Jestem asystentem opartym na **Gemini 2.5 Pro** 🤖\n\n"
        "Potrafię:\n"
        "• Odpowiadać na pytania i prowadzić rozmowę\n"
        "• Pisać i **uruchamiać kod Python** (interpreter wbudowany)\n"
        "• Analizować dane, sygnały giełdowe, strategie\n"
        "• Pomagać z Pythonem, Linuxem, botami Telegram\n\n"
        "Napisz cokolwiek, żeby zacząć. `/help` — lista komend.",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "*Dostępne komendy:*\n\n"
        "/start — powitanie i opis możliwości\n"
        "/help — ta wiadomość\n"
        "/clear — wyczyść historię rozmowy\n\n"
        "*Możliwości:*\n"
        "• Pytania i rozmowa — po prostu pisz\n"
        "• Kod Python — napisz zadanie, model napisze i uruchomi kod\n"
        "• Analiza danych — wklej dane, model je przeanalizuje\n\n"
        f"Model: `{MODEL_NAME}` | Thinking: `{THINKING_BUDGET}` tokenów",
        parse_mode="Markdown",
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    histories[user_id].clear()
    await update.message.reply_text("Historia rozmowy wyczyszczona. Zaczynamy od nowa!")


async def _send_reply(update: Update, text: str) -> None:
    """Wysyła odpowiedź z Markdown, fallback do plain text przy błędzie parsowania."""
    chunks = [text[i:i+4096] for i in range(0, len(text), 4096)]
    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(chunk)


# --- message handler ---

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    history = histories[user_id]
    contents = _build_contents(history, text)

    # Wysyłaj "typing..." co 4s przez cały czas oczekiwania na Gemini
    chat_id = update.effective_chat.id
    stop_typing = asyncio.Event()

    async def keep_typing() -> None:
        while not stop_typing.is_set():
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            try:
                await asyncio.wait_for(asyncio.shield(stop_typing.wait()), timeout=4)
            except asyncio.TimeoutError:
                pass

    typing_task = asyncio.create_task(keep_typing())

    try:
        reply = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _call_gemini(contents)
        )
    except Exception as exc:
        logger.error("Gemini error dla user %s: %s", user_id, exc)
        stop_typing.set()
        typing_task.cancel()
        await update.message.reply_text(f"Błąd Gemini: {exc}")
        return
    finally:
        stop_typing.set()
        typing_task.cancel()

    history.append(types.Content(role="user", parts=[types.Part(text=text)]))
    history.append(types.Content(role="model", parts=[types.Part(text=reply)]))

    await _send_reply(update, reply)


# --- main ---

def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Gemini bot started (model=%s, thinking=%s)", MODEL_NAME, THINKING_BUDGET)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
