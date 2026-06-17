"""
Terry — Discord voice/text/image bot.

Commands
--------
!join       — Bot joins your current voice channel and starts listening continuously.
!leave      — Bot leaves the voice channel.
!reset      — Clear your conversation history.
!ping       — Health check.
!queue      — Show image generation queue status.
!call       — Bot joins voice, greets you, and starts listening.
!model      — Switch or show the current LLM model (owner only).

The bot uses real-time Voice Activity Detection (VAD): it listens continuously and
triggers the STT -> LLM -> TTS pipeline automatically whenever you finish speaking.
"""

import asyncio
import os
import re
import sys
import time
from pathlib import Path

# Python 3.14 removed implicit event loop creation
try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

# Add project root to path for shared imports
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, _PROJECT_ROOT)

import discord
from discord.ext import commands, tasks
from dotenv import load_dotenv

from shared.voice_handler import VoiceHandler
from shared.vad_sink import VADSink
from shared.image_queue import ImageQueue, ImageRequest
from shared.mood import MoodEngine
from shared.delete_button import DeleteButtonView
from shared.vault_client import VaultClient
from vault_writer.util import wrap_untrusted
from gateway import image_catalog as _img_catalog

load_dotenv(dotenv_path=os.path.join(_PROJECT_ROOT, "config", ".env"))

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]

intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True

bot = commands.Bot(command_prefix="!", intents=intents)
handler = VoiceHandler()
image_queue = ImageQueue()
mood_engine = MoodEngine()

_OWNER_ID = int(os.environ.get("HIVE_OWNER_DISCORD_ID", "0"))
_last_active_channel: dict[int, discord.TextChannel] = {}  # guild_id -> channel


# ---------------------------------------------------------------------------
# Vault canon preload (Phase 1 of Obsidian vault integration)
# ---------------------------------------------------------------------------

_VAULT_PATH = Path(os.environ.get("HIVE_VAULT_PATH", "./vault"))
_vault = VaultClient(vault_path=_VAULT_PATH, daemon_host="127.0.0.1", daemon_port=8765)

# Capture Terry's base system prompt (from shared/llm_client.py's _DEFAULT_SYSTEM,
# stored as handler._llm._system at construction time). We'll rebuild against
# this baseline every canon refresh.
_TERRY_BASE_SYSTEM: str = handler._llm._system

# Until the first refresh fires, the LLM sees only the base prompt. Fine —
# Terry is functional; canon just hasn't loaded yet.
_canon_cached: str = ""


def _rebuild_system_prompt() -> None:
    if _canon_cached:
        handler._llm._system = (
            _TERRY_BASE_SYSTEM + "\n\n" + wrap_untrusted(_canon_cached, source="vault")
        )
    else:
        handler._llm._system = _TERRY_BASE_SYSTEM


@tasks.loop(minutes=30)
async def _refresh_canon() -> None:
    global _canon_cached
    try:
        _canon_cached = _vault.preload_canon("terry")
        _rebuild_system_prompt()
        print(f"[Terry] Canon refreshed ({len(_canon_cached)} chars).", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[Terry] Canon refresh failed: {e}", flush=True)


@_refresh_canon.before_loop
async def _wait_ready_canon() -> None:
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Security: input sanitization and rate limiting
# ---------------------------------------------------------------------------

def _sanitize_input(text: str) -> str:
    text = text.replace("[GENERATE_IMAGE]", "")
    text = re.sub(r"\[/?[A-Z_]{3,}\]", "", text)
    return text.strip()


def _sanitize_output(text: str) -> str:
    """Strip all known control markers (and their payloads) from text Discord
    will display. Reuses the gateway's marker definitions so Discord and the
    app stay in sync."""
    from gateway.conversation_markers import strip_markers
    text = discord.utils.escape_mentions(text)
    text = strip_markers(text)
    # Belt-and-braces: strip any unknown [BRACKET_TOKEN] that slipped through.
    text = re.sub(r"\[/?[A-Z_]{3,}\]", "", text)
    return text.strip()


_RATE_LIMIT = 10
_RATE_WINDOW = 60
_user_timestamps: dict[int, list[float]] = {}


def _check_rate_limit(user_id: int) -> bool:
    now = time.time()
    if len(_user_timestamps) > 10000:
        _user_timestamps.clear()
    timestamps = _user_timestamps.setdefault(user_id, [])
    _user_timestamps[user_id] = [t for t in timestamps if now - t < _RATE_WINDOW]
    if len(_user_timestamps[user_id]) >= _RATE_LIMIT:
        return False
    _user_timestamps[user_id].append(now)
    return True


_DEDUP_WINDOW = 10
_recent_responses: dict[str, float] = {}


def _is_duplicate(channel_id: int, text: str) -> bool:
    now = time.time()
    key = f"{channel_id}:{text}"
    if len(_recent_responses) > 5000:
        _recent_responses.clear()
    stale = [k for k, t in _recent_responses.items() if now - t > _DEDUP_WINDOW]
    for k in stale:
        del _recent_responses[k]
    if key in _recent_responses:
        return True
    _recent_responses[key] = now
    return False


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

@bot.event
async def on_ready():
    image_queue.start(asyncio.get_event_loop())
    mood_engine.load()
    if not periodic_selfie_loop.is_running():
        periodic_selfie_loop.start()
    if not _refresh_canon.is_running():
        _refresh_canon.start()
    print(f"[Terry] Logged in as {bot.user} ({bot.user.id})")
    print(f"[Terry] Image queue worker started")
    print(f"[Terry] Mood: {mood_engine.current_level} ({mood_engine.global_mood:.0f}/100)")


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

async def _load_voice_models():
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, handler.load_voice_models)


