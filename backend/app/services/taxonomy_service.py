"""Apollo Taxonomy Service — PostgreSQL-backed with pgvector embeddings.

Stores all Apollo keywords + industries in DB. Grows from every enrichment call.
Embeddings computed via OpenAI text-embedding-3-small for semantic search.

On startup: seeds from file cache if DB is empty.
On enrichment: upserts new terms, queues embedding computation.
On query: cosine similarity search via pgvector for keyword shortlisting.
"""
import json
import logging
import asyncio
from pathlib import Path
from typing import Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

EMPLOYEE_RANGES = [
    "1,10", "11,50", "51,200", "201,500",
    "501,1000", "1001,5000", "5001,10000", "10001,",
]

# Seed file paths (baked into Docker image)
_APP_ROOT = Path("/app") if Path("/app/apollo_filters").exists() else Path(__file__).parent.parent.parent
SEED_CACHE = _APP_ROOT / "apollo_filters" / "apollo_taxonomy_cache.json"


class TaxonomyService:
    """DB-backed Apollo filter vocabulary with embedding similarity search."""

    def __init__(self):
        self._seeded = False

    async def _ensure_seeded(self, session):
        """Seed DB from file cache if empty (runs once on first call)."""
        if self._seeded:
            return
        self._seeded = True

        from sqlalchemy import select, func
        from app.models.taxonomy import ApolloTaxonomy

        count = (await session.execute(select(func.count(ApolloTaxonomy.id)))).scalar() or 0
        if count > 0:
            logger.info(f"Taxonomy DB: {count} terms already loaded")
            return

        # Seed from file
        if not SEED_CACHE.exists():
            logger.warning("No taxonomy seed file and DB is empty")
            return

        try:
            cache = json.loads(SEED_CACHE.read_text())
            terms_added = 0

            for kw, meta in cache.get("keywords", {}).items():
                kw = self._sanitize(kw).lower()
                if not kw or len(kw) < 2:
                    continue
                session.add(ApolloTaxonomy(
                    term=kw, term_type="keyword", source="seed",
                    seen_count=meta.get("seen_count", 1) if isinstance(meta, dict) else 1,
                ))
                terms_added += 1

            for ind, meta in cache.get("industries", {}).items():
                ind = self._sanitize(ind)
                if not ind:
                    continue
                session.add(ApolloTaxonomy(
                    term=ind, term_type="industry", source="seed",
                    seen_count=meta.get("seen_count", 1) if isinstance(meta, dict) else 1,
                ))
                terms_added += 1

            await session.flush()
            logger.info(f"Taxonomy DB: seeded {terms_added} terms from cache file")
        except Exception as e:
            logger.error(f"Taxonomy seed failed: {e}")

    async def get_all_industries(self, session) -> List[str]:
        """Return all known Apollo industry names from DB."""
        await self._ensure_seeded(session)
        from sqlalchemy import select
        from app.models.taxonomy import ApolloTaxonomy
        result = await session.execute(
            select(ApolloTaxonomy.term).where(ApolloTaxonomy.term_type == "industry")
            .order_by(ApolloTaxonomy.seen_count.desc())
        )
        return [r[0] for r in result.all()]

    async def get_all_keywords(self, session) -> List[str]:
        """Return all known Apollo keyword tags from DB."""
        await self._ensure_seeded(session)
        from sqlalchemy import select
        from app.models.taxonomy import ApolloTaxonomy
        result = await session.execute(
            select(ApolloTaxonomy.term).where(ApolloTaxonomy.term_type == "keyword")
            .order_by(ApolloTaxonomy.seen_count.desc())
        )
        return [r[0] for r in result.all()]

    def get_employee_ranges(self) -> List[str]:
        return EMPLOYEE_RANGES.copy()

    async def get_keyword_shortlist(
        self, query: str, openai_key: str, session, top_n: int = 50
    ) -> List[str]:
        """Semantic search: return top N keywords most similar to query using pgvector."""
        await self._ensure_seeded(session)
        from sqlalchemy import text

        # Check if embeddings exist
        has_embeddings = (await session.execute(
            text("SELECT COUNT(*) FROM apollo_taxonomy WHERE embedding IS NOT NULL AND term_type='keyword'")
        )).scalar() or 0

        if has_embeddings == 0:
            # No embeddings — return top keywords by seen_count
            logger.info("No keyword embeddings yet — returning by frequency")
            from sqlalchemy import select
            from app.models.taxonomy import ApolloTaxonomy
            result = await session.execute(
                select(ApolloTaxonomy.term).where(ApolloTaxonomy.term_type == "keyword")
                .order_by(ApolloTaxonomy.seen_count.desc()).limit(top_n)
            )
            return [r[0] for r in result.all()]

        # Embed the query
        query_emb = await self._embed_text(query, openai_key)
        if not query_emb:
            from sqlalchemy import select
            from app.models.taxonomy import ApolloTaxonomy
            result = await session.execute(
                select(ApolloTaxonomy.term).where(ApolloTaxonomy.term_type == "keyword")
                .order_by(ApolloTaxonomy.seen_count.desc()).limit(top_n)
            )
            return [r[0] for r in result.all()]

        # pgvector cosine similarity search
        emb_str = "[" + ",".join(str(x) for x in query_emb) + "]"
        result = await session.execute(text(
            f"SELECT term, 1 - (embedding <=> CAST(:emb AS vector)) as similarity "
            f"FROM apollo_taxonomy WHERE term_type='keyword' AND embedding IS NOT NULL "
            f"ORDER BY embedding <=> CAST(:emb AS vector) LIMIT :n"
        ), {"emb": emb_str, "n": top_n})
        rows = result.all()

        if rows:
            logger.info(f"Keyword shortlist: {len(rows)} from pgvector "
                        f"(top sim: {rows[0][1]:.3f}, bottom: {rows[-1][1]:.3f})")
        return [r[0] for r in rows]

    @staticmethod
    def _sanitize(term: str) -> str:
        """Clean HTML entities and garbage from Apollo tags."""
        import html
        term = html.unescape(term)  # &amp; → &, &#39; → ', etc.
        term = term.replace("&amp;", "&").replace("&#39;", "'").replace("&lt;", "<").replace("&gt;", ">")
        # Remove non-printable chars
        term = "".join(c for c in term if c.isprintable())
        return term.strip()

    async def add_from_enrichment(self, enriched_org: Dict, session, segment: str = ""):
        """Learn from an enriched Apollo company. Upserts keywords + industry."""
        await self._ensure_seeded(session)
        from sqlalchemy import text

        industry = enriched_org.get("industry")
        if industry:
            industry = self._sanitize(industry)
            await session.execute(text(
                "INSERT INTO apollo_taxonomy (term, term_type, source, last_segment, seen_count) "
                "VALUES (:term, 'industry', 'enrichment', :seg, 1) "
                "ON CONFLICT (term, term_type) DO UPDATE SET "
                "seen_count = apollo_taxonomy.seen_count + 1, "
                "last_segment = COALESCE(:seg, apollo_taxonomy.last_segment), "
                "updated_at = NOW()"
            ), {"term": industry.strip(), "seg": segment or None})

        kw_tags = enriched_org.get("keywords") or enriched_org.get("keyword_tags") or []
        if isinstance(kw_tags, str):
            kw_tags = [k.strip() for k in kw_tags.split(",")]

        for kw in kw_tags:
            kw = self._sanitize(kw).lower()
            if not kw or len(kw) < 2:
                continue
            await session.execute(text(
                "INSERT INTO apollo_taxonomy (term, term_type, source, last_segment, seen_count) "
                "VALUES (:term, 'keyword', 'enrichment', :seg, 1) "
                "ON CONFLICT (term, term_type) DO UPDATE SET "
                "seen_count = apollo_taxonomy.seen_count + 1, "
                "last_segment = COALESCE(:seg, apollo_taxonomy.last_segment), "
                "updated_at = NOW()"
            ), {"term": kw, "seg": segment or None})

    async def add_bulk_from_enrichment(self, orgs: List[Dict], session, segment: str = ""):
        """Batch learn from multiple enriched orgs."""
        for org in orgs:
            await self.add_from_enrichment(org, session, segment)
        await session.flush()

    async def rebuild_embeddings(self, openai_key: str, session, batch_size: int = 100):
        """Compute embeddings for all terms that don't have one."""
        from sqlalchemy import text

        # Get terms without embeddings
        result = await session.execute(text(
            "SELECT id, term FROM apollo_taxonomy WHERE embedding IS NULL ORDER BY seen_count DESC LIMIT 2500"
        ))
        rows = result.all()
        if not rows:
            logger.info("All taxonomy terms have embeddings")
            return 0

        logger.info(f"Computing embeddings for {len(rows)} taxonomy terms")
        computed = 0

        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            texts = [r[1] for r in batch]
            ids = [r[0] for r in batch]

            try:
                async with httpx.AsyncClient(timeout=30) as c:
                    resp = await c.post("https://api.openai.com/v1/embeddings",
                        headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                        json={"model": "text-embedding-3-small", "input": texts})
                    data = resp.json()

                for j, item in enumerate(data.get("data", [])):
                    emb = item["embedding"]
                    emb_str = "[" + ",".join(str(x) for x in emb) + "]"
                    await session.execute(text(
                        "UPDATE apollo_taxonomy SET embedding = CAST(:emb AS vector) WHERE id = :id"
                    ), {"emb": emb_str, "id": ids[j]})
                    computed += 1

                await session.flush()
                logger.info(f"Embeddings: {computed}/{len(rows)} computed")
            except Exception as e:
                logger.error(f"Embedding batch failed: {e}")
                break

        return computed

    async def stats(self, session) -> Dict:
        """Return taxonomy stats."""
        await self._ensure_seeded(session)
        from sqlalchemy import text
        kw = (await session.execute(text("SELECT COUNT(*) FROM apollo_taxonomy WHERE term_type='keyword'"))).scalar() or 0
        ind = (await session.execute(text("SELECT COUNT(*) FROM apollo_taxonomy WHERE term_type='industry'"))).scalar() or 0
        emb = (await session.execute(text("SELECT COUNT(*) FROM apollo_taxonomy WHERE embedding IS NOT NULL"))).scalar() or 0
        return {"keywords": kw, "industries": ind, "embeddings": emb}

    async def _embed_text(self, text: str, openai_key: str) -> Optional[List[float]]:
        """Embed a single text using OpenAI."""
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                resp = await c.post("https://api.openai.com/v1/embeddings",
                    headers={"Authorization": f"Bearer {openai_key}", "Content-Type": "application/json"},
                    json={"model": "text-embedding-3-small", "input": [text]})
                data = resp.json()
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning(f"Embedding failed: {e}")
            return None


# Global instance
taxonomy_service = TaxonomyService()
