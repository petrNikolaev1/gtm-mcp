"""Google Sheet adapter — import companies from Google Sheets via service account or public CSV export."""
import csv
import io
import os
import re
from typing import Any, Dict, List, Optional
from app.services.gathering_adapters.base import BaseGatheringAdapter
from app.services.gathering_adapters.column_detector import detect_columns, extract_company


class GoogleSheetAdapter(BaseGatheringAdapter):
    source_type = "google_sheets.companies.sheet"
    description = "Import companies from Google Sheet (free)"

    def validate_filters(self, filters: Dict[str, Any]) -> tuple[bool, str]:
        sheet_url = filters.get("sheet_url")
        file_path = filters.get("file_path")  # local fallback for testing

        if file_path:
            if not os.path.isfile(file_path):
                return False, f"File not found: {file_path}"
            return True, ""

        if not sheet_url:
            return False, "Need 'sheet_url' (Google Sheets URL) or 'file_path' for local testing"

        # Validate Google Sheets URL format
        if not re.match(r"https://docs\.google\.com/spreadsheets/d/", sheet_url):
            return False, "URL must be a Google Sheets URL (https://docs.google.com/spreadsheets/d/...)"

        return True, ""

    def _extract_sheet_id(self, url: str) -> Optional[str]:
        """Extract sheet ID from Google Sheets URL."""
        match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
        return match.group(1) if match else None

    async def gather(self, filters: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        file_path = filters.get("file_path")
        sheet_url = filters.get("sheet_url")
        tab_name = filters.get("tab_name")
        column_mapping = filters.get("column_mapping")

        if file_path:
            # Local file for testing
            return self._parse_csv_file(file_path, column_mapping)

        if sheet_url:
            sheet_id = self._extract_sheet_id(sheet_url)
            if not sheet_id:
                raise ValueError(f"Cannot extract sheet ID from URL: {sheet_url}")
            csv_content = await self._fetch_sheet_csv(sheet_id, tab_name)
            return self._parse_csv_content(csv_content, column_mapping)

        raise ValueError("No sheet_url or file_path provided")

    def _parse_csv_file(self, file_path: str, column_mapping: Dict = None) -> List[Dict[str, Any]]:
        """Parse local CSV file (testing path)."""
        results = []
        seen_domains = set()

        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            mapping = column_mapping or detect_columns(headers)

            if not mapping.get("domain"):
                raise ValueError(f"Cannot find domain column in: {headers[:10]}")

            for row in reader:
                company = extract_company(row, mapping)
                if not company:
                    continue
                if company["domain"] in seen_domains:
                    continue
                seen_domains.add(company["domain"])
                company["source_data"]["source"] = "google_sheet"
                results.append(company)

        return results

    def _parse_csv_content(self, content: str, column_mapping: Dict = None) -> List[Dict[str, Any]]:
        """Parse CSV content string from Google Sheets export."""
        results = []
        seen_domains = set()

        reader = csv.DictReader(io.StringIO(content))
        headers = reader.fieldnames or []
        mapping = column_mapping or detect_columns(headers)

        if not mapping.get("domain"):
            raise ValueError(f"Cannot find domain column in: {headers[:10]}")

        for row in reader:
            company = extract_company(row, mapping)
            if not company:
                continue
            if company["domain"] in seen_domains:
                continue
            seen_domains.add(company["domain"])
            company["source_data"]["source"] = "google_sheet"
            results.append(company)

        return results

    async def _fetch_sheet_csv(self, sheet_id: str, tab_name: Optional[str] = None) -> str:
        """Fetch Google Sheet as CSV using public export URL.

        Works for:
        1. Sheets shared with the system's Google Service Account
        2. Sheets set to "Anyone with the link"
        3. Sheets in the shared Google Drive folder
        """
        import httpx

        # Build CSV export URL
        export_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv"
        if tab_name:
            export_url += f"&sheet={tab_name}"

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(export_url)
            if resp.status_code == 200:
                return resp.text

            # If public export fails, try gviz for service-account-accessible sheets
            gviz_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/gviz/tq?tqx=out:csv"
            resp = await client.get(gviz_url)
            resp.raise_for_status()
            return resp.text