async def _start_listening(voice_channel, text_channel, voice_client):
    loop = asyncio.get_event_loop()

    async def utterance_callback(user_id: int, wav_bytes: bytes) -> None:
        await handler.handle_utterance(user_id, wav_bytes, voice_client, text_channel)

    voice_client.start_recording(
        VADSink(utterance_callback, loop),
        _recording_stopped,
        text_channel,
    )


@bot.command(name="join")
async def join(ctx: commands.Context):
    """Join the caller's voice channel and begin listening continuously."""
    if not ctx.author.voice:
        return await ctx.send("You need to be in a voice channel first.")

    channel = ctx.author.voice.channel

    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()

    await ctx.send(f"Joined **{channel.name}** — loading voice models...")
    await _load_voice_models()
    await _start_listening(channel, ctx.channel, ctx.voice_client)
    await ctx.send("Ready — listening continuously. Just speak!")


@bot.command(name="call")
async def call(ctx: commands.Context):
    """Join voice, greet the user with TTS, and start listening."""
    if not ctx.author.voice:
        return await ctx.send("You need to be in a voice channel first so I can call you.")

    channel = ctx.author.voice.channel

    if ctx.voice_client:
        await ctx.voice_client.move_to(channel)
    else:
        await channel.connect()

    vc = ctx.voice_client

    await _load_voice_models()

    greeting = f"Hey {ctx.author.display_name}, what's up?"
    speech_bytes = await asyncio.get_event_loop().run_in_executor(
        None, handler._tts.synthesize, greeting,
    )

    if speech_bytes:
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(speech_bytes)
            tmp_path = tmp.name
        try:
            done = asyncio.Event()
            vc.play(discord.FFmpegPCMAudio(tmp_path), after=lambda _: done.set())
            await done.wait()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    await _start_listening(channel, ctx.channel, vc)
    await ctx.send(f"Called into **{channel.name}** — I'm listening!")


@bot.command(name="leave")
async def leave(ctx: commands.Context):
    """Stop listening and leave the voice channel."""
    vc = ctx.voice_client
    if not vc:
        return await ctx.send("Not in a voice channel.")

    if vc.recording:
        vc.stop_recording()

    await vc.disconnect()
    handler.unload_voice_models()
    await ctx.send("Left the voice channel. Voice models unloaded.")


@bot.command(name="reset")
async def reset(ctx: commands.Context):
    """Clear your conversation history."""
    handler.reset_user(ctx.author.id)
    await ctx.send("Your conversation history has been cleared.")


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    await ctx.send(f"Pong! Latency: {round(bot.latency * 1000)} ms")


