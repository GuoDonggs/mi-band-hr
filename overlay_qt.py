"""悬浮窗 UI（PyQt5）：显示实时心率，支持缩放、透明背景、心率区间样式。

使用 WA_TranslucentBackground 实现真正的逐像素 alpha 透明，
解决 tkinter -transparentcolor 方案产生的文字锯齿和黑色描边问题。

鼠标拖拽逻辑：
- 窗口边缘 6px 范围内：缩放
- 窗口内部（非交互控件）：拖拽移动
- 标题栏按钮（⚙/×）：点击事件，不穿透
- 其余标签设置 WA_TransparentForMouseEvents 穿透到容器处理
"""

import logging
import queue
from PyQt5.QtWidgets import (
    QWidget, QLabel, QMenu, QApplication,
    QColorDialog, QVBoxLayout, QHBoxLayout,
    QSizePolicy,
)
from PyQt5.QtCore import Qt, QTimer, QPoint, QRect, QSize
from PyQt5.QtGui import (
    QPainter, QColor, QFont, QMouseEvent,
)

import config

logger = logging.getLogger(__name__)

GRIP = 6
MIN_W = 160
MIN_H = 120
DEFAULT_W = 240
DEFAULT_H = 160

FAMILY_HEART = "Segoe UI Symbol"
FAMILY_MONO = "Consolas"
FAMILY_UI = "Microsoft YaHei"

FONT_HEART_BASE = 16
FONT_HR_BASE = 44
FONT_ZONE_BASE = 11
FONT_UNIT_BASE = 7
FONT_TITLE_BASE = 9
FONT_STATUS_BASE = 7
FONT_MENU_BASE = 9
FONT_SCALE_PRESETS = [0.7, 0.8, 0.9, 1.0, 1.1, 1.25, 1.5]


def _scaled_font(family: str, base: int, scale: float, bold: bool = False) -> QFont:
    font = QFont(family)
    font.setPointSizeF(base * scale)
    font.setBold(bold)
    return font

MENU_STYLE = """
    QMenu {
        background: #21262d; color: #c9d1d9;
        border: 1px solid #30363d; padding: 4px;
    }
    QMenu::item { padding: 4px 20px; }
    QMenu::item:selected { background: #30363d; color: #f0f6fc; }
"""

_ALIGN_LABELS = {Qt.AlignLeft: "左对齐", Qt.AlignCenter: "居中", Qt.AlignRight: "右对齐"}


