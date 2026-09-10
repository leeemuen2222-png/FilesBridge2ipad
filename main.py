
import sys
import time
import ctypes
import asyncio
import threading
from pathlib import Path
from ctypes import wintypes

from PySide6.QtCore import Qt, QPoint, QTimer, Signal, QObject
from PySide6.QtGui import QPainter, QColor, QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QPushButton,
    QVBoxLayout,
    QMessageBox,
    QFileDialog,
)

try:
    from bleak import BleakScanner, BleakClient
    BLE_AVAILABLE = True
except Exception:
    BLE_AVAILABLE = False


APP_NAME = "FilesBridge2ipad"

# -----------------------------------------------------------------------------
# Win32 helpers
# -----------------------------------------------------------------------------

user32 = ctypes.windll.user32

GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.restype = wintypes.HWND

SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.argtypes = [wintypes.HWND]
SetForegroundWindow.restype = wintypes.BOOL

keybd_event = user32.keybd_event

VK_CONTROL = 0x11
VK_P = 0x50
KEYEVENTF_KEYUP = 0x0002


def press_ctrl_p():
    keybd_event(VK_CONTROL, 0, 0, 0)
    keybd_event(VK_P, 0, 0, 0)
    keybd_event(VK_P, 0, KEYEVENTF_KEYUP, 0)
    keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


# -----------------------------------------------------------------------------
# BLE protocol reserved for future iPad receiver
# -----------------------------------------------------------------------------

FILES_BRIDGE_SERVICE_UUID = "7f7d0001-5a10-4c91-9b1a-6ab31d8a1001"
CONTROL_CHAR_UUID = "7f7d0002-5a10-4c91-9b1a-6ab31d8a1001"
DATA_CHAR_UUID = "7f7d0003-5a10-4c91-9b1a-6ab31d8a1001"


class BluetoothSignals(QObject):
    status = Signal(str)
    connected = Signal(str)
    failed = Signal(str)


class BluetoothManager:
    def __init__(self):
        self.signals = BluetoothSignals()
        self.client = None
        self.device = None
        self._thread = None

    @property
    def is_connected(self):
        return bool(self.client and self.client.is_connected)

    def scan_and_connect(self):
        if not BLE_AVAILABLE:
            self.signals.failed.emit(
                "缺少 Bluetooth 组件 bleak。\n请重新运行 run.bat 安装依赖。"
            )
            return

        if self._thread and self._thread.is_alive():
            return

        self._thread = threading.Thread(
            target=self._thread_entry,
            daemon=True,
        )
        self._thread.start()

    def _thread_entry(self):
        try:
            asyncio.run(self._scan_and_connect_async())
        except Exception as exc:
            self.signals.failed.emit(f"蓝牙连接失败：{exc}")

    async def _scan_and_connect_async(self):
        self.signals.status.emit("搜索 iPad…")

        found = await BleakScanner.discover(timeout=6.0, return_adv=True)

        target_device = None
        for _address, item in found.items():
            device, adv = item
            uuids = [u.lower() for u in (adv.service_uuids or [])]
            if FILES_BRIDGE_SERVICE_UUID.lower() in uuids:
                target_device = device
                break

        if target_device is None:
            self.signals.failed.emit(
                "未找到 FilesBridge2ipad 接收端。\n\n"
                "目前还没有 iPad 端 App，因此这是预期结果。"
            )
            return

        self.signals.status.emit("正在连接…")
        client = BleakClient(target_device)
        await client.connect(timeout=10.0)

        if not client.is_connected:
            self.signals.failed.emit("发现接收端，但连接失败。")
            return

        self.client = client
        self.device = target_device
        self.signals.connected.emit(target_device.name or "iPad")


