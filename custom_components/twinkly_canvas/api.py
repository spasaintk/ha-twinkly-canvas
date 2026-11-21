"""Async client for the Twinkly local HTTP API (/xled/v1)."""

from __future__ import annotations

import asyncio
import base64
from http import HTTPStatus
import re
import secrets
from typing import Any

import aiohttp


class TwinklyError(Exception):
    """Base class of the client errors."""


class TwinklyConnectionError(TwinklyError):
    """The device did not answer, or not in time."""


class TwinklyAuthError(TwinklyError):
    """The device rejected the token, also right after a new login."""


class TwinklyResponseError(TwinklyError):
    """The device answered with an error, or with something that is not a Twinkly reply."""

    def __init__(self, code: int | None, message: str) -> None:
        super().__init__(message)
        # The JSON "code", the HTTP status of a failed request, or None: not a Twinkly reply
        # (not JSON, not an object, or without a field the client reads).
        self.code = code


class TwinklyClient:
    """One device. Logs in on the first authenticated call, and once more after a 401."""

    def __init__(self, host: str, session: aiohttp.ClientSession, timeout: float = 10.0) -> None:
        self._base = f"http://{host}/xled/v1"
        self._session = session
        self._timeout = timeout
        self._token: str | None = None
        self._login_lock = asyncio.Lock()
        self.token_epoch = 0  # +1 per successful login, so callers can tell the token changed

    async def _send(
        self,
        method: str,
        path: str,
        token: str | None = None,
        *,
        json: dict | None = None,
        data: bytes | None = None,
        timeout: float | None = None,
    ) -> dict:
        """One request; returns the JSON reply when its code is 1000 (OK)."""
        headers = {"X-Auth-Token": token} if token else {}
        if data is not None:
            headers["Content-Type"] = "application/octet-stream"
        try:
            async with self._session.request(
                method,
                f"{self._base}/{path}",
                headers=headers,
                json=json,
                data=data,
                timeout=aiohttp.ClientTimeout(total=timeout or self._timeout),
            ) as resp:
                if resp.status == HTTPStatus.UNAUTHORIZED:
                    raise TwinklyAuthError(f"{method} {path}: token rejected")
                if resp.status != HTTPStatus.OK:
                    raise TwinklyResponseError(
                        resp.status, f"Twinkly answered code {resp.status} to {method} {path}"
                    )
                reply = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            # str(), never repr(): the repr of some aiohttp errors holds the request headers.
            reason = str(err) or type(err).__name__
            raise TwinklyConnectionError(f"{method} {path}: {reason}") from err
        except ValueError as err:  # not JSON, so not a Twinkly reply (a wrong host, say)
            raise TwinklyResponseError(None, f"Not a Twinkly reply to {method} {path}") from err
        code = reply.get("code") if isinstance(reply, dict) else None
        if code != 1000:
            raise TwinklyResponseError(code, f"Twinkly answered code {code} to {method} {path}")
        return reply

    async def _read(
        self, method: str, path: str, *keys: str, auth: bool = True, **kwargs: Any
    ) -> dict:
        """A request whose reply must hold keys; one left out raises TwinklyResponseError."""
        reply = await (self._call if auth else self._send)(method, path, **kwargs)
        for key in keys:
            if key not in reply:
                raise TwinklyResponseError(
                    None, f"Twinkly left {key!r} out of its reply to {method} {path}"
                )
        return reply

    async def _login(self, seen_epoch: int) -> None:
        async with self._login_lock:
            if self.token_epoch != seen_epoch:
                return  # another call logged in while this one waited
            challenge = base64.b64encode(secrets.token_bytes(32)).decode()
            reply = await self._read(
                "POST",
                "login",
                "authentication_token",
                "challenge-response",
                auth=False,
                json={"challenge": challenge},
            )
            token = reply["authentication_token"]
            response = reply["challenge-response"]
            await self._send("POST", "verify", token, json={"challenge-response": response})
            self._token = token
            self.token_epoch += 1

    async def _call(self, method: str, path: str, **kwargs: Any) -> dict:
        """Authenticated request."""
        if self._token is None:
            await self._login(self.token_epoch)
        epoch = self.token_epoch
        try:
            return await self._send(method, path, self._token, **kwargs)
        except TwinklyAuthError:
            await self._login(epoch)
        return await self._send(method, path, self._token, **kwargs)  # a second 401 raises

    async def login(self) -> None:
        """Get and verify a new token; the device then rejects every older one."""
        await self._login(self.token_epoch)

    async def gestalt(self) -> dict:
        return await self._send("GET", "gestalt")

    async def firmware_version(self) -> str:
        return (await self._read("GET", "fw/version", "version", auth=False))["version"]

    async def led_config(self) -> dict:
        return await self._call("GET", "led/config")

    async def get_mode(self) -> dict:
        return await self._read("GET", "led/mode", "mode")

    async def set_mode(self, mode: str) -> None:
        await self._call("POST", "led/mode", json={"mode": mode})

    async def get_brightness(self) -> int:
        reply = await self._read("GET", "led/out/brightness", "value")
        if reply.get("mode") == "disabled":
            return 100  # not dimmed
        return reply["value"]

    async def set_brightness(self, value: int) -> None:
        """Value 1-100."""
        await self._call(
            "POST", "led/out/brightness", json={"mode": "enabled", "type": "A", "value": value}
        )

    async def get_effects(self) -> dict:
        return await self._read("GET", "led/effects", "effects_number")

    async def get_current_effect(self) -> int:
        return (await self._read("GET", "led/effects/current", "preset_id"))["preset_id"]

    async def set_effect(self, effect_id: int) -> None:
        await self._call("POST", "led/effects/current", json={"effect_id": effect_id})

    async def get_movies(self) -> dict:
        return await self._read("GET", "movies", "movies", "available_frames", "max_capacity")

    async def get_current_movie(self) -> dict:
        return await self._call("GET", "movies/current")

    async def set_current_movie(self, movie_id: int) -> None:
        await self._call("POST", "movies/current", json={"id": movie_id})

    async def new_movie(
        self, name: str, unique_id: str, frames: int, fps: int, leds: int, descriptor: str
    ) -> dict:
        """Create a movie entry; upload_movie() must follow directly with its frames."""
        return await self._call(
            "POST",
            "movies/new",
            json={
                "name": name,
                "unique_id": unique_id,
                "descriptor_type": descriptor,
                "leds_per_frame": leds,
                "frames_number": frames,
                "fps": fps,
            },
        )

    async def upload_movie(self, data: bytes) -> dict:
        # 20 KB/s worst case; the device managed about 51 KB/s.
        return await self._call("POST", "movies/full", data=data, timeout=10 + len(data) / 20000)

    async def delete_movie(self, unique_id: str) -> None:
        # Plain ids only: "", ".", "?" or "#" would turn into DELETE /movies/ (wipes every movie).
        if not re.fullmatch(r"[0-9A-Za-z-]+", unique_id):
            raise ValueError(f"Refusing to delete movie {unique_id!r}")
        await self._call("DELETE", f"movies/{unique_id}")

    async def send_rt_frame(self, data: bytes) -> None:
        await self._call("POST", "led/rt/frame", data=data)
