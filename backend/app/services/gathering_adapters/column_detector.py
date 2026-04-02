"""Auto-detect CSV/Sheet column mappings from headers.

Maps common column name patterns to standard fields:
  domain, name, industry, employee_count, country, city, address,
  linkedin_url, phone, description, founded_year, keywords
"""
import re
from typing import Dict, List, Optional

# Standard field → list of header patterns (case-insensitive, checked in order)
COLUMN_PATTERNS: Dict[str, List[str]] = {
    "domain": [
        "website", "primary domain", "domain", "company_website", "url",
        "company website", "company url", "web", "homepage", "site",
    ],
    "name": [
        "company name", "organization name", "company_name", "name",
        "company", "org name", "organisation name", "account name",
    ],
    "industry": [
        "industry", "company_industry", "sector", "vertical",
        "organization_industry", "business type",
    ],
    "employee_count": [
        "# employees", "employees", "employee count", "employee_count",
        "estimated num employees", "estimated_num_employees",
        "company_employee_count", "headcount", "num employees",
        "number of employees", "size", "company size",
    ],
    "country": [
        "company country", "country", "company_country",
        "organization_country", "hq country", "headquarters country",
    ],
    "city": [
        "city", "company_city", "organization_city", "hq city",
    ],
    "address": [
        "company address", "address", "company_address",
        "headquarters", "hq address", "location",
    ],
    "linkedin_url": [
        "company linkedin url", "linkedin url", "linkedin_url",
        "company_linkedin_url", "linkedin", "li url",
    ],
    "phone": [
        "company phone", "phone", "company_phone", "phone number",
    ],
    "description": [
        "short description", "description", "company_description",
        "about", "bio", "summary", "seo description",
    ],
    "founded_year": [
        "founded year", "founded", "founded_year", "year founded",
        "establishment year",
    ],
    "keywords": [
        "keywords", "tags", "company_keywords", "specialties",
    ],
}


def detect_columns(headers: List[str]) -> Dict[str, str]:
    """Map standard field names to actual CSV header names.

    Returns dict like {"domain": "Website", "name": "Company Name", ...}
    Only includes fields that were detected.
    """
    mapping = {}
    headers_lower = {h.lower().strip(): h for h in headers}

    for field, patterns in COLUMN_PATTERNS.items():
        for pattern in patterns:
            if pattern in headers_lower:
                mapping[field] = headers_lower[pattern]
                break

    return mapping


def extract_company(row: Dict[str, str], mapping: Dict[str, str]) -> Optional[Dict]:
    """Extract company data from a row using the detected column mapping.

    Returns a standard company dict or None if no domain found.
    """
    domain_col = mapping.get("domain")
    if not domain_col:
        return None

    raw_domain = row.get(domain_col, "").strip()
    if not raw_domain:
        return None

    # Normalize domain
    domain = raw_domain.lower()
    if "://" in domain:
        from urllib.parse import urlparse
        domain = urlparse(domain).hostname or domain
    elif "/" in domain:
        domain = domain.split("/")[0]
    domain = domain.rstrip(".")
    if domain.startswith("www."):
        domain = domain[4:]

    if not domain or len(domain) < 3:
        return None

    # Extract all available fields
    name = row.get(mapping.get("name", ""), "").strip() or None
    industry = row.get(mapping.get("industry", ""), "").strip() or None
    country = row.get(mapping.get("country", ""), "").strip() or None
    city = row.get(mapping.get("city", ""), "").strip() or None
    address = row.get(mapping.get("address", ""), "").strip() or None
    linkedin = row.get(mapping.get("linkedin_url", ""), "").strip() or None
    phone = row.get(mapping.get("phone", ""), "").strip() or None
    description = row.get(mapping.get("description", ""), "").strip() or None
    keywords = row.get(mapping.get("keywords", ""), "").strip() or None
    founded = row.get(mapping.get("founded_year", ""), "").strip() or None

    # Employee count: try to parse as int
    emp_raw = row.get(mapping.get("employee_count", ""), "").strip()
    employee_count = None
    if emp_raw:
        try:
            employee_count = int(emp_raw.replace(",", "").replace("+", ""))
        except ValueError:
            pass

    return {
        "domain": domain,
        "name": name,
        "industry": industry,
        "employee_count": employee_count,
        "country": country,
        "city": city,
        "description": description[:500] if description else None,
        "linkedin_url": linkedin,
        "website_url": raw_domain if raw_domain.startswith("http") else f"http://{raw_domain}",
        "source_data": {
            "source": None,  # Filled by adapter
            "address": address,
            "phone": phone,
            "founded_year": founded,
            "keywords": keywords[:200] if keywords else None,
            "raw_domain": raw_domain,
        },
    }
