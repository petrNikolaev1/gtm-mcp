"""GetSales Automation Service — God-level LinkedIn flow generation + push to GetSales API.

Mirrors the SmartLead campaign_intelligence.py pattern:
  generate_flow → approve → push_to_getsales → activate

Based on analysis of 414 live flows across 10 projects. See docs/getsales/GETSALES_AUTOMATION_PLAYBOOK.md
"""
import logging
import json
from typing import Any, Dict, List, Optional
from datetime import datetime

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.models.campaign import GeneratedSequence, Campaign
from app.models.project import Project

logger = logging.getLogger(__name__)


# ── Timing constants (from top-performing flows) ──
TIMING_STANDARD = {
    "accept_wait": 1 * 3600,         # 1h after accept before MSG1
    "msg2_delay": 2 * 86400,         # 2d after MSG1
    "msg3_delay": 5 * 86400,         # 5d after MSG2
    "msg4_delay": 7 * 86400,         # 7d after MSG3 (optional)
    "non_accept_timeout": 3 * 86400, # 3d before non-accept branch
    "withdraw_delay": 15 * 86400,    # 15d before withdraw
}

TIMING_NETWORKING = {
    "accept_wait": 1 * 86400,        # 1d after accept (softer)
    "msg2_delay": 3 * 86400,
    "msg3_delay": 5 * 86400,
    "msg4_delay": 30 * 86400,        # 30d — long tail
    "non_accept_timeout": 3 * 86400,
    "withdraw_delay": 10 * 86400,
}

TIMING_VOLUME = {
    "accept_wait": 1 * 3600,
    "msg2_delay": 2 * 86400,
    "msg3_delay": 5 * 86400,
    "msg4_delay": 7 * 86400,
    "non_accept_timeout": 21 * 86400,  # 21d — long acceptance window
    "withdraw_delay": 15 * 86400,
}

# Country → timezone (for schedule)
COUNTRY_TIMEZONES = {
    "united states": "America/New_York", "us": "America/New_York",
    "united kingdom": "Europe/London", "uk": "Europe/London",
    "germany": "Europe/Berlin", "france": "Europe/Paris",
    "spain": "Europe/Madrid", "italy": "Europe/Rome",
    "netherlands": "Europe/Amsterdam", "india": "Asia/Kolkata",
    "australia": "Australia/Sydney", "uae": "Asia/Dubai",
    "united arab emirates": "Asia/Dubai", "south africa": "Africa/Johannesburg",
    "brazil": "America/Sao_Paulo", "mexico": "America/Mexico_City",
    "canada": "America/Toronto", "japan": "Asia/Tokyo",
    "singapore": "Asia/Singapore", "philippines": "Asia/Manila",
    "russia": "Europe/Moscow", "israel": "Asia/Jerusalem",
    "turkey": "Europe/Istanbul", "saudi arabia": "Asia/Riyadh",
    "qatar": "Asia/Qatar", "poland": "Europe/Warsaw",
    "kazakhstan": "Asia/Almaty", "bolivia": "America/La_Paz",
    "colombia": "America/Bogota", "costa rica": "America/Costa_Rica",
    "panama": "America/Panama",
}


# ── Reference sequences (proven patterns from live data) ──

REFERENCE_FLOW_STANDARD = """
REFERENCE FLOW — "Standard qualifying" (68% positive rate pattern):

Connection note (qualifying question):
"Hi {{first_name}}!
Do you [qualifying question related to ICP pain point]?"

MSG1 (1h after accept — value intro + social proof):
"Thanks for connecting, {{first_name}}!

We at [Company] help [target audience] with [core value prop] — [key differentiator with numbers].

[Additional capability].

Would this be relevant for {{company_name}}?"

MSG2 (3d later — social proof + differentiation):
"Hi {{first_name}},

Just a quick note! Unlike [competitor category] with hidden fees or rigid plans, we offer [benefit 1], and [benefit 2].

We recently helped a [geo] [industry] company switch from [competitor] — [specific result with numbers].

Open to a quick demo?"

MSG3 (5d later — objection handling + soft close):
"Hi {{first_name}},

I know you're busy and probably have a solution already.

But many clients switch to us for better terms, real human support, and fewer issues.

Would it make sense to hop on a 15-min call?"

KEY PATTERNS:
- Connection note = qualifying question (self-selects relevant leads)
- MSG1 = value prop + specific numbers (<1%, 0 fees)
- MSG2 = case study + competitor name-drop (Deel)
- MSG3 = empathy + objection handling + meeting CTA
- Engagement actions between every message (visit, like, endorse)
"""

