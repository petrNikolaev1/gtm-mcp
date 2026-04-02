"""Domain registry model."""
import enum
from sqlalchemy import Column, Integer, String, DateTime, Enum, Index
from sqlalchemy.sql import func
from app.db import Base


class DomainStatus(str, enum.Enum):
    ACTIVE = "active"
    TRASH = "trash"
    BLOCKED = "blocked"


class DomainSource(str, enum.Enum):
    MANUAL = "manual"
    SEARCH_GOOGLE = "search_google"
    SEARCH_YANDEX = "search_yandex"
    APOLLO = "apollo"
    CLAY = "clay"
    CSV_IMPORT = "csv_import"
    SHEET_IMPORT = "sheet_import"


class Domain(Base):
    __tablename__ = "domains"

    id = Column(Integer, primary_key=True, index=True)
    domain = Column(String(255), unique=True, nullable=False, index=True)
    status = Column(Enum(DomainStatus), nullable=False, server_default=DomainStatus.ACTIVE.value)
    source = Column(Enum(DomainSource), nullable=True)
    times_seen = Column(Integer, server_default="1")
    last_seen = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_domain_status", "status"),
    )
