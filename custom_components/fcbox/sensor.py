"""Sensor platform for the 丰巢快递柜 integration."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .coordinator import FcboxCoordinator


def _latest(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the most recently stored parcel (sendTm is sortable as text)."""
    if not items:
        return None
    return max(items, key=lambda item: item.get("sendTm") or "")


def _earliest_retention(items: list[dict[str, Any]]) -> str | None:
    """Return the earliest retention deadline among pending parcels."""
    values = [item.get("retentionTm") for item in items if item.get("retentionTm")]
    return min(values) if values else None


def _parse_dt(value: str | None) -> datetime | None:
    """Parse a 丰巢 datetime string ('2026-11-25 17:16:27', Beijing time)."""
    if not value:
        return None
    try:
        parsed = dt_util.parse_datetime(value)
        if parsed is None:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone(timedelta(hours=8)))
        return parsed
    except Exception:  # noqa: BLE001
        return None


def _box_label(item: dict[str, Any]) -> str | None:
    """Return 'L0213 第2行' for a parcel."""
    box = item.get("boxId")
    row = item.get("boxGlobalRow")
    if box and row is not None:
        return f"{box} 第{row}行"
    return box


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up sensors from a config entry."""
    coordinator: FcboxCoordinator = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [
        FcboxCountSensor(coordinator, entry),
        FcboxLatestSensor(coordinator, entry, "company", "最新快递公司", _company),
        FcboxLatestSensor(coordinator, entry, "express_id", "最新运单号", _express_id),
        FcboxLatestSensor(coordinator, entry, "address", "最新柜子地址", _address),
        FcboxLatestSensor(coordinator, entry, "box", "最新格口", _box_label),
        FcboxLatestSensor(coordinator, entry, "code", "最新取件码", _code),
        FcboxLatestTimeSensor(coordinator, entry, "send_time", "到站时间", "sendTm"),
        FcboxEarliestRetentionSensor(coordinator, entry),
        FcboxRetentionDateSensor(coordinator, entry),
        FcboxTotalFeeSensor(coordinator, entry),
    ]
    async_add_entities(entities)


def _company(item: dict[str, Any]) -> str | None:
    return item.get("companyName")


def _express_id(item: dict[str, Any]) -> str | None:
    return item.get("expressId")


def _address(item: dict[str, Any]) -> str | None:
    return item.get("address")


def _code(item: dict[str, Any]) -> str | None:
    return item.get("code")


class _FcboxBase(CoordinatorEntity[FcboxCoordinator], SensorEntity):
    """Common base for 丰巢 sensors."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: FcboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._entry = entry
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": "丰巢快递柜",
            "manufacturer": "丰巢",
        }

    @property
    def _items(self) -> list[dict[str, Any]]:
        return self.coordinator.data.get("items") or []


class FcboxCountSensor(_FcboxBase):
    """Pending pickup count with the full list as attributes."""

    _attr_name = "待取件"
    _attr_icon = "mdi:package-variant-closed"
    _attr_native_unit_of_measurement = "件"

    def __init__(self, coordinator: FcboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_to_pick"

    @property
    def native_value(self) -> int:
        return self.coordinator.data.get("to_pick_total", 0)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self._items
        summary = [
            {
                "address": item.get("address"),
                "code": item.get("code"),
                "box": item.get("boxId"),
                "row": item.get("boxGlobalRow"),
                "company": item.get("companyName"),
                "express_id": item.get("expressId"),
                "send_time": item.get("sendTm"),
                "retention_time": item.get("retentionTm"),
            }
            for item in items
        ]
        addresses: list[str] = []
        for item in items:
            addr = item.get("address")
            if addr and addr not in addresses:
                addresses.append(addr)
        return {
            "items": summary,
            "count": len(summary),
            "cabinet_addresses": addresses,
        }


class FcboxLatestSensor(_FcboxBase):
    """Latest parcel attribute as a standalone sensor."""

    def __init__(
        self,
        coordinator: FcboxCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        getter,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_latest_{key}"
        self._getter = getter

    @property
    def native_value(self) -> str | None:
        latest = _latest(self._items)
        if latest is None:
            return None
        return self._getter(latest)


class FcboxLatestTimeSensor(_FcboxBase):
    """Latest parcel time (timestamp device class)."""

    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:clock-outline"

    def __init__(
        self,
        coordinator: FcboxCoordinator,
        entry: ConfigEntry,
        key: str,
        name: str,
        field: str,
    ) -> None:
        super().__init__(coordinator, entry)
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._field = field

    @property
    def native_value(self) -> datetime | None:
        latest = _latest(self._items)
        if latest is None:
            return None
        return _parse_dt(latest.get(self._field))


class FcboxEarliestRetentionSensor(_FcboxBase):
    """Earliest retention deadline (the 18h free window closes then)."""

    _attr_name = "收费倒计时"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:timer-alert-outline"

    def __init__(self, coordinator: FcboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_earliest_retention"

    @property
    def native_value(self) -> datetime | None:
        return _parse_dt(_earliest_retention(self._items))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        items = self._items
        if not items:
            return {}
        target = _earliest_retention(items)
        for item in items:
            if item.get("retentionTm") == target:
                return {
                    "address": item.get("address"),
                    "code": item.get("code"),
                    "company": item.get("companyName"),
                }
        return {}


class FcboxRetentionDateSensor(_FcboxBase):
    """Exact time (minute precision) when the 18h free window ends."""

    _attr_name = "收费时间"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:calendar-clock"

    def __init__(self, coordinator: FcboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_retention_date"

    @property
    def native_value(self) -> datetime | None:
        return _parse_dt(_earliest_retention(self._items))


class FcboxTotalFeeSensor(_FcboxBase):
    """Sum of overdue custody fees across pending parcels."""

    _attr_name = "超时费合计"
    _attr_icon = "mdi:cash-clock"
    _attr_native_unit_of_measurement = "元"

    def __init__(self, coordinator: FcboxCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator, entry)
        self._attr_unique_id = f"{entry.entry_id}_total_fee"

    @property
    def native_value(self) -> float:
        total = 0.0
        for item in self._items:
            info = item.get("custodyFeeInfo") or {}
            fee = info.get("custodyFee")
            if isinstance(fee, (int, float)):
                total += float(fee)
        return round(total, 2)
