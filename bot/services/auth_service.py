# bot/services/auth_service.py
# Phase 3.5: Telegram-native async RBAC with structured exception handling

from __future__ import annotations

import logging
from typing import Any

from telegram import ChatMemberAdministrator, ChatMemberOwner
from telegram.error import BadRequest, Forbidden, TelegramError

from bot.database.connection import SessionLocal
from bot.database.models import GroupAdmin, User

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Internal: private chat guard
# ---------------------------------------------------------------------------

def _is_private_chat(chat_id: int) -> bool:
    """
    Telegram private chat IDs are positive integers.
    Group / supergroup / channel IDs are negative.
    Private chats have no admin concept — skip API calls entirely.
    """
    return chat_id > 0


# ---------------------------------------------------------------------------
# Internal: Telegram API admin check (async, structured exception handling)
# ---------------------------------------------------------------------------

async def _telegram_admin_check(
    chat_id: int,
    user_id: int,
    context: Any,
) -> bool:
    """
    Calls get_chat_member and returns True if the user is an administrator
    or creator of the group.

    Exception handling follows strict specificity order:
      1. Forbidden  — bot lacks rights or is restricted
      2. BadRequest — invalid chat_id or user_id
      3. TelegramError — any other API-level failure

    Never raises. Always returns bool.
    """
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
        is_privileged = isinstance(member, (ChatMemberAdministrator, ChatMemberOwner))

        if is_privileged:
            logger.info(
                "TELEGRAM ADMIN CHECK PASSED | user=%d chat=%d status=%s",
                user_id, chat_id, member.status,
            )
        else:
            logger.debug(
                "TELEGRAM ADMIN CHECK FAILED | user=%d chat=%d status=%s",
                user_id, chat_id, member.status,
            )
        return is_privileged

    except Forbidden as exc:
        # Bot is not an admin, was kicked, or lacks get_chat_member rights
        logger.warning(
            "Bot lacks permission to verify admin status | chat=%d | %s",
            chat_id, exc,
        )
        return False

    except BadRequest as exc:
        # Invalid chat_id, user not found, or malformed request
        logger.warning(
            "TELEGRAM ADMIN CHECK bad request | user=%d chat=%d | %s",
            user_id, chat_id, exc,
        )
        return False

    except TelegramError as exc:
        # Network errors, rate limits, and all other API-level failures
        logger.warning(
            "TELEGRAM ADMIN CHECK API error | user=%d chat=%d | %s",
            user_id, chat_id, exc,
        )
        return False

    except Exception as exc:
        # Catch-all for unexpected non-Telegram errors (e.g. attribute errors)
        logger.error(
            "TELEGRAM ADMIN CHECK unexpected error | user=%d chat=%d | %s",
            user_id, chat_id, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Internal: DB superadmin check (synchronous, isolated session)
# ---------------------------------------------------------------------------

def _db_superadmin_check(user_id: int) -> bool:
    """
    Returns True if the user has role='superadmin' in the users table.
    Used as the fallback when the Telegram API is unavailable or in DMs.
    Never raises — returns False on any DB error.
    """
    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .filter(User.telegram_id == user_id, User.role == "superadmin")
            .first()
        )
        result = user is not None
        if result:
            logger.info("DB SUPERADMIN CHECK PASSED | user=%d", user_id)
        else:
            logger.debug("DB SUPERADMIN CHECK FAILED | user=%d", user_id)
        return result
    except Exception as exc:
        logger.error("_db_superadmin_check error | user=%d | %s", user_id, exc)
        return False
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------

async def is_owner(chat_id: int, user_id: int, context: Any) -> bool:
    """
    True if:
      - Telegram reports the user as 'creator' of the group, OR
      - User holds role='superadmin' in the local DB (DMs / API fallback).

    Always returns bool. Never raises. Never returns None.
    """
    try:
        if _is_private_chat(chat_id):
            return _db_superadmin_check(user_id)

        try:
            member = await context.bot.get_chat_member(chat_id, user_id)
            if isinstance(member, ChatMemberOwner):
                logger.info(
                    "OWNER CHECK PASSED (creator) | user=%d chat=%d",
                    user_id, chat_id,
                )
                return True

        except Forbidden as exc:
            logger.warning(
                "is_owner Forbidden | chat=%d | %s", chat_id, exc
            )
        except BadRequest as exc:
            logger.warning(
                "is_owner BadRequest | user=%d chat=%d | %s", user_id, chat_id, exc
            )
        except TelegramError as exc:
            logger.warning(
                "is_owner TelegramError | user=%d chat=%d | %s", user_id, chat_id, exc
            )

        # DB superadmin is the fallback owner override
        result = _db_superadmin_check(user_id)
        if not result:
            logger.warning("OWNER CHECK FAILED | user=%d chat=%d", user_id, chat_id)
        return result

    except Exception as exc:
        logger.error(
            "is_owner unexpected error | user=%d chat=%d | %s",
            user_id, chat_id, exc,
        )
        return False


