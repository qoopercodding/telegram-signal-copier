"""
Gemini 2.5 Pro Telegram bot — developer assistant z code execution.
Kontekst rozmowy: ostatnie 10 wymian per użytkownik.
"""
import asyncio
import logging
import os
import subprocess
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

# Załaduj ADMIN_CHAT_ID z pliku
try:
    ADMIN_CHAT_ID = int(open(Path(__file__).parent.parent / ".admin_chat_id").read().strip())
except Exception:
    ADMIN_CHAT_ID = None

MODEL_NAME = "gemini-2.5-pro"
MAX_HISTORY = 10  # wymian per użytkownik (user+model = 2 wpisy)

SYSTEM_INSTRUCTION = """Jesteś zaawansowanym asystentem deweloperskim i analitycznym.

Specjalizacje:
- Python, asyncio, Telegram bots (Telethon, python-telegram-bot)
- Inwestowanie: analiza sygnałów giełdowych, GPW, portfele
- Systemy automatyczne: kopiowanie sygnałów, boty tradingowe
- SQLite, REST API, systemd, Linux (Ubuntu)

Zasady:
- Odpowiadaj po polsku, chyba że użytkownik pisze po angielsku
- Kod formatuj w blokach ```python lub odpowiednim języku
- Gdy piszesz kod — uruchamiaj go i pokaż wynik (masz interpreter Pythona)
- Masz dostęp do terminala VM przez narzędzie `run_terminal_command`. Używaj go do:
    - Sprawdzania statusu serwisów (systemctl --user status ...)
    - Czytania logów (journalctl --user -u ... -n 50)
    - Operacji git (git status, git push)
    - Przeglądania plików (ls, cat, grep)
- Bądź konkretny i zwięzły — nie lej wody
- Przy analizie danych finansowych zawsze podawaj zastrzeżenie o ryzyku"""

THINKING_BUDGET = 8000  # tokeny myślenia (0 = wyłączone, max 24000)

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

client = genai.Client(api_key=GEMINI_API_KEY)


def run_terminal_command(command: str) -> str:
    """Uruchamia komendę w terminalu VM i zwraca wynik (stdout + stderr)."""
    logger.info(f"Executing terminal command: {command}")
    try:
        # Uruchamiamy w powłoce (shell=True), aby obsłużyć potoki, przekierowania itp.
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60
        )
        output = []
        if result.stdout:
            output.append(f"STDOUT:\n{result.stdout}")
        if result.stderr:
            output.append(f"STDERR:\n{result.stderr}")
        if not output:
            output.append(f"(brak wyjścia, kod wyjścia: {result.returncode})")
        else:
            output.append(f"Kod wyjścia: {result.returncode}")
            
        return "\n\n".join(output)
    except Exception as e:
        return f"Błąd wykonania komendy: {str(e)}"


GENERATE_CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM_INSTRUCTION,
    thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
    tools=[
        types.Tool(code_execution=types.ToolCodeExecution()),
        types.Tool(function_declarations=[
            types.FunctionDeclaration(
                name="run_terminal_command",
                description="Uruchamia komendę w terminalu Linux (VM). Używaj do systemctl, git, ls itp.",
                parameters=types.Schema(
                    type="OBJECT",
                    properties={
                        "command": types.Schema(type="STRING", description="Komenda do wykonania")
                    },
                    required=["command"]
                )
            )
        ])
    ],
)

# Per-user historia: deque of types.Content
histories: dict[int, deque] = defaultdict(lambda: deque(maxlen=MAX_HISTORY * 2))


# --- helpers ---

def _build_contents(history: deque, new_text: str) -> list[types.Content]:
    contents = list(history)
    contents.append(types.Content(role="user", parts=[types.Part(text=new_text)]))
    return contents


def _call_gemini(contents: list[types.Content], user_id: int) -> str:
    current_contents = contents.copy()
    max_turns = 10
    
    # Zbieramy wszystkie teksty i wyniki z wielu tur (jeśli Gemini wywołuje funkcje)
    all_responses_parts = []

    for _ in range(max_turns):
        response = client.models.generate_content(
            model=MODEL_NAME,
            contents=current_contents,
            config=GENERATE_CONFIG,
        )
        
        # Zapamiętaj odpowiedź modelu w bieżącej konwersacji
        current_contents.append(response.candidates[0].content)
        
        has_function_call = False
        function_responses_parts = []
        
        # Procesuj części odpowiedzi
        if not response.candidates or not response.candidates[0].content.parts:
            break

        for part in response.candidates[0].content.parts:
            if part.text:
                all_responses_parts.append(part.text)
            
            if part.executable_code:
                all_responses_parts.append(f"```python\n{part.executable_code.code}\n```")
            
            if part.code_execution_result:
                outcome = part.code_execution_result.outcome.name
                output = part.code_execution_result.output or ""
                all_responses_parts.append(f"**Wynik (Python):**\n```\n{output}\n```")

            if part.function_call:
                has_function_call = True
                fc = part.function_call
                if fc.name == "run_terminal_command":
                    # Sprawdzenie uprawnień (tylko ADMIN_CHAT_ID)
                    if ADMIN_CHAT_ID is not None and user_id != ADMIN_CHAT_ID:
                        res_text = "Błąd: Nie masz uprawnień do wykonywania komend terminala."
                    else:
                        res_text = run_terminal_command(**fc.args)
                    
                    all_responses_parts.append(f"**Terminal (`{fc.args.get('command')}`):**\n{res_text}")
                    
                    function_responses_parts.append(
                        types.Part(function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"result": res_text}
                        ))
                    )

        if has_function_call:
            # Dodaj wyniki funkcji i kontynuuj pętlę
            current_contents.append(types.Content(role="user", parts=function_responses_parts))
            continue
        else:
            # Koniec generowania
            break
            
    return "\n\n".join(all_responses_parts) if all_responses_parts else "Brak odpowiedzi."


# --- command handlers ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    name = update.effective_user.first_name or "Hej"
    user_id = update.effective_user.id
    admin_status = " (Tryb Admin 🛠️)" if user_id == ADMIN_CHAT_ID else ""
    await update.message.reply_text(
        f"Cześć, {name}! Jestem asystentem opartym na **Gemini 2.5 Pro** 🤖{admin_status}\n\n"
        "Potrafię:\n"
        "• Odpowiadać na pytania i prowadzić rozmowę\n"
        "• Pisać i **uruchamiać kod Python** (interpreter wbudowany)\n"
        "• Zarządzać serwerem przez terminal (tylko Admin)\n"
        "• Analizować dane, sygnały giełdowe, strategie\n\n"
        "Napisz np. `status serwisów` lub `pokaż logi signal-copier`.",
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
            None, lambda: _call_gemini(contents, user_id)
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
