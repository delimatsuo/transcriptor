"""Calendar integration and scheduled interview aggregator for Transcriptor.

Monitors scheduled recruiting events from:
1. Workable Events API (direct ATS interviews, calls, meetings)
2. Optional Google Calendar / Outlook private iCal feeds (CALENDAR_ICAL_URL)

Provides unified scheduled interview objects to automatically prepare sessions
without manual recruiter data entry.
"""

from __future__ import annotations

import asyncio
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
    candidate_email: str | None = Field(default=None, description="Candidate email if available")
    job_shortcode: str | None = Field(default=None, description="Job shortcode if matched")
    job_title: str | None = Field(default=None, description="Job title if matched")
    conference_url: str | None = Field(default=None, description="Video conference meeting link")
    workable_url: str | None = Field(default=None, description="Workable candidate or job URL if detected in event")
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


def extract_candidate_and_job_from_title(summary: str) -> tuple[str | None, str | None]:
    """Extract candidate name and job title from standard recruiting calendar event titles."""
    candidate_name = None
    job_title = None

    if " - " in summary:
        parts = summary.split(" - ", 1)
        name_part = parts[0].strip()
        job_title = parts[1].strip() or None
    elif ":" in summary:
        parts = summary.split(":", 1)
        name_part = parts[1].strip()
        job_title = None
    else:
        name_part = summary.strip()

    # Strip prefixes like "Interview", "Entrevista", "Call with", "Conversa", "Screening"
    cleaned_name = re.sub(
        r"^(?:interview|entrevista|call\s+with|call|conversa|screening)\s*(?:with|com|de|da|do|para)?\s*:?\s*",
        "",
        name_part,
        flags=re.I,
    ).strip()

    # Check for "X and Y" or "X e Y" or "X + Y"
    and_match = re.split(r"\s+(?:and|e|\+)\s+", cleaned_name, flags=re.I)
    if len(and_match) == 2:
        part1, part2 = and_match[0].strip(), and_match[1].strip()
        if "deli" in part1.lower():
            candidate_name = part2
        elif "deli" in part2.lower():
            candidate_name = part1
        else:
            candidate_name = part2
    else:
        candidate_name = cleaned_name or None

    return candidate_name, job_title