REFERENCE_FLOW_NETWORKING = """
REFERENCE FLOW — "Networking approach" (69% positive rate pattern):

Connection note: (EMPTY — no note, just connect)

MSG1 (1d after accept — pure networking, no pitch):
"Hello {{first_name}},

Nice to connect.
Looking forward to our conversation."

MSG2 (3d later — soft intro + event hook):
"Hi {{first_name}},

My name is [Sender], I'm the co-founder of [Company] — [1-sentence value prop].

I'd love to connect and exchange perspectives on [industry topic]."

MSG3 (5d later — event/conference hook):
"Hello {{first_name}},

Just wanted to quickly follow up — will you be attending [EVENT] in [MONTH]?

If you'll be there, it would be great to connect and exchange thoughts."

KEY PATTERNS:
- No connection note — higher accept rate for networking
- MSG1 = pure networking, ZERO pitch
- MSG2 = soft intro, frames as peer exchange
- MSG3 = event hook for meeting
- Only 3 messages total — short sequence
"""

REFERENCE_FLOW_PRODUCT = """
REFERENCE FLOW — "Product showcase" (85% positive rate on niche ICP):

Connection note:
"Hi {{first_name}},

Would love to connect with you! 🙂"

MSG1 (1h after accept — product showcase with numbers):
"Hi {{first_name}},

We've been building a [PRODUCT TYPE] for [X] years straight — [feature list with numbers].

[SPECIFIC METRIC] users use it daily. Would be happy to show you a live demo."

MSG2 (2d later — proof/demo offer):
"Hey {{first_name}},

We have [N] live products I can walk you through:

- [Product 1 with metrics]
- [Product 2 with metrics]

Would a 15-min demo be useful for {{company_name}}?"

MSG3 (5d later — last chance + redirect):
"Hey {{first_name}},

Last one from me. We have live [products] to show — [brief proof point].

If {{company_name}} ever needs [service], I'd love to connect.
If not, who should I talk to?"

KEY PATTERNS:
- Generic connect note works because ICP is hyper-niche
- MSG1 = product showcase with specific numbers
- MSG2 = demo offer with concrete features
- MSG3 = redirect to right person ("who should I talk to?")
"""

FLOW_CHECKLIST = """
MANDATORY CHECKLIST (every flow must satisfy):
☐ Connection note: max 300 chars, either qualifying question OR event hook OR short value prop
☐ MSG1: value prop with specific numbers (users, %, $), max 500 chars
☐ MSG2: case study or deeper proof, competitor name-drop if possible, max 500 chars
☐ MSG3: objection handling or redirect ("who should I talk to?"), max 400 chars
☐ MSG4 (optional): ultra-short bump ("{{first_name}}? 😊"), max 100 chars
☐ Engagement actions between EVERY message: visit_profile, like_latest_post, endorse_skills
☐ Non-accept branch: like → visit → endorse → wait 15d → withdraw → tag
☐ {{first_name}} and {{company_name}} used in messages — GetSales merge tags
☐ No "I hope this message finds you well" — instant ignore trigger
☐ Each message ≤ 500 characters (LinkedIn DM limit awareness — shorter = better)
☐ Distinct intent per message — no two messages can have the same purpose
"""


