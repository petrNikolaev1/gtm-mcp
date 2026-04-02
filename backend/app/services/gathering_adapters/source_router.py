"""Smart source suggestion — recommend the best source based on user input and available keys."""
import re
from typing import Any, Dict, List, Optional


def suggest_source(user_input: str, available_keys: List[str] = None) -> Dict[str, Any]:
    """Suggest the best gathering source based on user input.

    Args:
        user_input: What the user said/provided (text, URL, file path)
        available_keys: List of configured integration names (e.g. ["apollo", "smartlead"])

    Returns:
        Dict with source_type, explanation, and optional filters
    """
    available_keys = available_keys or []
    text = user_input.lower()

    # Check for Google Drive folder URL
    if re.search(r"drive\.google\.com/drive/folders/", text):
        folder_match = re.search(r"(https://drive\.google\.com/drive/folders/[a-zA-Z0-9_-]+)", user_input)
        return {
            "source_type": "google_drive.companies.folder",
            "explanation": "Detected Google Drive folder link — will import all CSV/Sheet files from the folder.",
            "filters": {"drive_url": folder_match.group(1)} if folder_match else {},
        }

    # Check for Google Sheets URL
    if re.search(r"docs\.google\.com/spreadsheets/d/", text):
        sheet_match = re.search(r"(https://docs\.google\.com/spreadsheets/d/[a-zA-Z0-9_-]+[^\s]*)", user_input)
        return {
            "source_type": "google_sheets.companies.sheet",
            "explanation": "Detected Google Sheets link — will import companies from the spreadsheet.",
            "filters": {"sheet_url": sheet_match.group(1)} if sheet_match else {},
        }

    # Check for CSV file path or mention
    if re.search(r"\.(csv|tsv)\b", text) or re.search(r"/[\w/]+\.\w+", text) or "csv" in text:
        path_match = re.search(r"((?:/[\w.-]+)+\.csv)", user_input)
        return {
            "source_type": "csv.companies.file",
            "explanation": "Detected CSV file reference — will import companies from the file.",
            "filters": {"file_path": path_match.group(1)} if path_match else {},
        }

    # Check for Google Doc URL
    if re.search(r"docs\.google\.com/document/d/", text):
        return {
            "source_type": "csv.companies.file",
            "explanation": "Detected Google Doc — will try to extract company data from the document.",
            "filters": {},
        }

    # Keyword search → Apollo (if key available)
    search_indicators = [
        "find", "search", "gather", "discover", "look for", "companies in",
        "employees", "industry", "startup", "agency", "brand",
    ]
    if any(ind in text for ind in search_indicators):
        if "apollo" in available_keys:
            return {
                "source_type": "apollo.companies.api",
                "explanation": "This looks like a company search query. Using Apollo to find matching companies.",
                "filters": {},
            }
        else:
            return {
                "source_type": "manual.companies.manual",
                "explanation": (
                    "This looks like a search query, but Apollo API key is not configured. "
                    "You can: (1) Configure your Apollo key via 'configure_integration', "
                    "or (2) Provide a CSV/Google Sheet with your company list."
                ),
                "needs_key": "apollo",
                "alternatives": ["csv.companies.file", "google_sheets.companies.sheet"],
                "filters": {},
            }

    # Fallback: suggest listing sources
    return {
        "source_type": "manual.companies.manual",
        "explanation": (
            "I'm not sure what source to use. You can provide: "
            "(1) a CSV file, (2) a Google Sheet URL, (3) a Google Drive folder URL, "
            "or (4) describe what companies you're looking for (requires Apollo key)."
        ),
        "filters": {},
    }


def list_sources() -> List[Dict[str, Any]]:
    """List all available gathering sources with descriptions and cost."""
    return [
        {
            "source_type": "apollo.companies.api",
            "name": "Apollo Search",
            "description": "Search Apollo database by keywords, locations, employee size, etc.",
            "cost": "1 credit per page (25 companies/page)",
            "requires_key": "apollo",
        },
        {
            "source_type": "csv.companies.file",
            "name": "CSV File Import",
            "description": "Import companies from a CSV file. Auto-detects column mappings.",
            "cost": "Free",
            "requires_key": None,
        },
        {
            "source_type": "google_sheets.companies.sheet",
            "name": "Google Sheet Import",
            "description": "Import companies from a Google Sheet. Must be shared with the system or set to 'Anyone with link'.",
            "cost": "Free",
            "requires_key": None,
        },
        {
            "source_type": "google_drive.companies.folder",
            "name": "Google Drive Folder Import",
            "description": "Import companies from multiple CSV/Sheet files in a Google Drive folder. Deduplicates across files.",
            "cost": "Free",
            "requires_key": None,
        },
        {
            "source_type": "manual.companies.manual",
            "name": "Manual Domain List",
            "description": "Provide a list of domains directly.",
            "cost": "Free",
            "requires_key": None,
        },
    ]
