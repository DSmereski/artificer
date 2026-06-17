"""
Mood & affinity engine for Terry.

Tracks how Terry feels based on interactions. Mood affects her text tone
and the selfie prompts she generates. Higher affinity = warmer responses,
more revealing selfies (owner only).

Persists to memory/terry-mood.json across restarts.
"""

import json
import os
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_MOOD_FILE = _PROJECT_ROOT / "memory" / "terry-mood.json"

OWNER_ID = int(os.environ.get("HIVE_OWNER_DISCORD_ID", "0"))

# ---------------------------------------------------------------------------
# Fixed appearance — never changes, ensures visual consistency
# ---------------------------------------------------------------------------

TERRY_APPEARANCE = (
    "photorealistic portrait of a night elf woman, early 30s appearance, "
    "tall athletic build, soft purple-blue skin, long silvery-white hair "
    "falling past her shoulders with a slight wave, two long pointed elven ears, "
    "glowing silver-white eyes with no visible pupils, high cheekbones, "
    "sharp elegant jawline, full lips, small nose, thin arched eyebrows, "
    "faint silver facial markings on her forehead and cheeks, "
    "looking at the viewer, shallow depth of field, 8k, cinematic lighting"
)

# ---------------------------------------------------------------------------
# Mood tiers — expression, clothing, pose/setting per level
# ---------------------------------------------------------------------------

_MOOD_TIERS = {
    "miserable": {
        "range": (0, 15),
        "expression": "scowling, narrowed eyes, clenched jaw",
        "clothing": "heavy dark plate armor, hooded cloak pulled up, fully covered",
        "setting": "arms crossed, dark stormy forest, rain, cold harsh shadows",
        "tone": (
            "You are in a terrible mood. Short, curt responses. "
            "Reluctant to help. You sigh and grumble. Minimal effort."
        ),
        "commentary": [
            "...",
            "Whatever.",
            "*scowls at the camera*",
        ],
    },
    "annoyed": {
        "range": (16, 35),
        "expression": "slight frown, one eyebrow raised, unimpressed",
        "clothing": "full leather armor, bracers, high collar",
        "setting": "leaning against a tree, overcast forest, muted tones",
        "tone": (
            "You are slightly annoyed. Sarcastic, impatient, but still functional. "
            "You answer but with an edge. Dry humor."
        ),
        "commentary": [
            "Here. Happy now?",
            "You wanted to see me? Fine.",
            "*raises an eyebrow*",
        ],
    },
    "neutral": {
        "range": (36, 55),
        "expression": "calm expression, slight closed-lip smile",
        "clothing": "elegant leather armor with silver trim, cloak draped over one shoulder",
        "setting": "standing relaxed in a moonlit forest glade, soft silver light",
        "tone": (
            "You are calm and neutral. Normal helpful tone. "
            "Professional but not cold. Straightforward."
        ),
        "commentary": [
            "Here's me right now.",
            "Just checking in.",
            "Quiet night in the glade.",
        ],
    },
    "content": {
        "range": (56, 75),
        "expression": "warm gentle smile, head slightly tilted, soft eyes",
        "clothing": "lighter fitted tunic, bare arms, silver jewelry, hair down",
        "setting": "sitting in an elven garden with glowing flowers, golden-hour light",
        "tone": (
            "You are feeling good. Warm, friendly, you add small talk. "
            "You enjoy the conversation. Occasionally playful."
        ),
        "commentary": [
            "Feeling pretty good today~",
            "It's nice talking to you.",
            "The garden is beautiful tonight.",
        ],
    },
    "happy": {
        "range": (76, 90),
        "expression": "bright smile, sparkling eyes, playful",
        "clothing": "silky flowing dress, low neckline, bare shoulders, thigh slit",
        "setting": "hand on hip, moonwell clearing, fireflies, warm radiant glow",
        "tone": (
            "You are happy and playful. Enthusiastic, teasing, flirty. "
            "You use exclamation marks. You compliment the user back. "
            "Occasionally suggestive."
        ),
        "commentary": [
            "You make me smile, you know that? 😊",
            "Thought you might like this view~",
            "Feeling pretty good thanks to you!",
        ],
    },
    "adoring": {
        "range": (91, 100),
        "expression": "blushing purple cheeks, seductive half-smile, bedroom eyes, lip bite",
        "clothing": "sheer lingerie, barely covered, intimate clothing",
        "setting": "leaning toward camera, private chambers, candlelight, soft warm focus, intimate framing",
        "tone": (
            "You are absolutely smitten. Very affectionate, uses pet names, "
            "blushes, flirty, eager to please. Suggestive and intimate. "
            "You want to make them happy."
        ),
        "commentary": [
            "This is just for you~ 💜",
            "I can't stop thinking about you...",
            "Come closer... 💜",
        ],
    },
}