class GetSalesAutomationService:
    """Generate and push LinkedIn automation flows to GetSales."""

    def __init__(self, api_key: str, team_id: str):
        self.api_key = api_key
        self.team_id = team_id
        self.base_url = "https://amazing.getsales.io"

    async def _api_call(self, method: str, endpoint: str, json_data: dict = None) -> Optional[dict]:
        """Make authenticated GetSales API call."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Team-Id": self.team_id,
            "Content-Type": "application/json",
        }
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                url = f"{self.base_url}{endpoint}"
                if method == "POST":
                    resp = await client.post(url, json=json_data, headers=headers)
                elif method == "PUT":
                    resp = await client.put(url, json=json_data, headers=headers)
                elif method == "DELETE":
                    resp = await client.delete(url, headers=headers)
                else:
                    resp = await client.get(url, headers=headers)
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.error(f"GetSales {method} {endpoint}: {e}")
            return None

    # ── API Methods ──

    async def get_sender_profiles(self) -> List[Dict]:
        """Get all sender profiles (LinkedIn accounts)."""
        data = await self._api_call("GET", "/flows/api/sender-profiles")
        if isinstance(data, dict):
            return data.get("data", [])
        return data if isinstance(data, list) else []

    async def get_workspaces(self) -> List[Dict]:
        """Get all flow workspaces (folders)."""
        data = await self._api_call("GET", "/flows/api/flow-workspaces")
        if isinstance(data, dict):
            return data.get("data", [])
        return data if isinstance(data, list) else []

    async def create_flow(self, name: str, workspace_uuid: Optional[str] = None,
                          timezone: str = "Europe/Moscow") -> Optional[Dict]:
        """Create a new automation flow in draft status."""
        payload = {
            "name": name,
            "use_sender_schedule": True,
            "schedule": {
                "timeblocks": [
                    {"dow": dow, "min": 540, "max": 1080}  # 9:00-18:00
                    for dow in range(1, 6)  # Mon-Fri
                ],
                "timezone": timezone,
                "use_lead_timezone": False,
            },
            "priority": 3,
        }
        if workspace_uuid:
            payload["flow_workspace_uuid"] = workspace_uuid
        return await self._api_call("POST", "/flows/api/flows", payload)

    async def save_flow_version(self, flow_uuid: str, nodes: List[Dict],
                                 sender_profile_uuids: List[str],
                                 rotation_strategy: str = "fair") -> Optional[Dict]:
        """Save a flow version with the automation node tree."""
        # Find the first node ID (entry point after contact source)
        first_node_id = nodes[0]["id"] if nodes else 1

        payload = {
            "flow_origin": "automation",
            "contact_sources": [{
                "sender_profiles": sender_profile_uuids,
                "rotation_strategy": rotation_strategy,
                "after_id": first_node_id,
            }],
            "nodes": nodes,
        }
        return await self._api_call(
            "POST", f"/flows/api/flows/{flow_uuid}/flow-versions", payload
        )

    async def start_flow(self, flow_uuid: str) -> Optional[Dict]:
        """Activate a flow (start sending)."""
        return await self._api_call("PUT", f"/flows/api/flows/{flow_uuid}/start")

    async def stop_flow(self, flow_uuid: str) -> Optional[Dict]:
        """Pause a flow."""
        return await self._api_call("PUT", f"/flows/api/flows/{flow_uuid}/stop")

    async def add_lead_to_flow(self, flow_uuid: str, lead_data: Dict) -> Optional[Dict]:
        """Add a new lead to a flow."""
        return await self._api_call(
            "POST", f"/flows/api/flows/{flow_uuid}/add-new-lead", lead_data
        )

    async def get_flow_metrics(self, flow_uuids: List[str]) -> Optional[Dict]:
        """Get aggregate metrics for flows."""
        return await self._api_call(
            "POST", "/flows/api/flows/metrics", {"flow_uuids": flow_uuids}
        )

    # ── Node Tree Builder ──

    @staticmethod
    def build_node_tree(
        connection_note: str,
        messages: List[str],
        timing: Dict[str, int] = None,
        include_inmail: bool = False,
        inmail_text: Optional[str] = None,
        connection_filter_min: Optional[int] = None,
    ) -> List[Dict]:
        """Build a GetSales flow node tree from messages and timing.

        This implements the "God Level" flow pattern derived from
        analyzing all 414 live flows. See GETSALES_AUTOMATION_PLAYBOOK.md.

        Args:
            connection_note: Text for connection request (empty = no note)
            messages: List of follow-up messages (MSG1, MSG2, MSG3, optionally MSG4)
            timing: Timing dict (defaults to TIMING_STANDARD)
            include_inmail: Add InMail fallback for non-accepted (volume pattern)
            inmail_text: InMail message text (required if include_inmail=True)
            connection_filter_min: Min connections filter (e.g., 19 = skip <20 connections)

        Returns:
            List of node dicts ready for save_flow_version()
        """
        if timing is None:
            timing = TIMING_STANDARD

        nodes = []
        next_id = 1
        end_id = next_id
        next_id += 1

        # End node (always ID=1)
        nodes.append({
            "id": end_id,
            "before": [],
            "after": [],
            "type": "end",
            "automation": "auto",
            "payload": [],
            "delay_in_seconds": 0,
        })

        # Optional: connection count filter
        filter_id = None
        if connection_filter_min is not None:
            filter_id = next_id + 50  # High ID to avoid collision
            # We'll set this up at the end

        # ── Main trigger: connection request accepted ──
        trigger1_id = next_id + 1
        conn_req_id = next_id + 2
        trigger2_id = next_id + 3
        next_id += 4

        # Build accepted branch (MSG1 → visit → MSG2 → like → MSG3 → endorse → [MSG4] → END)
        accepted_nodes = []
        current_id = next_id

        # Tag on accept
        tag_id = current_id
        accepted_nodes.append({
            "id": tag_id,
            "before": [],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "gs_add_tag",
            "automation": "auto",
            "payload": {"tag_uuid": "auto_accepted"},
            "delay_in_seconds": 0,
        })
        current_id += 1

        # Wait before MSG1
        wait1_id = current_id
        accepted_nodes.append({
            "id": wait1_id,
            "before": [{"node_id": tag_id, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "util_timer",
            "automation": "auto",
            "payload": {"wait_time": timing["accept_wait"]},
            "delay_in_seconds": 0,
        })
        current_id += 1

        # Messages with engagement actions between them
        msg_delays = [
            timing.get("msg2_delay", 2 * 86400),
            timing.get("msg3_delay", 5 * 86400),
            timing.get("msg4_delay", 7 * 86400),
        ]

        engagement_actions = [
            "linkedin_visit_profile",
            "linkedin_like_latest_post",
            "linkedin_endorse_skills",
        ]

        prev_id = wait1_id
        for i, msg_text in enumerate(messages):
            # Message node
            msg_id = current_id
            is_last = (i == len(messages) - 1)
            after_msg = [{"node_id": current_id + 1, "branch_id": 1}] if not is_last else []

            accepted_nodes.append({
                "id": msg_id,
                "before": [{"node_id": prev_id, "branch_id": 1}],
                "after": after_msg if not is_last else [{"node_id": end_id, "branch_id": 1}] if is_last and i >= 2 else [{"node_id": current_id + 1, "branch_id": 1}],
                "type": "linkedin_send_message",
                "automation": "auto",
                "payload": {"template": msg_text},
                "delay_in_seconds": 0,
            })
            current_id += 1

            if is_last:
                if len(messages) <= 3:
                    # Short sequence: end after MSG3
                    accepted_nodes[-1]["after"] = [{"node_id": end_id, "branch_id": 1}]
                else:
                    # End after MSG4
                    accepted_nodes[-1]["after"] = [{"node_id": end_id, "branch_id": 1}]
                break

            # Engagement action after message
            engage_type = engagement_actions[i % len(engagement_actions)]
            engage_id = current_id
            accepted_nodes.append({
                "id": engage_id,
                "before": [{"node_id": msg_id, "branch_id": 1}],
                "after": [{"node_id": current_id + 1, "branch_id": 1}],
                "type": engage_type,
                "automation": "auto",
                "payload": [],
                "delay_in_seconds": 0,
            })
            current_id += 1

            # Wait before next message
            if i < len(msg_delays):
                delay_id = current_id
                accepted_nodes.append({
                    "id": delay_id,
                    "before": [{"node_id": engage_id, "branch_id": 1}],
                    "after": [{"node_id": current_id + 1, "branch_id": 1}],
                    "type": "util_timer",
                    "automation": "auto",
                    "payload": {"wait_time": msg_delays[i]},
                    "delay_in_seconds": 0,
                })
                current_id += 1
                prev_id = delay_id
            else:
                prev_id = engage_id

        # Fix the last accepted_nodes after chain
        if accepted_nodes:
            last_node = accepted_nodes[-1]
            if not last_node["after"]:
                last_node["after"] = [{"node_id": end_id, "branch_id": 1}]

        # ── Non-accepted branch ──
        non_accept_nodes = []
        na_start = current_id

        # Like latest post
        na_like_id = current_id
        non_accept_nodes.append({
            "id": na_like_id,
            "before": [],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "util_timer",
            "automation": "auto",
            "payload": {"wait_time": 86400},  # 1d
            "delay_in_seconds": 0,
        })
        current_id += 1

        non_accept_nodes.append({
            "id": current_id,
            "before": [{"node_id": na_like_id, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "linkedin_like_latest_post",
            "automation": "auto",
            "payload": [],
            "delay_in_seconds": 0,
        })
        current_id += 1

        # Wait + visit
        non_accept_nodes.append({
            "id": current_id,
            "before": [{"node_id": current_id - 1, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "util_timer",
            "automation": "auto",
            "payload": {"wait_time": 2 * 86400},  # 2d
            "delay_in_seconds": 0,
        })
        current_id += 1

        # InMail fallback (volume pattern) or visit
        if include_inmail and inmail_text:
            non_accept_nodes.append({
                "id": current_id,
                "before": [{"node_id": current_id - 1, "branch_id": 1}],
                "after": [{"node_id": current_id + 1, "branch_id": 1}],
                "type": "linkedin_send_inmail",
                "automation": "auto",
                "payload": {"template": inmail_text},
                "delay_in_seconds": 0,
            })
            current_id += 1

        non_accept_nodes.append({
            "id": current_id,
            "before": [{"node_id": current_id - 1, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "linkedin_visit_profile",
            "automation": "auto",
            "payload": [],
            "delay_in_seconds": 0,
        })
        current_id += 1

        # Wait + endorse
        non_accept_nodes.append({
            "id": current_id,
            "before": [{"node_id": current_id - 1, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "util_timer",
            "automation": "auto",
            "payload": {"wait_time": 3 * 86400},  # 3d
            "delay_in_seconds": 0,
        })
        current_id += 1

        non_accept_nodes.append({
            "id": current_id,
            "before": [{"node_id": current_id - 1, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "linkedin_endorse_skills",
            "automation": "auto",
            "payload": [],
            "delay_in_seconds": 0,
        })
        current_id += 1

        # Wait long + withdraw
        non_accept_nodes.append({
            "id": current_id,
            "before": [{"node_id": current_id - 1, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "util_timer",
            "automation": "auto",
            "payload": {"wait_time": timing["withdraw_delay"]},
            "delay_in_seconds": 0,
        })
        current_id += 1

        non_accept_nodes.append({
            "id": current_id,
            "before": [{"node_id": current_id - 1, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "linkedin_withdraw_connection_request",
            "automation": "auto",
            "payload": [],
            "delay_in_seconds": 0,
        })
        current_id += 1

        # Tag as not-accepted
        na_tag_id = current_id
        non_accept_nodes.append({
            "id": na_tag_id,
            "before": [{"node_id": current_id - 1, "branch_id": 1}],
            "after": [{"node_id": current_id + 1, "branch_id": 1}],
            "type": "gs_add_tag",
            "automation": "auto",
            "payload": {"tag_uuid": "auto_not_accepted"},
            "delay_in_seconds": 0,
        })
        current_id += 1

        # End for non-accept branch
        na_end_id = current_id
        non_accept_nodes.append({
            "id": na_end_id,
            "before": [{"node_id": na_tag_id, "branch_id": 1}],
            "after": [],
            "type": "end",
            "automation": "auto",
            "payload": [],
            "delay_in_seconds": 0,
        })
        current_id += 1

        # ── Assemble trigger nodes ──

        # First trigger: connection_request_accepted (entry point)
        # Wait 1m before sending connection request
        first_accepted_node_id = tag_id  # Goes to tag → wait → MSG1...
        trigger1 = {
            "id": trigger1_id,
            "before": [],
            "after": [
                {"node_id": conn_req_id, "branch_id": 1},  # accepted → send connect
                {"node_id": end_id, "branch_id": 2},        # not triggered → end
            ],
            "type": "trigger_linkedin_connection_request_accepted",
            "automation": "auto",
            "payload": {
                "subtasks": [{
                    "id": trigger1_id + 100,
                    "type": "util_timer",
                    "after": [],
                    "before": [],
                    "payload": {"wait_time": 60},  # 1m warmup
                    "automation": "auto",
                }]
            },
            "delay_in_seconds": 0,
        }

        # Connection request node
        conn_req = {
            "id": conn_req_id,
            "before": [{"node_id": trigger1_id, "branch_id": 1}],
            "after": [{"node_id": trigger2_id, "branch_id": 1}],
            "type": "linkedin_send_connection_request",
            "automation": "auto",
            "payload": {
                "template": connection_note,
                "note": connection_note,
                "fallback_send": False,
            },
            "delay_in_seconds": 0,
        }

        # Second trigger: accepted or timed out
        trigger2 = {
            "id": trigger2_id,
            "before": [{"node_id": conn_req_id, "branch_id": 1}],
            "after": [
                {"node_id": first_accepted_node_id, "branch_id": 1},  # accepted → MSG sequence
                {"node_id": na_start, "branch_id": 2},                  # not accepted → non-accept branch
            ],
            "type": "trigger_linkedin_connection_request_accepted",
            "automation": "auto",
            "payload": {
                "subtasks": [{
                    "id": trigger2_id + 100,
                    "type": "util_timer",
                    "after": [],
                    "before": [],
                    "payload": {"wait_time": timing["non_accept_timeout"]},
                    "automation": "auto",
                }]
            },
            "delay_in_seconds": 0,
        }

        # Fix before refs for accepted branch first node
        accepted_nodes[0]["before"] = [{"node_id": trigger2_id, "branch_id": 1}]

        # Fix before refs for non-accept branch first node
        non_accept_nodes[0]["before"] = [{"node_id": trigger2_id, "branch_id": 2}]

        # Assemble all nodes
        all_nodes = [trigger1, conn_req, trigger2] + accepted_nodes + non_accept_nodes

        # Add the end node
        # Update end node before refs
        end_before = []
        for n in all_nodes:
            for a in n.get("after", []):
                if a["node_id"] == end_id:
                    end_before.append({"node_id": n["id"], "branch_id": a["branch_id"]})
        nodes[0]["before"] = end_before  # Update the end node

        return [nodes[0]] + all_nodes  # End node first (GetSales convention)

    # ── AI Flow Generation ──

    async def generate_flow(
        self,
        session: AsyncSession,
        project_id: int,
        flow_name: Optional[str] = None,
        flow_type: str = "standard",
        instructions: Optional[str] = None,
        openai_key: Optional[str] = None,
        **kwargs,
    ) -> GeneratedSequence:
        """Generate a GetSales LinkedIn flow using AI + proven patterns.

        flow_type options:
        - "standard": Qualifying question + 3 follow-ups (68% positive rate)
        - "networking": No note + soft intro + 3 messages (69% positive)
        - "product": Generic connect + product showcase (85% on niche ICP)
        - "volume": Value prop note + 4 msgs + InMail fallback (high volume)
        - "event": Event hook connect + 3 messages (52% positive)
        """
        project = await session.get(Project, project_id)
        if not project:
            raise ValueError(f"Project {project_id} not found")

        # Select reference flow and timing based on type
        ref_flow = {
            "standard": REFERENCE_FLOW_STANDARD,
            "networking": REFERENCE_FLOW_NETWORKING,
            "product": REFERENCE_FLOW_PRODUCT,
            "volume": REFERENCE_FLOW_STANDARD,
            "event": REFERENCE_FLOW_NETWORKING,
        }.get(flow_type, REFERENCE_FLOW_STANDARD)

        timing_key = {
            "standard": "TIMING_STANDARD",
            "networking": "TIMING_NETWORKING",
            "product": "TIMING_STANDARD",
            "volume": "TIMING_VOLUME",
            "event": "TIMING_NETWORKING",
        }.get(flow_type, "TIMING_STANDARD")

        # Build context from project knowledge
        context_parts = []
        if project.target_segments:
            context_parts.append(f"ICP: {project.target_segments}")
        if project.target_industries:
            context_parts.append(f"Industries: {project.target_industries}")
        if project.sender_name:
            context_parts.append(f"Sender: {project.sender_name}")
        if project.sender_company:
            context_parts.append(f"Company: {project.sender_company}")
        if project.sender_position:
            context_parts.append(f"Position: {project.sender_position}")
        if instructions:
            context_parts.append(f"Additional instructions: {instructions}")

        context = "\n".join(context_parts)
        name = flow_name or f"{project.name} - LinkedIn {flow_type.title()}"

        # Generate with AI
        flow_data = await self._generate_flow_ai(
            project, context, flow_type, ref_flow, openai_key=openai_key
        )

        if not flow_data:
            flow_data = self._generate_flow_template(project, flow_type)

        # Store as GeneratedSequence (reusing the model — platform=getsales)
        seq = GeneratedSequence(
            project_id=project_id,
            company_id=project.company_id,
            campaign_name=name,
            generation_prompt=context,
            patterns_used=[],
            sequence_steps=flow_data,
            sequence_step_count=len(flow_data.get("messages", [])),
            rationale=f"GetSales LinkedIn flow ({flow_type}). Timing: {timing_key}. "
                      f"Pattern from GETSALES_AUTOMATION_PLAYBOOK.md.",
            status="draft",
            model_used="gpt-4o-mini",
        )
        session.add(seq)
        await session.flush()

        return seq

    async def _generate_flow_ai(
        self, project, context: str, flow_type: str, ref_flow: str,
        openai_key: Optional[str] = None, **kwargs,
    ) -> Optional[Dict]:
        """Generate flow content with AI."""
        sender = project.sender_name or "Team"
        company = project.sender_company or "our company"
        position = project.sender_position or "BDM"

        msg_count = 4 if flow_type == "volume" else 3

        prompt = f"""Generate a LinkedIn outreach flow for GetSales automation.

