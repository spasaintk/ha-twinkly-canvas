"""Tests for the Twinkly HTTP client."""

import asyncio
import base64

import aiohttp
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.twinkly_canvas.api import (
    TwinklyAuthError,
    TwinklyClient,
    TwinklyConnectionError,
    TwinklyResponseError,
)

HOST = "192.0.2.10"
BASE = f"http://{HOST}/xled/v1"


@pytest.fixture
async def client(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> TwinklyClient:
    """Client on the mocked session; every login hands out token "tok"."""
    aioclient_mock.post(
        f"{BASE}/login",
        json={"authentication_token": "tok", "challenge-response": "cr", "code": 1000},
    )
    aioclient_mock.post(f"{BASE}/verify", json={"code": 1000})
    return TwinklyClient(HOST, async_get_clientsession(hass))


def _calls(mock: AiohttpClientMocker, path: str) -> list[tuple]:
    """Recorded (method, url, data, headers) calls to one endpoint."""
    return [c for c in mock.mock_calls if c[1].path == f"/xled/v1/{path}"]


async def test_gestalt_no_auth(client, aioclient_mock):
    aioclient_mock.get(
        f"{BASE}/gestalt", json={"device_name": "Test Tree", "code": 1000}
    )
    assert (await client.gestalt())["device_name"] == "Test Tree"
    assert aioclient_mock.call_count == 1  # no login
    assert "X-Auth-Token" not in (aioclient_mock.mock_calls[0][3] or {})


async def test_firmware_version(client, aioclient_mock):
    aioclient_mock.get(f"{BASE}/fw/version", json={"version": "2.8.18", "code": 1000})
    assert await client.firmware_version() == "2.8.18"
    assert aioclient_mock.call_count == 1  # no login
    assert "X-Auth-Token" not in (aioclient_mock.mock_calls[0][3] or {})


async def test_login_then_token_header(client, aioclient_mock):
    aioclient_mock.get(f"{BASE}/led/mode", json={"mode": "movie", "code": 1000})
    await client.login()
    await client.get_mode()
    login, verify, mode = aioclient_mock.mock_calls
    assert len(base64.b64decode(login[2]["challenge"])) == 32
    assert verify[2] == {"challenge-response": "cr"}
    assert verify[3]["X-Auth-Token"] == "tok"
    assert mode[3]["X-Auth-Token"] == "tok"
    assert client.token_epoch == 1


async def test_relogin_on_401(client, aioclient_mock):
    replies = [{"status": 401}, {"json": {"mode": "movie", "code": 1000}}]

    async def answer(method, url, data):
        return AiohttpClientMockResponse(method, url, **replies.pop(0))

    aioclient_mock.get(f"{BASE}/led/mode", side_effect=answer)
    assert (await client.get_mode())["mode"] == "movie"
    assert len(_calls(aioclient_mock, "login")) == 2
    assert client.token_epoch == 2


async def test_concurrent_calls_login_once(client, aioclient_mock):
    """Every login kills the previous token, so a call that waited must reuse the new one."""

    async def slow_login(method, url, data):
        await asyncio.sleep(
            0
        )  # let the second call start while this login is in flight
        return AiohttpClientMockResponse(
            method,
            url,
            json={
                "authentication_token": "tok",
                "challenge-response": "cr",
                "code": 1000,
            },
        )

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/login", side_effect=slow_login)
    aioclient_mock.post(f"{BASE}/verify", json={"code": 1000})
    aioclient_mock.get(f"{BASE}/led/mode", json={"mode": "movie", "code": 1000})
    await asyncio.gather(client.get_mode(), client.get_mode())
    assert len(_calls(aioclient_mock, "login")) == 1
    assert client.token_epoch == 1


async def test_second_401_raises(client, aioclient_mock):
    aioclient_mock.get(f"{BASE}/led/mode", status=401)
    with pytest.raises(TwinklyAuthError):
        await client.get_mode()
    assert len(_calls(aioclient_mock, "login")) == 2
    assert len(_calls(aioclient_mock, "led/mode")) == 2


async def test_bad_code_raises(client, aioclient_mock):
    aioclient_mock.get(f"{BASE}/led/mode", json={"code": 1103})
    with pytest.raises(TwinklyResponseError, match="1103 to GET led/mode") as err:
        await client.get_mode()
    assert err.value.code == 1103


@pytest.mark.parametrize("reply", [{"text": "<html></html>"}, {"json": []}])
async def test_not_a_twinkly_reply_raises(client, aioclient_mock, reply):
    """A wrong host may answer 200 with a web page; that must stay a TwinklyError."""
    aioclient_mock.get(f"{BASE}/gestalt", **reply)
    with pytest.raises(TwinklyResponseError, match="GET gestalt") as err:
        await client.gestalt()
    assert err.value.code is None


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("fw/version", "firmware_version"),
        ("led/out/brightness", "get_brightness"),
        ("led/effects/current", "get_current_effect"),
        ("led/mode", "get_mode"),
        ("led/effects", "get_effects"),
        ("movies", "get_movies"),
    ],
)
async def test_reply_without_field_raises(client, aioclient_mock, path, method):
    """A code-1000 reply that lacks the field the client reads stays a TwinklyError."""
    aioclient_mock.get(f"{BASE}/{path}", json={"code": 1000})
    with pytest.raises(TwinklyResponseError, match=f"GET {path}"):
        await getattr(client, method)()