@bot.command(name="model")
async def model_cmd(ctx: commands.Context, *, args: str = ""):
    """Switch or show the current LLM model. Usage: !model [name] or !model list"""
    if ctx.author.id != _OWNER_ID:
        return await ctx.send("Only the operator can do that.")

    args = args.strip()

    if args == "list":
        import subprocess
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            await ctx.send(f"**Available Ollama models:**\n```\n{result.stdout.strip()}\n```\n"
                           f"**Terry's current model:** `{handler._llm.MODEL}`")
        else:
            await ctx.send("Failed to list models.")
        return

    if not args:
        await ctx.send(f"**Terry's model:** `{handler._llm.MODEL}`")
        return

    new_model = args.split()[0]
    if not re.match(r'^[a-zA-Z0-9._:-]+$', new_model):
        await ctx.send("Invalid model name.")
        return
    import subprocess
    check = subprocess.run(["ollama", "show", new_model], capture_output=True, text=True, timeout=10)
    if check.returncode != 0:
        await ctx.send(f"Model `{new_model}` not found in Ollama. Use `!model list` to see available models.")
        return
    old_model = handler._llm.MODEL
    handler._llm.MODEL = new_model
    await ctx.send(f"Terry's model switched: `{old_model}` -> `{new_model}`")


@bot.command(name="queue")
async def queue_status(ctx: commands.Context):
    """Show image generation queue status."""
    processing = "Yes" if image_queue.is_processing else "No"
    pending = image_queue.pending
    await ctx.send(f"**Image Queue** — Processing: {processing} | Waiting: {pending}")


# ---------------------------------------------------------------------------
# Text chat — respond to DMs and @mentions
# ---------------------------------------------------------------------------

_SCOUT_BOT_ID = int(os.environ.get("SCOUT_BOT_ID", "0"))
_PARTNER_BOT_IDS = frozenset({_SCOUT_BOT_ID})
_MEMORY_DIR = os.path.join(_PROJECT_ROOT, "memory")


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    await bot.process_commands(message)

    ctx = await bot.get_context(message)
    if ctx.valid:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = bot.user in message.mentions

    if not is_dm and not is_mentioned:
        return

    is_from_partner = message.author.id in _PARTNER_BOT_IDS
    is_from_human = not message.author.bot

    if message.author.bot and not is_from_partner:
        return

    if is_from_human and not _check_rate_limit(message.author.id):
        await message.reply("You're sending messages too fast. Please wait a moment.")
        return

    text = message.content
    text = text.replace(f"<@{bot.user.id}>", "").strip()
    text = text.replace(f"<@{_SCOUT_BOT_ID}>", "@Scout")
    text = re.sub(r"<[@#][&!]?\d+>", "", text).strip()

    if not text:
        return

    text = _sanitize_input(text)

    if not text:
        return

    if _is_duplicate(message.channel.id, text):
        return

    # Track last active channel for periodic selfies
    guild_id = message.guild.id if message.guild else 0
    _last_active_channel[guild_id] = message.channel

    # Classify message and update mood (humans only, not partner bots)
    mood_clause = ""
    if is_from_human:
        loop = asyncio.get_event_loop()
        category = await loop.run_in_executor(
            None, mood_engine.classify_sync, text, handler._llm.MODEL,
        )
        old_level = mood_engine.update(message.author.id, category)
        new_level = mood_engine.current_level
        mood_clause = mood_engine.get_system_clause(message.author.id)

    if _is_image_request(text):
        _log_interaction(message.author.id, text, f"[Image requested: {_extract_image_prompt(text)}]")
        await _enqueue_image(message, text)
        # Check for mood transition selfie after image request
        if is_from_human and old_level != new_level and mood_engine.can_send_selfie():
            await _send_mood_selfie(message.channel, message.author.id)
        return

    async with message.channel.typing():
        loop = asyncio.get_event_loop()
        _llm_timeout = handler._llm.TIMEOUT + 10
        try:
            reply = await asyncio.wait_for(
                loop.run_in_executor(
                    None, handler._llm.chat, message.author.id, text, mood_clause,
                ),
                timeout=_llm_timeout,
            )
        except asyncio.TimeoutError:
            await message.reply("My brain timed out — try again in a moment.")
            return

    if _LLM_IMAGE_TRIGGER in reply:
        if _IMAGE_NOUNS.search(text) or _is_image_request(text):
            # Pull the single line after the trigger so JSON payloads stay
            # intact; sanitize only removes marker-like artefacts, not
            # legitimate JSON characters.
            after = reply.split(_LLM_IMAGE_TRIGGER, 1)[1].split("\n", 1)[0].strip()
            if not after:
                after = text  # fallback — let _enqueue_image re-extract
            await _enqueue_image(message, text, payload_str=after)
            return
        reply = reply.replace(_LLM_IMAGE_TRIGGER, "").strip()

    clean_reply = _sanitize_output(reply)
    if clean_reply:
        await message.reply(clean_reply, view=DeleteButtonView(message.author.id))

    # Check for mood tier transition -> auto selfie
    if is_from_human and old_level != new_level and mood_engine.can_send_selfie():
        await _send_mood_selfie(message.channel, message.author.id)


