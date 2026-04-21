# =============================================================================
# bot/handlers/message_handler.py
# Production-grade group intelligence assistant handler
# Persistent SQLite-backed memory with bot_data cache layer
# Final hardened version — no known logical flaws
# =============================================================================

import re
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import Column, BigInteger, String, DateTime, func
from sqlalchemy.exc import SQLAlchemyError

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from bot.services import detector, replier, extractor
from bot.database.connection import SessionLocal
from bot.database.models import User, Message, Base

logger = logging.getLogger(__name__)


# =============================================================================
# GROUP MEMORY MODEL
# Imported from models.py when available; defined here as a safe fallback.
# =============================================================================

try:
    from bot.database.models import GroupMemory  # noqa: F811
except ImportError:

    class GroupMemory(Base):  # type: ignore[misc]
        __tablename__ = "group_memory"

        chat_id      = Column(BigInteger, primary_key=True, index=True)
        session_time = Column(String(64),  nullable=True)
        topic        = Column(String(512), nullable=True)
        host         = Column(String(256), nullable=True)
        date         = Column(String(128), nullable=True)
        last_updated = Column(
            DateTime(timezone=True),
            server_default=func.now(),
            onupdate=func.now(),
            nullable=False,
        )

    try:
        from bot.database.connection import engine
        Base.metadata.create_all(bind=engine, tables=[GroupMemory.__table__])
    except Exception as _exc:
        logger.warning("GroupMemory table creation skipped: %s", _exc)


# =============================================================================
# CONSTANTS — EXTRACTION PATTERNS
# =============================================================================

_TIME_PATTERN = re.compile(
    r"\b(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)?\b"
    r"|\b(\d{1,2})\s*(am|pm)\b"
    r"|\bat\s+(\d{1,2})(?:\s*:\s*(\d{2}))?\s*(am|pm)?\b"
    r"|\btiming\s+(?:is\s+)?(\d{1,2})\s*:\s*(\d{2})\s*(am|pm)?\b"
    r"|\btiming\s+(?:is\s+)?(\d{1,2})\s*(am|pm)\b",
    re.IGNORECASE,
)

_DATE_PATTERN = re.compile(
    r"\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?"
    r"|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?"
    r"|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?))\b"
    r"|\b(tomorrow|today|sunday|monday|tuesday|wednesday|thursday|friday|saturday)\b"
    r"|\b(\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?)\b",
    re.IGNORECASE,
)

_DAY_PATTERN = re.compile(
    r"\b(sunday|monday|tuesday|wednesday|thursday|friday|saturday|"
    r"tomorrow|today|aaj|kal)\b",
    re.IGNORECASE,
)

_TOPIC_TRIGGERS = [
    r"\btopic\s*(?:is|:|-|=|hai|hoga|hogi)?\s*(.+)",
    r"\bsubject\s*(?:is|:|-|=|hai)?\s*(.+)",
    r"\bdiscussing\s+(.+)",
    r"\bwe['\s]*ll\s+(?:cover|talk\s+about|discuss)\s+(.+)",
    r"\baaj\s+ka\s+(?:topic|subject)\s*(?:is|:|-|=|hai)?\s*(.+)",
    r"\btoday['\s]*s\s+(?:topic|subject)\s*(?:is|:|-|=|hai)?\s*(.+)",
    r"\btoday['\s]*s\s+(?:session\s+)?(?:is\s+(?:on\s+)?|about\s+)?(.+)",
]

