"""Apollo Organization API adapter — searches companies via Apollo API."""
from typing import Any, Dict, List
from app.services.gathering_adapters.base import BaseGatheringAdapter


class ApolloOrgApiAdapter(BaseGatheringAdapter):
    source_type = "apollo.companies.api"
    description = "Apollo org search API (1 credit per page)"

    def __init__(self, apollo_service=None):
        self._apollo = apollo_service

    def validate_filters(self, filters: Dict[str, Any]) -> tuple[bool, str]:
        if not filters.get("q_organization_keyword_tags") and not filters.get("organization_locations"):
            return False, "Need at least keyword_tags or locations"
        return True, ""

    def estimate_cost(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        max_pages = filters.get("max_pages", 10)
        per_page = filters.get("per_page", 25)
        return {"credits": max_pages, "cost_usd": 0, "max_results": max_pages * per_page}

    async def gather(self, filters: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        if not self._apollo:
            raise ValueError("Apollo service not configured")

        keyword_tags = filters.get("q_organization_keyword_tags", [])
        locations = filters.get("organization_locations")
        num_employees = filters.get("organization_num_employees_ranges")
        funding_stages = filters.get("organization_latest_funding_stage_cd")
        industry_tag_ids = filters.get("organization_industry_tag_ids")
        strategy = filters.get("filter_strategy", "keywords_only")
        max_pages = filters.get("max_pages", 10)
        per_page = filters.get("per_page", 100)

        # CRITICAL: NEVER combine industry_tag_ids + keyword_tags in one call
        # Apollo treats them as AND which SHRINKS results to near zero
        if strategy == "industry_first" and industry_tag_ids:
            search_keyword_tags = []  # industry only
            search_industry_ids = industry_tag_ids
        else:
            search_keyword_tags = keyword_tags  # keywords only
            search_industry_ids = None

        orgs = await self._apollo.search_organizations_all_pages(
            keyword_tags=search_keyword_tags,
            industry_tag_ids=search_industry_ids,
            locations=locations,
            num_employees_ranges=num_employees,
            latest_funding_stages=funding_stages,
            max_pages=max_pages,
            per_page=per_page,
        )

        results = []
        for org in orgs:
            domain = org.get("primary_domain") or org.get("domain") or org.get("website_url", "")
            if not domain:
                continue
            domain = domain.strip().lower()
            if domain.startswith("http"):
                from urllib.parse import urlparse
                domain = urlparse(domain).hostname or domain
            if domain.startswith("www."):
                domain = domain[4:]

            company = _extract_company(org, domain)
            # Fix Apollo name censoring (Cla*****, RE Par*****)
            if company["name"] and "*" in company["name"]:
                company["name"] = _clean_name(company["name"], domain)
            results.append(company)

        return results


def _clean_name(name: str, domain: str) -> str:
    """Fix Apollo censored names. If name has *, derive from domain."""
    if not name or "*" not in name:
        return name
    # Use domain to build a clean name: "clarifai.com" → "Clarifai"
    base = domain.split(".")[0]
    # Title case, handle hyphens
    return base.replace("-", " ").title()


def _extract_company(org: Dict, domain: str) -> Dict[str, Any]:
    """Extract company data from Apollo org/account response — handles both field formats."""

    # Industry: try multiple field names
    industry = (
        org.get("industry")
        or org.get("organization_industry")
        or _guess_industry_from_keywords(org)
    )

    # Employee count: accounts use different field names
    employee_count = (
        org.get("estimated_num_employees")
        or org.get("organization_num_employees")
        or org.get("num_contacts")  # fallback: at least shows something
    )

    # Employee range
    employee_range = org.get("organization_num_employees_ranges")

    # Revenue
    revenue = org.get("organization_revenue_printed") or org.get("annual_revenue_printed")
    revenue_raw = org.get("organization_revenue") or org.get("annual_revenue")

    # City: accounts use organization_city
    city = org.get("city") or org.get("organization_city")
    state = org.get("state") or org.get("organization_state")
    country = org.get("country") or org.get("organization_country")

    # LinkedIn
    linkedin = org.get("linkedin_url") or org.get("organization_linkedin_url")

    # Description
    description = org.get("short_description") or org.get("seo_description")

    # Founded
    founded_year = org.get("founded_year")

    # Build enriched source_data summary for UI
    return {
        "domain": domain,
        "name": org.get("name"),
        "industry": industry,
        "employee_count": employee_count,
        "employee_range": employee_range,
        "country": country,
        "city": f"{city}, {state}" if city and state else (city or state),
        "description": description,
        "linkedin_url": linkedin,
        "website_url": org.get("website_url"),
        "source_data": {
            # Store ALL useful fields (not the full 70-field blob)
            "apollo_id": org.get("id") or org.get("organization_id"),
            "name": org.get("name"),
            "domain": domain,
            "industry": industry,
            "employee_count": employee_count,
            "employee_range": employee_range,
            "revenue": revenue,
            "revenue_raw": revenue_raw,
            "founded_year": founded_year,
            "country": country,
            "city": city,
            "state": state,
            "linkedin_url": linkedin,
            "website_url": org.get("website_url"),
            "phone": org.get("phone") or org.get("primary_phone"),
            "num_contacts_in_apollo": org.get("num_contacts"),
            "sic_codes": org.get("sic_codes"),
            "naics_codes": org.get("naics_codes"),
            "languages": org.get("languages"),
            "alexa_ranking": org.get("alexa_ranking"),
            "headcount_6m_growth": org.get("organization_headcount_six_month_growth"),
            "headcount_12m_growth": org.get("organization_headcount_twelve_month_growth"),
        },
    }


def _guess_industry_from_keywords(org: Dict) -> str:
    """Guess industry from SIC/NAICS codes or name when field is missing."""
    sic = org.get("sic_codes") or []
    naics = org.get("naics_codes") or []
    if sic or naics:
        # Common SIC code prefixes
        codes = sic + naics
        code_str = " ".join(str(c) for c in codes)
        if any(str(c).startswith("73") for c in codes):
            return "Information Technology"
        if any(str(c).startswith("54") for c in naics):
            return "Professional Services"
        if any(str(c).startswith("51") for c in naics):
            return "Information Technology"
    return ""