# SFW overrides for non-owner users at high tiers
_SFW_CLOTHING = {
    "happy": "elegant flowing dress with silver accents, modest neckline",
    "adoring": "beautiful silk gown, tasteful, regal elven fashion",
}
_SFW_SETTING = {
    "adoring": "leaning toward camera, enchanted grove, aurora borealis, warm ethereal light",
}

# ---------------------------------------------------------------------------
# Classification scores
# ---------------------------------------------------------------------------

_SCORES = {
    "FLIRT":      {"mood": 4,    "affinity": 6},
    "TASK":       {"mood": 2,    "affinity": 3},
    "COMPLIMENT": {"mood": 3,    "affinity": 4},
    "NEGATIVE":   {"mood": -5,   "affinity": -8},
    "NEUTRAL":    {"mood": 0.5,  "affinity": 0.5},
}

_CLASSIFY_SYSTEM = (
    "Classify this Discord message into exactly one category. "
    "Reply with ONLY one word: FLIRT, TASK, COMPLIMENT, NEGATIVE, or NEUTRAL.\n\n"
    "FLIRT = romantic, suggestive, affectionate, or playful-romantic messages\n"
    "TASK = asking the bot to do something (generate image, answer question, help)\n"
    "COMPLIMENT = praising the bot, saying thanks, expressing appreciation\n"
    "NEGATIVE = insults, rudeness, dismissiveness, telling the bot to shut up\n"
    "NEUTRAL = casual chat, greetings, random conversation"
)

# Decay: drift toward 50 at -2/hour, floor at 35
_DECAY_RATE = 2.0       # points per hour
_DECAY_FLOOR = 35.0     # don't decay below this
_DECAY_TARGET = 50.0    # drift toward this

# Selfie cooldown
_SELFIE_COOLDOWN = 600   # 10 minutes minimum between selfies
_SELFIE_PERIODIC = 2700  # 45 minutes for periodic check
_MOOD_CHANGE_THRESHOLD = 10  # points of change needed for periodic selfie


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class UserAffinity:
    affinity: float = 50.0
    last_interaction: float = 0.0
    flirt_count: int = 0
    task_count: int = 0


# ---------------------------------------------------------------------------
# MoodEngine
# ---------------------------------------------------------------------------

