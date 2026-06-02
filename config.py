"""心率区间配置及显示设置，从外部 JSON 文件加载。"""

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hr_zones.json")

DEFAULT_ZONES: list[dict] = [
    {"name": "放松", "max": 90,  "color": "#CBFFCD", "accent": "#AAE7AC"},
    {"name": "热身", "max": 115, "color": "#4C98AF", "accent": "#81B9C7"},
    {"name": "燃脂", "max": 134, "color": "#00FF22", "accent": "#4DFF65"},
    {"name": "有氧", "max": 157, "color": "#E5F321", "accent": "#F4F664"},
    {"name": "无氧", "max": 172, "color": "#B06527", "accent": "#D8BA93"},
    {"name": "极限", "max": 999, "color": "#F43636", "accent": "#EF9A9A"},
]

DEFAULT_DISPLAY: dict[str, object] = {
    "show_heart": True,
    "show_zone_label": True,
    "show_zone_bar": True,
    "topmost": True,
    "alignment": 132,  # Qt.AlignCenter
    "font_scale": 1.0,
}


def _load_config(path: str) -> dict:
    """读取整个 JSON 配置文件，文件不存在或损坏时返回空 dict。"""
    if not os.path.exists(path):
        logger.debug("配置文件不存在: %s", path)
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        logger.debug("已加载配置: %s", path)
        return data
    except (json.JSONDecodeError, OSError):
        logger.warning("配置文件损坏或无法读取: %s", path)
        return {}


def _save_config(data: dict, path: str):
    """原子写入 JSON 配置文件：先写临时文件再替换，避免写入中断导致损坏。"""
    dirname = os.path.dirname(path) or "."
    try:
        fd, tmp = tempfile.mkstemp(dir=dirname, suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        os.replace(tmp, path)
        logger.debug("已保存配置: %s", path)
    except OSError:
        logger.exception("保存配置失败: %s", path)


def load_zones(path: str | None = None) -> list[dict]:
    """从 JSON 文件加载心率区间，若文件不存在则返回内置默认值。"""
    path = path or CONFIG_PATH
    data = _load_config(path)
    zones = data.get("zones", [])
    if zones:
        logger.info("从配置文件加载了 %d 个心率区间", len(zones))
        return zones
    logger.info("使用内置默认心率区间")
    return [dict(z) for z in DEFAULT_ZONES]


def load_display(path: str | None = None) -> dict:
    """从 JSON 文件加载显示设置，用默认值补全缺失的键。"""
    path = path or CONFIG_PATH
    data = _load_config(path)
    display = data.get("display", {})
    result = dict(DEFAULT_DISPLAY)
    result.update({k: v for k, v in display.items() if k in result})
    return result


def save_display(settings: dict, path: str | None = None):
    """将显示设置写回 JSON 文件（保留 zones 和 device_name 不变）。"""
    path = path or CONFIG_PATH
    data = _load_config(path)
    if "zones" not in data:
        data["zones"] = [dict(z) for z in DEFAULT_ZONES]
    data["display"] = settings
    _save_config(data, path)


def load_device_name(path: str | None = None) -> str:
    """从配置文件加载目标设备名，若未设置则返回空字符串。"""
    path = path or CONFIG_PATH
    data = _load_config(path)
    name = data.get("device_name", "")
    if name:
        logger.info("目标设备名: %s", name)
    return name


def save_device_name(name: str, path: str | None = None):
    """将目标设备名写回 JSON 配置文件（保留其他字段不变）。"""
    path = path or CONFIG_PATH
    data = _load_config(path)
    if "zones" not in data:
        data["zones"] = [dict(z) for z in DEFAULT_ZONES]
    data["device_name"] = name
    _save_config(data, path)


def get_zone(hr: int, zones: list[dict]) -> dict:
    """根据心率值返回对应的区间配置，未匹配时返回最后一个区间。"""
    for z in zones:
        if hr <= z["max"]:
            return z
    return zones[-1]
