"""
Gemini 2.5 Flash Telegram bot.
Remembers last 10 messages per user (conversation context).
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
from telegram.ext import ApplicationBuilder, ContextTypes, MessageHandler, filters

load_dotenv(Path(__file__).parent.parent / ".env")

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MODEL_NAME = "gemini-2.5-flash"
MAX_HISTORY = 10  # exchanges per user

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)

# Per-user history: deque of types.Content objects (user + model turns)
histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY * 2))


def _build_contents(history: deque, new_text: str) -> list[types.Content]:
    contents = list(history)
    contents.append(types.Content(role="user", parts=[types.Part(text=new_text)]))
    return contents


def _call_gemini(contents: list[types.Content]) -> str:
    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=contents,
    )
    return response.text


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    if not text:
        return

    history = histories[user_id]
    contents = _build_contents(history, text)

    try:
        reply = await asyncio.get_event_loop().run_in_executor(
            None, lambda: _call_gemini(contents)
        )
    except Exception as exc:
        logger.error("Gemini error: %s", exc)
        reply = f"Błąd Gemini: {exc}"
        await update.message.reply_text(reply)
        return

    history.append(types.Content(role="user", parts=[types.Part(text=text)]))
    history.append(types.Content(role="model", parts=[types.Part(text=reply)]))

    await update.message.reply_text(reply)


def main() -> None:
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("Gemini bot started (model=%s)", MODEL_NAME)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