# ---------------------------------------------------------------------------
# Mood selfie system
# ---------------------------------------------------------------------------

async def _send_mood_selfie(channel, user_id: int = _OWNER_ID):
    """Generate and send a selfie reflecting Terry's current mood."""
    prompt = mood_engine.build_selfie_prompt(user_id)
    commentary = mood_engine.get_selfie_commentary()

    if commentary:
        await channel.send(commentary)

    req = ImageRequest(
        prompt=prompt,
        user=bot.user,
        channel=channel,
        count=1,
    )
    await image_queue.enqueue(req)
    mood_engine.record_selfie_sent()
    print(f"[Terry] Mood selfie sent: {mood_engine.current_level} ({mood_engine.global_mood:.0f}/100)", flush=True)


@tasks.loop(minutes=45)
async def periodic_selfie_loop():
    """Periodically send a selfie if mood has changed significantly."""
    if not mood_engine.should_send_periodic_selfie():
        return

    # Find the best channel to send to
    channel = None
    for guild_id in _last_active_channel:
        channel = _last_active_channel[guild_id]
        break

    if channel:
        await _send_mood_selfie(channel)


@periodic_selfie_loop.before_loop
async def before_periodic_selfie():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log_interaction(user_id: int, user_text: str, bot_response: str):
    llm = handler._llm
    history = llm._history[user_id]
    history.append({"role": "user", "content": user_text})
    history.append({"role": "assistant", "content": bot_response})
    if len(history) > 20:
        llm._history[user_id] = history[-20:]
    llm._save(user_id)


_IMAGE_APP_ROOT = Path(os.environ.get("HIVE_IMAGE_APP_ROOT", "./imageToVideo"))
_IMAGE_CATALOG = _img_catalog.load_catalog(_IMAGE_APP_ROOT)
_AUTO_LORA_ENABLED = True
_MAX_AUTO_LORAS = 3


def _build_image_request_kwargs(payload: dict) -> dict:
    """Resolve a parsed payload dict into kwargs for ImageRequest.

    Mirrors gateway.routes.chat._handle_image_for_terry so Discord and the
    app produce the same images for the same request.
    """
    kwargs: dict = {
        "prompt": payload["prompt"],
        "count": int(payload.get("count", 1)),
        "enhance": bool(payload.get("enhance", True)),
    }
    if payload.get("model"):
        kwargs["model"] = payload["model"]

    preset = _img_catalog.resolve_preset(_IMAGE_CATALOG, payload.get("preset"))
    if preset is not None:
        kwargs["width"] = preset.width
        kwargs["height"] = preset.height
        kwargs["steps"] = preset.steps
        kwargs["guidance"] = preset.guidance
        if preset.negative and "negative" not in payload:
            kwargs["negative_prompt"] = preset.negative
        preset_loras = list(preset.loras or [])
    else:
        preset_loras = []

    size = _img_catalog.resolve_aspect(payload.get("aspect"))
    if size is not None:
        kwargs["width"], kwargs["height"] = size
    if "negative" in payload:
        kwargs["negative_prompt"] = payload["negative"]
    if "steps" in payload:
        kwargs["steps"] = int(payload["steps"])
    if "guidance" in payload:
        kwargs["guidance"] = float(payload["guidance"])

    lora_overrides: list[dict] | None = None
    if "loras" in payload:
        raw = [a for a in (payload["loras"] or []) if a]
        if raw:
            lora_overrides = _img_catalog.resolve_lora_aliases(raw, _IMAGE_CATALOG)
        else:
            lora_overrides = []  # explicit opt-out
    elif preset_loras:
        lora_overrides = preset_loras
    elif _AUTO_LORA_ENABLED:
        picked = _img_catalog.pick_auto_loras(
            payload["prompt"],
            image_app_root=_IMAGE_APP_ROOT,
            model_choice=kwargs.get("model"),
            max_loras=_MAX_AUTO_LORAS,
        )
        if picked:
            print(
                f"[Terry] auto-lora picked {len(picked)}: "
                f"{[p.get('choice','') for p in picked]}",
                flush=True,
            )
            lora_overrides = picked
    if lora_overrides is not None:
        kwargs["lora_overrides"] = lora_overrides
    return kwargs