class FloatingBall(QWidget):
    SIZE = 178

    def __init__(self):
        super().__init__()

        self.last_external_window = None
        self.drag_origin = QPoint()
        self.window_origin = QPoint()
        self.dragging = False

        # The file currently queued for sending.
        self.pending_file: Path | None = None

        self.bluetooth = BluetoothManager()
        self.bluetooth.signals.status.connect(self.on_bt_status)
        self.bluetooth.signals.connected.connect(self.on_bt_connected)
        self.bluetooth.signals.failed.connect(self.on_bt_failed)

        self.setFixedSize(self.SIZE, self.SIZE)
        self.setWindowTitle(APP_NAME)
        self.setAcceptDrops(True)

        self.setWindowFlags(
            Qt.FramelessWindowHint
            | Qt.WindowStaysOnTopHint
            | Qt.Tool
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        self.capture_btn = QPushButton("截取页面")
        self.connect_btn = QPushButton("连接 iPad")
        self.send_btn = QPushButton("发送文件")

        for button in (self.capture_btn, self.connect_btn, self.send_btn):
            button.setCursor(Qt.PointingHandCursor)
            button.setFixedHeight(35)
            button.setStyleSheet("""
                QPushButton {
                    background: rgba(255,255,255,230);
                    color: #171717;
                    border: 0px;
                    border-radius: 15px;
                    font-size: 14px;
                    font-weight: 600;
                    padding: 0 12px;
                }
                QPushButton:hover {
                    background: rgba(255,255,255,250);
                }
                QPushButton:pressed {
                    background: rgba(220,220,220,250);
                }
            """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(26, 23, 26, 23)
        layout.setSpacing(7)
        layout.addStretch(1)
        layout.addWidget(self.capture_btn)
        layout.addWidget(self.connect_btn)
        layout.addWidget(self.send_btn)
        layout.addStretch(1)

        self.capture_btn.clicked.connect(self.capture_page)
        self.connect_btn.clicked.connect(self.connect_ipad)
        self.send_btn.clicked.connect(self.choose_or_send_file)

        self.foreground_timer = QTimer(self)
        self.foreground_timer.timeout.connect(self.remember_foreground_window)
        self.foreground_timer.start(150)

    # -------------------------------------------------------------------------
    # Appearance
    # -------------------------------------------------------------------------

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        painter.setBrush(QColor(0, 0, 0, 48))
        painter.drawEllipse(6, 8, self.SIZE - 12, self.SIZE - 12)

        painter.setBrush(QColor(30, 32, 36, 239))
        painter.drawEllipse(2, 2, self.SIZE - 9, self.SIZE - 9)

        painter.setBrush(QColor(255, 255, 255, 11))
        painter.drawEllipse(16, 10, self.SIZE - 41, self.SIZE - 50)

    # -------------------------------------------------------------------------
    # Foreground window tracking / browser print
    # -------------------------------------------------------------------------

    def remember_foreground_window(self):
        hwnd = GetForegroundWindow()
        if hwnd and int(hwnd) != int(self.winId()):
            self.last_external_window = hwnd

    def capture_page(self):
        target = self.last_external_window
        if not target:
            QMessageBox.warning(
                self,
                APP_NAME,
                "没有找到刚才使用的窗口。\n请先点击浏览器页面，再点击“截取页面”。",
            )
            return

        self.hide()
        QApplication.processEvents()

        SetForegroundWindow(target)
        time.sleep(0.18)
        press_ctrl_p()
        time.sleep(0.35)

        self.show()
        self.raise_()

    # -------------------------------------------------------------------------
    # File selection / drag and drop
    # -------------------------------------------------------------------------

    def choose_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择要发送到 iPad 的文件",
            "",
            "所有文件 (*.*)",
        )

        if not file_path:
            return False

        self.set_pending_file(Path(file_path))
        return True

    def set_pending_file(self, file_path: Path):
        if not file_path.exists() or not file_path.is_file():
            QMessageBox.warning(self, APP_NAME, "选择的文件不存在。")
            return

        self.pending_file = file_path
        self.send_btn.setText("发送文件")
        self.send_btn.setToolTip(str(file_path))

    def dragEnterEvent(self, event: QDragEnterEvent):
        urls = event.mimeData().urls()
        if any(url.isLocalFile() for url in urls):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        local_files = [
            Path(url.toLocalFile())
            for url in event.mimeData().urls()
            if url.isLocalFile()
        ]

        local_files = [p for p in local_files if p.is_file()]

        if not local_files:
            return

        # v0.3 queues the first dropped file.
        self.set_pending_file(local_files[0])

        QMessageBox.information(
            self,
            APP_NAME,
            f"已选择：\n{local_files[0].name}\n\n点击“发送文件”即可发送。",
        )
        event.acceptProposedAction()

    # -------------------------------------------------------------------------
    # Bluetooth
    # -------------------------------------------------------------------------

    def connect_ipad(self):
        if self.bluetooth.is_connected:
            QMessageBox.information(self, APP_NAME, "iPad 已经连接。")
            return

        self.connect_btn.setText("搜索中…")
        self.connect_btn.setEnabled(False)
        self.bluetooth.scan_and_connect()

    def on_bt_status(self, text):
        self.connect_btn.setText(text)

    def on_bt_connected(self, device_name):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("iPad 已连接")
        self.connect_btn.setToolTip(device_name)

    def on_bt_failed(self, message):
        self.connect_btn.setEnabled(True)
        self.connect_btn.setText("连接 iPad")
        QMessageBox.information(self, "连接 iPad", message)

    # -------------------------------------------------------------------------
    # Send workflow
    # -------------------------------------------------------------------------

    def choose_or_send_file(self):
        # If no file is queued, pressing Send directly opens the file picker.
        if self.pending_file is None:
            if not self.choose_file():
                return

        if not self.bluetooth.is_connected:
            QMessageBox.information(
                self,
                "发送文件",
                f"已选择文件：\n{self.pending_file.name}\n\n"
                "尚未连接 iPad。\n"
                "请先点击“连接 iPad”。\n\n"
                "目前 iPad 接收端尚未制作，所以暂时不能完成实际传输。",
            )
            return

        # Future implementation point:
        # 1. Send file metadata over CONTROL_CHAR_UUID
        # 2. Chunk bytes over DATA_CHAR_UUID
        # 3. iPad app reconstructs file
        # 4. iPad invokes system Share Sheet
        QMessageBox.information(
            self,
            "发送文件",
            f"准备发送：\n{self.pending_file.name}\n\n"
            "蓝牙通道已经建立。\n"
            "实际文件字节传输将在 iPad 接收端完成后启用。",
        )

    # -------------------------------------------------------------------------
    # Drag orb itself
    # -------------------------------------------------------------------------

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            child = self.childAt(event.position().toPoint())
            if not isinstance(child, QPushButton):
                self.dragging = True
                self.drag_origin = event.globalPosition().toPoint()
                self.window_origin = self.pos()
                event.accept()

    def mouseMoveEvent(self, event):
        if self.dragging and (event.buttons() & Qt.LeftButton):
            delta = event.globalPosition().toPoint() - self.drag_origin
            self.move(self.window_origin + delta)
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.dragging = False
            event.accept()


def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)

    ball = FloatingBall()

    screen = app.primaryScreen().availableGeometry()
    x = screen.right() - FloatingBall.SIZE - 35
    y = screen.center().y() - FloatingBall.SIZE // 2
    ball.move(x, y)

    ball.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