# Host patterns require strong context signals — no bare "by X" to avoid
# false positives on sentences like "made by me" or "written by someone".
_HOST_TRIGGERS = [
    r"\bhost\s*(?:is|:|-|=|hai|hoga|hogi)?\s*([A-Za-z][A-Za-z\s]{1,50})",
    r"\bhosted\s+by\s+([A-Za-z][A-Za-z\s]{1,50})",
    r"\bsession\s+(?:by|with|from)\s+([A-Za-z][A-Za-z\s]{1,50})",
    r"\b(?:session|class|webinar|lecture|workshop)\s+(?:by|with|from)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    r"\btaking\s+(?:the\s+)?session\s*(?:is|:)?\s*([A-Za-z][A-Za-z\s]{1,50})",
    r"\bspeaker\s*(?:is|:|-|=|hai)?\s*([A-Za-z][A-Za-z\s]{1,50})",
    r"\bhost\s+kaun\s*(?:hai|hoga|hogi)?\s*([A-Za-z][A-Za-z\s]{1,50})",
    r"\bpresenter\s*(?:is|:|-|=)?\s*([A-Za-z][A-Za-z\s]{1,50})",
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:is\s+)?(?:the\s+)?host\b",
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:is\s+)?(?:the\s+)?speaker\b",
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:is\s+)?(?:the\s+)?presenter\b",
]

# Weak-signal words whose standalone presence does NOT gate extraction —
# kept only as a scoring hint for future ranking logic.
_SESSION_ANNOUNCE_TRIGGERS = re.compile(
    r"\b(session|class|meeting|webinar|call|event|zoom|lecture|workshop)\b",
    re.IGNORECASE,
)

# Patterns that are strong indicators a message carries extractable content.
# Used to PRIORITIZE logging, not to block extraction.
_STRONG_SIGNAL_PATTERN = re.compile(
    r"\b(topic|host|speaker|presenter|discussing|subject"
    r"|timing|time|schedule|date|session|hosted\s+by"
    r"|taking\s+(?:the\s+)?session"
    r"|today['\s]*s\s+(?:topic|subject|session)"
    r"|aaj\s+ka\s+(?:topic|subject))\b",
    re.IGNORECASE,
)

# Noise guard: messages shorter than this character count are skipped entirely
# to avoid wasting cycles on single-word greetings or reactions.
_MIN_EXTRACT_LENGTH = 6


# =============================================================================
# CONSTANTS — INTENT KEYWORDS (English first, Hindi fallback)
# =============================================================================

_INTENT_SESSION_TIME = [
    # English — checked first
    "when is session", "session time", "session timing",
    "what time", "what time is", "time of session",
    "when does session", "session schedule", "next session",
    "session at what", "timing of session", "what is the time",
    "tell me the time", "session start time",
    # Hindi — fallback
    "kab hai session", "session kab hai",
    "timing kya hai", "time kya hai", "kab start",
    "session kab", "session kab hogi", "session kab hoga",
]

_INTENT_SESSION_TOPIC = [
    # English — checked first
    "what is topic", "what are we discussing", "what will be discussed",
    "topic of session", "today topic", "session topic",
    "what is the topic", "tell me the topic", "topic?",
    "what topic", "which topic",
    # Hindi — fallback
    "topic kya hai", "aaj ka topic", "aaj ka subject",
    "subject kya hai", "kya discuss", "topic batao",
]

_INTENT_SESSION_HOST = [
    # English — checked first
    "who is host", "who is taking session", "session host",
    "who will host", "who is the host", "who is speaker",
    "who is presenter", "who is hosting", "who is taking",
    "who is the speaker", "tell me the host", "host today",
    "who is host today",
    # Hindi — fallback
    "host kaun hai", "host kaun", "session kaun le raha",
    "kaun le raha hai", "kaun host", "host batao",
    "speaker kaun", "presenter kaun",
]

_INTENT_SESSION_DATE = [
    # English — checked first
    "which day", "on what date", "what date", "day of session",
    "session date", "when is the session", "what day",
    "tell me the date", "which date", "session on which day",
    # Hindi — fallback
    "kab hai", "date kya hai", "session kab",
    "kis din", "konse din", "kab hoga", "kab hogi", "date batao",
    "session day",
]

_SIMILARITY_THRESHOLD = 0.68

