"""炬为电能表：由 HA 直接通过蓝牙（含 ESPHome BLE Proxy）连接并解析 FFE0/FFE1 私有帧。

链路：电能表 --BLE--> 手机 BLE Proxy / 本机蓝牙适配器 --HA bluetooth--> 本集成
      --解析 36 字节帧--> 5 个 sensor 实体
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import voluptuous as vol
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_ADDRESS, CONF_NAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CHAR_UUID,
    CONF_RECONNECT_INTERVAL,
    CONF_REFRESH_INTERVAL,
    DEFAULT_NAME,
    DEFAULT_RECONNECT_INTERVAL,
    DEFAULT_REFRESH_INTERVAL,
    DOMAIN,
    NOTIFY_SETTLE_DELAY,
    parse_frame,
)

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]
CONF_DEVICES = "devices"


def _as_int(value: Any, default: int, low: int, high: int) -> int:
    """把配置值夹到合法区间；缺省或损坏时用默认值。"""
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


# 平时完全用 UI 添加/选设备；YAML 只是可选的批量预置入口，支持多台
DEVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_ADDRESS): cv.string,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_REFRESH_INTERVAL): vol.Coerce(int),
        vol.Optional(CONF_RECONNECT_INTERVAL): vol.Coerce(int),
    }
)
CONFIG_SCHEMA = vol.Schema(
    {
        DOMAIN: vol.Schema(
            {
                vol.Optional(CONF_ADDRESS): cv.string,
                vol.Optional(CONF_NAME): cv.string,
                vol.Optional(CONF_REFRESH_INTERVAL): vol.Coerce(int),
                vol.Optional(CONF_RECONNECT_INTERVAL): vol.Coerce(int),
                vol.Optional(CONF_DEVICES): vol.All(cv.ensure_list, [DEVICE_SCHEMA]),
            }
        )
    },
    extra=vol.ALLOW_EXTRA,
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """YAML 可选导入：单台 address 或多台 devices，每台各起一个 flow。"""
    cfg = config.get(DOMAIN)
    if not cfg:
        return True

    devices = list(cfg.get(CONF_DEVICES) or [])
    if cfg.get(CONF_ADDRESS):
        single = dict(cfg)
        single.pop(CONF_DEVICES, None)
        devices.insert(0, single)
    for device in devices:
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": SOURCE_IMPORT},
                data=dict(device),
            )
        )
    return True


async def _async_update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项页改了设备/名称/间隔后自动重连。"""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = JuweiCoordinator(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    coordinator.async_start()
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    coordinator: JuweiCoordinator | None = hass.data[DOMAIN].pop(entry.entry_id, None)
    if coordinator is not None:
        coordinator.async_stop()
    return ok


class JuweiCoordinator(DataUpdateCoordinator[dict[str, float]]):
    """维持 BLE GATT 连接，按用户设定的间隔把最新一帧推给实体。"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_ADDRESS]}",
            update_interval=None,
        )
        self.address = str(entry.data[CONF_ADDRESS]).upper()
        self.device_name = str(entry.data.get(CONF_NAME) or DEFAULT_NAME)
        self.refresh_interval = _as_int(
            entry.data.get(CONF_REFRESH_INTERVAL), DEFAULT_REFRESH_INTERVAL, 1, 3600
        )
        self.reconnect_interval = _as_int(
            entry.data.get(CONF_RECONNECT_INTERVAL), DEFAULT_RECONNECT_INTERVAL, 1, 300
        )
        self._last_publish = 0.0
        self._client: Any = None
        self._cached_services: Any = None
        self._running = False
        self._task: asyncio.Task[Any] | None = None
        self._disconnected = asyncio.Event()
        self._lock = asyncio.Lock()
        self.last_update_success = False
        self.last_data_time = 0.0

    async def _async_update_data(self) -> dict[str, float]:
        return self.data or {}

    def async_start(self) -> None:
        self._running = True
        try:
            self._task = self.hass.async_create_background_task(
                self._connect_loop(), f"{DOMAIN} connect {self.address}"
            )
        except AttributeError:
            self._task = self.hass.async_create_task(self._connect_loop())
        _LOGGER.info(
            "%s: 开始等待蓝牙设备，刷新间隔 %ss，重连间隔 %ss",
            self.address,
            self.refresh_interval,
            self.reconnect_interval,
        )

    @callback
    def async_stop(self) -> None:
        self._running = False
        self._disconnected.set()
        if self._task is not None:
            self._task.cancel()
            self._task = None
        self.hass.async_create_task(self._async_close_client())

    def _resolve_ble_device(self):
        """PROXYFIX: 动态解析当前最优 BLE 设备（HA 按 RSSI/连接槽自动选代理）。"""
        try:
            return bluetooth.async_ble_device_from_address(
                self.hass, self.address, connectable=True
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("%s: 动态解析 BLE 设备失败: %s", self.address, err)
            return None

    async def _connect_loop(self) -> None:
        # PROXYFIX: 连续连接失败时清 GATT 缓存并指数退避，
        # 给代理时间释放连接槽（换代理后常见 -95 / stale connection）。
        failures = 0
        while self._running:
            try:
                ble_device = self._resolve_ble_device()
                if ble_device is None:
                    _LOGGER.debug("%s: 蓝牙代理尚未发现该设备", self.address)
                    self._mark_unavailable()
                    await asyncio.sleep(self.reconnect_interval)
                    continue

                await self._async_connect(ble_device)
                failures = 0  # 连上即重置退避
                # 连接期间挂在这里，断开回调会 set 这个 event
                await self._disconnected.wait()
                self._mark_unavailable()
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001
                failures += 1
                _LOGGER.warning(
                    "%s: BLE 连接异常（第 %s 次）：%s",
                    self.address, failures, err,
                )
                self._mark_unavailable()
                # 换代理后旧的 GATT 服务缓存可能失效，强制下次重新发现
                self._cached_services = None
            finally:
                await self._async_close_client()

            if self._running:
                delay = min(
                    self.reconnect_interval * (2 ** min(failures - 1, 3)), 60
                ) if failures else self.reconnect_interval
                if failures:
                    _LOGGER.debug("%s: %ss 后重试", self.address, delay)
                await asyncio.sleep(delay)

    async def _async_connect(self, ble_device: Any) -> None:
        async with self._lock:
            self._disconnected.clear()
            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                self.address,
                self._on_disconnected,
                cached_services=self._cached_services,
                # PROXYFIX: 重试时重新解析，自动切到当前信号最好的代理
                ble_device_callback=self._resolve_ble_device,
            )
            self._client = client
            self._cached_services = getattr(client, "services", None)
            # 设备 GATT 上电后需要约 1 秒才能开 notify（官方 App 同样处理）
            await asyncio.sleep(NOTIFY_SETTLE_DELAY)
            await client.start_notify(CHAR_UUID, self._on_notify)
            _LOGGER.info("%s: 已连接并订阅 FFE1 notify", self.address)

    @callback
    def _on_disconnected(self, _client: Any) -> None:
        _LOGGER.debug("%s: BLE 连接断开", self.address)
        self._mark_unavailable()
        self._disconnected.set()

    @callback
    def _on_notify(self, _sender: Any, data: bytearray) -> None:
        parsed = parse_frame(bytes(data))
        if parsed is None:
            return
        now = time.monotonic()
        # 每收到一帧有效数据都刷新链路新鲜度（与是否上送实体无关），
        # 这样连接状态判据只取决于 BLE 链路是否真的在通。
        self.last_data_time = now
        # 首帧 / 断线恢复立即上报，其余按自定义间隔节流，
        # 避免设备每秒一帧把 recorder 写爆；BLE 连接始终保持不断。
        if not self.last_update_success or (
            now - self._last_publish
        ) >= self.refresh_interval:
            self._last_publish = now
            self.async_set_updated_data(parsed)

    @callback
    def _mark_unavailable(self) -> None:
        if self.last_update_success:
            self.last_update_success = False
            self.async_update_listeners()

    async def _async_close_client(self) -> None:
        client, self._client = self._client, None
        if client is None:
            return
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
