# =============================================================================
# bot/handlers/message_handler.py
# Phase 3.5: error handler registered + missing intent triggers added
# =============================================================================

import logging
import random
import re
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler, MessageHandler, filters

from bot.services import detector, replier, extractor
from bot.services.auth_service import can_update_session
from bot.services import session_service
from bot.database.connection import SessionLocal
from bot.database.models import User, Message

logger = logging.getLogger(__name__)


# =============================================================================
# RESPONSE TEMPLATES
# =============================================================================

_RESPONSE_TEMPLATES: dict[str, list[str]] = {
    "set_ok": [
        "✅ Done, {name}! Session details have been updated.",
        "✅ Got it, {name}. I've saved the session info for everyone.",
        "✅ Session updated, {name}. The group will know when to show up!",
    ],
    "set_unauth": [
        "Hey {name}, only admins can update the session details in this group.",
        "Sorry {name}, you'll need admin rights to set session info here.",
        "Heads up {name} — session updates are restricted to group admins only.",
    ],
    "set_unclear": [
        "Hmm {name}, I couldn't quite parse that. Try something like: 'Session at 10 PM, topic: AI, host: Rahul'.",
        "I didn't catch the details, {name}. Could you rephrase? e.g. 'Session is at 9 PM, topic: Security'.",
        "Not sure I got that right, {name}. Try: 'Host is Rahul, session at 8 PM'.",
    ],
    "get_time": [
        "⏰ Hey {name}, the session is scheduled for {value}. See you there!",
        "⏰ Looks like it's at {value} today, {name}. Be ready!",
        "⏰ {name}, the session timing is set for {value}. Don't miss it!",
    ],
    "get_topic": [
        "🧠 Today's topic is '{value}', {name}. Should be a good one!",
        "🧠 {name}, we're covering '{value}' this session. Come prepared!",
        "🧠 The topic for this session is '{value}', {name}.",
    ],
    "get_host": [
        "👤 {name}, the session will be hosted by {value}.",
        "👤 {value} is taking this session, {name}. Should be great!",
        "👤 Host for this session is {value}, {name}.",
    ],
    "get_date": [
        "📅 {name}, the session is on {value}. Mark your calendar!",
        "📅 It's happening on {value}, {name}. Don't forget!",
        "📅 Session date is {value}, {name}.",
    ],
    "get_summary": [
        "📋 Here's what I have, {name}:\n{value}",
        "📋 Session details for you, {name}:\n{value}",
        "📋 {name}, here's the latest session info:\n{value}",
    ],
    "get_empty": [
        "❌ Session details haven't been announced yet, {name}.",
        "❌ Nothing stored yet, {name}. Ask an admin to set the session info.",
        "❌ I don't have any session info yet, {name}. Stay tuned!",
    ],
}


def _pick(template_key: str, **kwargs) -> str:
    templates = _RESPONSE_TEMPLATES.get(template_key, ["{name}, something went wrong."])
    return random.choice(templates).format(**kwargs)


# =============================================================================
# INTENT DETECTION
# =============================================================================

_SET_TRIGGERS = [
    "session is at", "session at", "timing is", "timing at",
    "topic is", "today topic", "aaj ka topic", "subject is",
    "host is", "host will be", "hosted by",                  # FIX: added "host will be"
    "speaker is", "presenter is",
    "session on", "meeting at", "call at", "zoom at",
    "event at", "session time is", "schedule is",
]


def _detect_set_intent(text_lower: str) -> bool:
    matched = any(trigger in text_lower for trigger in _SET_TRIGGERS)
    if matched:
        logger.debug("SET intent matched in: %r", text_lower[:80])
    else:
        logger.debug("SET intent: no trigger matched in: %r", text_lower[:80])
    return matched


def _detect_get_intent(text_lower: str) -> str | None:
    # 1. Text Normalization: Strip punctuation to create clean word tokens
    clean_text = "".join(c if c.isalnum() else " " for c in text_lower)
    words = set(clean_text.split())

    # 2. Hybrid "Contains + Token" Evaluation
    if "topic" in text_lower or "subject" in text_lower:
        logger.debug("GET TOPIC intent matched")
        return "topic"
    
    if "host" in text_lower or "speaker" in text_lower or "presenter" in text_lower:
        logger.debug("GET HOST intent matched")
        return "host"
    
    if "date" in text_lower or "day" in text_lower or "din" in text_lower:
        logger.debug("GET DATE intent matched")
        return "date"
    
    if "time" in text_lower or "kab" in text_lower or "when" in text_lower or "timing" in text_lower:
        logger.debug("GET TIME intent matched")
        return "time"
    
    # Use the clean 'words' set here so things like "details?" don't break the word match
    if "session" in text_lower and ("?" in text_lower or any(w in words for w in ["bata", "info", "detail", "details", "summary"])):
        logger.debug("GET SUMMARY intent matched")
        return "summary"

    logger.debug("GET intent: no trigger matched in: %r", text_lower[:80])
    return None


