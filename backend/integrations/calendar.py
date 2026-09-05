"""Calendar integration and scheduled interview aggregator for Transcriptor.

Monitors scheduled recruiting events from:
1. Workable Events API (direct ATS interviews, calls, meetings)
2. Optional Google Calendar / Outlook private iCal feeds (CALENDAR_ICAL_URL)

Provides unified scheduled interview objects to automatically prepare sessions
without manual recruiter data entry.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any

import httpx
from pydantic import BaseModel, Field

from backend.config import Settings
from backend.integrations.workable import WorkableClient, WorkableEvent

logger = logging.getLogger(__name__)


class ScheduledInterview(BaseModel):
    """Unified representation of an upcoming or recent interview event."""

    id: str = Field(description="Unique event ID")
    title: str = Field(description="Interview event title")
    starts_at: str = Field(description="ISO 8601 start timestamp")
    ends_at: str | None = Field(default=None, description="ISO 8601 end timestamp")
    source: str = Field(default="workable", description="Event source ('workable' or 'calendar')")
    candidate_id: str | None = Field(default=None, description="Linked candidate ID if matched")
    candidate_name: str | None = Field(default=None, description="Candidate name")
    job_shortcode: str | None = Field(default=None, description="Job shortcode if matched")
    job_title: str | None = Field(default=None, description="Job title if matched")
    conference_url: str | None = Field(default=None, description="Video conference meeting link")
    interviewers: list[str] = Field(default_factory=list, description="List of interviewer names")


def _unfold_ical(raw_ical: str) -> list[str]:
    """Unfold lines according to RFC 5545 Section 3.1."""
    lines: list[str] = []
    for line in raw_ical.splitlines():
        if not line:
            continue
        if (line.startswith(" ") or line.startswith("\t")) and lines:
            lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def parse_ical_events(raw_ical: str) -> list[ScheduledInterview]:
    """Parse RFC 5545 iCal stream into ScheduledInterview items."""
    events: list[ScheduledInterview] = []
    current_event: dict[str, str] = {}
    in_event = False

    unfolded = _unfold_ical(raw_ical)
    for line in unfolded:
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            current_event = {}
        elif line == "END:VEVENT":
            in_event = False
            summary = current_event.get("SUMMARY", "").strip()
            dtstart_raw = current_event.get("DTSTART", "")
            dtend_raw = current_event.get("DTEND")
            uid = current_event.get("UID", f"ical-{len(events)}")

            if summary and dtstart_raw:
                iso_start = _parse_ical_datetime(dtstart_raw)
                iso_end = _parse_ical_datetime(dtend_raw) if dtend_raw else None

                desc = current_event.get("DESCRIPTION", "")
                loc = current_event.get("LOCATION", "")
                conf_match = re.search(
                    r"https://(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com)/[^\s\"<>]+",
                    f"{desc} {loc}",
                )
                conf_url = conf_match.group(0).rstrip(".,;\\\"'") if conf_match else None

                is_interview = any(
                    kw in summary.lower()
                    for kw in ["entrevista", "interview", "call with", "conversa", "workable", "rtr", "screening"]
                )

                if is_interview:
                    events.append(
                        ScheduledInterview(
                            id=uid,
                            title=summary,
                            starts_at=iso_start,
                            ends_at=iso_end,
                            source="calendar",
                            conference_url=conf_url,
                        )
                    )
        elif in_event and ":" in line:
            key, _, val = line.partition(":")
            clean_key = key.split(";")[0].upper()
            current_event[clean_key] = val

    return events


def _parse_ical_datetime(raw: str) -> str:
    """Format iCal date/datetime string into ISO 8601."""
    raw = raw.strip()
    if len(raw) == 8 and raw.isdigit():
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}T00:00:00Z"
    if "T" in raw:
        cleaned = raw.replace("Z", "")
        parts = cleaned.split("T")
        d, t = parts[0], parts[1]
        if len(d) == 8 and len(t) >= 6:
            return f"{d[:4]}-{d[4:6]}-{d[6:8]}T{t[:2]}:{t[2:4]}:{t[4:6]}Z"
    return raw


class CalendarMonitor:
    """Aggregator service for Workable recruiting events and external calendar feeds."""

    def __init__(self, settings: Settings, workable_client: WorkableClient | None = None) -> None:
        self.settings = settings
        self.workable_client = workable_client or WorkableClient(
            subdomain=settings.workable_subdomain,
            api_key=settings.workable_api_key,
        )

    async def get_upcoming_interviews(
        self,
        days_ahead: int = 14,
        days_behind: int = 30,
        limit: int = 20,
    ) -> list[ScheduledInterview]:
        """Fetch and aggregate upcoming and recent scheduled interviews."""
        interviews: list[ScheduledInterview] = []

        # 1. Fetch from Workable Events if configured
        if self.workable_client.is_configured:
            now = datetime.now(timezone.utc)
            start_date = (now - timedelta(days=days_behind)).strftime("%Y-%m-%d")
            end_date = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

            try:
                events = await self.workable_client.get_events(start_date=start_date, end_date=end_date, limit=limit)
                # If narrow window returns empty (e.g. historical account or weekend), fallback to current year
                if not events:
                    events = await self.workable_client.get_events(
                        start_date=f"{now.year}-01-01",
                        end_date=f"{now.year}-12-31",
                        limit=limit,
                    )

                for ev in events:
                    if ev.cancelled:
                        continue
                    interviews.append(
                        ScheduledInterview(
                            id=f"workable-{ev.id}",
                            title=ev.title,
                            starts_at=ev.starts_at,
                            ends_at=ev.ends_at,
                            source="workable",
                            candidate_id=ev.candidate_id,
                            candidate_name=ev.candidate_name,
                            job_shortcode=ev.job_shortcode,
                            job_title=ev.job_title,
                            conference_url=ev.conference_url,
                            interviewers=ev.interviewers,
                        )
                    )
            except Exception as exc:
                logger.warning("Failed to fetch Workable scheduled events: %s", exc)

        # 2. Fetch from Google Calendar / Outlook iCal feed if configured
        if self.settings.calendar_ical_url:
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.get(self.settings.calendar_ical_url)
                    if resp.status_code == 200:
                        ical_events = parse_ical_events(resp.text)
                        interviews.extend(ical_events)
            except Exception as exc:
                logger.warning("Failed to fetch external iCal feed: %s", exc)

        # Sort reverse chronologically by start time (newest / most upcoming first)
        interviews.sort(key=lambda x: x.starts_at, reverse=True)
        return interviews[:limit]
