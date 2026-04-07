"""Google Sheets tools — create, export contacts, read for blacklist.

Auth: Service account via GOOGLE_SERVICE_ACCOUNT_JSON (inline JSON) or
      GOOGLE_APPLICATION_CREDENTIALS (file path).
All sheets created on Shared Drive (GOOGLE_SHARED_DRIVE_ID required).
"""
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Contact headers — only fields that can actually be filled from Apollo + pipeline data
CONTACT_HEADERS = [
    # Person (from enrichment)
    "first name", "last name", "Position", "Seniority", "Linkedin",
    "target_lead_email", "Phone",
    # Company (from enrichment org_data — the RICH source)
    "Company", "Website", "Company Location", "Country",
    "Industry", "Employees", "Founded", "Funding Stage",
    "Revenue", "Keywords", "Description",
    # Pipeline (from classification + campaign)
    "segment", "target_confidence", "target_reasoning",
    "Lead Source", "campaign",
]


def _build_service(config):
    """Build Google Sheets + Drive API services from config credentials."""
    sa_json = config.get("google_service_account_json")
    creds_path = config.get("google_application_credentials")

    if not sa_json and not creds_path:
        return None, None

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    SCOPES = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    if sa_json:
        info = json.loads(sa_json) if isinstance(sa_json, str) else sa_json
        creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    else:
        creds = service_account.Credentials.from_service_account_file(creds_path, scopes=SCOPES)

    sheets_svc = build("sheets", "v4", credentials=creds)
    drive_svc = build("drive", "v3", credentials=creds)
    return sheets_svc, drive_svc


# ---------------------------------------------------------------------------
# Create sheet on Shared Drive
# ---------------------------------------------------------------------------