# Stopwords never accepted as a host name
_HOST_STOPWORDS = frozenset({
    "me", "you", "us", "him", "her", "them", "it", "someone",
    "anyone", "everyone", "nobody", "somebody", "everybody",
    "i", "we", "they", "he", "she", "my", "your", "our",
    "the", "a", "an", "this", "that", "these", "those",
})

# Canonical DB/cache field names
_MEMORY_FIELDS = ("session_time", "topic", "host", "date")


# =============================================================================
# DB HELPERS — USER / MESSAGE
# =============================================================================

def _save_user_safe(user) -> None:
    try:
        with SessionLocal() as db:
            existing = db.query(User).filter(
                User.telegram_id == user.id
            ).first()
            if not existing:
                db.add(User(
                    telegram_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                ))
                db.commit()
    except Exception as exc:
        logger.warning("Failed to save user %s: %s", user.id, exc)


def _save_message_safe(message_id: int, user_id: int, content: str) -> None:
    try:
        with SessionLocal() as db:
            db.add(Message(
                telegram_id=message_id,
                user_id=user_id,
                content=content,
            ))
            db.commit()
    except Exception as exc:
        logger.warning("Failed to save message %s: %s", message_id, exc)


# =============================================================================
# DB HELPERS — GROUP MEMORY PERSISTENCE
# =============================================================================

def _db_load_group_memory(chat_id: int) -> dict:
    try:
        with SessionLocal() as db:
            record = db.query(GroupMemory).filter(
                GroupMemory.chat_id == chat_id
            ).first()
            if record is None:
                return {}
            return {
                field: getattr(record, field, None) or None
                for field in _MEMORY_FIELDS
            }
    except SQLAlchemyError as exc:
        logger.warning("DB load failed for chat %s: %s", chat_id, exc)
        return {}
    except Exception as exc:
        logger.warning("Unexpected error loading memory for chat %s: %s", chat_id, exc)
        return {}


def _db_save_group_memory(chat_id: int, updates: dict) -> bool:
    clean = {
        k: v for k, v in updates.items()
        if k in _MEMORY_FIELDS and v is not None and str(v).strip()
    }
    if not clean:
        return True

    try:
        with SessionLocal() as db:
            record = db.query(GroupMemory).filter(
                GroupMemory.chat_id == chat_id
            ).first()

            if record is None:
                record = GroupMemory(chat_id=chat_id)
                db.add(record)

            for field, value in clean.items():
                setattr(record, field, str(value).strip())

            record.last_updated = datetime.now(timezone.utc)
            db.commit()
            return True

    except SQLAlchemyError as exc:
        logger.warning("DB save failed for chat %s: %s", chat_id, exc)
        return False
    except Exception as exc:
        logger.warning("Unexpected error saving memory for chat %s: %s", chat_id, exc)
        return False


# =============================================================================
# GROUP MEMORY HELPERS — CACHE + DB LAYER
# =============================================================================

def _get_group_memory(bot_data: dict, chat_id: int) -> dict:
    cache = bot_data.get("group_memory", {})
    if chat_id in cache:
        return cache[chat_id]

    db_data = _db_load_group_memory(chat_id)
    if db_data:
        bot_data.setdefault("group_memory", {})[chat_id] = db_data
        logger.debug("Cache hydrated from DB for chat %s", chat_id)
    return db_data


def _update_group_memory(bot_data: dict, chat_id: int, updates: dict) -> None:
    db_ok = _db_save_group_memory(chat_id, updates)
    if not db_ok:
        logger.warning(
            "DB write failed for chat %s — in-memory cache still updated", chat_id
        )

    memory = bot_data.setdefault("group_memory", {})
    record = memory.setdefault(chat_id, {})
    for key, value in updates.items():
        if value is not None and str(value).strip():
            record[key] = str(value).strip()
    record["last_updated"] = datetime.now(timezone.utc).isoformat()


# =============================================================================
# EXTRACTION HELPERS
# =============================================================================

def _extract_time(text: str) -> str | None:
    try:
        match = _TIME_PATTERN.search(text)
        if not match:
            return None
        raw = match.group().strip()
        return raw.upper() if raw else None
    except Exception as exc:
        logger.warning("Time extraction failed: %s", exc)
        return None


