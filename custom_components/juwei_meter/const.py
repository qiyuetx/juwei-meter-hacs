"""炬为电能表 BLE 直连集成的常量。"""
from __future__ import annotations

DOMAIN = "juwei_meter"

CONF_ADDRESS = "address"
CONF_NAME = "name"
CONF_REFRESH_INTERVAL = "refresh_interval"
CONF_RECONNECT_INTERVAL = "reconnect_interval"

DEFAULT_NAME = "炬为电能表（蓝牙直连）"
# 设备约 1 秒推一帧；默认沿用每帧都更新（等价旧行为），用户可自行调大以减轻 recorder 写入
DEFAULT_REFRESH_INTERVAL = 1
DEFAULT_RECONNECT_INTERVAL = 5
MANUFACTURER = "炬为"
MODEL = "交流电能表"

# 炬为电能表的 GATT：服务 ffe0，数据特征 ffe1，开启 notify 后设备主动推 36 字节帧
SERVICE_UUID = "0000ffe0-0000-1000-8000-00805f9b34fb"
CHAR_UUID = "0000ffe1-0000-1000-8000-00805f9b34fb"

# 协议逆向自官方 E-test App，与 Android 端 PowerMeter.parse() 完全一致
FRAME_MIN_LEN = 36
FRAME_HEADER = (0xFF, 0x55)

RECONNECT_DELAY = 5.0
# 官方 App 也是连上后延迟 1 秒再开 notify，设备 GATT 需要时间初始化
NOTIFY_SETTLE_DELAY = 1.0


def _u24(data: bytes, i: int) -> int:
    return (data[i] & 0xFF) << 16 | (data[i + 1] & 0xFF) << 8 | (data[i + 2] & 0xFF)


def _u16(data: bytes, i: int) -> int:
    return (data[i] & 0xFF) << 8 | (data[i + 1] & 0xFF)


def _u32(data: bytes, i: int) -> int:
    return (
        (data[i] & 0xFF) << 24
        | (data[i + 1] & 0xFF) << 16
        | (data[i + 2] & 0xFF) << 8
        | (data[i + 3] & 0xFF)
    )


def parse_frame(data: bytes) -> dict[str, float] | None:
    """解析 36 字节帧，返回 5 个遥测量。无效帧返回 None。"""
    if len(data) < FRAME_MIN_LEN:
        return None
    if data[0] != FRAME_HEADER[0] or data[1] != FRAME_HEADER[1]:
        return None

    voltage = _u24(data, 4) / 10.0
    current = _u24(data, 7) / 1000.0
    power = _u24(data, 10) / 10.0
    factor = _u16(data, 22) / 1000.0
    if factor > 1.5:
        factor = 0.0
    energy = _u32(data, 13) / 100.0

    return {
        "voltage": voltage,
        "current": current,
        "power": power,
        "power_factor": factor,
        "energy": energy,
    }