async def is_admin(chat_id: int, user_id: int, context: Any) -> bool:
    """
    True if:
      1. Telegram reports the user as administrator or creator (groups only), OR
      2. User holds role='superadmin' in the local DB (DMs / API failure fallback).

    Always returns bool. Never raises. Never returns None.
    """
    try:
        if _is_private_chat(chat_id):
            return _db_superadmin_check(user_id)

        if await _telegram_admin_check(chat_id, user_id, context):
            return True

        result = _db_superadmin_check(user_id)
        if not result:
            logger.warning("ADMIN CHECK FAILED | user=%d chat=%d", user_id, chat_id)
        return result

    except Exception as exc:
        logger.error(
            "is_admin unexpected error | user=%d chat=%d | %s",
            user_id, chat_id, exc,
        )
        return False


async def can_update_session(chat_id: int, user_id: int, context: Any) -> bool:
    """
    Authorization pipeline — strict priority order:

      1. PRIMARY   : Telegram API admin check (administrator or creator).
                     Skipped for private chats.
      2. SECONDARY : DB superadmin fallback (role='superadmin' in users table).
      3. DEFAULT   : Deny (return False).

    Always returns bool. Never raises. Never returns None.
    """
    try:
        # Step 1: Telegram-native check (groups only)
        if not _is_private_chat(chat_id):
            if await _telegram_admin_check(chat_id, user_id, context):
                logger.info(
                    "SESSION UPDATE AUTHORIZED (Telegram admin) | user=%d chat=%d",
                    user_id, chat_id,
                )
                return True

        # Step 2: DB superadmin fallback
        if _db_superadmin_check(user_id):
            logger.info(
                "SESSION UPDATE AUTHORIZED (DB superadmin) | user=%d chat=%d",
                user_id, chat_id,
            )
            return True

        # Step 3: Deny
        logger.warning(
            "SESSION UPDATE DENIED | user=%d chat=%d", user_id, chat_id
        )
        return False

    except Exception as exc:
        logger.error(
            "can_update_session unexpected error | user=%d chat=%d | %s",
            user_id, chat_id, exc,
        )
        return False


# ---------------------------------------------------------------------------
# Synchronous DB-only helpers — no API calls, no async needed
# ---------------------------------------------------------------------------

def grant_admin(chat_id: int, target_user_id: int, granted_by: int) -> bool:
    """
    Insert a GroupAdmin row granting target_user_id admin rights for chat_id.
    Idempotent — silently succeeds if the row already exists.
    """
    db = SessionLocal()
    try:
        existing = (
            db.query(GroupAdmin)
            .filter(
                GroupAdmin.chat_id == chat_id,
                GroupAdmin.telegram_id == target_user_id,
            )
            .first()
        )
        if existing:
            logger.info(
                "GRANT ADMIN skipped — already exists | target=%d chat=%d",
                target_user_id, chat_id,
            )
            return True

        db.add(GroupAdmin(
            chat_id=chat_id,
            telegram_id=target_user_id,
            granted_by=granted_by,
        ))
        db.commit()
        logger.info(
            "ADMIN GRANTED | target=%d chat=%d by=%d",
            target_user_id, chat_id, granted_by,
        )
        return True
    except Exception as exc:
        db.rollback()
        logger.error(
            "grant_admin failed | target=%d chat=%d | %s",
            target_user_id, chat_id, exc,
        )
        return False
    finally:
        db.close()


def revoke_admin(chat_id: int, target_user_id: int) -> bool:
    """
    Remove GroupAdmin row for target_user_id in chat_id.
    Idempotent — succeeds even if the row does not exist.
    """
    db = SessionLocal()
    try:
        deleted = (
            db.query(GroupAdmin)
            .filter(
                GroupAdmin.chat_id == chat_id,
                GroupAdmin.telegram_id == target_user_id,
            )
            .delete()
        )
        db.commit()
        logger.info(
            "ADMIN REVOKED | target=%d chat=%d rows_deleted=%d",
            target_user_id, chat_id, deleted,
        )
        return True
    except Exception as exc:
        db.rollback()
        logger.error(
            "revoke_admin failed | target=%d chat=%d | %s",
            target_user_id, chat_id, exc,
        )
        return False
    finally:
        db.close()