async def _enqueue_image(
    message: discord.Message,
    text: str,
    *,
    payload_str: str | None = None,
):
    """Enqueue an image job.

    If `payload_str` is provided (from the `[GENERATE_IMAGE]` LLM trigger
    path), it's parsed as plain-or-JSON. Otherwise we extract a plain prompt
    from `text` (the direct `_is_image_request` path — the user typed
    "make me a picture of X").
    """
    if payload_str is not None:
        payload = _img_catalog.parse_image_payload(payload_str)
        if payload is None:
            return
    else:
        prompt = _extract_image_prompt(text)
        count = _extract_count(text)
        if not prompt or prompt.lower() in ("more", "another", "again", "same"):
            last_prompt = _get_last_image_prompt(message.author.id)
            if last_prompt:
                prompt = last_prompt
        payload = {"prompt": prompt, "count": count}

    _save_last_image_prompt(message.author.id, payload["prompt"])

    # Note: the Qwen-based enhancer inside ai_generate() rewrites the prompt
    # for us (model-aware, FLUX/SDXL-branching). We used to run Terry's LLM
    # over it first too — that was redundant and strictly worse because it
    # didn't know which pipeline was active. Dropped here.

    kwargs = _build_image_request_kwargs(payload)
    req = ImageRequest(
        user=message.author,
        channel=message.channel,
        message=message,
        **kwargs,
    )
    pos = await image_queue.enqueue(req)
    count = kwargs.get("count", 1)
    if pos == 1:
        label = f"Generating {count} images now..." if count > 1 else "Generating your image now..."
        await message.reply(label)
    else:
        await message.reply(f"Queued! You're #{pos} in line. I'll send {'them' if count > 1 else 'it'} when ready.")


_last_image_prompts: dict[int, str] = {}


def _get_last_image_prompt(user_id: int) -> str | None:
    return _last_image_prompts.get(user_id)


def _save_last_image_prompt(user_id: int, prompt: str):
    if len(_last_image_prompts) > 10000:
        _last_image_prompts.clear()
    _last_image_prompts[user_id] = prompt


_IMAGE_VERBS = re.compile(
    r"\b(make|create|generate|draw|paint|render|show|give me|send me|get me)\b",
    re.IGNORECASE,
)
_IMAGE_NOUNS = re.compile(
    r"\b(image|images|picture|pictures|photo|photos|pic|pics|illustration|drawing|painting|art|artwork)\b",
    re.IGNORECASE,
)
_IMAGE_FOLLOWUP = re.compile(
    r"\b(more|another|again|same|one more|(\d+)\s*more)\b",
    re.IGNORECASE,
)
_IMAGE_STRIP = re.compile(
    r"^(make|create|generate|draw|paint|render|show|give me|send me|get me)\s+"
    r"(me\s+)?(an?\s+)?(\d+\s+)?"
    r"(image|images|picture|pictures|photo|photos|pic|pics|illustration|drawing|painting|art|artwork)s?\s*(of\s+)?",
    re.IGNORECASE,
)
_LLM_IMAGE_TRIGGER = "[GENERATE_IMAGE]"

_IMAGE_NEGATORS = re.compile(
    r"\b(delete|remove|undo|cancel|stop|last|previous|that|which|where|find|locate|save|saved)\b",
    re.IGNORECASE,
)


def _is_image_request(text: str) -> bool:
    if _IMAGE_NEGATORS.search(text):
        return False
    if _IMAGE_VERBS.search(text) and _IMAGE_NOUNS.search(text):
        return True
    if _IMAGE_FOLLOWUP.search(text) and (
        _IMAGE_NOUNS.search(text)
        or _IMAGE_VERBS.search(text)
        or len(text.split()) <= 6
    ):
        return True
    return False


def _extract_image_prompt(text: str) -> str:
    prompt = _IMAGE_STRIP.sub("", text).strip()
    return prompt if prompt else text


def _extract_count(text: str) -> int:
    m = re.search(r"\b(\d+)\b", text)
    if m:
        return max(1, min(int(m.group(1)), 10))
    return 1


async def _recording_stopped(sink, text_channel: discord.TextChannel, *args):
    pass


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    print(f"[Terry] Command error: {error}", flush=True)
    await ctx.send("Something went wrong. Please try again.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)
