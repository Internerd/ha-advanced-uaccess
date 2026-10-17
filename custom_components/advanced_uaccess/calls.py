"""Intercom call tracking ("who was rung") from the Access system log.

The ring history (system log topic "rings") is the only source that names the
directory room of a call. The Access notification WebSocket is used as a fast
trigger (access.remote_view / access.remote_view.change); a short poll covers
calls that do not produce a WebSocket event, e.g. a doorbell call that only
rings viewers, and the "missed" entry that Access writes after the timeout.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
import json
import logging
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .api import AccessAuthError, AccessClient, AccessError
from .const import DIRECT_CALL, DOMAIN

_LOGGER = logging.getLogger(__name__)

EVENT_CALL = f"{DOMAIN}_call"

CALL_INCOMING = "incoming"
CALL_ACCEPTED = "accepted"
CALL_REJECTED = "rejected"
CALL_MISSED = "missed"
CALL_CANCELED = "canceled"
CALL_ENDED = "ended"
CALL_EVENT_TYPES = [
    CALL_INCOMING,
    CALL_ACCEPTED,
    CALL_REJECTED,
    CALL_MISSED,
    CALL_CANCELED,
    CALL_ENDED,
]

POLL_INTERVAL = timedelta(seconds=15)
LOOKBACK = timedelta(minutes=5)
SEEN_TTL = timedelta(hours=25)
PRIME_LOOKBACK = timedelta(hours=24)
# An "accepted" entry has no reader_capture; match it to the newest open call
# of the same room that started at most this long ago.
ACCEPT_WINDOW = timedelta(minutes=3)
WS_TRIGGER_EVENTS = ("access.remote_view", "access.remote_view.change")
WS_RECONNECT_MAX = 300


def classify(log_key: str, event_type: str) -> str | None:
    """Map a ring log entry to a call event type."""
    key = log_key.lower()
    if event_type == "access.remotecall.request" or "made.doorbell" in key:
        return CALL_INCOMING
    if key.endswith("visitor.rang.remotecall") or key == "access.doorbell.visitor.rang":
        return CALL_INCOMING
    if event_type == "access.remotecall.accepted" or ".accept" in key:
        return CALL_ACCEPTED
    if ".reject" in key:
        return CALL_REJECTED
    if "missed" in key:
        return CALL_MISSED
    if "canceled" in key or "cancelled" in key:
        return CALL_CANCELED
    return None


@dataclass(slots=True)
class RingEntry:
    """One parsed entry of the ring history."""

    id: str
    published: datetime
    log_key: str
    kind: str | None
    caller_id: str
    target_id: str
    target_name: str
    actor_name: str = ""
    actor_type: str = ""
    viewer_id: str = ""
    viewer_name: str = ""
    call_id: str = ""
    thumbnail_url: str = ""
    video_url: str = ""
    message: str = ""


def _obj(value: Any) -> Mapping[str, Any]:
    if isinstance(value, list):
        value = value[0] if value else {}
    return value if isinstance(value, Mapping) else {}


def parse_ring_event(event: Mapping[str, Any]) -> RingEntry | None:
    """Parse a system log event of topic "rings"."""
    if "metadata" not in event and isinstance(event.get("_source"), Mapping):
        event = event["_source"]
    meta = event.get("metadata") or {}
    published = event.get("published")
    if event.get("id") is None or not isinstance(published, (int, float)):
        return None

    caller_id = _obj(meta.get("device_ua_pro")).get("id") or ""
    if not caller_id:
        for device in meta.get("device") or []:
            if isinstance(device, Mapping) and "intercom" in str(device.get("type", "")).lower():
                caller_id = device.get("id") or ""
                break
    directory = _obj(meta.get("directory"))
    actor = _obj(meta.get("actor"))
    viewer = _obj(meta.get("viewer"))
    capture = _obj(meta.get("reader_capture"))
    log_key = str(event.get("log_key") or "")
    return RingEntry(
        id=str(event["id"]),
        published=datetime.fromtimestamp(published / 1000, UTC),
        log_key=log_key,
        kind=classify(log_key, str(event.get("event_type") or "")),
        caller_id=str(caller_id),
        target_id=str(directory.get("id") or DIRECT_CALL),
        target_name=str(directory.get("display_name") or ""),
        actor_name=str(actor.get("display_name") or ""),
        actor_type=str(actor.get("type") or ""),
        viewer_id=str(viewer.get("id") or ""),
        viewer_name=str(viewer.get("display_name") or ""),
        call_id=str(capture.get("id") or ""),
        thumbnail_url=str(capture.get("thumbnail_url") or ""),
        video_url=str(capture.get("video_url") or ""),
        message=str(event.get("message") or ""),
    )


@dataclass(slots=True)
class Call:
    """A call from an intercom to a room or to the doorbell receivers."""

    call_id: str
    caller_id: str
    target_id: str
    target_name: str
    started: datetime
    status: str = CALL_INCOMING
    answered_by: str = ""
    answered_by_type: str = ""
    ended: datetime | None = None
    thumbnail_url: str = ""
    video_url: str = ""
    log_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Return a JSON friendly representation."""
        data = asdict(self)
        data["started"] = self.started.isoformat()
        data["ended"] = self.ended.isoformat() if self.ended else None
        return data