# =============================================================================
# EXTRACTION
# =============================================================================

_TIME_RE = re.compile(
    r"\b(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b"
    r"|\bat\s+(\d{1,2}(?::\d{2})?(?:\s*(?:am|pm))?)\b"
    r"|\bis\s+(\d{1,2}(?::\d{2})?\s*(?:am|pm))\b",
    re.IGNORECASE,
)

_DAY_RE = re.compile(
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday"
    r"|tomorrow|today|aaj|kal|\d{1,2}[\/\-]\d{1,2}(?:[\/\-]\d{2,4})?)\b",
    re.IGNORECASE,
)


def _safe_capture(pattern: re.Pattern, text: str) -> str | None:
    try:
        m = pattern.search(text)
        if m:
            value = next((g for g in m.groups() if g), None)
            return value.strip() if value else None
        return None
    except Exception as exc:
        logger.warning("_safe_capture failed: %s", exc)
        return None


def _extract_after_keyword(text: str, keywords: list[str]) -> str | None:
    lower = text.lower()
    for kw in keywords:
        idx = lower.find(kw)
        if idx == -1:
            continue
        remainder = text[idx + len(kw):].strip(" :")
        end = re.search(r"[,.\n!?]", remainder)
        value = remainder[: end.start()].strip() if end else remainder.strip()
        if value:
            return value[0].upper() + value[1:]
    return None


def _extract_session_data(text: str) -> dict | None:
    try:
        result: dict = {"raw_source": text}

        time_val = _safe_capture(_TIME_RE, text)
        if time_val:
            result["session_time"] = time_val.upper()
            logger.debug("Extracted session_time: %r", result["session_time"])

        day_val = _safe_capture(_DAY_RE, text)
        if day_val:
            result["event_date"] = day_val.capitalize()
            logger.debug("Extracted event_date: %r", result["event_date"])

        topic_val = _extract_after_keyword(
            text, ["topic is", "today topic", "aaj ka topic", "subject is", "topic:"]
        )
        if topic_val:
            result["topic"] = topic_val
            logger.debug("Extracted topic: %r", result["topic"])

        host_val = _extract_after_keyword(
            text, ["host is", "host will be", "hosted by", "speaker is", "presenter is"]
        )
        if host_val:
            result["host"] = host_val
            logger.debug("Extracted host: %r", result["host"])

        platform_val = _extract_after_keyword(
            text, ["on zoom", "via zoom", "on meet", "via meet", "platform is"]
        )
        if platform_val:
            result["platform"] = platform_val

        meaningful = {"session_time", "topic", "host", "event_date"}
        if not meaningful.intersection(result.keys()):
            logger.warning(
                "extraction_failed: no meaningful fields found | text=%r", text[:80]
            )
            return None

        return result

    except Exception as exc:
        logger.warning("_extract_session_data failed: %s", exc)
        return None


def _build_summary(record: dict) -> str:
    lines = []
    if record.get("session_time"):
        lines.append(f"⏰ Time: {record['session_time']}")
    if record.get("event_date"):
        lines.append(f"📅 Date: {record['event_date']}")
    if record.get("topic"):
        lines.append(f"🧠 Topic: {record['topic']}")
    if record.get("host"):
        lines.append(f"👤 Host: {record['host']}")
    if record.get("platform"):
        lines.append(f"💻 Platform: {record['platform']}")
    return "\n".join(lines) if lines else "No details available yet."


# =============================================================================
# DB HELPERS
# =============================================================================

def _save_user_safe(user) -> None:
    try:
        with SessionLocal() as db:
            existing = (
                db.query(User)
                .filter(User.telegram_id == user.id)
                .first()
            )
            if not existing:
                db.add(User(
                    telegram_id=user.id,
                    username=user.username,
                    first_name=user.first_name,
                    last_name=user.last_name,
                ))
                db.commit()
    except Exception as exc:
        logger.warning("_save_user_safe failed | user=%s | %s", user.id, exc)


def _save_message_safe(message_id: int, user_id: int, content: str) -> None:
    try:
        with SessionLocal() as db:
            db.add(Message(telegram_id=message_id, user_id=user_id, content=content))
            db.commit()
    except Exception as exc:
        logger.warning("_save_message_safe failed | msg=%s | %s", message_id, exc)


# =============================================================================
# START COMMAND
# =============================================================================

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user = update.effective_user
        _save_user_safe(user)
        await update.message.reply_text(
            f"👋 Welcome {user.first_name}!\n\n"
            "I'm your group session assistant 🤖\n"
            "Admins can set session info naturally — I'll remember it.\n"
            "Anyone can ask: 'When is session?' or 'Who is host?'"
        )
    except Exception as exc:
        logger.error("start_handler error: %s", exc)
        try:
            await update.message.reply_text("👋 Hello! I'm your group assistant.")
        except Exception:
            pass


