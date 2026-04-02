"""A8: Cost Estimator — pure math, no GPT.

Calculates Apollo credits needed for a given target count.
Called at every cost checkpoint before spending credits.
"""
from typing import Dict


DEFAULT_TARGET_RATE = 0.35  # 35% of Apollo companies are targets
DEFAULT_CONTACTS_PER_COMPANY = 3
DEFAULT_PER_PAGE = 100  # Apollo uses per_page=100 (was 25 — caused 4x overestimate)
EFFECTIVE_PER_PAGE = 60  # Apollo returns ~60 unique per page in practice
APOLLO_COST_PER_CREDIT = 0.01  # $0.01 per credit estimate
ENRICHMENT_CREDITS = 5  # exploration enriches top 5


def estimate_cost(
    target_count: int = 100,
    contacts_per_company: int = DEFAULT_CONTACTS_PER_COMPANY,
    target_rate: float = DEFAULT_TARGET_RATE,
    per_page: int = DEFAULT_PER_PAGE,
    total_available: int = 0,
    include_enrichment: bool = True,
) -> Dict:
    """Calculate credits and cost for a target count.

    Args:
        target_count: How many contacts the user wants (default 100)
        contacts_per_company: Contacts to extract per target company (default 3)
        target_rate: Expected target conversion rate (default 35%)
        per_page: Apollo results per page (default 25)
        total_available: Total companies available in Apollo for these filters
        include_enrichment: Include 5 enrichment credits for exploration

    Returns:
        {
            "target_contacts": 100,
            "target_companies_needed": 34,
            "companies_from_apollo": 97,
            "pages_needed": 4,
            "search_credits": 4,
            "enrichment_credits": 5,
            "total_credits": 9,
            "total_cost_usd": 0.09,
            "people_credits": target_count,
        "people_cost_usd": round(target_count * APOLLO_COST_PER_CREDIT, 4),
        "people_note": "1 credit per net-new email (people/bulk_match)",
            "target_rate_used": 0.35,
            "contacts_per_company": 3,
            "note": "Estimated. Actual target rate varies by segment."
        }
    """
    target_companies = max(1, target_count // contacts_per_company)
    companies_from_apollo = max(1, int(target_companies / target_rate)) if target_rate > 0 else target_companies * 3

    # Cap at total_available if known
    if total_available > 0:
        companies_from_apollo = min(companies_from_apollo, total_available)

    # Use effective per_page (Apollo returns ~60 unique per 100 requested)
    effective = EFFECTIVE_PER_PAGE if per_page >= 100 else per_page
    pages = max(1, (companies_from_apollo + effective - 1) // effective)
    search_credits = pages

    enrichment = 0  # No exploration phase anymore
    # People: search is FREE, but email enrichment via /people/bulk_match costs 1 credit per person
    people_credits = target_count  # 1 credit per contact for verified email
    total_credits = search_credits + enrichment + people_credits
    total_usd = total_credits * APOLLO_COST_PER_CREDIT

    return {
        "target_contacts": target_count,
        "target_companies_needed": target_companies,
        "companies_from_apollo": companies_from_apollo,
        "pages_needed": pages,
        "search_credits": search_credits,
        "enrichment_credits": enrichment,
        "total_credits": total_credits,
        "total_cost_usd": round(total_usd, 4),
        "people_credits": people_credits,
        "people_cost_usd": round(people_credits * APOLLO_COST_PER_CREDIT, 4),
        "people_note": "Search FREE, email enrichment 1 credit/person via /people/bulk_match",
        "target_rate_used": target_rate,
        "contacts_per_company": contacts_per_company,
        "note": "Estimated. Actual target rate varies by segment.",
    }


def estimate_continue(
    current_contacts: int,
    target_contacts: int,
    current_pages: int,
    contacts_per_company: int = DEFAULT_CONTACTS_PER_COMPANY,
    target_rate: float = DEFAULT_TARGET_RATE,
    per_page: int = DEFAULT_PER_PAGE,
) -> Dict:
    """Calculate cost to continue gathering from current state.

    Returns:
        {
            "current_contacts": 102,
            "target_contacts": 500,
            "contacts_needed": 398,
            "additional_pages": 5,
            "additional_credits": 5,
            "additional_cost_usd": 0.05,
            "page_offset": 5,
            "projected_total": 207,
        }
    """
    contacts_needed = max(0, target_contacts - current_contacts)
    companies_needed = max(1, contacts_needed // contacts_per_company)
    apollo_needed = max(1, int(companies_needed / target_rate)) if target_rate > 0 else companies_needed * 3
    additional_pages = max(1, (apollo_needed + per_page - 1) // per_page)
    additional_credits = additional_pages

    projected_new_targets = int(additional_pages * per_page * target_rate)
    projected_new_contacts = projected_new_targets * contacts_per_company
    projected_total = current_contacts + projected_new_contacts

    return {
        "current_contacts": current_contacts,
        "target_contacts": target_contacts,
        "contacts_needed": contacts_needed,
        "additional_pages": additional_pages,
        "additional_credits": additional_credits,
        "additional_cost_usd": round(additional_credits * APOLLO_COST_PER_CREDIT, 4),
        "page_offset": current_pages + 1,
        "projected_total": projected_total,
    }
