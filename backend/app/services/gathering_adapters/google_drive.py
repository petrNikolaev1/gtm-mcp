"""Google Drive folder adapter — import companies from multiple files in a Drive folder."""
import csv
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional
from app.services.gathering_adapters.base import BaseGatheringAdapter
from app.services.gathering_adapters.column_detector import detect_columns, extract_company


class GoogleDriveAdapter(BaseGatheringAdapter):
    source_type = "google_drive.companies.folder"
    description = "Import companies from multiple files in Google Drive folder (free)"

    def validate_filters(self, filters: Dict[str, Any]) -> tuple[bool, str]:
        folder_path = filters.get("folder_path")
        drive_url = filters.get("drive_url")

        if folder_path:
            if not os.path.isdir(folder_path):
                return False, f"Folder not found: {folder_path}"
            return True, ""

        if drive_url:
            if not re.match(r"https://drive\.google\.com/drive/folders/", drive_url):
                return False, "URL must be a Google Drive folder URL"
            return True, ""

        return False, "Need 'folder_path' or 'drive_url'"

    async def gather(self, filters: Dict[str, Any], **kwargs) -> List[Dict[str, Any]]:
        folder_path = filters.get("folder_path")
        drive_url = filters.get("drive_url")
        column_mapping = filters.get("column_mapping")

        if folder_path:
            return self._parse_local_folder(folder_path, column_mapping)

        if drive_url:
            folder_id = self._extract_folder_id(drive_url)
            if not folder_id:
                raise ValueError(f"Cannot extract folder ID from URL: {drive_url}")
            return await self._parse_drive_folder(folder_id, column_mapping)

        raise ValueError("No folder_path or drive_url provided")

    def _extract_folder_id(self, url: str) -> Optional[str]:
        """Extract folder ID from Google Drive folder URL."""
        match = re.search(r"/folders/([a-zA-Z0-9_-]+)", url)
        return match.group(1) if match else None

    def _parse_local_folder(self, folder_path: str, column_mapping: Dict = None) -> List[Dict[str, Any]]:
        """Parse all CSV files in a local folder with cross-file dedup."""
        results = []
        seen_domains = set()
        folder = Path(folder_path)

        # Find all CSV files in folder
        csv_files = sorted(folder.glob("*.csv"))
        if not csv_files:
            raise ValueError(f"No CSV files found in {folder_path}")

        for csv_file in csv_files:
            file_results = self._parse_one_csv(
                str(csv_file), column_mapping, seen_domains
            )
            results.extend(file_results)

        return results

    def _parse_one_csv(
        self, file_path: str, column_mapping: Dict = None,
        seen_domains: set = None,
    ) -> List[Dict[str, Any]]:
        """Parse a single CSV file, skipping already-seen domains."""
        if seen_domains is None:
            seen_domains = set()

        results = []
        filename = os.path.basename(file_path)

        with open(file_path, "r", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            mapping = column_mapping or detect_columns(headers)

            if not mapping.get("domain"):
                # Skip files without domain column
                return []

            for row in reader:
                company = extract_company(row, mapping)
                if not company:
                    continue

                # Cross-file dedup within this gather call
                if company["domain"] in seen_domains:
                    continue
                seen_domains.add(company["domain"])

                company["source_data"]["source"] = "google_drive"
                company["source_data"]["source_file"] = filename
                results.append(company)

        return results

    async def _parse_drive_folder(self, folder_id: str, column_mapping: Dict = None) -> List[Dict[str, Any]]:
        """Fetch and parse all files from a Google Drive folder.

        Uses Google Drive API (via service account) to list files,
        then downloads each as CSV and parses.
        """
        import httpx
        import tempfile

        results = []
        seen_domains = set()

        # List files in folder via Drive API
        file_list = await self._list_drive_files(folder_id)
        if not file_list:
            raise ValueError(f"No files found in Drive folder {folder_id}")

        for file_info in file_list:
            file_id = file_info["id"]
            mime = file_info.get("mimeType", "")
            name = file_info.get("name", "unknown")

            # Download file content
            if "spreadsheet" in mime:
                # Google Sheet — export as CSV
                content = await self._export_sheet_csv(file_id)
            elif mime == "text/csv" or name.endswith(".csv"):
                content = await self._download_file(file_id)
            else:
                continue  # Skip non-CSV/Sheet files

            # Parse content
            tmp = tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode="w")
            tmp.write(content)
            tmp.close()

            try:
                file_results = self._parse_one_csv(tmp.name, column_mapping, seen_domains)
                # Override source_file with Drive filename
                for r in file_results:
                    r["source_data"]["source_file"] = name
                results.extend(file_results)
            finally:
                os.unlink(tmp.name)

        return results

    async def _list_drive_files(self, folder_id: str) -> List[Dict]:
        """List files in a Google Drive folder using the service account."""
        import httpx

        token = await self._get_service_account_token()
        if not token:
            raise ValueError("Google Service Account not configured")

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                "https://www.googleapis.com/drive/v3/files",
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "q": f"'{folder_id}' in parents and trashed=false",
                    "fields": "files(id,name,mimeType)",
                    "pageSize": 100,
                },
            )
            resp.raise_for_status()
            return resp.json().get("files", [])

    async def _export_sheet_csv(self, file_id: str) -> str:
        """Export a Google Sheet as CSV."""
        import httpx

        token = await self._get_service_account_token()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}/export",
                headers={"Authorization": f"Bearer {token}"},
                params={"mimeType": "text/csv"},
            )
            resp.raise_for_status()
            return resp.text

    async def _download_file(self, file_id: str) -> str:
        """Download a file from Google Drive."""
        import httpx

        token = await self._get_service_account_token()
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                params={"alt": "media"},
            )
            resp.raise_for_status()
            return resp.text

    async def _get_service_account_token(self) -> Optional[str]:
        """Get access token from Google Service Account credentials.

        Uses the same service account as the main app's Google Sheets integration.
        Looks for GOOGLE_SERVICE_ACCOUNT_JSON env var or credentials file.
        """
        import json
        import time

        creds_json = os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON")
        creds_file = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")

        if not creds_json and creds_file and os.path.isfile(creds_file):
            with open(creds_file) as f:
                creds_json = f.read()

        if not creds_json:
            return None

        try:
            import jwt
            creds = json.loads(creds_json)
            now = int(time.time())
            payload = {
                "iss": creds["client_email"],
                "scope": "https://www.googleapis.com/auth/drive.readonly",
                "aud": "https://oauth2.googleapis.com/token",
                "iat": now,
                "exp": now + 3600,
            }
            signed = jwt.encode(payload, creds["private_key"], algorithm="RS256")

            import httpx
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                        "assertion": signed,
                    },
                )
                resp.raise_for_status()
                return resp.json()["access_token"]
        except ImportError:
            # PyJWT not installed — fall back to no auth
            return None
        except Exception:
            return None