class _ProgressBar(QWidget):
    """简易区间进度条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(6)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._fraction = 0.0
        self._color = QColor("#4CAF50")
        self._track_color = QColor("#30363d")

    def sizeHint(self):
        return self.minimumSizeHint()

    def minimumSizeHint(self):
        return QSize(100, 6)

    def set_fraction(self, v: float, color: QColor):
        self._fraction = max(0.0, min(1.0, v))
        self._color = color
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(Qt.NoPen)
        p.setBrush(self._track_color)
        p.drawRoundedRect(QRect(0, 0, w, h), 2, 2)
        fill_w = int(w * self._fraction)
        if fill_w > 0:
            p.setBrush(self._color)
            p.drawRoundedRect(QRect(0, 0, fill_w, h), 2, 2)
        p.end()


class HeartRateOverlay(QWidget):
    def __init__(self, cmd_queue: queue.Queue):
        super().__init__()
        self.cmd_queue = cmd_queue
        self.zones = config.load_zones()
        self.display = config.load_display()

        self.bg_color = QColor("#0d1117")
        self.chrome_bg = QColor("#161b22")
        self.alpha = 0.92
        self.transparent_bg = False
        self._font_scale = float(self.display.get("font_scale", 1.0))
        self._current_zone_key = None
        self._resize_dir = ""
        self._drag_pos = QPoint()
        self._drag_geo = QRect()

        self.setWindowFlags(
            Qt.FramelessWindowHint |
            (Qt.WindowStaysOnTopHint if self.display["topmost"] else Qt.Widget)
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setWindowOpacity(self.alpha)
        self.setMouseTracking(True)

        screen = QApplication.primaryScreen().availableGeometry()
        self.setGeometry(
            screen.width() - DEFAULT_W - 20, 40,
            DEFAULT_W, DEFAULT_H,
        )
        self.setMinimumSize(MIN_W, MIN_H)

        logger.info("创建悬浮窗 (%dx%d)", DEFAULT_W, DEFAULT_H)

        self._build_ui()
        self._apply_display_settings()
        self._setup_drag_resize()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_queue)
        self._timer.start(100)

    # ═══════════════════════════════════════════════════
    #  UI Construction
    # ═══════════════════════════════════════════════════

    def _build_ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ── title bar ──
        self.title_bar = QWidget(self)
        self.title_bar.setFixedHeight(28)
        self.title_bar.setStyleSheet(f"background: {self.chrome_bg.name()};")
        tb = QHBoxLayout(self.title_bar)
        tb.setContentsMargins(10, 0, 6, 0)
        tb.setSpacing(0)

        self.label_title = QLabel("♥ 心率监测")
        self.label_title.setFont(_scaled_font(FAMILY_UI, FONT_TITLE_BASE, self._font_scale))
        self.label_title.setStyleSheet("color: #8b949e; background: transparent;")
        tb.addWidget(self.label_title)
        tb.addStretch()

        btn_css = "color: #8b949e; background: transparent; padding: 0 4px;"

        self.btn_settings = QLabel("⚙")
        self.btn_settings.setFont(QFont("Segoe UI Symbol", 10))
        self.btn_settings.setStyleSheet(btn_css)
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.mousePressEvent = self._on_settings_click
        tb.addWidget(self.btn_settings)

        self.btn_close = QLabel("×")
        self.btn_close.setFont(QFont("Segoe UI", 13, QFont.Bold))
        self.btn_close.setStyleSheet(btn_css)
        self.btn_close.setCursor(Qt.PointingHandCursor)
        self.btn_close.mousePressEvent = lambda e: self._close()
        self.btn_close.enterEvent = lambda e: self.btn_close.setStyleSheet(
            btn_css.replace("#8b949e", "#f85149"))
        self.btn_close.leaveEvent = lambda e: self.btn_close.setStyleSheet(btn_css)
        tb.addWidget(self.btn_close)

        root.addWidget(self.title_bar)

        # ── content ──
        self.content = QWidget(self)
        self._content_layout = QVBoxLayout(self.content)
        self._content_layout.setContentsMargins(14, 8, 14, 4)
        self._content_layout.setSpacing(2)

        # heart + HR row
        self._hr_row = QWidget()
        self._hr_row.setStyleSheet("background: transparent;")
        self._hr_layout = QHBoxLayout(self._hr_row)
        self._hr_layout.setContentsMargins(0, 0, 0, 0)
        self._hr_layout.setSpacing(6)

        self.heart_label = QLabel("♥")
        self.heart_label.setFont(_scaled_font(FAMILY_HEART, FONT_HEART_BASE, self._font_scale))
        self.heart_label.setStyleSheet("color: #f85149; background: transparent;")
        self._hr_layout.addWidget(self.heart_label)

        self.label_hr = QLabel("--")
        self.label_hr.setFont(_scaled_font(FAMILY_MONO, FONT_HR_BASE, self._font_scale, bold=True))
        self.label_hr.setStyleSheet("color: #ffffff; background: transparent;")
        self._hr_layout.addWidget(self.label_hr)

        self._content_layout.addWidget(self._hr_row)

        self.label_zone = QLabel("")
        self.label_zone.setFont(_scaled_font(FAMILY_UI, FONT_ZONE_BASE, self._font_scale, bold=True))
        self.label_zone.setStyleSheet("color: #8b949e; background: transparent;")
        self._content_layout.addWidget(self.label_zone)

        self._zone_bar = _ProgressBar(self.content)
        self._content_layout.addWidget(self._zone_bar)

        self.label_unit = QLabel("BPM")
        self.label_unit.setFont(_scaled_font(FAMILY_UI, FONT_UNIT_BASE, self._font_scale))
        self.label_unit.setStyleSheet("color: #484f58; background: transparent;")
        self._content_layout.addWidget(self.label_unit)

        self._content_layout.addStretch()
        root.addWidget(self.content, stretch=1)

        # ── status bar ──
        self.status_bar = QWidget(self)
        self.status_bar.setFixedHeight(20)
        self.status_bar.setStyleSheet(f"background: {self.chrome_bg.name()};")
        sl = QHBoxLayout(self.status_bar)
        sl.setContentsMargins(8, 0, 8, 0)

        self.label_status = QLabel("扫描设备中...")
        self.label_status.setFont(_scaled_font(FAMILY_UI, FONT_STATUS_BASE, self._font_scale))
        self.label_status.setStyleSheet("color: #484f58; background: transparent;")
        sl.addWidget(self.label_status)
        sl.addStretch()

        root.addWidget(self.status_bar)

        # 初始对齐方式
        self._alignment = int(self.display.get("alignment", Qt.AlignCenter))
        self._apply_alignment()

    # ═══════════════════════════════════════════════════
    #  Drag & resize setup
    # ═══════════════════════════════════════════════════

    def _setup_drag_resize(self):
        """让非交互标签穿透鼠标事件，由容器处理拖拽和缩放。"""
        # 这些标签不处理点击，让事件穿透到父级容器
        pass_through = [
            self.label_title, self.heart_label, self.label_hr,
            self.label_zone, self.label_unit, self.label_status,
        ]
        for w in pass_through:
            w.setAttribute(Qt.WA_TransparentForMouseEvents, True)

        # 容器 widget 接收鼠标事件 → 缩放或拖拽
        containers = [self.title_bar, self.content, self.status_bar]
        for c in containers:
            c.setMouseTracking(True)
            c.mousePressEvent = self._container_press
            c.mouseMoveEvent = self._container_move
            c.mouseReleaseEvent = self._container_release

    def _global_to_local(self, gp: QPoint) -> QPoint:
        """将全局坐标转为相对于本窗口的本地坐标。"""
        return self.mapFromGlobal(gp)

    def _container_press(self, event: QMouseEvent):
        gp = event.globalPos()
        local = self._global_to_local(gp)
        edge = self._edge_name(local)
        self._resize_dir = edge
        self._drag_pos = gp
        self._drag_geo = self.geometry()

    def _container_move(self, event: QMouseEvent):
        gp = event.globalPos()
        if self._resize_dir:
            self._do_resize_gp(gp)
        elif event.buttons() & Qt.LeftButton and not self._drag_pos.isNull():
            delta = gp - self._drag_pos
            self.move(self._drag_geo.topLeft() + delta)
        else:
            local = self._global_to_local(gp)
            self._update_cursor(local)

    def _container_release(self, event: QMouseEvent):
        self._resize_dir = ""
        self._drag_pos = QPoint()

    # ═══════════════════════════════════════════════════
    #  Edge detection & resize logic
    # ═══════════════════════════════════════════════════

    def _edge_name(self, pos):
        d = GRIP
        w, h = self.width(), self.height()
        parts = []
        if pos.x() <= d:
            parts.append("W")
        elif pos.x() >= w - d:
            parts.append("E")
        if pos.y() <= d:
            parts.append("N")
        elif pos.y() >= h - d:
            parts.append("S")
        return "".join(parts)

    def _update_cursor(self, pos):
        cursors = {
            "N": Qt.SizeVerCursor, "S": Qt.SizeVerCursor,
            "E": Qt.SizeHorCursor, "W": Qt.SizeHorCursor,
            "NE": Qt.SizeBDiagCursor, "NW": Qt.SizeFDiagCursor,
            "SE": Qt.SizeFDiagCursor, "SW": Qt.SizeBDiagCursor,
        }
        self.setCursor(cursors.get(self._edge_name(pos), Qt.ArrowCursor))

    def _do_resize_gp(self, gp: QPoint):
        delta = gp - self._drag_pos
        g = QRect(self._drag_geo)
        d = self._resize_dir
        if "E" in d:
            g.setRight(max(g.left() + MIN_W, g.right() + delta.x()))
        if "S" in d:
            g.setBottom(max(g.top() + MIN_H, g.bottom() + delta.y()))
        if "W" in d:
            g.setLeft(min(g.right() - MIN_W, g.left() + delta.x()))
        if "N" in d:
            g.setTop(min(g.bottom() - MIN_H, g.top() + delta.y()))
        self.setGeometry(g)

    # ═══════════════════════════════════════════════════
    #  Paint
    # ═══════════════════════════════════════════════════

    def paintEvent(self, event):
        if self.transparent_bg:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(self.bg_color)
        p.drawRoundedRect(self.rect(), 8, 8)
        p.end()

    # ═══════════════════════════════════════════════════
    #  Menu
    # ═══════════════════════════════════════════════════

    def _on_settings_click(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            self._popup_menu(event.globalPos())

    def contextMenuEvent(self, event):
        self._popup_menu(event.globalPos())

    def _popup_menu(self, pos):
        menu = self._build_menu()
        menu.exec_(pos)

    def _build_menu(self):
        menu = QMenu(self)
        menu.setFont(_scaled_font(FAMILY_UI, FONT_MENU_BASE, self._font_scale))
        menu.setStyleSheet(MENU_STYLE)

        # 透明度
        am = QMenu("窗口透明度", menu)
        am.setFont(_scaled_font(FAMILY_UI, FONT_MENU_BASE, self._font_scale))
        for v in [1.0, 0.9, 0.8, 0.7, 0.55, 0.4]:
            act = am.addAction(f"{int(v * 100)}%")
            act.triggered.connect(lambda checked, a=v: self._set_alpha(a))
        menu.addMenu(am)

        # 背景
        bm = QMenu("背景设置", menu)
        bm.setFont(_scaled_font(FAMILY_UI, FONT_MENU_BASE, self._font_scale))
        bm.addAction("选择背景颜色...").triggered.connect(self._choose_bg_color)
        label = "关闭透明背景" if self.transparent_bg else "启用透明背景"
        bm.addAction(label).triggered.connect(self._toggle_transparent)
        menu.addMenu(bm)

        # 显示
        dm = QMenu("显示设置", menu)
        dm.setFont(_scaled_font(FAMILY_UI, FONT_MENU_BASE, self._font_scale))
        dm.addAction(
            "关闭心形" if self.display["show_heart"] else "显示心形"
        ).triggered.connect(self._toggle_heart)
        dm.addAction(
            "关闭区间名" if self.display["show_zone_label"] else "显示区间名"
        ).triggered.connect(self._toggle_zone_label)
        dm.addAction(
            "关闭进度条" if self.display["show_zone_bar"] else "显示进度条"
        ).triggered.connect(self._toggle_zone_bar)
        dm.addSeparator()
        dm.addAction(
            "取消置顶" if self.display["topmost"] else "始终置顶"
        ).triggered.connect(self._toggle_topmost)
        dm.addSeparator()
        font_menu = QMenu("字体大小", dm)
        font_menu.setFont(_scaled_font(FAMILY_UI, FONT_MENU_BASE, self._font_scale))
        for v in FONT_SCALE_PRESETS:
            label = f"{int(v * 100)}%"
            if v == 1.0:
                label += " (默认)"
            act = font_menu.addAction(label)
            act.triggered.connect(lambda checked, s=v: self._set_font_scale(s))
        dm.addMenu(font_menu)
        align_menu = QMenu("内容对齐", dm)
        align_menu.setFont(_scaled_font(FAMILY_UI, FONT_MENU_BASE, self._font_scale))
        for a_val, a_name in _ALIGN_LABELS.items():
            act = align_menu.addAction(a_name)
            act.triggered.connect(lambda checked, v=a_val: self._set_alignment(v))
        dm.addMenu(align_menu)
        menu.addMenu(dm)

        menu.addSeparator()
        menu.addAction("重新加载区间配置").triggered.connect(self._reload_zones)
        menu.addSeparator()
        menu.addAction("退出").triggered.connect(self._close)

        return menu

    # ═══════════════════════════════════════════════════
    #  Settings actions
    # ═══════════════════════════════════════════════════

    def _set_alpha(self, value: float):
        self.alpha = value
        self.setWindowOpacity(value)

    def _set_font_scale(self, scale: float):
        self._font_scale = scale
        self._apply_font_scale()
        self.display["font_scale"] = scale
        config.save_display(self.display)
        logger.info("字体大小: %.0f%%", scale * 100)

    def _apply_font_scale(self):
        """将当前字体缩放应用到所有 widget。"""
        s = self._font_scale
        self.label_title.setFont(_scaled_font(FAMILY_UI, FONT_TITLE_BASE, s))
        self.heart_label.setFont(_scaled_font(FAMILY_HEART, FONT_HEART_BASE, s))
        self.label_hr.setFont(_scaled_font(FAMILY_MONO, FONT_HR_BASE, s, bold=True))
        self.label_zone.setFont(_scaled_font(FAMILY_UI, FONT_ZONE_BASE, s, bold=True))
        self.label_unit.setFont(_scaled_font(FAMILY_UI, FONT_UNIT_BASE, s))
        self.label_status.setFont(_scaled_font(FAMILY_UI, FONT_STATUS_BASE, s))
        # 标题栏和状态栏高度随字体缩放
        self.title_bar.setFixedHeight(max(24, int(28 * s)))
        self.status_bar.setFixedHeight(max(16, int(20 * s)))

    def _choose_bg_color(self):
        color = QColorDialog.getColor(self.bg_color, self, "选择背景颜色")
        if color.isValid():
            self.bg_color = color
            self.update()

    def _toggle_transparent(self):
        self.transparent_bg = not self.transparent_bg
        logger.info("透明背景: %s", "开" if self.transparent_bg else "关")
        if self.transparent_bg:
            self.title_bar.hide()
            self.status_bar.hide()
        else:
            self.title_bar.show()
            self.status_bar.show()
        self._current_zone_key = None
        self.update()

    def _toggle_heart(self):
        self.display["show_heart"] = not self.display["show_heart"]
        self._apply_display_settings()
        config.save_display(self.display)

    def _toggle_zone_label(self):
        self.display["show_zone_label"] = not self.display["show_zone_label"]
        self._apply_display_settings()
        config.save_display(self.display)

    def _toggle_zone_bar(self):
        self.display["show_zone_bar"] = not self.display["show_zone_bar"]
        self._apply_display_settings()
        config.save_display(self.display)

    def _toggle_topmost(self):
        self.display["topmost"] = not self.display["topmost"]
        flags = self.windowFlags()
        if self.display["topmost"]:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()
        config.save_display(self.display)

    def _apply_display_settings(self):
        self.heart_label.setVisible(self.display["show_heart"])
        self.label_zone.setVisible(self.display["show_zone_label"])
        self._zone_bar.setVisible(self.display["show_zone_bar"])

    # ═══════════════════════════════════════════════════
    #  Content alignment
    # ═══════════════════════════════════════════════════

    def _set_alignment(self, align: int):
        self._alignment = align
        self._apply_alignment()
        self.display["alignment"] = int(align)
        config.save_display(self.display)

    def _apply_alignment(self):
        """根据 _alignment 重新设置 hr_row 的 stretch 和子元素对齐。"""
        align = Qt.Alignment(self._alignment)
        layout = self._hr_layout

        # 清除现有的 stretch 项
        while layout.count():
            item = layout.takeAt(0)
            # 不删除 widget，只移除 layout item

        # 重新添加
        if align == Qt.AlignRight:
            layout.addStretch()
            layout.addWidget(self.heart_label)
            layout.addWidget(self.label_hr)

        elif align == Qt.AlignCenter:
            layout.addStretch()
            layout.addWidget(self.heart_label)
            layout.addWidget(self.label_hr)
            layout.addStretch()

        else:  # Qt.AlignLeft
            layout.addWidget(self.heart_label)
            layout.addWidget(self.label_hr)
            layout.addStretch()

        # zone / bar / unit 在 QVBoxLayout 中的对齐
        cl = self._content_layout
        cl.setAlignment(self.label_zone, align)
        cl.setAlignment(self.label_unit, align)

    def _reload_zones(self):
        self.zones = config.load_zones()
        self.display = config.load_display()
        self._font_scale = float(self.display.get("font_scale", 1.0))
        self._alignment = int(self.display.get("alignment", Qt.AlignCenter))
        self._apply_font_scale()
        self._apply_alignment()
        self._apply_display_settings()
        self._current_zone_key = None
        self.label_status.setText("区间配置已重载")

    # ═══════════════════════════════════════════════════
    #  Zone styling
    # ═══════════════════════════════════════════════════

    def _update_zone_style(self, hr: int):
        zone = config.get_zone(hr, self.zones)
        key, color, accent = zone["name"], zone["color"], zone["accent"]

        if key != self._current_zone_key:
            self._current_zone_key = key
            self.label_hr.setStyleSheet(f"color: {color}; background: transparent;")
            self.heart_label.setStyleSheet(f"color: {color}; background: transparent;")
            self.label_unit.setStyleSheet(f"color: {accent}; background: transparent;")
            if self.display["show_zone_label"]:
                self.label_zone.setText(key)
                self.label_zone.setStyleSheet(f"color: {accent}; background: transparent;")

        if self.display["show_zone_bar"]:
            lower = 30
            for z in self.zones:
                if z["name"] == key:
                    break
                lower = z["max"] + 1
            upper = zone["max"] if zone["max"] != 999 else 200
            fraction = (hr - lower) / max(1, upper - lower)
            self._zone_bar.set_fraction(fraction, QColor(color))

    # ═══════════════════════════════════════════════════
    #  Queue polling
    # ═══════════════════════════════════════════════════

    def _poll_queue(self):
        try:
            while True:
                msg = self.cmd_queue.get_nowait()
                if msg["type"] == "hr":
                    hr = msg["value"]
                    self.label_hr.setText(str(hr))
                    self._update_zone_style(hr)
                elif msg["type"] == "status":
                    self.label_status.setText(msg["text"][:30])
                elif msg["type"] == "quit":
                    self._timer.stop()
                    QApplication.quit()
                    return
        except queue.Empty:
            pass
        except Exception:
            logger.exception("_poll_queue 异常")

    # ═══════════════════════════════════════════════════
    #  Close
    # ═══════════════════════════════════════════════════

    def _close(self):
        logger.info("用户请求退出")
        self.cmd_queue.put({"type": "quit"})
        self._timer.stop()
        QApplication.quit()

    def closeEvent(self, event):
        self._close()
        event.accept()
