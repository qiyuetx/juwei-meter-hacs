"""BLE 链路诊断实体：连接状态 / 信号强度 / 最后广播时间。

判据：
- 直连设备（HA 主机能扫到广播）：以 HA 蓝牙发现缓存（async_last_service_info）为准。
- 经蓝牙代理转发的设备：HA 主机通常扫不到设备自身的广播，本集成数据经由
  代理的 BLE 连接持续上送。此时改用「本集成数据采集链路」最近是否成功取数
  （coordinator.last_data_time）判断在线，避免误报「已断开」。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from homeassistant.components import bluetooth
from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.helpers.entity import DeviceInfo

# 超过这个秒数没收到新广播 / 新数据就视为离线
STALE_AFTER = 120.0


def ble_status(hass: Any, address: str) -> dict[str, Any]:
    """HA 主机蓝牙栈扫描到的广播缓存。"""
    info = bluetooth.async_last_service_info(hass, address, True)
    if info is None:
        info = bluetooth.async_last_service_info(hass, address, False)
    if info is None:
        return {
            "online": False,
            "rssi": None,
            "last_seen": None,
            "age": None,
            "source": None,
            "connectable": False,
        }
    age = max(0.0, time.monotonic() - info.time)
    return {
        "online": age <= STALE_AFTER,
        "rssi": info.rssi,
        "last_seen": datetime.now(timezone.utc) - timedelta(seconds=age),
        "age": round(age, 1),
        "source": info.source,
        "connectable": info.connectable,
    }


def data_fresh_time(coordinator: Any) -> float | None:
    """coordinator 最后成功取数的 monotonic 时间戳；无 coordinator 则返回 None。"""
    if coordinator is None:
        return None
    return getattr(coordinator, "last_data_time", None)


def resolve_data_time(coordinator: Any, last_comm_func: Any) -> float | None:
    """在线判据的时间戳来源：优先 coordinator 的数据新鲜时间，其次自定义通信时间函数。"""
    if coordinator is not None:
        t = getattr(coordinator, "last_data_time", None)
        if t is not None:
            return t
    if last_comm_func is not None:
        try:
            return last_comm_func()
        except Exception:  # noqa: BLE001
            return None
    return None


class BleConnectivity(BinarySensorEntity):
    """蓝牙连接状态（已连接 / 已断开）。

    直连设备看广播；经代理转发的设备看本集成数据是否在刷新。
    """

    _attr_should_poll = True
    _attr_has_entity_name = True
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_name = "蓝牙连接状态"

    def __init__(
        self,
        address: str,
        device_info: DeviceInfo,
        unique_prefix: str,
        coordinator: Any = None,
        last_comm_func: Any = None,
        rssi_func: Any = None,
        ping_func: Any = None,
    ) -> None:
        self._address = address
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_prefix}_ble_connectivity"
        self._coordinator = coordinator
        self._last_comm_func = last_comm_func
        self._rssi_func = rssi_func
        self._ping_func = ping_func
        self._status: dict[str, Any] = {}
        self._data_time: float | None = None

    async def async_update(self) -> None:
        self._status = ble_status(self.hass, self._address)
        if self._ping_func is not None:
            try:
                await self._ping_func()
            except Exception:  # noqa: BLE001
                pass
        self._data_time = resolve_data_time(self._coordinator, self._last_comm_func)

    @property
    def available(self) -> bool:
        # 始终可用，离线时显示「已断开」而不是「不可用」
        return True

    @property
    def is_on(self) -> bool:
        if self._status.get("online"):
            return True
        # 代理设备：HA 主机扫不到广播，改看本集成数据是否在刷新
        if self._data_time is not None and (time.monotonic() - self._data_time) <= STALE_AFTER:
            return True
        return False

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = {
            "address": self._address,
            "last_advertisement_age": self._status.get("age"),
            "source": self._status.get("source"),
            "connectable": self._status.get("connectable"),
        }
        if self._data_time is not None:
            d["last_data_update_age"] = round(time.monotonic() - self._data_time, 1)
            d["link"] = "data_proxy" if not self._status.get("online") else "ble_scan"
        return d


class BleRssi(SensorEntity):
    """最近一次广播 / 数据链路的信号强度。"""

    _attr_should_poll = True
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.SIGNAL_STRENGTH
    _attr_native_unit_of_measurement = "dBm"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_name = "信号强度"

    def __init__(
        self,
        address: str,
        device_info: DeviceInfo,
        unique_prefix: str,
        coordinator: Any = None,
        last_comm_func: Any = None,
        rssi_func: Any = None,
        ping_func: Any = None,
    ) -> None:
        self._address = address
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_prefix}_ble_rssi"
        self._coordinator = coordinator
        self._last_comm_func = last_comm_func
        self._rssi_func = rssi_func
        self._ping_func = ping_func
        self._status: dict[str, Any] = {}
        self._data_time: float | None = None

    async def async_update(self) -> None:
        self._status = ble_status(self.hass, self._address)
        if self._ping_func is not None:
            try:
                await self._ping_func()
            except Exception:  # noqa: BLE001
                pass
        self._data_time = resolve_data_time(self._coordinator, self._last_comm_func)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        if self._rssi_func is not None:
            try:
                return self._rssi_func()
            except Exception:  # noqa: BLE001
                return None
        return self._status.get("rssi")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = {
            "address": self._address,
            "last_advertisement_age": self._status.get("age"),
            "source": self._status.get("source"),
            "connectable": self._status.get("connectable"),
        }
        if self._data_time is not None:
            d["last_data_update_age"] = round(time.monotonic() - self._data_time, 1)
        return d


class BleLastAdvertisement(SensorEntity):
    """最近一次收到广播 / 数据更新的时间（前端显示为「x 秒前」）。"""

    _attr_should_poll = True
    _attr_has_entity_name = True
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_name = "最后广播时间"

    def __init__(
        self,
        address: str,
        device_info: DeviceInfo,
        unique_prefix: str,
        coordinator: Any = None,
        last_comm_func: Any = None,
        rssi_func: Any = None,
        ping_func: Any = None,
    ) -> None:
        self._address = address
        self._attr_device_info = device_info
        self._attr_unique_id = f"{unique_prefix}_ble_last_advertisement"
        self._coordinator = coordinator
        self._last_comm_func = last_comm_func
        self._rssi_func = rssi_func
        self._ping_func = ping_func
        self._status: dict[str, Any] = {}
        self._data_time: float | None = None

    async def async_update(self) -> None:
        self._status = ble_status(self.hass, self._address)
        if self._ping_func is not None:
            try:
                await self._ping_func()
            except Exception:  # noqa: BLE001
                pass
        self._data_time = resolve_data_time(self._coordinator, self._last_comm_func)

    @property
    def available(self) -> bool:
        return True

    @property
    def native_value(self):
        seen = self._status.get("last_seen")
        if seen is not None:
            return seen
        # 代理设备无广播记录：退化为最后成功取数的时间
        if self._data_time is not None:
            age = max(0.0, time.monotonic() - self._data_time)
            return datetime.now(timezone.utc) - timedelta(seconds=age)
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        d = {
            "address": self._address,
            "last_advertisement_age": self._status.get("age"),
            "source": self._status.get("source"),
            "connectable": self._status.get("connectable"),
        }
        if self._data_time is not None and self._status.get("last_seen") is None:
            d["last_data_update_age"] = round(time.monotonic() - self._data_time, 1)
            d["note"] = "本设备经蓝牙代理转发，HA 主机未缓存其广播；此处为最后数据更新时间"
        return d