# =============================================================================
# GLOBAL ERROR HANDLER
# Catches all unhandled framework-level errors (network drops, timeouts, etc.)
# Logs one clean warning line instead of spamming a full traceback.
# =============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.warning("Telegram framework error: %s", context.error)


# =============================================================================
# MAIN MESSAGE HANDLER
# =============================================================================

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message is None:
        return
    if update.message.text is None:
        return

    try:
        text: str = update.message.text.strip()
        if not text:
            return

        tg_user = update.effective_user
        chat_id: int = update.effective_chat.id
        user_id: int = tg_user.id
        name: str = tg_user.first_name or "there"
        text_lower: str = text.lower()

        logger.debug(
            "handle_message | user=%d chat=%d text=%r", user_id, chat_id, text[:80]
        )

        _save_user_safe(tg_user)
        _save_message_safe(update.message.message_id, user_id, text)

        # ── INTENT: SET SESSION ───────────────────────────────────────────────
        if _detect_set_intent(text_lower):
            logger.info(
                "SET INTENT detected | user=%d chat=%d text=%r",
                user_id, chat_id, text[:80],
            )

            authorized = await can_update_session(chat_id, user_id, context)
            if not authorized:
                logger.warning(
                    "auth_failed | UNAUTHORIZED SET attempt | user=%d chat=%d",
                    user_id, chat_id,
                )
                await update.message.reply_text(_pick("set_unauth", name=name))
                return

            logger.info("AUTH PASSED | user=%d chat=%d", user_id, chat_id)

            session_data = _extract_session_data(text)
            if not session_data:
                logger.warning(
                    "extraction_failed | SET INTENT got None | user=%d chat=%d text=%r",
                    user_id, chat_id, text[:80],
                )
                await update.message.reply_text(_pick("set_unclear", name=name))
                return

            logger.info(
                "extraction_ok | fields=%s | user=%d chat=%d",
                list(session_data.keys()), user_id, chat_id,
            )

            success = await session_service.set_session(
                chat_id, user_id, session_data, context
            )
            if success:
                logger.info(
                    "SESSION SET OK | user=%d chat=%d data=%s",
                    user_id, chat_id, session_data,
                )
                await update.message.reply_text(_pick("set_ok", name=name))
            else:
                logger.warning(
                    "set_session returned False | user=%d chat=%d", user_id, chat_id
                )
                await update.message.reply_text(_pick("set_unclear", name=name))
            return

        # ── INTENT: GET SESSION ───────────────────────────────────────────────
        get_field = _detect_get_intent(text_lower)
        if get_field:
            logger.info(
                "GET INTENT '%s' detected | user=%d chat=%d",
                get_field, user_id, chat_id,
            )

            record = await session_service.get_latest_session(chat_id)

            if not record:
                logger.info("GET: no session record found | chat=%d", chat_id)
                await update.message.reply_text(_pick("get_empty", name=name))
                return

            if get_field == "time":
                value = record.get("session_time")
                if value:
                    await update.message.reply_text(
                        _pick("get_time", name=name, value=value)
                    )
                else:
                    await update.message.reply_text(_pick("get_empty", name=name))

            elif get_field == "topic":
                value = record.get("topic")
                if value:
                    await update.message.reply_text(
                        _pick("get_topic", name=name, value=value)
                    )
                else:
                    await update.message.reply_text(_pick("get_empty", name=name))

            elif get_field == "host":
                value = record.get("host")
                if value:
                    await update.message.reply_text(
                        _pick("get_host", name=name, value=value)
                    )
                else:
                    await update.message.reply_text(_pick("get_empty", name=name))

            elif get_field == "date":
                value = record.get("event_date")
                if value:
                    await update.message.reply_text(
                        _pick("get_date", name=name, value=value)
                    )
                else:
                    await update.message.reply_text(_pick("get_empty", name=name))

            else:  # summary
                summary = _build_summary(record)
                await update.message.reply_text(
                    _pick("get_summary", name=name, value=summary)
                )

            return

        # ── FALLBACK ──────────────────────────────────────────────────────────
        logger.info(
            "intent_failed | no SET or GET intent matched | user=%d chat=%d text=%r",
            user_id, chat_id, text[:80],
        )

        try:
            intent = detector.detect_intent(text_lower)
        except Exception as exc:
            logger.warning("detector.detect_intent failed: %s", exc)
            intent = "general"

        urls, hashtags = [], []
        try:
            urls = extractor.extract_urls(text) or []
            hashtags = extractor.extract_hashtags(text) or []
        except Exception as exc:
            logger.warning("extractor failed: %s", exc)

        try:
            response = replier.format_general_reply(intent) or "👍 Got it!"
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
        logger.error(
            "Unhandled error in handle_message | %s", exc, exc_info=True
        )


# =============================================================================
# HANDLER REGISTRATION
# =============================================================================

def register_handlers(application):
    application.add_handler(CommandHandler("start", start_handler))
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    )
    application.add_error_handler(error_handler)