import logging
import datetime
from datetime import datetime as dt

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

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
    updated_at = Column(DateTime, default=dt.utcnow, onupdate=dt.utcnow, nullable=False)
    created_at = Column(DateTime, default=dt.utcnow, nullable=False)

    __table_args__ = (
        Index("ix_group_memory_chat_key", "chat_id", "key"),
    )


# ─────────────────────────────────────────────────────────
# USER MODEL (MERGED VERSION)
# ─────────────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)

    telegram_id = Column(BigInteger, unique=True, nullable=False, index=True)
    username = Column(String(255))
    first_name = Column(String(255))
    last_name = Column(String(255))

    # Your existing
    created_at = Column(DateTime, default=dt.utcnow)

    # Claude additions
    is_active = Column(Boolean, nullable=False, default=True)
    role = Column(String(32), nullable=False, default="member")

    messages = relationship("Message", back_populates="user", lazy="select")


# ─────────────────────────────────────────────────────────
# MESSAGE MODEL (MERGED VERSION)
# ─────────────────────────────────────────────────────────

class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, autoincrement=True)

    telegram_id = Column(BigInteger, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    content = Column(Text)

    created_at = Column(DateTime, default=func.now())

    user = relationship("User", back_populates="messages")


# ─────────────────────────────────────────────────────────
# SESSION MODEL (YOUR EXISTING — KEEP)
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

    created_at = Column(DateTime, default=dt.utcnow, nullable=False)
    updated_at = Column(DateTime, default=dt.utcnow, onupdate=dt.utcnow, nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_sessions_chat_created", "chat_id", "created_at"),
    )


# ─────────────────────────────────────────────────────────
# NEW TABLES (CLAUDE — ADD SAFE)
# ─────────────────────────────────────────────────────────

class Group(Base):
    __tablename__ = "groups"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, unique=True, nullable=False, index=True)
    title = Column(String(256))
    is_active = Column(Boolean, nullable=False, default=True)
    plan = Column(String(32), nullable=False, default="free")

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    admins = relationship("GroupAdmin", back_populates="group", lazy="select")
    session_events = relationship("SessionEvent", back_populates="group", lazy="select")


class GroupAdmin(Base):
    __tablename__ = "group_admins"
    __table_args__ = (UniqueConstraint("chat_id", "telegram_id"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("groups.chat_id"), nullable=False, index=True)
    telegram_id = Column(BigInteger, nullable=False)
    granted_by = Column(BigInteger, nullable=True)

    granted_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    group = relationship("Group", back_populates="admins")


class SessionEvent(Base):
    __tablename__ = "session_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    chat_id = Column(BigInteger, ForeignKey("groups.chat_id"), nullable=False, index=True)

    session_time = Column(String(32))
    topic = Column(String(512))
    host = Column(String(256))
    event_date = Column(String(64))
    platform = Column(String(128))
    raw_source = Column(Text)

    is_active = Column(Boolean, nullable=False, default=True, index=True)
    created_by = Column(BigInteger)

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.datetime.now(datetime.timezone.utc),
        onupdate=lambda: datetime.datetime.now(datetime.timezone.utc),
    )

    group = relationship("Group", back_populates="session_events")