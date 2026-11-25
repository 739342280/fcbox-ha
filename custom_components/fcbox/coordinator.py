"""DataUpdateCoordinator for the 丰巢快递柜 integration."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from typing import Any

from aiohttp import ClientTimeout

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    API_BODY,
    API_URL,
    CONF_AUTHORIZATION,
    CONF_FC_USER_AUTH,
    CONF_FC_USER_FLAG,
    DOMAIN,
    UPDATE_INTERVAL_MINUTES,
    USER_AGENT,
)

_LOGGER = logging.getLogger(__name__)


class FcboxCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetch pending-pickup parcels from the 丰巢 consumer API."""

    def __init__(self, hass: HomeAssistant, data: dict[str, Any]) -> None:
        """Initialise the coordinator."""
        self._fc_user_flag: str = data[CONF_FC_USER_FLAG]
        self._fc_user_auth: str = data[CONF_FC_USER_AUTH]
        self._authorization: str = data[CONF_AUTHORIZATION]
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(minutes=UPDATE_INTERVAL_MINUTES),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the pending pickup list."""
        session = async_get_clientsession(self.hass)
        headers = {
            "FC_USER_FLAG": self._fc_user_flag,
            "FC_USER_AUTH": self._fc_user_auth,
            "Authorization": self._authorization,
            "User-Agent": USER_AGENT,
            "Content-Type": "application/x-www-form-urlencoded",
        }
        try:
            async with asyncio.timeout(30):
                resp = await session.post(
                    API_URL,
                    headers=headers,
                    data=API_BODY,
                    timeout=ClientTimeout(total=30),
                )
                if resp.status != 200:
                    raise UpdateFailed(f"HTTP {resp.status}")
                payload = await resp.json(content_type=None)
        except asyncio.TimeoutError as err:
            raise UpdateFailed("timeout") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(str(err)) from err

        if not payload.get("success") and payload.get("code") not in ("030100000",):
            raise UpdateFailed(f"API error: {payload.get('msg')}")

        inner = payload.get("data") or {}
        return {
            "to_pick_total": inner.get("toPickTotal") or 0,
            "items": inner.get("data") or [],
        }
