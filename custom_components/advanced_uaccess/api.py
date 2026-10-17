"""Local client for the internal UniFi Access intercom directory API.

The endpoints used here are the ones the Access web UI uses. They are not
documented by Ubiquiti and may change with any Access update.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
import logging
from typing import Any

import aiohttp

from .const import (
    DIRECT_CALL,
    GROUP_KEYS,
    RECEIVER_TYPES,
    TYPE_ADMIN,
    TYPE_CHIME,
    TYPE_VIEWER,
    api_receiver_type,
)

_LOGGER = logging.getLogger(__name__)

API_BASE = "/proxy/access/api/v2"
ULP_BASE = "/proxy/access/ulp-go/api/v2"
LOGIN_PATH = "/api/auth/login"
REQUEST_TIMEOUT = 20
USERS_PAGE_SIZE = 100


class AccessError(Exception):
    """Base error for the Access client."""


class AccessConnectionError(AccessError):
    """The console could not be reached."""


class AccessAuthError(AccessError):
    """Login failed or the session was rejected."""


class AccessApiError(AccessError):
    """The API answered with an error envelope (code != 1)."""

    def __init__(self, code: Any, code_s: str, message: str) -> None:
        """Store the API error details."""
        super().__init__(f"{code_s or code}: {message}")
        self.code = code
        self.code_s = code_s
        self.message = message


@dataclass(slots=True)
class Receiver:
    """A receiver attached to a room or to the direct call."""

    type: str
    id: str
    name: str = ""
    schedule_id: str = ""
    schedule_name: str = ""
    schedule_is_private: bool = False

    @property
    def key(self) -> tuple[str, str]:
        """Return the identity of the receiver."""
        return (self.type, self.id)


@dataclass(slots=True)
class Target:
    """A directory room, or the doorbell direct call (id == DIRECT_CALL)."""

    id: str
    name: str
    number: str = ""
    number_check: bool = True
    receivers: list[Receiver] = field(default_factory=list)

    @property
    def is_direct(self) -> bool:
        """Return True for the doorbell direct call."""
        return self.id == DIRECT_CALL

    def find(self, receiver_type: str, receiver_id: str) -> Receiver | None:
        """Return the receiver with the given identity, if attached."""
        for receiver in self.receivers:
            if receiver.type == receiver_type and receiver.id == receiver_id:
                return receiver
        return None


@dataclass(slots=True)
class Caller:
    """An intercom (caller) and its directory."""

    id: str
    name: str
    alias: str = ""
    model: str = ""
    firmware: str = ""
    online: bool = True
    disable_directory: bool = False
    rooms: dict[str, Target] = field(default_factory=dict)
    direct: Target = field(default_factory=lambda: Target(id=DIRECT_CALL, name="Doorbell call"))

    @property
    def display_name(self) -> str:
        """Return the alias if set, otherwise the device name."""
        return self.alias or self.name or self.id

    def target(self, target_id: str) -> Target | None:
        """Return a room by id, or the direct call."""
        if target_id == DIRECT_CALL:
            return self.direct
        return self.rooms.get(target_id)


def _receiver_id(vertex_type: str, extra: Mapping[str, Any]) -> str:
    """Return the id a receiver vertex is listed with in receiver_groups."""
    if vertex_type in (TYPE_VIEWER, TYPE_CHIME):
        keys: tuple[str, ...] = ("device_id",)
    elif vertex_type == TYPE_ADMIN:
        keys = ("id",)
    else:
        keys = ("unique_id", "id", "device_id")
    for key in keys:
        if value := extra.get(key):
            return str(value)
    return ""


def _receiver_name(vertex: Mapping[str, Any], extra: Mapping[str, Any]) -> str:
    for value in (
        extra.get("display_name"),
        extra.get("name"),
        vertex.get("name"),
        extra.get("phone_number"),
    ):
        if value:
            return str(value)
    return ""


def parse_topology(
    caller_id: str, data: Mapping[str, Any]
) -> tuple[dict[str, Target], Target, dict[str, Any]]:
    """Parse the topology graph into rooms and the direct call.

    Returns (rooms, direct, caller_extra).
    """
    vertices: dict[str, Mapping[str, Any]] = {
        v["node_id"]: v for v in data.get("vertices") or [] if "node_id" in v
    }
    children: dict[str, list[str]] = {}
    for edge in data.get("edges") or []:
        children.setdefault(edge.get("uplink_id"), []).append(edge.get("downlink_id"))

    def build_receivers(node_id: str) -> list[Receiver]:
        receivers: list[Receiver] = []
        for child_id in children.get(node_id, []):
            vertex = vertices.get(child_id)
            if vertex is None or vertex.get("type") not in RECEIVER_TYPES:
                continue
            extra = vertex.get("extra") or {}
            rid = _receiver_id(vertex["type"], extra)
            if not rid:
                _LOGGER.debug("Skipping receiver vertex without id: %s", vertex)
                continue
            receivers.append(
                Receiver(
                    type=vertex["type"],
                    id=rid,
                    name=_receiver_name(vertex, extra),
                    schedule_id=extra.get("schedule_id") or "",
                    schedule_name=extra.get("schedule_name") or "",
                    schedule_is_private=bool(extra.get("schedule_is_private")),
                )
            )
        return receivers

    caller_node = next((v for v in vertices.values() if v.get("type") == "caller"), None)
    rooms: dict[str, Target] = {}
    direct = Target(id=DIRECT_CALL, name="Doorbell call")
    if caller_node is None:
        _LOGGER.warning("Topology of caller %s has no caller vertex", caller_id)
        return rooms, direct, {}

    caller_extra = dict(caller_node.get("extra") or {})
    direct.receivers = build_receivers(caller_node["node_id"])
    for child_id in children.get(caller_node["node_id"], []):
        vertex = vertices.get(child_id)
        if vertex is None or vertex.get("type") != "directory":
            continue
        extra = vertex.get("extra") or {}
        room_id = extra.get("room_id")
        if not room_id:
            continue
        rooms[room_id] = Target(
            id=room_id,
            name=extra.get("name") or str(extra.get("room") or room_id),
            number=str(extra.get("room") or ""),
            number_check=bool(extra.get("number_check", True)),
            receivers=build_receivers(child_id),
        )
    return rooms, direct, caller_extra


def receiver_groups(receivers: Iterable[Receiver]) -> list[dict[str, list[str]]]:
    """Build the single receiver_group payload for a list of receivers."""
    group: dict[str, list[str]] = {key: [] for key in GROUP_KEYS.values()}
    for receiver in receivers:
        ids = group[GROUP_KEYS[receiver.type]]
        if receiver.id not in ids:
            ids.append(receiver.id)
    return [group]


def format_phone(area_code: Any, number: Any) -> str:
    """Join country code and number; drop the trunk 0 after a +country code."""
    area = str(area_code or "").strip()
    num = str(number or "").strip()
    if area.startswith("+") and num.startswith("0"):
        num = num[1:]
    return " ".join(p for p in (area, num) if p)


def _find_viewers(node: Any, found: dict[str, str]) -> None:
    """Collect intercom viewers from the /devices/topology4 tree."""
    if isinstance(node, Mapping):
        device_type = str(node.get("device_type") or "")
        if "viewer" in device_type.lower() and node.get("unique_id"):
            found[str(node["unique_id"])] = str(
                node.get("alias") or node.get("name") or node["unique_id"]
            )
        for value in node.values():
            _find_viewers(value, found)
    elif isinstance(node, list):
        for item in node:
            _find_viewers(item, found)


class AccessClient:
    """Talks to /proxy/access on a UniFi OS console."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        username: str,
        password: str,
        verify_ssl: bool = False,
    ) -> None:
        """Initialize the client. The session must use an unsafe cookie jar."""
        self._session = session
        host = host.strip().rstrip("/")
        if not host.startswith(("http://", "https://")):
            host = f"https://{host}"
        self._base = host
        self._username = username
        self._password = password
        self._ssl: bool | None = None if verify_ssl else False
        self._csrf: str | None = None
        self._login_lock = asyncio.Lock()

    @property
    def host(self) -> str:
        """Return the base URL of the console."""
        return self._base

    async def login(self) -> None:
        """Log in with a local UniFi OS account."""
        async with self._login_lock:
            await self._login()

    async def _login(self) -> None:
        try:
            async with self._session.post(
                f"{self._base}{LOGIN_PATH}",
                json={
                    "username": self._username,
                    "password": self._password,
                    "rememberMe": True,
                },
                ssl=self._ssl,
                timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                if resp.status in (400, 401, 403):
                    raise AccessAuthError(f"Login rejected (HTTP {resp.status})")
                if resp.status == 499 or resp.status >= 500:
                    raise AccessConnectionError(f"Login failed (HTTP {resp.status})")
                if resp.status != 200:
                    raise AccessAuthError(f"Unexpected login response {resp.status}")
                body = await resp.json(content_type=None)
                if isinstance(body, Mapping) and body.get("required") == "2fa":
                    raise AccessAuthError("Account requires MFA; use a local account")
                # "" marks a session without CSRF token, so we do not log in again
                # before every request.
                self._csrf = resp.headers.get("X-CSRF-Token") or self._csrf or ""
        except (aiohttp.ClientError, TimeoutError) as err:
            raise AccessConnectionError(str(err)) from err

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        base: str = API_BASE,
        envelope: bool = True,
    ) -> Any:
        """Send a request, re-login once on 401/403 and unwrap the envelope."""
        for attempt in (1, 2):
            if self._csrf is None:
                await self.login()
            headers = {"X-CSRF-Token": self._csrf} if self._csrf else {}
            try:
                async with self._session.request(
                    method,
                    f"{self._base}{base}{path}",
                    params=params,
                    json=json,
                    headers=headers,
                    ssl=self._ssl,
                    timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
                ) as resp:
                    if updated := resp.headers.get("X-Updated-CSRF-Token"):
                        self._csrf = updated
                    if resp.status in (401, 403):
                        self._csrf = None
                        if attempt == 1:
                            continue
                        raise AccessAuthError(f"Session rejected (HTTP {resp.status})")
                    if resp.status >= 400:
                        text = await resp.text()
                        raise AccessConnectionError(
                            f"{method} {path} -> HTTP {resp.status}: {text[:200]}"
                        )
                    body = await resp.json(content_type=None)
            except (aiohttp.ClientError, TimeoutError) as err:
                raise AccessConnectionError(f"{method} {path}: {err}") from err

            if not envelope:
                return body
            if not isinstance(body, Mapping) or "code" not in body:
                raise AccessApiError(None, "", f"Unexpected response for {path}")
            if body.get("code") not in (1, "1"):
                raise AccessApiError(
                    body.get("code"),
                    str(body.get("codeS") or ""),
                    str(body.get("msg") or body.get("error") or ""),
                )
            return body
        raise AccessAuthError("Session rejected")  # pragma: no cover

    async def _data(self, method: str, path: str, **kwargs: Any) -> Any:
        return (await self.request(method, path, **kwargs)).get("data")

    # ---- read -----------------------------------------------------------

    async def get_callers(self) -> list[dict[str, Any]]:
        """Return all intercoms."""
        return list(await self._data("GET", "/callers") or [])

    async def get_caller(self, caller_id: str) -> dict[str, Any]:
        """Return caller config incl. rooms and direct-call receivers."""
        return dict(await self._data("GET", f"/callers/{caller_id}") or {})

    async def get_topology(self, caller_id: str) -> dict[str, Any]:
        """Return the full directory graph of a caller."""
        return dict(
            await self._data(
                "GET",
                f"/callers/{caller_id}/topology",
                params={"flow": "true", "support_delivery": "true"},
            )
            or {}
        )

    async def get_schedule(self, schedule_id: str) -> dict[str, Any]:
        """Return a schedule by id."""
        return dict(await self._data("GET", f"/schedule/{schedule_id}") or {})

    async def get_phone_numbers(self) -> dict[str, str]:
        """Return {unique_id: display} of configured phone numbers."""
        result: dict[str, str] = {}
        for item in await self._data("GET", "/callers/phone_number") or []:
            if uid := item.get("unique_id"):
                result[uid] = format_phone(item.get("area_code"), item.get("phone_number")) or uid
        return result

    async def get_users(self) -> dict[str, str]:
        """Return {unique_id: full_name} of all active users."""
        result: dict[str, str] = {}
        page = 1
        while True:
            body = await self.request(
                "GET",
                "/users/search",
                base=ULP_BASE,
                params={
                    "page_num": page,
                    "page_size": USERS_PAGE_SIZE,
                    "status": "ACTIVE",
                },
            )
            items = body.get("data") or []
            for item in items:
                if uid := item.get("unique_id"):
                    name = item.get("full_name") or " ".join(
                        p for p in (item.get("first_name"), item.get("last_name")) if p
                    )
                    result[uid] = name or item.get("username") or uid
            total = int(body.get("total") or 0)
            if not items or page * USERS_PAGE_SIZE >= total:
                return result
            page += 1

    async def get_chimes(self) -> dict[str, str]:
        """Return {MAC without colons: name} of Protect chimes."""
        data = await self._data(
            "GET", "/protect_devices", params={"include_adopted_by_access": "true"}
        )
        result: dict[str, str] = {}
        for chime in (data or {}).get("chimes") or []:
            if mac := chime.get("mac"):
                mac = str(mac).replace(":", "").upper()
                result[mac] = chime.get("name") or mac
        return result

    async def get_viewers(self) -> dict[str, str]:
        """Return {unique_id: alias} of intercom viewers."""
        found: dict[str, str] = {}
        _find_viewers(await self._data("GET", "/devices/topology4"), found)
        return found

    # ---- write ----------------------------------------------------------

    async def set_room_receivers(
        self, caller_id: str, room: Target, receivers: Iterable[Receiver]
    ) -> None:
        """Replace the receiver list of a room."""
        await self.request(
            "POST",
            f"/callers/{caller_id}/rooms/{room.id}",
            json={
                "room": room.number,
                "number_check": room.number_check,
                "receiver_groups": receiver_groups(receivers),
            },
        )

    async def set_direct_receivers(self, caller_id: str, receivers: Iterable[Receiver]) -> None:
        """Replace the receiver list of the doorbell direct call."""
        await self.request(
            "PUT",
            f"/callers/rooms/{caller_id}",
            json={"receiver_groups": receiver_groups(receivers)},
        )

    async def remove_receiver(
        self, caller_id: str, target_id: str, receiver_type: str, receiver_id: str
    ) -> None:
        """Remove a single receiver from a room or the direct call."""
        params = {
            "caller_id": caller_id,
            "receiver_id": receiver_id,
            "receiver_type": api_receiver_type(receiver_type),
        }
        if target_id != DIRECT_CALL:
            params["room_id"] = target_id
        await self.request("DELETE", f"/callers/receivers/{receiver_id}", params=params)

    async def create_room(
        self,
        caller_id: str,
        name: str,
        number: str,
        number_check: bool,
        receivers: Iterable[Receiver],
    ) -> dict[str, Any]:
        """Create a directory room and return it."""
        return dict(
            await self._data(
                "POST",
                f"/callers/{caller_id}/rooms/receivers",
                json={
                    "disable_directory": False,
                    "name": name,
                    "room": number,
                    "number_check": number_check,
                    "receiver_groups": receiver_groups(receivers),
                },
            )
            or {}
        )

    async def delete_room(self, caller_id: str, room_id: str) -> None:
        """Delete a directory room."""
        await self.request("DELETE", f"/callers/{caller_id}/rooms/{room_id}")

    async def set_schedule(
        self,
        caller_id: str,
        target_id: str,
        receiver_type: str,
        receiver_id: str,
        week_schedule: Mapping[str, list[dict[str, str]]],
        holiday_schedule: list[dict[str, str]] | None = None,
        name: str | None = None,
        stamp: int = 0,
    ) -> None:
        """Set a private ("custom") schedule for a receiver."""
        body: dict[str, Any] = {
            "target_type": api_receiver_type(receiver_type),
            "receiver_id": receiver_id,
            "schedule": {
                "name": name or f"visitor-{stamp}",
                "schedule_type": "access",
                "is_private": True,
                "holiday_timezone": {
                    "day_schedule": list(holiday_schedule or []),
                    "name": f"holiday {stamp}",
                },
                "user_timezone": {
                    "name": f"user {stamp}",
                    "week_schedule": {
                        str(day): list(week_schedule.get(str(day), [])) for day in range(7)
                    },
                },
                "holiday_group_id": "",
            },
        }
        if target_id != DIRECT_CALL:
            body["room_id"] = target_id
        await self.request("PUT", f"/callers/{caller_id}/schedules", json=body)

    async def clear_schedule(
        self, caller_id: str, target_id: str, receiver_type: str, receiver_id: str
    ) -> None:
        """Reset a receiver's schedule to "always"."""
        params = {"target_type": api_receiver_type(receiver_type)}
        if target_id != DIRECT_CALL:
            params["room_id"] = target_id
        await self.request(
            "DELETE",
            f"/callers/{caller_id}/receivers/{receiver_id}/schedules",
            params=params,
        )


def extract_week_schedule(
    schedule: Any,
) -> tuple[dict[str, list[dict[str, str]]], list[dict[str, str]]] | None:
    """Find week_schedule / holiday day_schedule in a GET /schedule response.

    The exact shape of GET /schedule/{id} was not captured, so this searches
    the response for the same keys the PUT body uses.
    """
    week: dict[str, list[dict[str, str]]] | None = None
    holiday: list[dict[str, str]] = []

    def walk(node: Any, parent_key: str = "") -> None:
        nonlocal week, holiday
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key == "week_schedule" and isinstance(value, Mapping) and week is None:
                    week = {
                        str(day): [
                            {"start_time": s["start_time"], "end_time": s["end_time"]}
                            for s in value.get(str(day)) or []
                            if "start_time" in s and "end_time" in s
                        ]
                        for day in range(7)
                    }
                elif key == "day_schedule" and "holiday" in parent_key and isinstance(value, list):
                    holiday = [dict(v) for v in value if isinstance(v, Mapping)]
                else:
                    walk(value, key)
        elif isinstance(node, list):
            for item in node:
                walk(item, parent_key)

    walk(schedule)
    if week is None:
        return None
    return week, holiday