def parse_ical_events(raw_ical: str) -> list[ScheduledInterview]:
    """Parse RFC 5545 iCal stream into ScheduledInterview items."""
    events: list[ScheduledInterview] = []
    current_event: dict[str, str] = {}
    current_attendees: list[str] = []
    in_event = False

    unfolded = _unfold_ical(raw_ical)
    for line in unfolded:
        line = line.strip()
        if line == "BEGIN:VEVENT":
            in_event = True
            current_event = {}
            current_attendees = []
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
                x_conf = current_event.get("X-GOOGLE-CONFERENCE", "")

                # Clean conference URL: check X-GOOGLE-CONFERENCE first, then desc and loc
                cleaned_text = desc.replace(r"\n", " ").replace(r"\N", " ").replace(r"\r", " ") + " " + loc
                conf_match = re.search(
                    r"https://(?:meet\.google\.com|zoom\.us|teams\.microsoft\.com)/[^\s\"<>]+",
                    f"{x_conf} {cleaned_text}",
                )
                conf_url = conf_match.group(0).rstrip(".,;\\\"'") if conf_match else None

                is_interview = any(
                    kw in summary.lower()
                    for kw in ["entrevista", "interview", "call with", "conversa", "workable", "rtr", "screening"]
                )

                if is_interview:
                    candidate_name, job_title = extract_candidate_and_job_from_title(summary)

                    # Extract candidate email and interviewers from attendees and organizer
                    candidate_email = None
                    interviewers: list[str] = []
                    org = current_event.get("ORGANIZER", "")
                    all_people_lines = current_attendees + ([org] if org else [])
                    for person in all_people_lines:
                        emails = re.findall(r"mailto:([^\s;>\"'\?]+)", person, re.I)
                        if not emails:
                            emails = re.findall(r"[\w\.-]+@[\w\.-]+\.\w+", person)
                        for em in emails:
                            em_clean = em.strip().lower()
                            if em_clean.endswith("@ellaexecutivesearch.com") or "deli" in em_clean:
                                if em_clean not in interviewers:
                                    interviewers.append(em_clean)
                            elif not em_clean.endswith("@google.com") and not em_clean.endswith("@calendar.google.com"):
                                candidate_email = em_clean

                    # Extract Workable link if present in description
                    workable_match = re.search(r"https://[^\s\"<>]*workable\.com/[^\s\"<>]+", cleaned_text)
                    workable_url = workable_match.group(0).rstrip(".,;\\\"'") if workable_match else None

                    events.append(
                        ScheduledInterview(
                            id=uid,
                            title=summary,
                            starts_at=iso_start,
                            ends_at=iso_end,
                            source="calendar",
                            candidate_name=candidate_name,
                            candidate_email=candidate_email,
                            job_title=job_title,
                            conference_url=conf_url,
                            workable_url=workable_url,
                            interviewers=interviewers,
                        )
                    )
        elif in_event:
            if line.startswith("ATTENDEE"):
                current_attendees.append(line)
            elif ":" in line:
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
        self._candidate_cache: dict[str, Any] = {}

    async def _enrich_interview_from_workable(self, item: ScheduledInterview) -> None:
        """Enrich a calendar event with matching Workable candidate dossier metadata."""
        if not self.workable_client.is_configured or item.candidate_id:
            return

        try:
            candidates: list[dict[str, Any]] = []
            cache_key = item.candidate_email or item.candidate_name
            if cache_key and cache_key in self._candidate_cache:
                candidates = self._candidate_cache[cache_key]
            else:
                if item.candidate_email:
                    candidates = await self.workable_client.search_candidates(email=item.candidate_email)
                    if candidates:
                        self._candidate_cache[item.candidate_email] = candidates

                # Fallback to name search if email search returned no results
                if not candidates and item.candidate_name:
                    candidates = await self.workable_client.search_candidates(name=item.candidate_name)
                    if candidates:
                        self._candidate_cache[item.candidate_name] = candidates

            if not candidates:
                return

            # Selection logic:
            # 1. Prefer active / non-disqualified
            active = [c for c in candidates if not c.get("disqualified")]
            pool = active if active else candidates

            # 2. If job_title hint is available, match by job title keywords
            if item.job_title and len(pool) > 1:
                words = [w.lower() for w in re.findall(r"\w+", item.job_title) if len(w) > 2]
                matching = [
                    c for c in pool
                    if c.get("job") and any(w in c["job"].get("title", "").lower() for w in words)
                ]
                if matching:
                    pool = matching

            best = pool[0]
            item.candidate_id = best.get("id")
            if best.get("name"):
                item.candidate_name = best["name"]
            if best.get("job"):
                item.job_shortcode = best["job"].get("shortcode")
                item.job_title = best["job"].get("title") or item.job_title
            if not item.candidate_email and best.get("email"):
                item.candidate_email = best["email"]
        except Exception as exc:
            logger.debug("Candidate Workable auto-enrichment failed for %s: %s", item.id, exc)

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

        # Sort: upcoming (starts_at >= now) sorted ascending (soonest first),
        # followed by past events reverse chronological
        now = datetime.now(timezone.utc)
        min_start = (now - timedelta(days=days_behind)).isoformat()
        max_start = (now + timedelta(days=days_ahead)).isoformat()

        windowed = [
            ev for ev in interviews
            if min_start <= ev.starts_at <= max_start
        ]
        pool = windowed if windowed else interviews

        upcoming = [e for e in pool if e.starts_at >= (now - timedelta(hours=1)).isoformat()]
        upcoming.sort(key=lambda x: x.starts_at)

        past = [e for e in pool if e.starts_at < (now - timedelta(hours=1)).isoformat()]
        past.sort(key=lambda x: x.starts_at, reverse=True)

        ordered = upcoming + past
        top_interviews = ordered[:limit]

        # Auto-enrich calendar interviews with Workable candidate details (rate-limit safe)
        sem = asyncio.Semaphore(2)

        async def _safe_enrich(item: ScheduledInterview):
            async with sem:
                await self._enrich_interview_from_workable(item)

        enrich_tasks = [
            _safe_enrich(item)
            for item in top_interviews[:5]
            if item.source == "calendar" and not item.candidate_id
        ]
        if enrich_tasks:
            await asyncio.gather(*enrich_tasks, return_exceptions=True)

        return top_interviews

    async def match_interview_by_meet_code(
        self,
        meet_code: str,
        time_window_minutes: int = 30,
    ) -> ScheduledInterview | None:
        """Find an upcoming or active interview that matches a Google Meet code.

        Matches by:
        1. Exact occurrence of meet_code in conference_url (e.g. 'meet.google.com/abc-defg-hij').
        2. Proximity in time: if an interview is scheduled within ±time_window_minutes AND
           does not have a conflicting meet.google.com conference URL. If multiple qualify,
           the one closest in time to current moment is returned.
        """
        clean_code = meet_code.strip().lower()
        if not clean_code:
            return None

        interviews = await self.get_upcoming_interviews(days_ahead=7, days_behind=2, limit=50)

        # 1. Exact match on meeting code in conference_url
        matched: ScheduledInterview | None = None
        for item in interviews:
            if item.conference_url and clean_code in item.conference_url.lower():
                matched = item
                break

        # 2. Time proximity match: ONLY if event does NOT have a conflicting Google Meet link
        if not matched:
            now = datetime.now(timezone.utc)
            eligible_candidates: list[tuple[float, ScheduledInterview]] = []

            for item in interviews:
                # If the event already has an explicit meet.google.com link and it did not match in step 1,
                # it belongs to a different room; do not trigger a false-positive prompt.
                if item.conference_url and "meet.google.com" in item.conference_url.lower():
                    continue

                try:
                    ts_str = item.starts_at.replace("Z", "+00:00")
                    start_dt = datetime.fromisoformat(ts_str)
                    diff_seconds = abs((now - start_dt).total_seconds())
                    if diff_seconds <= time_window_minutes * 60:
                        eligible_candidates.append((diff_seconds, item))
                except Exception:
                    continue

            if eligible_candidates:
                # Pick the interview closest in time to current moment
                eligible_candidates.sort(key=lambda x: x[0])
                matched = eligible_candidates[0][1]

        if matched:
            if not matched.candidate_id:
                await self._enrich_interview_from_workable(matched)
            return matched

        return None