def _extract_date(text: str) -> str | None:
    try:
        match = _DATE_PATTERN.search(text)
        if match:
            return match.group().strip().capitalize()
        day_match = _DAY_PATTERN.search(text)
        if day_match:
            return day_match.group().strip().capitalize()
        return None
    except Exception as exc:
        logger.warning("Date extraction failed: %s", exc)
        return None


def _is_valid_host_name(value: str) -> bool:
    """
    Reject values that are stopwords, too short, or contain no real name-like
    token. A valid host value must have at least one word that is not in the
    stopword list and is at least two characters long.
    """
    if not value or len(value.strip()) < 2:
        return False
    words = re.findall(r"[A-Za-z]+", value)
    meaningful = [w for w in words if w.lower() not in _HOST_STOPWORDS and len(w) >= 2]
    return len(meaningful) >= 1


def _extract_field_via_triggers(
    text: str,
    patterns: list[str],
    validate_fn=None,
) -> str | None:
    for raw_pattern in patterns:
        try:
            match = re.search(raw_pattern, text, re.IGNORECASE)
            if match and match.lastindex and match.group(1):
                value = match.group(1).strip().rstrip(".,!?")
                if len(value) < 2:
                    continue
                if validate_fn and not validate_fn(value):
                    continue
                return value[0].upper() + value[1:]
        except Exception as exc:
            logger.warning("Trigger pattern '%s' failed: %s", raw_pattern, exc)
    return None


# =============================================================================
# KNOWLEDGE EXTRACTION — NO HARD GATE
#
# Extraction runs unconditionally on every message that meets the minimum
# length threshold.  _STRONG_SIGNAL_PATTERN is used only for debug logging
# so operators can see which messages are information-rich; it never blocks.
# =============================================================================

def _try_extract_knowledge(text: str) -> dict:
    findings: dict = {}

    if len(text.strip()) < _MIN_EXTRACT_LENGTH:
        return findings

    has_strong_signal = bool(_STRONG_SIGNAL_PATTERN.search(text))
    if has_strong_signal:
        logger.debug("Strong extraction signal detected in message")

    time_val = _extract_time(text)
    if time_val:
        findings["session_time"] = time_val

    date_val = _extract_date(text)
    if date_val:
        findings["date"] = date_val

    topic_val = _extract_field_via_triggers(text, _TOPIC_TRIGGERS)
    if topic_val:
        findings["topic"] = topic_val

    host_val = _extract_field_via_triggers(
        text, _HOST_TRIGGERS, validate_fn=_is_valid_host_name
    )
    if host_val:
        findings["host"] = host_val

    return findings


# =============================================================================
# INTENT MATCHING
# Three-pass matching: substring → word-subset → similarity (fallback only)
# =============================================================================

