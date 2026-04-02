"""Step Executor — runs processing steps (AI, regex, scrape) on pipeline companies.

Key design principle: If a step can be solved by regex or algorithmic logic,
DO NOT use AI. AI is for tasks that genuinely require understanding.

Examples of regex steps (NO AI needed):
- Extract TLD from domain → regex
- Filter by country → exact match
- Classify by employee count range (SMALL/MEDIUM/LARGE) → comparison
- Extract year from founded_year → regex
- Check if domain contains keyword → regex
- Filter by industry keyword → string match

Examples of AI steps (AI needed):
- Classify business model from website text
- Determine if company is B2C vs B2B
- Segment by value proposition
- Assess product-market fit
"""
import re
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Step type detection ──

REGEX_INDICATORS = [
    # Size classification by employee count
    (r"(large|medium|small|micro).*(\d+).*employees", "employee_range"),
    # Country filtering
    (r"(filter|only|exclude).*country", "country_filter"),
    # Domain TLD extraction
    (r"(tld|domain extension|extract.*domain)", "tld_extract"),
    # Industry keyword match
    (r"(contains|includes|has).*industry", "industry_match"),
    # Year extraction
    (r"(founded|year|established).*(\d{4})", "year_extract"),
    # Boolean check
    (r"(has|does|is).*(website|linkedin|phone|email)", "has_field"),
]


def detect_step_type(description: str) -> str:
    """Determine if a step should use AI or regex based on the description.

    Returns: 'ai', 'regex', 'scrape', or 'filter'
    """
    desc_lower = description.lower()

    # Scrape detection
    if any(w in desc_lower for w in ["scrape", "crawl", "fetch page", "website text"]):
        return "scrape"

    # Filter detection
    if any(w in desc_lower for w in ["filter out", "remove", "exclude", "drop", "only keep", "filter only"]):
        return "filter"

    # Industry keyword match (simple string check, no AI needed)
    if re.search(r"(contains|includes|has|match)\b.*\b(industry|keyword|tag)", desc_lower):
        return "regex"
    if re.search(r"(industry|keyword|tag)\b.*\b(contains|includes|has|match)", desc_lower):
        return "regex"

    # Regex/algorithmic detection
    for pattern, _ in REGEX_INDICATORS:
        if re.search(pattern, desc_lower):
            return "regex"

    # Simple classification by known fields → regex
    simple_fields = ["country", "employee_count", "industry", "domain", "founded_year"]
    if any(f"by {field}" in desc_lower or f"based on {field}" in desc_lower for field in simple_fields):
        return "regex"

    # Employee size classification is ALWAYS regex
    if re.search(r"(small|medium|large|micro|enterprise).*(employee|size|headcount)", desc_lower):
        return "regex"
    if re.search(r"(employee|size|headcount).*(small|medium|large|micro|enterprise)", desc_lower):
        return "regex"

    # Default: AI for anything requiring understanding
    return "ai"


# ── Regex step builders ──

