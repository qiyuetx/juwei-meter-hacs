"""炬为电能表配置流：手选设备 / 手输 MAC / 多台 / 改选 / 自定义刷新时间。"""
from __future__ import annotations

import re
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, CONF_NAME
from homeassistant.core import callback

from .const import (
    CONF_RECONNECT_INTERVAL,
    CONF_REFRESH_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_RECONNECT_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
)

MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$")
MANUAL = "__manual__"

LIKELY_HINTS = ("test", "atorch", "jdy", "meter", "pow", "炬")
LIKELY_MAC_PREFIX = "00:00:00:"

REFRESH_FIELD = vol.All(vol.Coerce(int), vol.Range(min=1, max=3600))
RECONNECT_FIELD = vol.All(vol.Coerce(int), vol.Range(min=1, max=300))


def _is_likely(address: str, name: str) -> bool:
    return address.startswith(LIKELY_MAC_PREFIX) or any(
        hint in name.lower() for hint in LIKELY_HINTS
    )


def _discovered(hass) -> dict[str, str]:
    """已发现的可连接设备 {MAC: 显示名}，疑似电能表排前面并加标记。"""
    found: list[tuple[str, str]] = []
    for info in bluetooth.async_discovered_service_info(hass, connectable=True):
        found.append((info.address.upper(), info.name or "未知设备"))
    found.sort(key=lambda item: (0 if _is_likely(*item) else 1, item[1].lower(), item[0]))

    out: dict[str, str] = {}
    for address, name in found:
        tag = " ★疑似电能表" if _is_likely(address, name) else ""
        out[address] = f"{name} ({address}){tag}"
    return out


def _configured(hass) -> set[str]:
    return {
        str(entry.data.get(CONF_ADDRESS, "")).upper()
        for entry in hass.config_entries.async_entries(DOMAIN)
    }


def _default_name(hass, address: str) -> str:
    label = _discovered(hass).get(address)
    if not label:
        return DEFAULT_NAME
    name = label.split(" (")[0]
    return DEFAULT_NAME if name in ("", "未知设备") else name


def _mac(value: Any) -> str:
    return str(value or "").strip().upper()


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _intervals(data: dict[str, Any]) -> tuple[int, int]:
    return (
        _bounded(data.get(CONF_REFRESH_INTERVAL), DEFAULT_REFRESH_INTERVAL, 1, 3600),
        _bounded(data.get(CONF_RECONNECT_INTERVAL), DEFAULT_RECONNECT_INTERVAL, 1, 300),
    )


def _interval_fields(data: dict[str, Any]) -> dict[Any, Any]:
    refresh, reconnect = _intervals(data)
    return {
        vol.Required(CONF_REFRESH_INTERVAL, default=refresh): REFRESH_FIELD,
        vol.Required(CONF_RECONNECT_INTERVAL, default=reconnect): RECONNECT_FIELD,
    }


def _entry_data(address: str, name: str, refresh: int, reconnect: int) -> dict[str, Any]:
    return {
        CONF_ADDRESS: address,
        CONF_NAME: name,
        CONF_REFRESH_INTERVAL: refresh,
        CONF_RECONNECT_INTERVAL: reconnect,
    }


class JuweiConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    _address: str = ""

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return JuweiOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            raw = user_input.get(CONF_ADDRESS)
            if raw == MANUAL:
                return await self.async_step_manual()
            self._address = _mac(raw)
            return await self.async_step_confirm()

        taken = _configured(self.hass)
        options = {
            address: label
            for address, label in _discovered(self.hass).items()
            if address not in taken
        }
        options[MANUAL] = "手动输入 MAC 地址…"
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): vol.In(options)}),
            description_placeholders={"count": str(len(options) - 1)},
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        errors: dict[str, str] = {}
        if user_input is not None:
            address = _mac(user_input.get(CONF_ADDRESS))
            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_mac"
            elif address in _configured(self.hass):
                errors[CONF_ADDRESS] = "already_configured"
            else:
                self._address = address
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_ADDRESS): str}),
            errors=errors,
        )

    async def async_step_confirm(self, user_input: dict[str, Any] | None = None):
        if user_input is not None:
            refresh, reconnect = _intervals(user_input)
            return await self._async_create(
                self._address,
                str(user_input.get(CONF_NAME) or DEFAULT_NAME),
                refresh,
                reconnect,
            )
        schema: dict[Any, Any] = {
            vol.Required(
                CONF_NAME, default=_default_name(self.hass, self._address)
            ): str,
        }
        schema.update(_interval_fields({}))
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema(schema),
            description_placeholders={"address": self._address},
        )

    async def async_step_import(self, import_info: dict[str, Any]):
        """兼容 configuration.yaml 导入（每台设备一个 flow）。"""
        address = _mac(import_info.get(CONF_ADDRESS))
        if not MAC_RE.match(address):
            return self.async_abort(reason="invalid_mac")
        refresh, reconnect = _intervals(import_info)
        return await self._async_create(
            address,
            str(import_info.get(CONF_NAME) or DEFAULT_NAME),
            refresh,
            reconnect,
        )

    async def _async_create(self, address: str, name: str, refresh: int, reconnect: int):
        await self.async_set_unique_id(address.lower())
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=name, data=_entry_data(address, name, refresh, reconnect)
        )


class JuweiOptionsFlow(config_entries.OptionsFlow):
    """改选设备、改名、调整刷新与重连间隔。"""

    async def async_step_init(self, user_input: dict[str, Any] | None = None):
        entry = self.config_entry
        current = _mac(entry.data.get(CONF_ADDRESS))

        if user_input is not None:
            raw = user_input.get(CONF_ADDRESS)
            if raw == MANUAL:
                return await self.async_step_manual()
            refresh, reconnect = _intervals(user_input)
            return await self._apply(
                _mac(raw),
                str(user_input.get(CONF_NAME) or DEFAULT_NAME),
                refresh,
                reconnect,
            )

        devices = _discovered(self.hass)
        options: dict[str, str] = {}
        if current and current not in devices:
            options[current] = f"{entry.title} ({current}) · 当前设备（未发现广播）"
        for address, label in devices.items():
            options[address] = label + (" · 当前设备" if address == current else "")
        options[MANUAL] = "手动输入 MAC 地址…"

        schema: dict[Any, Any] = {
            vol.Required(CONF_ADDRESS, default=current): vol.In(options),
            vol.Required(
                CONF_NAME, default=str(entry.data.get(CONF_NAME) or entry.title)
            ): str,
        }
        schema.update(_interval_fields(entry.data))
        return self.async_show_form(step_id="init", data_schema=vol.Schema(schema))

    async def async_step_manual(self, user_input: dict[str, Any] | None = None):
        entry = self.config_entry
        current = _mac(entry.data.get(CONF_ADDRESS))
        errors: dict[str, str] = {}

        if user_input is not None:
            address = _mac(user_input.get(CONF_ADDRESS))
            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_mac"
            elif address in (_configured(self.hass) - {current}):
                errors[CONF_ADDRESS] = "already_configured"
            else:
                refresh, reconnect = _intervals(user_input)
                return await self._apply(
                    address,
                    str(user_input.get(CONF_NAME) or DEFAULT_NAME),
                    refresh,
                    reconnect,
                )

        schema: dict[Any, Any] = {
            vol.Required(CONF_ADDRESS, default=current): str,
            vol.Required(
                CONF_NAME, default=str(entry.data.get(CONF_NAME) or DEFAULT_NAME)
            ): str,
        }
        schema.update(_interval_fields(entry.data))
        return self.async_show_form(
            step_id="manual", data_schema=vol.Schema(schema), errors=errors
        )

    async def _apply(self, address: str, name: str, refresh: int, reconnect: int):
        entry = self.config_entry
        # 改 MAC 时同步 unique_id，否则以后重新添加同一台会被误判成重复
        self.hass.config_entries.async_update_entry(
            entry,
            data=_entry_data(address, name, refresh, reconnect),
            title=name,
            unique_id=address.lower(),
        )
        return self.async_create_entry(title="", data={})