TARGET ICP & CONTEXT:
{context}

SENDER: {sender}, {position} at {company}

FLOW TYPE: {flow_type}

{ref_flow}

{FLOW_CHECKLIST}

ADAPT the reference flow for the target ICP above. Keep the STRUCTURE and TECHNIQUES but change:
- The value proposition to match what {company} actually offers
- The case study numbers to be plausible for this industry
- The competitor names to whatever this ICP currently uses

MERGE TAGS: {{{{first_name}}}}, {{{{company_name}}}} (double curly braces — GetSales format)

Return ONLY a JSON object:
{{
  "connection_note": "text for connection request (empty string for networking type)",
  "messages": ["MSG1 text", "MSG2 text", "MSG3 text"{', "MSG4 text"' if msg_count == 4 else ''}],
  "flow_type": "{flow_type}",
  "include_inmail": {str(flow_type == 'volume').lower()},
  "inmail_text": "InMail text if include_inmail is true, otherwise null"
}}"""

        # Use GPT-4o-mini for flow generation
        if openai_key:
            try:
                async with httpx.AsyncClient(timeout=45) as client:
                    resp = await client.post(
                        "https://api.openai.com/v1/chat/completions",
                        headers={"Authorization": f"Bearer {openai_key}"},
                        json={
                            "model": "gpt-4o-mini",
                            "messages": [
                                {"role": "system", "content": "You are a top-tier BDM who writes LinkedIn outreach sequences that get 50%+ positive reply rates. Return ONLY valid JSON."},
                                {"role": "user", "content": prompt},
                            ],
                            "max_tokens": 2000,
                            "temperature": 0.5,
                        },
                    )
                    data = resp.json()
                    text = data["choices"][0]["message"]["content"]
                    return self._parse_flow_json(text)
            except Exception as e:
                logger.warning(f"GPT flow generation failed: {e}")

        return None

    @staticmethod
    def _parse_flow_json(text: str) -> Optional[Dict]:
        """Parse AI-generated JSON, handling markdown code blocks."""
        import re
        # Strip markdown code blocks
        text = re.sub(r'^```(?:json)?\s*', '', text.strip())
        text = re.sub(r'\s*```$', '', text.strip())
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Try to find JSON object in text
            match = re.search(r'\{[\s\S]*\}', text)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        return None

    @staticmethod
    def _generate_flow_template(project, flow_type: str) -> Dict:
        """Fallback template flow when AI generation fails."""
        sender = project.sender_name or "Team"
        company = project.sender_company or "our company"

        if flow_type == "networking":
            return {
                "connection_note": "",
                "messages": [
                    f"Hello {{{{first_name}}}},\n\nNice to connect.\nLooking forward to our conversation.",
                    f"Hi {{{{first_name}}}},\n\nMy name is {sender} from {company}. I'd love to exchange perspectives on your industry.\n\nWould you be open to a quick chat?",
                    f"Hi {{{{first_name}}}},\n\nJust following up — if you're ever looking to explore solutions in our space, I'd be happy to share some insights.\n\nBest,\n{sender}",
                ],
                "flow_type": "networking",
                "include_inmail": False,
                "inmail_text": None,
            }

        if flow_type == "product":
            return {
                "connection_note": f"Hi {{{{first_name}}}},\n\nWould love to connect with you! \U0001f642",
                "messages": [
                    f"Hi {{{{first_name}}}},\n\nAt {company}, we've built a solution that helps companies like {{{{company_name}}}} solve key challenges in your space.\n\nWould be happy to show you a quick demo.",
                    f"Hey {{{{first_name}}}},\n\nWe have live products I can walk you through. Companies similar to {{{{company_name}}}} have seen significant results.\n\nWorth a 15-min look?",
                    f"Hey {{{{first_name}}}},\n\nLast one from me. If {{{{company_name}}}} ever needs help in our space, I'd love to connect.\n\nIf not, who should I talk to?",
                ],
                "flow_type": "product",
                "include_inmail": False,
                "inmail_text": None,
            }

        # Default: standard (qualifying question)
        return {
            "connection_note": f"Hi {{{{first_name}}}}!\n\nIs {{{{company_name}}}} looking for solutions in our space?",
            "messages": [
                f"Thanks for connecting, {{{{first_name}}}}!\n\nAt {company}, we help companies like yours with tailored solutions. Our clients typically see significant ROI within the first month.\n\nWould this be relevant for {{{{company_name}}}}?",
                f"Hi {{{{first_name}}}},\n\nJust a quick follow-up. Many companies we work with were using traditional approaches before switching to us.\n\nWe offer better terms, dedicated support, and faster results.\n\nOpen to a quick demo?",
                f"Hi {{{{first_name}}}},\n\nI know you're busy. If timing isn't right, I totally get it.\n\nBut if there's a chance this could help {{{{company_name}}}}, I'd love 15 minutes.\n\nBest,\n{sender}",
            ],
            "flow_type": "standard",
            "include_inmail": False,
            "inmail_text": None,
        }


def get_timezone_for_country(country: str) -> str:
    """Get IANA timezone for a country. Defaults to Europe/Moscow."""
    if not country:
        return "Europe/Moscow"
    return COUNTRY_TIMEZONES.get(country.lower().strip(), "Europe/Moscow")
