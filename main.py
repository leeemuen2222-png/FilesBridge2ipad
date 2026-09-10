
import sys
import time
import ctypes
import threading
import asyncio
import subprocess
import tempfile
import shutil
import os
from pathlib import Path
from datetime import datetime
from tkinter import Tk, Canvas, Button, filedialog, messagebox

APP_NAME = "FilesBridge2ipad"

# -----------------------------------------------------------------------------
# Optional BLE support
# -----------------------------------------------------------------------------

try:
    from bleak import BleakScanner, BleakClient
    BLE_AVAILABLE = True
    BLE_IMPORT_ERROR = ""
except Exception as exc:
    BLE_AVAILABLE = False
    BLE_IMPORT_ERROR = str(exc)

FILES_BRIDGE_SERVICE_UUID = "7f7d0001-5a10-4c91-9b1a-6ab31d8a1001"


# -----------------------------------------------------------------------------
# Win32 helpers
# -----------------------------------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

GetForegroundWindow = user32.GetForegroundWindow
SetForegroundWindow = user32.SetForegroundWindow

VK_CONTROL = 0x11
VK_L = 0x4C
VK_C = 0x43
VK_ESCAPE = 0x1B
KEYEVENTF_KEYUP = 0x0002


def key_down(vk):
    user32.keybd_event(vk, 0, 0, 0)


def key_up(vk):
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP, 0)


def press_combo(modifier, key):
    key_down(modifier)
    key_down(key)
    key_up(key)
    key_up(modifier)


def press_key(key):
    key_down(key)
    key_up(key)


def get_clipboard_text(root):
    try:
        return root.clipboard_get()
    except Exception:
        return ""


# -----------------------------------------------------------------------------
# Browser detection
# -----------------------------------------------------------------------------

def find_browser_executable():
    candidates = []

    local = os.environ.get("LOCALAPPDATA", "")
    program_files = os.environ.get("PROGRAMFILES", "")
    program_files_x86 = os.environ.get("PROGRAMFILES(X86)", "")

    if local:
        candidates += [
            Path(local) / "Google/Chrome/Application/chrome.exe",
            Path(local) / "Microsoft/Edge/Application/msedge.exe",
        ]

    if program_files:
        candidates += [
            Path(program_files) / "Google/Chrome/Application/chrome.exe",
            Path(program_files) / "Microsoft/Edge/Application/msedge.exe",
        ]

    if program_files_x86:
        candidates += [
            Path(program_files_x86) / "Google/Chrome/Application/chrome.exe",
            Path(program_files_x86) / "Microsoft/Edge/Application/msedge.exe",
        ]

    for name in ("chrome.exe", "msedge.exe"):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))

    for candidate in candidates:
        if candidate.exists():
            return candidate

    return None


# -----------------------------------------------------------------------------
# Main floating orb
# -----------------------------------------------------------------------------