class CallTracker:
    """Turns the ring history into call events."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: AccessClient,
        signal: str,
        target_name: Callable[[str, str], str],
    ) -> None:
        """Initialize the tracker; target_name(caller_id, target_id) names rooms."""
        self.hass = hass
        self.client = client
        self.signal = signal
        self._target_name = target_name
        self._seen: dict[str, datetime] = {}
        self._calls: dict[str, Call] = {}
        self.last_call: dict[str, Call] = {}
        self.recent: list[dict[str, Any]] = []
        self.ws_connected = False
        self._lock = asyncio.Lock()
        self._primed = False

    # ---- fetching -------------------------------------------------------

    async def async_refresh(self, lookback: timedelta = LOOKBACK) -> None:
        """Read the recent ring history and emit events for new entries.

        The first call only records the history (no events), so a restart does
        not replay old calls but still knows the last call.
        """
        async with self._lock:
            now = dt_util.utcnow()
            try:
                events = await self.client.search_system_log("rings", now - lookback, now)
            except AccessError as err:
                _LOGGER.debug("Reading ring history failed: %s", err)
                return
            entries = [e for e in map(parse_ring_event, events) if e is not None]
            entries.sort(key=lambda e: (e.published, int(e.id) if e.id.isdigit() else 0))
            for entry in entries:
                if entry.id in self._seen:
                    continue
                self._seen[entry.id] = entry.published
                self.recent = [*self.recent[-19:], {"id": entry.id, "log_key": entry.log_key}]
                self._handle(entry, emit=self._primed)
            self._primed = True
            cutoff = now - SEEN_TTL
            self._seen = {k: v for k, v in self._seen.items() if v >= cutoff}
            self._calls = {k: c for k, c in self._calls.items() if c.started >= cutoff}

    # ---- call state -----------------------------------------------------

    def _find_open_call(self, entry: RingEntry) -> Call | None:
        candidates = [
            c
            for c in self._calls.values()
            if c.caller_id == entry.caller_id
            and c.target_id == entry.target_id
            and c.ended is None
            and entry.published - c.started <= ACCEPT_WINDOW
        ]
        return max(candidates, key=lambda c: c.started, default=None)

    def _handle(self, entry: RingEntry, emit: bool) -> None:
        if entry.kind is None:
            _LOGGER.debug("Ignoring ring log entry %s (%s)", entry.id, entry.log_key)
            return

        call = self._calls.get(entry.call_id) if entry.call_id else None
        if call is None and entry.kind != CALL_INCOMING:
            call = self._find_open_call(entry)

        if entry.kind == CALL_INCOMING or call is None:
            call = Call(
                call_id=entry.call_id or f"log-{entry.id}",
                caller_id=entry.caller_id,
                target_id=entry.target_id,
                target_name=entry.target_name
                or self._target_name(entry.caller_id, entry.target_id),
                started=entry.published,
                thumbnail_url=entry.thumbnail_url,
                video_url=entry.video_url,
            )
            self._calls[call.call_id] = call

        kind = entry.kind
        if kind == CALL_INCOMING:
            call.status = CALL_INCOMING
        elif kind == CALL_ACCEPTED:
            call.status = CALL_ACCEPTED
            call.answered_by = entry.actor_name or entry.viewer_name
            call.answered_by_type = entry.actor_type or ("viewer" if entry.viewer_name else "")
        elif kind == CALL_REJECTED and call.status == CALL_ACCEPTED:
            # Access logs "Call Rejected" when an accepted call is hung up.
            kind = CALL_ENDED
            call.ended = entry.published
        else:
            call.status = kind
            call.ended = entry.published
            if kind == CALL_REJECTED:
                call.answered_by = entry.actor_name or entry.viewer_name
                call.answered_by_type = entry.actor_type or ("viewer" if entry.viewer_name else "")
        call.log_ids.append(entry.id)

        previous = self.last_call.get(call.caller_id)
        if previous is None or call.started >= previous.started:
            self.last_call[call.caller_id] = call

        if emit:
            self._emit(kind, call, entry)

    @callback
    def _emit(self, kind: str, call: Call, entry: RingEntry) -> None:
        data = {
            "type": kind,
            "caller_id": call.caller_id,
            "room_id": call.target_id,
            "room_name": call.target_name,
            "call_id": call.call_id,
            "status": call.status,
            "answered_by": call.answered_by,
            "answered_by_type": call.answered_by_type,
            "started": call.started.isoformat(),
            "time": entry.published.isoformat(),
            "log_key": entry.log_key,
            "thumbnail_url": call.thumbnail_url,
        }
        self.hass.bus.async_fire(EVENT_CALL, data)
        async_dispatcher_send(self.hass, self.signal, kind, call, data)

    # ---- background tasks -----------------------------------------------

    async def async_poll_loop(self) -> None:
        """Poll the ring history (fallback for calls without WebSocket event)."""
        while True:
            try:
                await self.async_refresh()
            except Exception:
                # A surprise in the log format must not stop call tracking.
                _LOGGER.exception("Unexpected error while reading the ring history")
            await asyncio.sleep(POLL_INTERVAL.total_seconds())

    async def async_ws_loop(self) -> None:
        """Listen to the notification WebSocket and refresh on call events."""
        delay = 5
        while True:
            try:
                ws = await self.client.ws_connect()
            except AccessAuthError:
                ws = None
                try:
                    await self.client.login()
                except AccessError as err:
                    _LOGGER.debug("Re-login for the WebSocket failed: %s", err)
            except AccessError as err:
                _LOGGER.debug("Notification WebSocket unavailable: %s", err)
                ws = None
            if ws is not None:
                delay = 5
                self.ws_connected = True
                try:
                    await self._read(ws)
                finally:
                    self.ws_connected = False
                    await ws.close()
            await asyncio.sleep(delay)
            delay = min(delay * 2, WS_RECONNECT_MAX)

    async def _read(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        async for msg in ws:
            if msg.type != aiohttp.WSMsgType.TEXT:
                if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    return
                continue
            try:
                payload = json.loads(msg.data)
            except ValueError:
                continue  # "Hello" keep-alives
            if isinstance(payload, Mapping) and payload.get("event") in WS_TRIGGER_EVENTS:
                # The log entry is written in the same second; refresh twice to
                # be safe against a short delay.
                self.hass.async_create_task(self._refresh_soon())

    async def _refresh_soon(self) -> None:
        await asyncio.sleep(1)
        await self.async_refresh()
        await asyncio.sleep(3)
        await self.async_refresh()
