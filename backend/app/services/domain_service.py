"""Domain Service — trash filtering and dedup. Adapted for MCP (no blacklist file dependency)."""
from typing import List, Dict, Set, Optional
from urllib.parse import urlparse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from datetime import datetime
import logging

from app.models.domain import Domain, DomainStatus, DomainSource

logger = logging.getLogger(__name__)

# Hardcoded trash patterns (no file dependency)
BASE_TRASH: Set[str] = {
    "ya.ru", "yandex.ru", "google.com", "avito.ru", "vk.com", "dzen.ru",
    "youtube.com", "wikipedia.org", "facebook.com", "instagram.com",
    "linkedin.com", "twitter.com", "x.com", "reddit.com", "pinterest.com",
    "tiktok.com", "ok.ru", "mail.ru", "t.me",
}

TRASH_PATTERNS: List[str] = [
    "t.me", "telegram", "vk.com", "facebook.com", "instagram.com",
    "twitter.com", "x.com", "tiktok.com", "youtube.com", "ok.ru",
    "linkedin.com", "pinterest.com", "reddit.com",
    "binance.com", "bybit.com", "coinmarketcap", "coingecko",
    "coinbase.com", "tradingview.com",
    "booking.com", "agoda.com", "tripadvisor", "airbnb",
    "wikipedia.org", "wiki.", "google.com", "dzen.ru",
    "wise.com", "revolut.com", "stripe.com", "paypal",
]


def normalize_domain(raw: str) -> str:
    d = raw.strip().lower()
    if not d:
        return ""
    if "://" in d:
        parsed = urlparse(d)
        d = parsed.hostname or d
    elif "/" in d:
        d = d.split("/")[0]
    d = d.rstrip(".")
    if d.startswith("www."):
        d = d[4:]
    return d


def matches_trash_pattern(domain: str) -> bool:
    d = domain.lower()
    if d in BASE_TRASH:
        return True
    for pattern in TRASH_PATTERNS:
        if pattern in d:
            return True
    return False


class DomainService:
    async def add_domain(self, session: AsyncSession, raw_domain: str,
                         status: DomainStatus = DomainStatus.ACTIVE,
                         source: DomainSource = DomainSource.MANUAL) -> Optional[Domain]:
        domain_str = normalize_domain(raw_domain)
        if not domain_str:
            return None
        if status == DomainStatus.ACTIVE and matches_trash_pattern(domain_str):
            status = DomainStatus.TRASH
        existing = await session.execute(select(Domain).where(Domain.domain == domain_str))
        db_domain = existing.scalar_one_or_none()
        if db_domain:
            db_domain.times_seen += 1
            db_domain.last_seen = datetime.utcnow()
            return db_domain
        new_domain = Domain(domain=domain_str, status=status, source=source)
        session.add(new_domain)
        return new_domain

    async def is_trash(self, session: AsyncSession, raw_domain: str) -> bool:
        domain_str = normalize_domain(raw_domain)
        if not domain_str:
            return True
        if matches_trash_pattern(domain_str):
            return True
        result = await session.execute(select(Domain.status).where(Domain.domain == domain_str))
        row = result.scalar_one_or_none()
        return row == DomainStatus.TRASH

    async def check_domains(self, session: AsyncSession, raw_domains: List[str]) -> List[Dict[str, str]]:
        results = []
        seen: Set[str] = set()
        for raw in raw_domains:
            d = normalize_domain(raw)
            if not d:
                continue
            if matches_trash_pattern(d):
                results.append({"domain": d, "status": "trash"})
                continue
            if d in seen:
                results.append({"domain": d, "status": "known"})
                continue
            seen.add(d)
            existing = await session.execute(select(Domain).where(Domain.domain == d))
            db = existing.scalar_one_or_none()
            if db:
                results.append({"domain": d, "status": "trash" if db.status == DomainStatus.TRASH else "known"})
            else:
                results.append({"domain": d, "status": "new"})
        return results

    async def filter_domains(self, session: AsyncSession, raw_domains: List[str],
                              source: DomainSource = DomainSource.APOLLO) -> Dict[str, List[str]]:
        new, trash, duplicate = [], [], []
        seen: Set[str] = set()
        for raw in raw_domains:
            d = normalize_domain(raw)
            if not d:
                continue
            if matches_trash_pattern(d):
                trash.append(d)
                continue
            if d in seen:
                duplicate.append(d)
                continue
            seen.add(d)
            existing = await session.execute(select(Domain).where(Domain.domain == d))
            db = existing.scalar_one_or_none()
            if db:
                duplicate.append(d) if db.status != DomainStatus.TRASH else trash.append(d)
            else:
                new.append(d)
                session.add(Domain(domain=d, status=DomainStatus.ACTIVE, source=source))
        return {"new": new, "trash": trash, "duplicate": duplicate}
