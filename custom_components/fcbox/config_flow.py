"""Config flow for the 丰巢快递柜 integration."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    API_BODY,
    API_URL,
    BASE_URL,
    CONF_AUTHORIZATION,
    CONF_FC_USER_AUTH,
    CONF_FC_USER_FLAG,
    CONF_MOBILE,
    CONF_VERIFY_CODE,
    DOMAIN,
    OP_CODE,
    USER_AGENT,
)


def _headers(mobile: str, fc_user_auth: str, authorization: str | None = None) -> dict[str, str]:
    headers = {
        "User-Agent": USER_AGENT,
        "FC_USER_AUTH": fc_user_auth,
        "Content-Type": "application/x-www-form-urlencoded",
    }
    if authorization:
        headers["Authorization"] = authorization
    return headers


async def _check_mobile(hass, mobile: str, fc_user_auth: str) -> None:
    """Check the mobile number is registered."""
    session = async_get_clientsession(hass)
    url = (
        f"{BASE_URL}/v1/account/secureCheckMobile?mobile={mobile}"
        f"&type=11&opCode={OP_CODE}&nationCode=86"
    )
    async with asyncio.timeout(30):
        resp = await session.get(url, headers=_headers(mobile, fc_user_auth))
        payload = await resp.json(content_type=None)
    if payload.get("code") != 150100000:
        raise aiohttp.ClientError(payload.get("msg", "check mobile failed"))


async def _send_code(hass, mobile: str, fc_user_auth: str) -> None:
    """Ask 丰巢 to send an SMS verification code."""
    await _check_mobile(hass, mobile, fc_user_auth)
    session = async_get_clientsession(hass)
    url = (
        f"{BASE_URL}/v1/account/secureSendCode?mobile={mobile}"
        f"&type=11&opCode={OP_CODE}&nationCode=86&sliderTicket=&sliderRandstr=&sign=abc"
    )
    async with asyncio.timeout(30):
        resp = await session.post(url, headers=_headers(mobile, fc_user_auth))
        payload = await resp.json(content_type=None)
    if payload.get("code") != 150100000:
        raise aiohttp.ClientError(payload.get("msg", "send code failed"))


async def _login(
    hass, mobile: str, verify_code: str, fc_user_auth: str
) -> tuple[str, str]:
    """Login with the SMS code, return (authorization, user_id)."""
    session = async_get_clientsession(hass)
    url = (
        f"{BASE_URL}/v1/account/secureLoginByPhone?mobile={mobile}"
        f"&verifyCode={verify_code}&channel=0&type=1&weiXinUser=&nationCode=86"
        f"&opCode={OP_CODE}&sign=abc"
    )
    async with asyncio.timeout(30):
        resp = await session.post(url, headers=_headers(mobile, fc_user_auth))
        payload = await resp.json(content_type=None)
    authorization = resp.headers.get("Authorization")
    if not authorization:
        raise aiohttp.ClientError(payload.get("msg", "login failed"))
    user_id = (payload.get("data") or {}).get("userId")
    if not user_id:
        raise aiohttp.ClientError("no userId in login response")
    return authorization, str(user_id)


class FcboxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for 丰巢快递柜 (mobile + SMS code login)."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._mobile: str = ""
        self._fc_user_auth: str = ""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: mobile number, send the SMS code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._mobile = str(user_input[CONF_MOBILE]).strip()
            self._fc_user_auth = str(user_input.get(CONF_FC_USER_AUTH) or "").strip()
            try:
                await _send_code(self.hass, self._mobile, self._fc_user_auth)
            except asyncio.TimeoutError:
                errors["base"] = "timeout"
            except aiohttp.ClientError as err:
                errors["base"] = "cannot_send"
                self._last_error = str(err)
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_send"
            else:
                return await self.async_step_verify()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MOBILE): str,
                    vol.Optional(CONF_FC_USER_AUTH, default=""): str,
                }
            ),
            errors=errors,
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: verification code, perform the login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                authorization, user_id = await _login(
                    self.hass,
                    self._mobile,
                    str(user_input[CONF_VERIFY_CODE]).strip(),
                    self._fc_user_auth,
                )
            except asyncio.TimeoutError:
                errors["base"] = "timeout"
            except Exception:  # noqa: BLE001
                errors["base"] = "login_failed"
            else:
                await self.async_set_unique_id(user_id)
                existing = next(
                    (
                        e
                        for e in self.hass.config_entries.async_entries(DOMAIN)
                        if e.unique_id == user_id
                    ),
                    None,
                )
                new_data = {
                    CONF_MOBILE: self._mobile,
                    CONF_AUTHORIZATION: authorization,
                    CONF_FC_USER_FLAG: user_id,
                    CONF_FC_USER_AUTH: self._fc_user_auth,
                }
                if existing is not None:
                    self.hass.config_entries.async_update_entry(
                        existing, data=new_data
                    )
                    return self.async_abort(reason="already_configured")
                return self.async_create_entry(
                    title=f"丰巢 {self._mobile}",
                    data=new_data,
                )

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required(CONF_VERIFY_CODE): str}),
            errors=errors,
            description_placeholders={"mobile": self._mobile},
        )

    @staticmethod
    def async_get_options_flow(config_entry: ConfigEntry) -> "FcboxOptionsFlow":
        """Return the options flow handler."""
        return FcboxOptionsFlow()


class FcboxOptionsFlow(OptionsFlow):
    """Re-login / update the Authorization token."""

    def __init__(self) -> None:
        """Initialise."""
        self._mobile: str = ""
        self._fc_user_auth: str = ""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 1: mobile number, send the SMS code."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._mobile = str(user_input[CONF_MOBILE]).strip()
            self._fc_user_auth = str(user_input.get(CONF_FC_USER_AUTH) or "").strip()
            try:
                await _send_code(self.hass, self._mobile, self._fc_user_auth)
            except asyncio.TimeoutError:
                errors["base"] = "timeout"
            except Exception:  # noqa: BLE001
                errors["base"] = "cannot_send"
            else:
                return await self.async_step_verify()

        current = self.config_entry.data
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_MOBILE, default=current.get(CONF_MOBILE, "")
                    ): str,
                    vol.Optional(
                        CONF_FC_USER_AUTH, default=current.get(CONF_FC_USER_AUTH, "")
                    ): str,
                }
            ),
            errors=errors,
        )

    async def async_step_verify(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Step 2: verification code, perform the login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                authorization, user_id = await _login(
                    self.hass,
                    self._mobile,
                    str(user_input[CONF_VERIFY_CODE]).strip(),
                    self._fc_user_auth,
                )
            except asyncio.TimeoutError:
                errors["base"] = "timeout"
            except Exception:  # noqa: BLE001
                errors["base"] = "login_failed"
            else:
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={
                        **self.config_entry.data,
                        CONF_MOBILE: self._mobile,
                        CONF_AUTHORIZATION: authorization,
                        CONF_FC_USER_FLAG: user_id,
                        CONF_FC_USER_AUTH: self._fc_user_auth,
                    },
                )
                return self.async_create_entry(title="", data={})

        return self.async_show_form(
            step_id="verify",
            data_schema=vol.Schema({vol.Required(CONF_VERIFY_CODE): str}),
            errors=errors,
            description_placeholders={"mobile": self._mobile},
        )