def build_regex_config(description: str, step_name: str) -> Dict[str, Any]:
    """Build regex step config from natural language description."""
    desc_lower = description.lower()

    # Employee size classification
    if re.search(r"(small|medium|large|micro).*(employee|size)", desc_lower) or \
       re.search(r"(employee|size).*(small|medium|large|micro)", desc_lower):
        # Extract thresholds from description
        thresholds = _extract_thresholds(description)
        return {
            "type": "employee_range",
            "input_field": "employee_count",
            "rules": thresholds or [
                {"label": "MICRO", "max": 10},
                {"label": "SMALL", "max": 50},
                {"label": "MEDIUM", "max": 200},
                {"label": "LARGE", "min": 200},
            ],
        }

    # Country filter (also catches "from Argentina and Chile" without explicit "filter")
    if "country" in desc_lower or _extract_countries(description):
        countries = _extract_countries(description)
        if countries:
            is_exclude = "exclude" in desc_lower
            return {
                "type": "country_filter",
                "input_field": "country",
                "values": countries,
                "mode": "exclude" if is_exclude else "include",
            }

    # Domain TLD extraction
    if "tld" in desc_lower or "domain extension" in desc_lower:
        return {
            "type": "regex_extract",
            "input_field": "domain",
            "pattern": r"\.([a-z]{2,})$",
            "output_format": "group",
            "group": 1,
        }

    # Industry keyword match
    if "industry" in desc_lower and any(w in desc_lower for w in ["contains", "includes", "match"]):
        keywords = _extract_quoted_terms(description)
        return {
            "type": "keyword_match",
            "input_field": "industry",
            "keywords": keywords,
            "output_format": "boolean",
        }

    # Founded year range
    if re.search(r"founded.*(before|after|between)", desc_lower):
        return {
            "type": "year_range",
            "input_field": "source_data.founded_year",
            "pattern": _extract_year_condition(description),
        }

    # Boolean field check
    if re.search(r"(has|does|is).*(website|linkedin|phone)", desc_lower):
        field = "linkedin_url" if "linkedin" in desc_lower else "website_url" if "website" in desc_lower else "source_data.phone"
        return {
            "type": "has_field",
            "input_field": field,
            "output_format": "boolean",
        }

    # Generic regex pattern
    pattern = _extract_quoted_terms(description)
    return {
        "type": "generic_regex",
        "input_field": "name",  # default
        "pattern": pattern[0] if pattern else ".*",
        "output_format": "match",
    }


def build_filter_config(description: str) -> Dict[str, Any]:
    """Build filter step config from description."""
    desc_lower = description.lower()

    # "filter out OTHER" / "remove NOT_VALID"
    reject_terms = re.findall(r"(?:filter out|remove|drop|exclude)\s+['\"]?(\w+)['\"]?", desc_lower)
    if reject_terms:
        return {"reject_values": [t.upper() for t in reject_terms]}

    # "only keep FASHION_BRAND"
    keep_terms = re.findall(r"(?:only keep|keep only|include only)\s+['\"]?(\w+)['\"]?", desc_lower)
    if keep_terms:
        return {"keep_values": [t.upper() for t in keep_terms]}

    # "column != value"
    neq_match = re.search(r"(\w+)\s*!=\s*['\"]?(\w+)['\"]?", description)
    if neq_match:
        return {"source_column": neq_match.group(1), "reject_values": [neq_match.group(2).upper()]}

    return {"reject_values": ["OTHER", "NOT_VALID", "NOT_A_MATCH"]}


# ── Step execution ──

def execute_regex_step(company_data: Dict[str, Any], config: Dict[str, Any]) -> Optional[str]:
    """Execute a regex/algorithmic step on a single company.

    Returns the result value for the output column.
    """
    step_type = config.get("type", "generic_regex")

    if step_type == "employee_range":
        emp = company_data.get("employee_count")
        if not emp:
            return "UNKNOWN"
        emp = int(emp) if isinstance(emp, (int, float, str)) and str(emp).isdigit() else 0
        for rule in config.get("rules", []):
            if "max" in rule and emp <= rule["max"]:
                return rule["label"]
            if "min" in rule and "max" not in rule and emp >= rule["min"]:
                return rule["label"]
        return "UNKNOWN"

    if step_type == "country_filter":
        country = (company_data.get("country") or "").strip()
        values = [v.lower() for v in config.get("values", [])]
        mode = config.get("mode", "include")
        if mode == "include":
            return "MATCH" if country.lower() in values else "NO_MATCH"
        else:
            return "NO_MATCH" if country.lower() in values else "MATCH"

    if step_type == "regex_extract":
        field = config.get("input_field", "domain")
        value = str(_get_nested(company_data, field) or "")
        pattern = config.get("pattern", ".*")
        match = re.search(pattern, value)
        if match:
            group = config.get("group", 0)
            return match.group(group)
        return None

    if step_type == "keyword_match":
        field = config.get("input_field", "industry")
        value = str(_get_nested(company_data, field) or "").lower()
        keywords = config.get("keywords", [])
        return "MATCH" if any(kw.lower() in value for kw in keywords) else "NO_MATCH"

    if step_type == "has_field":
        field = config.get("input_field", "website_url")
        value = _get_nested(company_data, field)
        return "YES" if value and str(value).strip() else "NO"

    if step_type == "year_range":
        # TODO: implement year comparison
        return None

    if step_type == "generic_regex":
        field = config.get("input_field", "name")
        value = str(_get_nested(company_data, field) or "")
        pattern = config.get("pattern", ".*")
        match = re.search(pattern, value, re.IGNORECASE)
        return match.group(0) if match else None

    return None


