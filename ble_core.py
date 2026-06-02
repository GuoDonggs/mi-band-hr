"""BLE 操作：扫描、连接、心率数据解析，支持断线自动重连。"""

import asyncio
import logging
import queue
import struct

from bleak import BleakScanner, BleakClient

import config

logger = logging.getLogger(__name__)

HEART_RATE_SERVICE_UUID = "0000180d-0000-1000-8000-00805f9b34fb"
HEART_RATE_MEASUREMENT_UUID = "00002a37-0000-1000-8000-00805f9b34fb"

RECONNECT_DELAY = 5       # 断线后等待 N 秒再重连
SCAN_RETRIES = 10         # 单次扫描最大重试次数
SCAN_RETRY_DELAY = 2      # 扫描重试间隔（秒）
NOT_FOUND_RETRY = 30      # 未找到设备时等待 N 秒再重新扫描


def parse_hr(data: bytes) -> int | None:
    """解析标准 Bluetooth Heart Rate Measurement 数据。
    Flags bit 0 决定 HR 是 8-bit 还是 16-bit 格式。
    """
    if not data:
        return None
    flags = data[0]
    if flags & 0x01:
        hr = struct.unpack_from("<H", data, 1)[0]  # 16-bit 小端
    else:
        hr = data[1]  # 8-bit
    return hr


def _quit_requested(cmd_queue: queue.Queue) -> bool:
    """非阻塞检查队列中是否有 quit 信号。
    非 quit 消息会被放回队列，避免丢失。
    """
    try:
        msg = cmd_queue.get_nowait()
        if msg.get("type") == "quit":
            logger.debug("收到 quit 信号")
            return True
        # 非 quit 消息放回，避免丢失
        cmd_queue.put(msg)
    except queue.Empty:
        pass
    return False


async def find_mi_band(target_name: str = ""):
    """扫描 BLE 设备并匹配。
    若配置了 target_name 则按设备名匹配（忽略大小写），
    否则回退为匹配含 "Band" 的设备。
    """
    logger.debug("开始 BLE 扫描 (timeout=5s)")
    devices = await BleakScanner.discover(timeout=5.0, return_adv=True)
    logger.debug("扫描到 %d 个设备", len(devices))

    for dev, adv in devices.values():
        name = dev.name or ""
        if target_name:
            if target_name.lower() in name.lower():
                logger.info("按名称匹配到设备: %s (%s)", name, dev.address)
                return dev
        elif "Band" in name:
            logger.info("自动匹配到含 Band 的设备: %s (%s)", name, dev.address)
            return dev

    logger.debug("未匹配到设备 (target_name=%r)", target_name)
    return None


async def _scan_until_found(cmd_queue: queue.Queue, target_name: str = ""):
    """反复扫描直到找到匹配设备或收到 quit。"""
    for attempt in range(SCAN_RETRIES):
        if _quit_requested(cmd_queue):
            return None
        cmd_queue.put({"type": "status", "text": f"扫描中 ({attempt + 1}/{SCAN_RETRIES})..."})
        device = await find_mi_band(target_name)
        if device:
            cmd_queue.put({"type": "status", "text": f"找到: {device.name}"})
            return device
        await asyncio.sleep(SCAN_RETRY_DELAY)
    return None


async def _monitor_connection(client: BleakClient, cmd_queue: queue.Queue):
    """保持连接并持续接收心率通知，直到断线或收到 quit。"""
    def hr_callback(_, data):
        hr = parse_hr(data)
        if hr is not None:
            cmd_queue.put({"type": "hr", "value": hr})

    await client.start_notify(HEART_RATE_MEASUREMENT_UUID, hr_callback)
    cmd_queue.put({"type": "status", "text": "监听中..."})
    logger.info("已开始心率通知监听")

    while True:
        if _quit_requested(cmd_queue):
            return "quit"
        if not client.is_connected:
            logger.warning("BLE 连接断开")
            return "disconnected"
        await asyncio.sleep(1)


def run_ble(cmd_queue: queue.Queue):
    """后台线程入口：循环 扫描→连接→监听，断线自动重连。"""

    async def ble_task():
        while True:
            if _quit_requested(cmd_queue):
                logger.info("BLE 线程收到 quit，退出")
                return

            target_name = config.load_device_name()
            logger.debug("当前目标设备: %r", target_name)
            device = await _scan_until_found(cmd_queue, target_name)

            if device is None:
                hint = target_name or "含 Band 的设备"
                logger.warning("未找到设备 (%s)，%ds 后重试", hint, NOT_FOUND_RETRY)
                cmd_queue.put({"type": "status", "text": f"未找到({hint}), {NOT_FOUND_RETRY}s后重试"})
                for _ in range(NOT_FOUND_RETRY):
                    if _quit_requested(cmd_queue):
                        return
                    await asyncio.sleep(1)
                continue

            try:
                async with BleakClient(device.address) as client:
                    logger.info("已连接: %s (%s)", device.name, device.address)
                    cmd_queue.put({"type": "status", "text": f"已连接 {device.name}"})
                    reason = await _monitor_connection(client, cmd_queue)

                    if reason == "quit":
                        return
                    cmd_queue.put({"type": "status", "text": "连接断开, 重连中..."})

            except Exception as e:
                logger.exception("BLE 连接异常: %s", e)
                cmd_queue.put({"type": "status", "text": f"错误: {str(e)[:15]}"})

            logger.info("%ds 后尝试重连", RECONNECT_DELAY)
            cmd_queue.put({"type": "status", "text": f"{RECONNECT_DELAY}s后重连..."})
            for _ in range(RECONNECT_DELAY):
                if _quit_requested(cmd_queue):
                    return
                await asyncio.sleep(1)

    asyncio.run(ble_task())