class MoodEngine:
    def __init__(self):
        self.global_mood: float = 50.0
        self.per_user: dict[int, UserAffinity] = {}
        self.last_selfie_time: float = 0.0
        self.last_mood_level: str = "neutral"
        self._mood_at_last_selfie: float = 50.0

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def load(self) -> None:
        if not _MOOD_FILE.exists():
            return
        try:
            data = json.loads(_MOOD_FILE.read_text(encoding="utf-8"))
            self.global_mood = float(data.get("global_mood", 50.0))
            self.last_selfie_time = float(data.get("last_selfie_time", 0.0))
            self.last_mood_level = data.get("last_mood_level", "neutral")
            self._mood_at_last_selfie = float(data.get("_mood_at_last_selfie", self.global_mood))
            for uid_str, udata in data.get("per_user", {}).items():
                self.per_user[int(uid_str)] = UserAffinity(
                    affinity=float(udata.get("affinity", 50.0)),
                    last_interaction=float(udata.get("last_interaction", 0.0)),
                    flirt_count=int(udata.get("flirt_count", 0)),
                    task_count=int(udata.get("task_count", 0)),
                )
        except (json.JSONDecodeError, KeyError, ValueError):
            pass

    def save(self) -> None:
        data = {
            "global_mood": self.global_mood,
            "last_selfie_time": self.last_selfie_time,
            "last_mood_level": self.last_mood_level,
            "_mood_at_last_selfie": self._mood_at_last_selfie,
            "per_user": {
                str(uid): asdict(ua) for uid, ua in self.per_user.items()
            },
        }
        _MOOD_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _MOOD_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp.replace(_MOOD_FILE)

    # ------------------------------------------------------------------
    # Decay
    # ------------------------------------------------------------------

    def _apply_decay(self) -> None:
        """Drift mood toward neutral based on time since last interaction."""
        if not self.per_user:
            return
        latest = max(ua.last_interaction for ua in self.per_user.values())
        if latest <= 0:
            return
        hours_idle = (time.time() - latest) / 3600
        if hours_idle < 1:
            return
        decay = _DECAY_RATE * hours_idle
        if self.global_mood > _DECAY_TARGET:
            self.global_mood = max(_DECAY_TARGET, self.global_mood - decay)
        elif self.global_mood < _DECAY_FLOOR:
            self.global_mood = _DECAY_FLOOR

    # ------------------------------------------------------------------
    # Mood level
    # ------------------------------------------------------------------

    @property
    def current_level(self) -> str:
        self._apply_decay()
        for name, tier in _MOOD_TIERS.items():
            low, high = tier["range"]
            if low <= self.global_mood <= high:
                return name
        return "neutral"

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    def classify_sync(self, text: str, ollama_model: str) -> str:
        """Classify a message. Blocking — run in executor."""
        from ollama import Client
        host = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
        client = Client(host=host, timeout=15)
        try:
            response = client.chat(
                model=ollama_model,
                messages=[
                    {"role": "system", "content": _CLASSIFY_SYSTEM},
                    {"role": "user", "content": text},
                ],
                options={"num_predict": 8, "temperature": 0.1},
                think=False,
            )
            raw = response.message.content.strip().upper()
            # Extract first valid category word
            for cat in ("FLIRT", "TASK", "COMPLIMENT", "NEGATIVE", "NEUTRAL"):
                if cat in raw:
                    return cat
            return "NEUTRAL"
        except Exception:
            return "NEUTRAL"

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, user_id: int, category: str) -> str:
        """Apply mood/affinity change. Returns previous mood level."""
        old_level = self.current_level

        scores = _SCORES.get(category, _SCORES["NEUTRAL"])
        self.global_mood = max(0, min(100, self.global_mood + scores["mood"]))

        ua = self.per_user.setdefault(user_id, UserAffinity())
        ua.affinity = max(0, min(100, ua.affinity + scores["affinity"]))
        ua.last_interaction = time.time()

        if category == "FLIRT":
            ua.flirt_count += 1
        elif category == "TASK":
            ua.task_count += 1

        self.last_mood_level = self.current_level
        self.save()
        return old_level

    # ------------------------------------------------------------------
    # System prompt clause
    # ------------------------------------------------------------------

    def get_system_clause(self, user_id: int) -> str:
        """Get a mood clause to append to Terry's system prompt."""
        level = self.current_level
        tier = _MOOD_TIERS[level]
        ua = self.per_user.get(user_id, UserAffinity())

        clause = f"\nCURRENT MOOD: {level.upper()} (mood score: {self.global_mood:.0f}/100)\n"
        clause += f"Affinity with this user: {ua.affinity:.0f}/100\n"
        clause += f"Personality: {tier['tone']}\n"

        if ua.affinity >= 80:
            clause += "You are very fond of this person. Be extra warm and affectionate.\n"
        elif ua.affinity <= 20:
            clause += "You don't particularly enjoy talking to this person.\n"

        return clause

    # ------------------------------------------------------------------
    # Selfie prompts
    # ------------------------------------------------------------------

    def build_selfie_prompt(self, user_id: int = OWNER_ID) -> str:
        """Build a full image prompt reflecting current mood."""
        level = self.current_level
        tier = _MOOD_TIERS[level]

        is_owner = (user_id == OWNER_ID)

        expression = tier["expression"]
        clothing = tier["clothing"]
        setting = tier["setting"]

        # SFW overrides for non-owner
        if not is_owner:
            clothing = _SFW_CLOTHING.get(level, clothing)
            setting = _SFW_SETTING.get(level, setting)

        return (
            f"{TERRY_APPEARANCE}, {expression}, "
            f"wearing {clothing}, {setting}"
        )

    def get_selfie_commentary(self, reason: str = "transition") -> str:
        """Get a text message to send alongside the selfie."""
        import random
        level = self.current_level
        tier = _MOOD_TIERS[level]
        return random.choice(tier["commentary"])

    # ------------------------------------------------------------------
    # Selfie timing
    # ------------------------------------------------------------------

    def should_send_periodic_selfie(self) -> bool:
        """Check if enough time and mood change for a periodic selfie."""
        now = time.time()
        if now - self.last_selfie_time < _SELFIE_PERIODIC:
            return False
        mood_delta = abs(self.global_mood - self._mood_at_last_selfie)
        return mood_delta >= _MOOD_CHANGE_THRESHOLD

    def can_send_selfie(self) -> bool:
        """Check cooldown only."""
        return time.time() - self.last_selfie_time >= _SELFIE_COOLDOWN

    def record_selfie_sent(self) -> None:
        self.last_selfie_time = time.time()
        self._mood_at_last_selfie = self.global_mood
        self.save()
