"""HTTP level tests of AccessClient."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from http import HTTPStatus

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.advanced_uaccess.api import (
    AccessApiError,
    AccessAuthError,
    AccessClient,
    Receiver,
)

HOST = "https://192.168.1.1"
API = f"{HOST}/proxy/access/api/v2"
OK = {"code": 1, "codeS": "SUCCESS", "msg": "success", "data": None}


@pytest.fixture
async def mocker(hass: HomeAssistant) -> AsyncGenerator[AiohttpClientMocker]:
    """Return a request mocker whose sessions are closed after the test."""
    mock = AiohttpClientMocker()
    mock.sessions = []
    yield mock
    for session in mock.sessions:
        await session.close()


def _client(hass: HomeAssistant, mocker: AiohttpClientMocker) -> AccessClient:
    session = mocker.create_session(hass.loop)
    mocker.sessions.append(session)
    return AccessClient(session, "192.168.1.1/", "u", "p")


def _login(mocker: AiohttpClientMocker, token: str = "csrf1") -> None:
    mocker.post(f"{HOST}/api/auth/login", json={}, headers={"X-CSRF-Token": token})


async def test_login_and_csrf_header(hass: HomeAssistant, mocker: AiohttpClientMocker) -> None:
    """The CSRF token from the login is sent with every request."""
    _login(mocker)
    mocker.get(f"{API}/callers", json={**OK, "data": [{"unique_id": "c1"}]})
    client = _client(hass, mocker)
    assert await client.get_callers() == [{"unique_id": "c1"}]
    data = mocker.mock_calls[0][2]
    assert data["username"] == "u"
    assert mocker.mock_calls[1][3] == {"X-CSRF-Token": "csrf1"}


async def test_login_rejected(hass: HomeAssistant, mocker: AiohttpClientMocker) -> None:
    """HTTP 401 on login is an auth error."""
    mocker.post(f"{HOST}/api/auth/login", status=HTTPStatus.UNAUTHORIZED)
    with pytest.raises(AccessAuthError):
        await _client(hass, mocker).login()


async def test_envelope_error(hass: HomeAssistant, mocker: AiohttpClientMocker) -> None:
    """code != 1 raises AccessApiError although HTTP is 200."""
    _login(mocker)
    mocker.get(
        f"{API}/callers",
        json={"code": 170014, "codeS": "CODE_ACCESS_POLICY_SCHEDULE_NOT_FOUND", "msg": "x"},
    )
    with pytest.raises(AccessApiError) as err:
        await _client(hass, mocker).get_callers()
    assert err.value.code_s == "CODE_ACCESS_POLICY_SCHEDULE_NOT_FOUND"


async def test_relogin_on_401(hass: HomeAssistant, mocker: AiohttpClientMocker) -> None:
    """An expired session triggers one new login."""
    _login(mocker)
    responses = iter([HTTPStatus.UNAUTHORIZED, HTTPStatus.OK])

    async def side_effect(method, url, data):
        status = next(responses)
        return AiohttpClientMockResponse(
            method, url, status=status, json={**OK, "data": []} if status == 200 else {}
        )

    mocker.get(f"{API}/callers", side_effect=side_effect)
    client = _client(hass, mocker)
    assert await client.get_callers() == []
    assert [c[0].lower() for c in mocker.mock_calls] == ["post", "get", "post", "get"]


async def test_write_requests(hass: HomeAssistant, mocker: AiohttpClientMocker) -> None:
    """Paths, query parameters and bodies match the recorded UI requests."""
    _login(mocker)
    mocker.put(f"{API}/callers/rooms/c1", json=OK)
    mocker.delete(f"{API}/callers/receivers/0cea1417bf7d", json=OK)
    mocker.delete(f"{API}/callers/receivers/E43883358449", json=OK)
    client = _client(hass, mocker)

    await client.set_direct_receivers("c1", [Receiver("chime", "E43883358449")])
    body = mocker.mock_calls[1][2]
    assert body["receiver_groups"][0]["chimes"] == ["E43883358449"]

    await client.remove_receiver("c1", "room1", "viewer", "0cea1417bf7d")
    url = mocker.mock_calls[2][1]
    assert dict(url.query) == {
        "caller_id": "c1",
        "room_id": "room1",
        "receiver_id": "0cea1417bf7d",
        "receiver_type": "viewer_device",
    }

    await client.remove_receiver("c1", "direct", "chime", "E43883358449")
    assert "room_id" not in mocker.mock_calls[3][1].query
    assert mocker.mock_calls[3][1].query["receiver_type"] == "chime"


async def test_users_pagination(hass: HomeAssistant, mocker: AiohttpClientMocker) -> None:
    """Users are read page by page until total is reached."""
    _login(mocker)
    url = f"{HOST}/proxy/access/ulp-go/api/v2/users/search"
    pages = iter(
        [
            {
                "code": 1,
                "total": 101,
                "data": [{"unique_id": f"u{i}", "full_name": f"U{i}"} for i in range(100)],
            },
            {
                "code": 1,
                "total": 101,
                "data": [{"unique_id": "u100", "first_name": "Last", "last_name": "One"}],
            },
        ]
    )

    async def side_effect(method, req_url, data):
        return AiohttpClientMockResponse(method, req_url, json=next(pages))

    mocker.get(url, side_effect=side_effect)
    users = await _client(hass, mocker).get_users()
    assert len(users) == 101
    assert users["u100"] == "Last One"
