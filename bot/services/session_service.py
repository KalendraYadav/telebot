# bot/services/session_service.py

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from bot.database.connection import SessionLocal
from bot.database.models import Group, SessionEvent
from bot.services.auth_service import can_update_session

logger = logging.getLogger(__name__)

_ALLOWED_FIELDS: frozenset[str] = frozenset(
    {"session_time", "topic", "host", "event_date", "platform", "raw_source"}
)

_REQUIRED_FIELDS: frozenset[str] = frozenset(
    {"session_time", "topic", "host", "event_date"}
)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_session_data(session_data: dict) -> tuple[bool, str]:
    if not session_data:
        return False, "session_data is empty"

    unknown = set(session_data.keys()) - _ALLOWED_FIELDS
    if unknown:
        return False, f"unknown fields: {unknown}"

    has_meaningful_field = any(
        session_data.get(f) and str(session_data[f]).strip()
        for f in _REQUIRED_FIELDS
    )
    if not has_meaningful_field:
        return False, "no meaningful field (session_time/topic/host/event_date) provided"

    return True, "ok"


# ---------------------------------------------------------------------------
# Sync DB helpers
# ---------------------------------------------------------------------------

def _sync_ensure_group(chat_id: int) -> None:
    db = SessionLocal()
    try:
        exists = db.query(Group).filter(Group.chat_id == chat_id).first()
        if not exists:
            db.add(Group(chat_id=chat_id))
            db.commit()
            logger.info("GROUP AUTO-REGISTERED | chat=%d", chat_id)
    except Exception as exc:
        db.rollback()
        logger.error("_sync_ensure_group failed | chat=%d | %s", chat_id, exc)
        raise
    finally:
        db.close()


def _sync_set_session(chat_id: int, user_id: int, session_data: dict) -> bool:
    """
    FIXED: Instead of wiping the old session and creating a blank new one,
    this function now:
      1. Looks for an existing active session for this chat.
      2. If found  → only updates the fields that are present in session_data.
                     Fields not mentioned in this message are left untouched.
      3. If not found → creates a brand new session record.
    This means time + host + topic accumulate across multiple messages.
    """
    db = SessionLocal()
    try:
        # ── Step 1: look for an existing active session ──────────────────────
        existing = (
            db.query(SessionEvent)
            .filter(
                SessionEvent.chat_id == chat_id,
                SessionEvent.is_active.is_(True),
            )
            .order_by(SessionEvent.created_at.desc())
            .first()
        )

        logger.info(
            "SET SESSION | chat=%d | existing_record=%s | incoming_data=%s",
            chat_id,
            {
                "id":           existing.id           if existing else None,
                "session_time": existing.session_time if existing else None,
                "topic":        existing.topic        if existing else None,
                "host":         existing.host         if existing else None,
                "event_date":   existing.event_date   if existing else None,
            },
            session_data,
        )

        if existing:
            # ── Step 2a: MERGE — only touch fields that arrived this message ─
            # If session_data has session_time → update it.
            # If session_data does NOT have session_time → leave existing value alone.
            if session_data.get("session_time"):
                existing.session_time = session_data["session_time"]

            if session_data.get("topic"):
                existing.topic = session_data["topic"]

            if session_data.get("host"):
                existing.host = session_data["host"]

            if session_data.get("event_date"):
                existing.event_date = session_data["event_date"]

            if session_data.get("platform"):
                existing.platform = session_data["platform"]

            if session_data.get("raw_source"):
                existing.raw_source = session_data["raw_source"]

            logger.info(
                "SESSION MERGED | chat=%d | event_id=%d | merged_result=%s",
                chat_id,
                existing.id,
                {
                    "session_time": existing.session_time,
                    "topic":        existing.topic,
                    "host":         existing.host,
                    "event_date":   existing.event_date,
                    "platform":     existing.platform,
                },
            )

        else:
            # ── Step 2b: no active session exists → create a fresh one ───────
            existing = SessionEvent(
                chat_id=chat_id,
                session_time=session_data.get("session_time"),
                topic=session_data.get("topic"),
                host=session_data.get("host"),
                event_date=session_data.get("event_date"),
                platform=session_data.get("platform"),
                raw_source=session_data.get("raw_source"),
                is_active=True,
                created_by=user_id,
            )
            db.add(existing)
            logger.info(
                "SESSION CREATED (new record) | chat=%d | user=%d | data=%s",
                chat_id, user_id, session_data,
            )

        db.commit()
        logger.info("SESSION SAVED OK | chat=%d | event_id=%d", chat_id, existing.id)
        return True

    except Exception as exc:
        db.rollback()
        logger.error(
            "_sync_set_session FAILED | chat=%d | user=%d | error=%s",
            chat_id, user_id, exc,
        )
        return False
    finally:
        db.close()


def _sync_get_latest_session(chat_id: int) -> Optional[dict]:
    db = SessionLocal()
    try:
        event = (
            db.query(SessionEvent)
            .filter(
                SessionEvent.chat_id == chat_id,
                SessionEvent.is_active.is_(True),
            )
            .order_by(SessionEvent.created_at.desc())
            .first()
        )
        if event is None:
            return None

        return {
            "id":           event.id,
            "chat_id":      event.chat_id,
            "session_time": event.session_time,
            "topic":        event.topic,
            "host":         event.host,
            "event_date":   event.event_date,
            "platform":     event.platform,
            "raw_source":   event.raw_source,
            "created_by":   event.created_by,
            "created_at":   event.created_at.isoformat() if event.created_at else None,
        }
    except Exception as exc:
        logger.error(
            "_sync_get_latest_session FAILED | chat=%d | error=%s", chat_id, exc
        )
        return None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Public async API  (unchanged — same signatures as before)
# ---------------------------------------------------------------------------

async def set_session(
    chat_id: int,
    user_id: int,
    session_data: dict,
    context,
) -> bool:
    if not await can_update_session(chat_id, user_id, context):
        logger.warning(
            "SET SESSION REJECTED (unauthorized) | user=%d | chat=%d",
            user_id, chat_id,
        )
        return False

    valid, reason = _validate_session_data(session_data)
    if not valid:
        logger.warning(
            "SET SESSION REJECTED (validation failed) | chat=%d | reason=%s",
            chat_id, reason,
        )
        return False

    try:
        await asyncio.to_thread(_sync_ensure_group, chat_id)
    except Exception as exc:
        logger.error(
            "set_session: group registration failed | chat=%d | error=%s", chat_id, exc
        )
        return False

    return await asyncio.to_thread(_sync_set_session, chat_id, user_id, session_data)


async def get_latest_session(chat_id: int) -> Optional[dict]:
    return await asyncio.to_thread(_sync_get_latest_session, chat_id)