def execute_filter_step(step_results: Dict[str, str], config: Dict[str, Any]) -> bool:
    """Check if a company passes the filter step. Returns True to KEEP, False to remove."""
    if not step_results:
        return False

    last_value = list(step_results.values())[-1] if step_results else ""
    if not last_value:
        return False
    last_upper = str(last_value).upper()

    reject_values = config.get("reject_values", [])
    if reject_values:
        return not any(rv in last_upper for rv in reject_values)

    keep_values = config.get("keep_values", [])
    if keep_values:
        return any(kv in last_upper for kv in keep_values)

    source_col = config.get("source_column")
    if source_col and source_col in step_results:
        col_value = str(step_results[source_col]).upper()
        if reject_values:
            return not any(rv in col_value for rv in reject_values)

    return True


# ── Helpers ──

def _get_nested(data: Dict, field: str) -> Any:
    """Get a nested field value: 'source_data.phone' → data['source_data']['phone']."""
    parts = field.split(".")
    current = data
    for part in parts:
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _extract_thresholds(description: str) -> List[Dict]:
    """Extract employee count thresholds from description like 'LARGE (100+), MEDIUM (20-100), SMALL (<20)'."""
    results = []
    patterns = re.findall(r"(\w+)\s*[\(:]?\s*(\d+)\s*[-–]\s*(\d+)\s*[\)]?", description)
    for label, low, high in patterns:
        results.append({"label": label.upper(), "min": int(low), "max": int(high)})

    single = re.findall(r"(\w+)\s*[\(:]?\s*[<>]?\s*(\d+)\s*\+?\s*[\)]?", description)
    for label, val in single:
        if label.upper() not in [r["label"] for r in results]:
            if "<" in description[:description.index(val)] if val in description else False:
                results.append({"label": label.upper(), "max": int(val)})
            else:
                results.append({"label": label.upper(), "min": int(val)})

    return results if results else None


def _extract_countries(description: str) -> List[str]:
    """Extract country names from description."""
    known = [
        "Argentina", "Chile", "Colombia", "Mexico", "Peru", "Uruguay", "Ecuador",
        "Venezuela", "Panama", "Paraguay", "Guatemala", "Bolivia", "Costa Rica",
        "Brazil", "USA", "UK", "Germany", "France", "Spain", "Italy", "India",
    ]
    found = [c for c in known if c.lower() in description.lower()]
    return found if found else []


def _extract_quoted_terms(description: str) -> List[str]:
    """Extract quoted or CAPS_LOCKED terms from description."""
    quoted = re.findall(r"['\"]([^'\"]+)['\"]", description)
    caps = re.findall(r"\b([A-Z_]{3,})\b", description)
    return quoted + caps


def _extract_year_condition(description: str) -> str:
    """Extract year condition like 'before 2010' or 'after 2015'."""
    match = re.search(r"(before|after|since)\s*(\d{4})", description.lower())
    if match:
        return f"{match.group(1)} {match.group(2)}"
    return ""


# ── Essential columns (cannot be removed) ──

ESSENTIAL_COLUMNS = [
    "domain", "name", "industry", "employee_count", "country", "city",
    "is_target", "analysis_segment", "analysis_confidence", "analysis_reasoning",
    "scrape_status", "status",
]

ESSENTIAL_STEP_NAMES = ["website_scrape", "icp_analysis"]