@pytest.mark.parametrize("field", ["authentication_token", "challenge-response"])
async def test_login_reply_without_field_raises(hass, aioclient_mock, field):
    reply = {"authentication_token": "tok", "challenge-response": "cr", "code": 1000}
    del reply[field]
    aioclient_mock.post(f"{BASE}/login", json=reply)
    aioclient_mock.post(f"{BASE}/verify", json={"code": 1000})
    with pytest.raises(TwinklyResponseError, match="POST login"):
        await TwinklyClient(HOST, async_get_clientsession(hass)).login()


async def test_http_error_raises(client, aioclient_mock):
    """Deleting the movie that plays answers HTTP 412; the client only raises, naming the request."""
    aioclient_mock.delete(f"{BASE}/movies/ABC", status=412)
    with pytest.raises(TwinklyResponseError) as err:
        await client.delete_movie("ABC")
    assert err.value.code == 412
    assert "DELETE" in str(err.value)
    assert "movies/ABC" in str(err.value)
    assert "tok" not in str(err.value)


@pytest.mark.parametrize("exc", [aiohttp.ClientError, TimeoutError])
async def test_connection_error(client, aioclient_mock, exc):
    aioclient_mock.get(f"{BASE}/gestalt", exc=exc())
    with pytest.raises(TwinklyConnectionError):
        await client.gestalt()


async def test_upload_is_octet_stream(client, aioclient_mock):
    aioclient_mock.post(f"{BASE}/movies/full", json={"frames_number": 2, "code": 1000})
    body = bytes(range(16))
    assert (await client.upload_movie(body))["frames_number"] == 2
    _, _, data, headers = _calls(aioclient_mock, "movies/full")[0]
    assert data == body
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["X-Auth-Token"] == "tok"


async def test_rt_frame_is_octet_stream(client, aioclient_mock):
    aioclient_mock.post(f"{BASE}/led/rt/frame", json={"code": 1000})
    frame = bytes([0, 255, 0, 0] * 4)
    await client.send_rt_frame(frame)
    _, _, data, headers = _calls(aioclient_mock, "led/rt/frame")[0]
    assert data == frame
    assert headers["Content-Type"] == "application/octet-stream"
    assert headers["X-Auth-Token"] == "tok"


async def test_delete_movie_path(client, aioclient_mock):
    aioclient_mock.delete(f"{BASE}/movies/abc", json={"code": 1000})
    await client.delete_movie("abc")
    method, url, _, _ = aioclient_mock.mock_calls[-1]
    assert method.upper() == "DELETE"
    assert url.path == "/xled/v1/movies/abc"


async def test_delete_movie_rejects_empty_id(client, aioclient_mock):
    for bad in ("", "  "):
        with pytest.raises(ValueError):
            await client.delete_movie(bad)
    assert aioclient_mock.call_count == 0


@pytest.mark.parametrize("bad", [".", "..", "?", "#", "%2e", "a/b"])
async def test_delete_movie_rejects_path_tricks(client, aioclient_mock, bad):
    """Not one plain path segment: ".", "?", "#" and "%2e" would become DELETE /movies/."""
    with pytest.raises(ValueError):
        await client.delete_movie(bad)
    assert aioclient_mock.call_count == 0


async def test_brightness_payload(client, aioclient_mock):
    aioclient_mock.post(f"{BASE}/led/out/brightness", json={"code": 1000})
    await client.set_brightness(42)
    assert _calls(aioclient_mock, "led/out/brightness")[0][2] == {
        "mode": "enabled",
        "type": "A",
        "value": 42,
    }


@pytest.mark.parametrize(("mode", "expected"), [("enabled", 40), ("disabled", 100)])
async def test_get_brightness(client, aioclient_mock, mode, expected):
    """Mode "disabled" means no dimming at all, whatever "value" says."""
    aioclient_mock.get(
        f"{BASE}/led/out/brightness", json={"mode": mode, "value": 40, "code": 1000}
    )
    assert await client.get_brightness() == expected
