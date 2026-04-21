import logging
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base

logger = logging.getLogger(__name__)

Base = declarative_base()

# ─────────────────────────────────────────────────────────
# EXISTING MODEL (DO NOT TOUCH)
# ─────────────────────────────────────────────────────────

class GroupMemory(Base):
    __tablename__ = "group_memory"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(String(64), nullable=False, index=True)
    key = Column(String(256), nullable=False)
    value = Column(Text, nullable=True)
    confidence = Column(String(16), nullable=True)
    source_message_id = Column(Integer, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_group_memory_chat_key", "chat_id", "key"),
    )


# ─────────────────────────────────────────────────────────
# ADD BACK USER + MESSAGE (FIX ERROR)
# ─────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(64), unique=True, nullable=False)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))
    created_at = Column(DateTime, default=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True)
    telegram_id = Column(String(64), unique=True, nullable=False)
    user_id = Column(String(64), nullable=False)
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────
# SESSION MODEL (KEEP)
# ─────────────────────────────────────────────────────────

class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)

    chat_id = Column(String(64), nullable=False)
    created_by = Column(String(64), nullable=True)

    topic = Column(String(512), nullable=True)
    host = Column(String(256), nullable=True)
    session_time = Column(String(64), nullable=True)
    date = Column(String(128), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_sessions_chat_created", "chat_id", "created_at"),
    )