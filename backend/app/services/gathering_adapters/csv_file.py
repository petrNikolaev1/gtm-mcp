"""CSV file adapter — import companies from CSV files with auto-column detection."""
import csv
import os
from typing import Any, Dict, List
from app.services.gathering_adapters.base import BaseGatheringAdapter
from app.services.gathering_adapters.column_detector import detect_columns, extract_company


class CSVFileAdapter(BaseGatheringAdapter):
    source_type = "csv.companies.file"
    description = "Import companies from CSV file (free)"

    def validate_filters(self, filters: Dict[str, Any]) -> tuple[bool, str]:
        file_path = filters.get("file_path")
        file_url = filters.get("file_url")
        if not file_path and not file_url:
            return False, "Need 'file_path' or 'file_url' to import CSV"
        if file_path and not os.path.isfile(file_path):
            return False, f"File not found: {file_path}"
        return True, ""

    async def gather(self, filters: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        file_path = filters.get("file_path")
        file_url = filters.get("file_url")
        column_mapping = filters.get("column_mapping")

        if file_url and not file_path:
            file_path = await self._download_csv(file_url)

        return self._parse_csv(file_path, column_mapping)

    def _parse_csv(self, file_path: str, column_mapping: Dict = None) -> List[Dict[str, Any]]:
        """Parse CSV file with auto-column detection."""
        results = []
        seen_domains = set()

        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []

            # Auto-detect or use provided mapping
            if column_mapping:
                mapping = column_mapping
            else:
                mapping = detect_columns(headers)

            if not mapping.get("domain"):
                raise ValueError(
                    f"Cannot find a domain/website column in headers: {headers[:10]}. "
                    f"Provide explicit column_mapping."
                )

            for row in reader:
                company = extract_company(row, mapping)
                if not company:
                    continue

                # Dedup within file
                if company["domain"] in seen_domains:
                    continue
                seen_domains.add(company["domain"])

                # Tag source
                company["source_data"]["source"] = "csv_file"
                company["source_data"]["source_file"] = os.path.basename(file_path)
                results.append(company)

        return results

    async def _download_csv(self, url: str) -> str:
        """Download CSV from URL to temp file."""
        import tempfile
        import httpx

        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            resp = await client.get(url)
            resp.raise_for_status()

        tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False)
        tmp.write(resp.content)
        tmp.close()
        return tmp.name