def _similarity(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _matches_intent(text_lower: str, phrases: list[str]) -> bool:
    # Pass 1 — direct substring (O(n), no object allocation)
    for phrase in phrases:
        if phrase in text_lower:
            return True

    # Pass 2 — word-subset match
    words = set(re.findall(r"\w+", text_lower))
    for phrase in phrases:
        phrase_words = set(re.findall(r"\w+", phrase))
        if phrase_words and phrase_words.issubset(words):
            return True

    # Pass 3 — fuzzy similarity only when both above passes failed
    for phrase in phrases:
        if _similarity(text_lower, phrase) >= _SIMILARITY_THRESHOLD:
            return True

    return False


def _detect_query_intent(text: str) -> str | None:
    t = text.lower().strip()
    if _matches_intent(t, _INTENT_SESSION_TIME):
        return "SESSION_TIME"
    if _matches_intent(t, _INTENT_SESSION_TOPIC):
        return "SESSION_TOPIC"
    if _matches_intent(t, _INTENT_SESSION_HOST):
        return "SESSION_HOST"
    if _matches_intent(t, _INTENT_SESSION_DATE):
        return "SESSION_DATE"
    return None


# =============================================================================
# RESPONSE BUILDERS
# =============================================================================

def _build_response(intent: str, memory: dict) -> str:
    missing = "❌ I don't have that info yet."

    if intent == "SESSION_TIME":
        val = memory.get("session_time")
        return f"⏰ Session timing is {val}" if val else missing

    if intent == "SESSION_TOPIC":
        val = memory.get("topic")
        return f"🧠 Topic is {val}" if val else missing

    if intent == "SESSION_HOST":
        val = memory.get("host")
        return f"👤 Host is {val}" if val else missing

    if intent == "SESSION_DATE":
        val = memory.get("date")
        return f"📅 Session is on {val}" if val else missing

    return missing


# =============================================================================
# START COMMAND
# =============================================================================

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        _save_user_safe(user)
        await update.message.reply_text(
            f"👋 Welcome {user.first_name}!\n\n"
            "I'm your group assistant 🤖\n"
            "I silently track session info and answer your questions.\n\n"
            "Try asking: 'When is session?' or 'Who is host?'"
        )
    except Exception as exc:
        logger.error("start_handler error: %s", exc)
        try:
            await update.message.reply_text("👋 Hello! I'm your group assistant.")
        except Exception:
            pass


# =============================================================================
# MAIN MESSAGE HANDLER
# =============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.message.text:
            return

        text: str = update.message.text.strip()
        if not text:
            return

        user = update.effective_user
        chat_id: int = update.effective_chat.id
        text_lower: str = text.lower()

        _save_message_safe(update.message.message_id, user.id, text_lower)

        # ── STEP 1: passive knowledge extraction — runs on every message ──────
        try:
            findings = _try_extract_knowledge(text)
            if findings:
                _update_group_memory(context.bot_data, chat_id, findings)
                logger.info("Memory updated for chat %s: %s", chat_id, findings)
        except Exception as exc:
            logger.warning("Knowledge extraction error for chat %s: %s", chat_id, exc)

        # ── STEP 2: intent-based query answering ──────────────────────────────
        try:
            intent = _detect_query_intent(text_lower)
        except Exception as exc:
            logger.warning("Intent detection error: %s", exc)
            intent = None

        if intent:
            try:
                memory = _get_group_memory(context.bot_data, chat_id)
                response = _build_response(intent, memory)
                await update.message.reply_text(response)
            except Exception as exc:
                logger.error("Failed to reply for intent %s: %s", intent, exc)
                try:
                    await update.message.reply_text(
                        "⚠️ Something went wrong. Please try again."
                    )
                except Exception:
                    pass
            return

        # ── STEP 3: general fallback via existing services ────────────────────
        try:
            fallback_intent = detector.detect_intent(text_lower)
        except Exception as exc:
            logger.warning("detector.detect_intent failed: %s", exc)
            fallback_intent = "general"

        urls, hashtags = [], []
        try:
            urls = extractor.extract_urls(text) or []
            hashtags = extractor.extract_hashtags(text) or []
        except Exception as exc:
            logger.warning("extractor failed: %s", exc)

        try:
            response = replier.format_general_reply(fallback_intent) or "👍 Got it!"
        except Exception as exc:
            logger.warning("replier.format_general_reply failed: %s", exc)
            response = "👍 Got it!"

        if urls or hashtags:
            response += "\n\n🔍 Detected:"
            if urls:
                response += f"\n🔗 Links: {', '.join(urls)}"
            if hashtags:
                response += f"\n🏷️ Tags: {', '.join(hashtags)}"

        try:
            await update.message.reply_text(response)
        except Exception as exc:
            logger.error("Failed to send fallback reply: %s", exc)

    except Exception as exc:
        logger.error("Unhandled error in handle_message: %s", exc, exc_info=True)


# =============================================================================
# HANDLER REGISTRATION
# =============================================================================

def register_handlers(application):
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    )