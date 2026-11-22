"""炬为电能表的 5 个遥测量 + 2 个 BLE 链路诊断量。"""
from __future__ import annotations

from datetime import timedelta

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfEnergy,
    UnitOfPower,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .ble_diagnostics import BleLastAdvertisement, BleRssi
from .const import DOMAIN, MANUFACTURER, MODEL

SCAN_INTERVAL = timedelta(seconds=20)


def juwei_device_info(coordinator) -> DeviceInfo:
    """统一设备信息，保证遥测量与 BLE 诊断实体挂到同一台设备。"""
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.address)},
        name=coordinator.device_name,
        manufacturer=MANUFACTURER,
        model=MODEL,
    )


SENSORS: tuple[SensorEntityDescription, ...] = (
    SensorEntityDescription(
        key="voltage",
        name="电压",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="current",
        name="电流",
        device_class=SensorDeviceClass.CURRENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    SensorEntityDescription(
        key="power",
        name="功率",
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
    ),
    SensorEntityDescription(
        key="power_factor",
        name="功率因数",
        device_class=SensorDeviceClass.POWER_FACTOR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=3,
    ),
    SensorEntityDescription(
        key="energy",
        name="累计电量",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator = hass.data[DOMAIN][entry.entry_id]
    info = juwei_device_info(coordinator)
    entities: list = [JuweiSensor(coordinator, desc) for desc in SENSORS]
    entities += [
        BleRssi(coordinator.address, info, coordinator.address, coordinator),
        BleLastAdvertisement(coordinator.address, info, coordinator.address, coordinator),
    ]
    async_add_entities(entities)


class JuweiSensor(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, description: SensorEntityDescription) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.address}_{description.key}"
        self._attr_device_info = juwei_device_info(coordinator)

    @property
    def native_value(self):
        data = self.coordinator.data
        return data.get(self.entity_description.key) if data else None
