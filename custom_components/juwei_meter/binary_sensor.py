"""炬为电能表蓝牙连接状态。"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .ble_diagnostics import BleConnectivity
from .const import DOMAIN
from .sensor import juwei_device_info

SCAN_INTERVAL = timedelta(seconds=20)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            BleConnectivity(
                coordinator.address,
                juwei_device_info(coordinator),
                coordinator.address,
                coordinator,
            )
        ]
    )
