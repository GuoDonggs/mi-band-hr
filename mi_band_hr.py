"""
小米手环心率广播接收器（PyQt5 版）

通过 BLE 接收标准 Heart Rate Service 数据，悬浮窗显示实时心率。
支持自由缩放、透明背景、心率区间样式切换、设备名匹配。

启动方式：python mi_band_hr.py  或  双击 run.bat
"""

import logging
import logging.handlers
import os
import queue
import sys
import threading

from PyQt5.QtWidgets import QApplication

import ble_core
import overlay_qt

LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mi_band_hr.log")
LOG_FMT_FILE = logging.Formatter(
    "%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
)
LOG_FMT_CONSOLE = logging.Formatter(
    "[%(levelname)s] %(name)s: %(message)s"
)


def _setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 文件日志（自动轮转，最多保留 3 个备份，每个最大 1 MB）
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, encoding="utf-8", maxBytes=1_048_576, backupCount=3
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(LOG_FMT_FILE)
    root.addHandler(fh)

    # 控制台日志
    ch = logging.StreamHandler(sys.stderr)
    ch.setLevel(logging.INFO)
    ch.setFormatter(LOG_FMT_CONSOLE)
    root.addHandler(ch)


def main():
    _setup_logging()
    logger = logging.getLogger("mi_band_hr")
    logger.info("应用启动")

    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    cmd_queue: queue.Queue = queue.Queue()

    overlay = overlay_qt.HeartRateOverlay(cmd_queue)
    overlay.show()
    logger.info("悬浮窗已显示")

    ble_thread = threading.Thread(
        target=ble_core.run_ble, args=(cmd_queue,), daemon=True
    )
    ble_thread.start()
    logger.info("BLE 后台线程已启动")

    exit_code = app.exec_()
    logger.info("应用退出 (code=%d)", exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
