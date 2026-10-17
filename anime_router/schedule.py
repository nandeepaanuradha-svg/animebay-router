"""SubsPlease weekly slots, with explicit estimates and honest outage messages."""

import asyncio
import time
from datetime import datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import httpx

from .parser import display_episode, normalize

DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
API = "https://subsplease.org/api/"


def slug_from(value):
    value = value.strip().rstrip("/")
    if "://" in value:
        url = urlparse(value)
        if url.hostname not in ("subsplease.org", "www.subsplease.org"):
            raise ValueError("Use a SubsPlease show URL or its slug")
        parts = url.path.strip("/").split("/")
        if len(parts) != 2 or parts[0] != "shows":
            raise ValueError("Expected https://subsplease.org/shows/show-slug/")
        value = parts[1]
    if not value or any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-" for ch in value):
        raise ValueError("Invalid SubsPlease slug")
    return value


def slots(data):
    """Validate provider data before treating it as a usable schedule."""
    tz = ZoneInfo(data["tz"])
    weekly = data["schedule"]
    if not isinstance(weekly, dict):
        raise ValueError("Unexpected SubsPlease schedule format")
    result = []
    for weekday, day in enumerate(DAYS):
        for entry in weekly.get(day, []):
            hh, mm = map(int, entry["time"].split(":"))
            if not 0 <= hh <= 23 or not 0 <= mm <= 59:
                raise ValueError("Invalid release time")
            result.append({"weekday": weekday, "hour": hh, "minute": mm,
                           "title": str(entry["title"]), "slug": slug_from(entry["page"]), "tz": tz})
    return result


def next_slot(entry, anchor):
    local = datetime.fromtimestamp(anchor, entry["tz"])
    day = local.date() + timedelta(days=(entry["weekday"] - local.weekday()) % 7)
    at = datetime(day.year, day.month, day.day, entry["hour"], entry["minute"], tzinfo=entry["tz"])
    if at.timestamp() <= anchor:
        at += timedelta(days=7)
    return int(at.timestamp())


class Schedule:
    def __init__(self, store, timezone="Asia/Colombo", client=None):
        self.store = store
        self.timezone = ZoneInfo(timezone)
        self.client = client or httpx.AsyncClient(timeout=20, follow_redirects=True,
                                                 headers={"User-Agent": "AnimeBayRouter/1.0"})
        self.lock = asyncio.Lock()
        self.retry_at = 0

    async def close(self):
        await self.client.aclose()

    async def load(self, force=False):
        async with self.lock:
            now = int(time.time())
            cache = self.store.get("subsplease_cache")
            if cache and now - cache["at"] < 900 and not force:
                return slots(cache["data"]), False
            if now >= self.retry_at:
                try:
                    response = await self.client.get(API, params={"f": "schedule", "tz": self.timezone.key})
                    response.raise_for_status()
                    data = response.json()
                    result = slots(data)
                    self.store.set("subsplease_cache", {"at": now, "data": data})
                    return result, False
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    # Do not hammer the service once per pending anime.
                    self.retry_at = now + 300
            if cache and now - cache["at"] < 7200:
                return slots(cache["data"]), True
            raise ValueError("SubsPlease is unavailable; the userbot will retry")

    async def search(self, query):
        entries, _ = await self.load()
        needle = normalize(query)
        seen = set()
        result = []
        for entry in entries:
            if needle in normalize(entry["title"] + entry["slug"]) and entry["slug"] not in seen:
                seen.add(entry["slug"])
                result.append(entry)
        return result

    async def message(self, route, episode=None, anchor=None):
        now = int(time.time())
        anchor = anchor if anchor is not None else now
        title = route["title"]
        heading = f"🗓 Next SubsPlease release\n📺 {title}"
        if episode is not None:
            heading += f"\n📗 After episode {display_episode(episode)}"
        if route["finished"]:
            return heading + "\n🏁 Season/series completed. No next episode is scheduled.", None
        override = route["override_at"]
        cached = False
        if override and override > anchor:
            at = override
            origin = "Time set by the channel administrator."
        else:
            try:
                entries, cached = await self.load()
            except ValueError:
                return heading + "\n⏳ Schedule temporarily unavailable. This message will update when data returns.", None
            matches = [e for e in entries if e["slug"] == route["slug"]]
            if not matches:
                return heading + "\n⏳ Next release time is not listed by SubsPlease. Awaiting a schedule update.", None
            at = min(next_slot(entry, anchor) for entry in matches)
            origin = "Expected from SubsPlease’s weekly schedule; delays or breaks may change it."
        local = datetime.fromtimestamp(at, self.timezone)
        timezone_label = "Sri Lanka · UTC+05:30" if self.timezone.key == "Asia/Colombo" else self.timezone.key
        text = heading + f"\n📅 {local.strftime('%A, %d %B %Y')}\n🕒 {local.strftime('%I:%M %p')} ({timezone_label})\nℹ️ {origin}"
        if cached:
            text += "\n⚠️ Using a recently cached schedule while SubsPlease is unavailable."
        if at <= now:
            text += "\n⏳ This expected time has passed. Check SubsPlease for release updates."
        return text, at