async def sheets_create(
    title: str, share_with: str = "", *, config=None,
) -> dict:
    """Create a Google Sheet on the Shared Drive with standard contact headers.

    Returns sheet_id and sheet_url. Optionally shares with an email (editor access).
    """
    config = config or _default_config()
    drive_id = config.get("google_shared_drive_id")
    if not drive_id:
        return {"success": False, "error": "GOOGLE_SHARED_DRIVE_ID not configured"}

    sheets_svc, drive_svc = _build_service(config)
    if not sheets_svc:
        return {"success": False, "error": "Google credentials not configured (GOOGLE_SERVICE_ACCOUNT_JSON or GOOGLE_APPLICATION_CREDENTIALS)"}

    try:
        # Create via Drive API on Shared Drive
        file_meta = {
            "name": title,
            "mimeType": "application/vnd.google-apps.spreadsheet",
            "parents": [drive_id],
        }
        created = drive_svc.files().create(
            body=file_meta, supportsAllDrives=True, fields="id,webViewLink",
        ).execute()
        sheet_id = created["id"]
        sheet_url = created.get("webViewLink", f"https://docs.google.com/spreadsheets/d/{sheet_id}")

        # Headers written later by sheets_export_contacts (after dynamic column filtering)
        # Only set formatting here — bold + freeze row 1

        # Bold + freeze header row
        sheets_svc.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [
                {"repeatCell": {
                    "range": {"sheetId": 0, "startRowIndex": 0, "endRowIndex": 1},
                    "cell": {"userEnteredFormat": {"textFormat": {"bold": True},
                             "backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}}},
                    "fields": "userEnteredFormat(textFormat,backgroundColor)",
                }},
                {"updateSheetProperties": {
                    "properties": {"sheetId": 0, "gridProperties": {"frozenRowCount": 1}},
                    "fields": "gridProperties.frozenRowCount",
                }},
            ]},
        ).execute()

        # Share with email if provided
        if share_with:
            drive_svc.permissions().create(
                fileId=sheet_id,
                supportsAllDrives=True,
                body={"type": "user", "role": "writer", "emailAddress": share_with},
                sendNotificationEmail=False,
            ).execute()

        return {"success": True, "data": {
            "sheet_id": sheet_id, "sheet_url": sheet_url, "title": title,
            "shared_with": share_with or None,
        }}
    except Exception as exc:
        logger.error("sheets_create failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Export contacts to sheet
# ---------------------------------------------------------------------------

async def sheets_export_contacts(
    project: str, campaign_slug: str = "", sheet_id: str = "",
    *, config=None, workspace=None,
) -> dict:
    """Export project contacts to a Google Sheet.

    If sheet_id provided → clear and re-export ALL contacts (full replace, same URL).
    If not → create new sheet, return URL.
    If campaign_slug → filter contacts by that campaign's segment.
    """
    config = config or _default_config()
    workspace = workspace or _default_workspace()

    # Load contacts
    contacts = workspace.load(project, "contacts.json")
    if not contacts:
        return {"success": False, "error": f"No contacts.json in project {project}"}

    # Filter by campaign segment if provided
    campaign_data = None
    if campaign_slug:
        campaign_data = workspace.load(project, f"campaigns/{campaign_slug}/campaign.yaml")
        if campaign_data and campaign_data.get("segment"):
            segment = campaign_data["segment"]
            contacts = [c for c in contacts if c.get("segment") == segment]

    if not contacts:
        return {"success": False, "error": "No contacts match the filter"}

    # Join with run file companies to get classification + apollo_data
    company_data: dict[str, dict] = {}  # domain → {apollo_data, classification}
    run_ids = (campaign_data or {}).get("run_ids", [])
    if not run_ids:
        # Scan for latest run file
        import os
        runs_dir = workspace._project_dir(project) / "runs"
        if runs_dir.exists():
            run_files = sorted(runs_dir.glob("run-*.json"))
            if run_files:
                run_ids = [run_files[-1].stem]

    for run_id in run_ids:
        run_data = workspace.load(project, f"runs/{run_id}.json")
        if run_data and isinstance(run_data.get("companies"), dict):
            for domain, comp in run_data["companies"].items():
                cls = comp.get("classification", {})
                apollo = comp.get("apollo_data", {})
                company_data[domain] = {
                    "name": comp.get("name", ""),
                    "confidence": cls.get("confidence", ""),
                    "reasoning": cls.get("reasoning", ""),
                    "industry": apollo.get("industry", ""),
                    "employee_count": apollo.get("employee_count", ""),
                    "employee_range": apollo.get("employee_range", ""),
                    "country": apollo.get("country", ""),
                    "city": apollo.get("city", ""),
                    "state": apollo.get("state", ""),
                    "revenue": apollo.get("revenue", ""),
                    "short_description": apollo.get("short_description", ""),
                    "funding_stage": apollo.get("funding_stage", ""),
                    "founded_year": apollo.get("founded_year", ""),
                    "keywords": ", ".join(apollo.get("keywords", [])[:5]) if apollo.get("keywords") else "",
                    "phone": apollo.get("phone", ""),
                }

    # Create sheet if none provided — auto-share with user_email from config
    if not sheet_id:
        title = f"{project} — Contacts"
        if campaign_slug:
            title = f"{campaign_slug} — Contacts"
        # Get user email for sharing
        user_email = config.get("user_email") or ""
        result = await sheets_create(title, share_with=user_email, config=config)
        if not result.get("success"):
            return result
        sheet_id = result["data"]["sheet_id"]
        sheet_url = result["data"]["sheet_url"]
    else:
        sheet_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}"

    sheets_svc, _ = _build_service(config)
    if not sheets_svc:
        return {"success": False, "error": "Google credentials not configured"}

    # Build rows from contacts + company apollo_data + classification
    rows = []
    for c in contacts:
        domain = c.get("company_domain", "")
        # Fallback: extract domain from email if company_domain is empty
        if not domain and c.get("email") and "@" in c["email"]:
            domain = c["email"].split("@")[1].lower()
        cd = company_data.get(domain, {})

        # Fallback: if no company data from run file, use contact-level org_data
        contact_org = c.get("org_data", {})
        if not cd.get("industry") and contact_org:
            for k, v in contact_org.items():
                if v and not cd.get(k):
                    cd[k] = v

        # Company location: city, country
        loc_parts = [cd.get("city", ""), cd.get("country", "")]
        company_location = ", ".join(p for p in loc_parts if p)

        # Employees: prefer count, show as number
        employees = cd.get("employee_count", "") or ""

        # Revenue
        revenue = cd.get("revenue", "")

        # Keywords as comma-separated
        keywords = cd.get("keywords", "")
        if isinstance(keywords, list):
            keywords = ", ".join(keywords[:8])

        rows.append([
            # Person
            c.get("first_name", "") or (c.get("name", "").split(" ")[0] if c.get("name") else ""),
            c.get("last_name", "") or (c.get("name", "").split(" ", 1)[1] if c.get("name") and " " in c.get("name", "") else ""),
            c.get("title", ""),
            c.get("seniority", ""),
            c.get("linkedin_url", ""),
            c.get("email", ""),
            c.get("phone", "") or "",
            # Company
            c.get("company_name_normalized", "") or cd.get("name", ""),
            domain,
            company_location,
            cd.get("country", ""),
            cd.get("industry", ""),
            str(employees) if employees else "",
            str(cd.get("founded_year", "")) if cd.get("founded_year") else "",
            cd.get("funding_stage", ""),
            str(revenue) if revenue else "",
            keywords,
            cd.get("short_description", ""),
            # Pipeline
            c.get("segment", ""),
            str(cd.get("confidence", "")),
            cd.get("reasoning", ""),
            "Apollo",
            campaign_slug or str((campaign_data or {}).get("campaign_id", "")),
        ])

    # Dynamically remove columns where ALL rows are empty
    if rows:
        num_cols = len(CONTACT_HEADERS)
        keep_cols = []
        for col_idx in range(num_cols):
            has_data = any(
                row[col_idx] if col_idx < len(row) else ""
                for row in rows
            )
            if has_data:
                keep_cols.append(col_idx)

        if len(keep_cols) < num_cols:
            filtered_headers = [CONTACT_HEADERS[i] for i in keep_cols]
            filtered_rows = [[row[i] if i < len(row) else "" for i in keep_cols] for row in rows]
            dropped = [CONTACT_HEADERS[i] for i in range(num_cols) if i not in keep_cols]
            logger.info("Dropped %d empty columns: %s", len(dropped), dropped)
        else:
            filtered_headers = list(CONTACT_HEADERS)
            filtered_rows = rows
    else:
        filtered_headers = list(CONTACT_HEADERS)
        filtered_rows = rows

    # Clear existing data first (prevents duplicates on re-export / append runs)
    try:
        sheets_svc.spreadsheets().values().clear(
            spreadsheetId=sheet_id,
            range="Sheet1",
        ).execute()
    except Exception:
        pass  # OK if sheet is already empty

    # Write headers (overwrite row 1 with filtered headers)
    sheets_svc.spreadsheets().values().update(
        spreadsheetId=sheet_id,
        range="Sheet1!A1",
        valueInputOption="RAW",
        body={"values": [filtered_headers]},
    ).execute()

    try:
        sheets_svc.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range="Sheet1!A2",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": filtered_rows},
        ).execute()

        return {"success": True, "data": {
            "sheet_id": sheet_id, "sheet_url": sheet_url,
            "contacts_exported": len(filtered_rows), "project": project,
            "columns": len(filtered_headers),
            "dropped_columns": [CONTACT_HEADERS[i] for i in range(len(CONTACT_HEADERS)) if i not in keep_cols] if rows else [],
        }}
    except Exception as exc:
        logger.error("sheets_export_contacts failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Read sheet data (for blacklist import)
# ---------------------------------------------------------------------------

async def sheets_read(sheet_id: str, tab: str = "Sheet1", *, config=None) -> dict:
    """Read all data from a Google Sheet tab. Returns rows as list of dicts.

    Used to import blacklist domains from an existing sheet, or to read
    any structured data the user has in a Google Sheet.
    """
    config = config or _default_config()
    sheets_svc, _ = _build_service(config)
    if not sheets_svc:
        return {"success": False, "error": "Google credentials not configured"}

    try:
        result = sheets_svc.spreadsheets().values().get(
            spreadsheetId=sheet_id, range=f"{tab}!A:ZZ",
        ).execute()
        raw = result.get("values", [])
        if not raw:
            return {"success": True, "data": {"rows": [], "count": 0}}

        headers = [h.strip().lower() for h in raw[0]]
        rows = []
        for row in raw[1:]:
            d = {}
            for i, h in enumerate(headers):
                d[h] = row[i] if i < len(row) else ""
            rows.append(d)

        return {"success": True, "data": {
            "sheet_id": sheet_id, "headers": headers,
            "rows": rows, "count": len(rows),
        }}
    except Exception as exc:
        logger.error("sheets_read failed: %s", exc)
        return {"success": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_config():
    from gtm_mcp.config import ConfigManager
    return ConfigManager()

def _default_workspace():
    from gtm_mcp.workspace import WorkspaceManager
    return WorkspaceManager(_default_config().dir)