class FloatingBall:
    SIZE = 180
    BG_KEY = "#010203"

    def __init__(self):
        self.root = Tk()
        self.root.title(APP_NAME)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.root.configure(bg=self.BG_KEY)
        try:
            self.root.wm_attributes("-transparentcolor", self.BG_KEY)
        except Exception:
            pass

        self.root.geometry(f"{self.SIZE}x{self.SIZE}+1200+350")

        self.canvas = Canvas(
            self.root,
            width=self.SIZE,
            height=self.SIZE,
            bg=self.BG_KEY,
            highlightthickness=0,
            bd=0,
        )
        self.canvas.place(x=0, y=0)

        self.canvas.create_oval(
            5, 7,
            self.SIZE - 5, self.SIZE - 3,
            fill="#1f2125",
            outline=""
        )
        self.canvas.create_oval(
            17, 12,
            self.SIZE - 28, self.SIZE - 45,
            fill="#292c31",
            outline=""
        )

        self.capture_btn = self.make_button(
            "截取页面",
            self.capture_page,
            y=34
        )

        self.connect_btn = self.make_button(
            "连接 iPad",
            self.connect_ipad,
            y=75
        )

        self.send_btn = self.make_button(
            "发送文件",
            self.choose_or_send_file,
            y=116
        )

        self.pending_file = None
        self.last_external_window = None
        self.ble_client = None
        self.ble_device = None
        self.scanning = False
        self.capture_running = False

        self.drag_start_x = 0
        self.drag_start_y = 0
        self.window_start_x = 0
        self.window_start_y = 0

        self.canvas.bind("<ButtonPress-1>", self.start_drag)
        self.canvas.bind("<B1-Motion>", self.do_drag)
        self.canvas.bind("<Button-3>", self.exit_app)

        self.root.after(200, self.track_foreground_window)

    def make_button(self, text, command, y):
        btn = Button(
            self.root,
            text=text,
            command=command,
            relief="flat",
            bd=0,
            bg="#f3f3f3",
            fg="#111111",
            activebackground="#dddddd",
            activeforeground="#111111",
            font=("Microsoft YaHei UI", 10, "bold"),
            cursor="hand2",
        )
        btn.place(x=31, y=y, width=118, height=33)
        return btn

    # -------------------------------------------------------------------------
    # Dragging
    # -------------------------------------------------------------------------

    def start_drag(self, event):
        self.drag_start_x = event.x_root
        self.drag_start_y = event.y_root
        self.window_start_x = self.root.winfo_x()
        self.window_start_y = self.root.winfo_y()

    def do_drag(self, event):
        dx = event.x_root - self.drag_start_x
        dy = event.y_root - self.drag_start_y
        self.root.geometry(
            f"+{self.window_start_x + dx}+{self.window_start_y + dy}"
        )

    def exit_app(self, event=None):
        if messagebox.askyesno(APP_NAME, "退出 FilesBridge2ipad？"):
            self.root.destroy()

    # -------------------------------------------------------------------------
    # Foreground tracking
    # -------------------------------------------------------------------------

    def track_foreground_window(self):
        try:
            hwnd = GetForegroundWindow()
            if self.root.focus_displayof() is None and hwnd:
                self.last_external_window = hwnd
        except Exception:
            pass

        self.root.after(200, self.track_foreground_window)

    # -------------------------------------------------------------------------
    # Automatic page -> PDF
    # -------------------------------------------------------------------------

    def capture_page(self):
        if self.capture_running:
            return

        if not self.last_external_window:
            messagebox.showwarning(
                APP_NAME,
                "没有找到刚才使用的浏览器窗口。"
            )
            return

        browser = find_browser_executable()

        if browser is None:
            messagebox.showerror(
                APP_NAME,
                "没有找到 Chrome 或 Microsoft Edge。\n"
                "当前版本需要其中一个 Chromium 浏览器来自动生成 PDF。"
            )
            return

        self.capture_running = True
        self.capture_btn.config(text="生成中…", state="disabled")

        threading.Thread(
            target=self._capture_worker,
            args=(browser,),
            daemon=True
        ).start()

    def _capture_worker(self, browser):
        try:
            target = self.last_external_window

            # Temporarily hide the orb, restore browser focus, copy URL.
            self.root.after(0, self.root.withdraw)
            time.sleep(0.15)

            SetForegroundWindow(target)
            time.sleep(0.20)

            # Ctrl+L -> Ctrl+C -> Esc
            press_combo(VK_CONTROL, VK_L)
            time.sleep(0.08)
            press_combo(VK_CONTROL, VK_C)
            time.sleep(0.12)
            press_key(VK_ESCAPE)
            time.sleep(0.08)

            # Reading Tk clipboard must happen on Tk's thread.
            url_box = {"value": ""}
            done = threading.Event()

            def read_clipboard():
                try:
                    url_box["value"] = get_clipboard_text(self.root).strip()
                finally:
                    done.set()

            self.root.after(0, read_clipboard)
            done.wait(timeout=2.0)

            url = url_box["value"]

            if not (
                url.startswith("http://")
                or url.startswith("https://")
                or url.startswith("file://")
            ):
                raise RuntimeError(
                    "无法读取当前浏览器页面的网址。\n"
                    "请确保刚才正在使用 Chrome 或 Edge 的网页标签页。"
                )

            temp_dir = Path(tempfile.gettempdir()) / APP_NAME
            temp_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            pdf_path = temp_dir / f"WebPage_{timestamp}.pdf"

            # Fully automatic PDF generation.
            command = [
                str(browser),
                "--headless=new",
                "--disable-gpu",
                "--no-first-run",
                "--no-default-browser-check",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=5000",
                "--print-to-pdf-no-header",
                f"--print-to-pdf={pdf_path}",
                url,
            ]

            creationflags = 0
            if os.name == "nt":
                creationflags = subprocess.CREATE_NO_WINDOW

            result = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=45,
                creationflags=creationflags,
            )

            if not pdf_path.exists() or pdf_path.stat().st_size == 0:
                err = (result.stderr or result.stdout or "").strip()
                if len(err) > 600:
                    err = err[-600:]

                raise RuntimeError(
                    "浏览器没有成功生成 PDF。"
                    + (("\n\n" + err) if err else "")
                )

            self.pending_file = pdf_path

            self.root.after(
                0,
                lambda: self._capture_success(pdf_path)
            )

        except Exception as exc:
            msg = str(exc)
            self.root.after(
                0,
                lambda: self._capture_failed(msg)
            )

    def _capture_success(self, pdf_path):
        self.capture_running = False
        self.capture_btn.config(text="截取页面", state="normal")

        self.root.deiconify()
        self.root.attributes("-topmost", True)

        answer = messagebox.askyesno(
            "页面已生成",
            "PDF 已自动创建：\n\n{}\n\n"
            "是否现在发送到 iPad？".format(pdf_path.name)
        )

        if answer:
            self.send_pending_file()
        else:
            messagebox.showinfo(
                APP_NAME,
                "PDF 已保留为当前待发送文件。\n\n"
                "之后点击“发送文件”即可发送。"
            )

    def _capture_failed(self, message):
        self.capture_running = False
        self.capture_btn.config(text="截取页面", state="normal")

        self.root.deiconify()
        self.root.attributes("-topmost", True)

        messagebox.showerror(
            "页面生成失败",
            message
        )

    # -------------------------------------------------------------------------
    # File selection
    # -------------------------------------------------------------------------

    def choose_file(self):
        path = filedialog.askopenfilename(
            title="选择要发送到 iPad 的文件",
            filetypes=[("所有文件", "*.*")]
        )

        if not path:
            return False

        self.pending_file = Path(path)
        return True

    # -------------------------------------------------------------------------
    # BLE
    # -------------------------------------------------------------------------

    def connect_ipad(self):
        if self.scanning:
            return

        if self.ble_client is not None:
            try:
                if self.ble_client.is_connected:
                    messagebox.showinfo(APP_NAME, "iPad 已连接。")
                    return
            except Exception:
                pass

        if not BLE_AVAILABLE:
            messagebox.showinfo(
                "连接 iPad",
                "当前未安装蓝牙组件 bleak。\n\n"
                "以后启用 iPad 接收端时运行：\n"
                "python -m pip install bleak"
            )
            return

        self.scanning = True
        self.connect_btn.config(text="搜索中…", state="disabled")

        threading.Thread(
            target=self._ble_thread,
            daemon=True
        ).start()

    def _ble_thread(self):
        try:
            asyncio.run(self._scan_ble())
        except Exception as exc:
            self.root.after(
                0,
                lambda: self._ble_failed("蓝牙连接失败：{}".format(exc))
            )

    async def _scan_ble(self):
        found = await BleakScanner.discover(
            timeout=6.0,
            return_adv=True
        )

        target_device = None

        for _address, item in found.items():
            device, adv = item
            uuids = [u.lower() for u in (adv.service_uuids or [])]

            if FILES_BRIDGE_SERVICE_UUID.lower() in uuids:
                target_device = device
                break

        if target_device is None:
            self.root.after(
                0,
                lambda: self._ble_failed(
                    "未找到 FilesBridge2ipad iPad 接收端。\n\n"
                    "目前尚未制作 iPad 端，因此这是正常结果。"
                )
            )
            return

        client = BleakClient(target_device)
        await client.connect(timeout=10.0)

        if not client.is_connected:
            self.root.after(
                0,
                lambda: self._ble_failed("发现接收端，但连接失败。")
            )
            return

        self.ble_client = client
        self.ble_device = target_device

        self.root.after(
            0,
            lambda: self._ble_connected(target_device.name or "iPad")
        )

    def _ble_connected(self, device_name):
        self.scanning = False
        self.connect_btn.config(
            text="iPad 已连接",
            state="normal"
        )
        messagebox.showinfo(
            APP_NAME,
            "已连接：{}".format(device_name)
        )

    def _ble_failed(self, message):
        self.scanning = False
        self.connect_btn.config(
            text="连接 iPad",
            state="normal"
        )
        messagebox.showinfo(
            "连接 iPad",
            message
        )

    # -------------------------------------------------------------------------
    # Send
    # -------------------------------------------------------------------------

    def choose_or_send_file(self):
        if self.pending_file is None:
            if not self.choose_file():
                return

        self.send_pending_file()

    def send_pending_file(self):
        if self.pending_file is None:
            return

        connected = False

        if self.ble_client is not None:
            try:
                connected = bool(self.ble_client.is_connected)
            except Exception:
                connected = False

        if not connected:
            messagebox.showinfo(
                "发送文件",
                "待发送文件：\n{}\n\n"
                "尚未连接 iPad。\n"
                "请先点击“连接 iPad”。".format(
                    self.pending_file.name
                )
            )
            return

        messagebox.showinfo(
            "发送文件",
            "准备发送：\n{}\n\n"
            "实际文件字节传输将在 iPad 接收端完成后启用。".format(
                self.pending_file.name
            )
        )

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    app = FloatingBall()
    app.run()
