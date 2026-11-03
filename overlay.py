#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
鼠标旁边的动效指示器。

用一个 WS_EX_LAYERED + WS_EX_TRANSPARENT + WS_EX_NOACTIVATE 的分层窗口，
在光标附近画：录音时的脉冲光环 + 音量条、转写时的转圈、成功/失败的徽标。

要点：
  * 逐像素 alpha（PIL 画好再 UpdateLayeredWindow 上传），所以边缘没有毛边
  * 永远不抢焦点——否则 Ctrl+V 就粘到我们自己身上了
  * 鼠标穿透，不会挡住下面的点击
"""

import ctypes
import ctypes.wintypes as wt
import math
import threading
import time

import numpy as np
from PIL import Image, ImageDraw

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32

WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOPMOST = 0x00000008
WS_EX_ALPHA_LAYERED = WS_EX_LAYERED

WS_POPUP = 0x80000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01

SW_HIDE = 0
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
HWND_TOPMOST = -1

DIB_RGB_COLORS = 0

BLUE = (32, 120, 255)
GREEN = (46, 190, 110)
RED = (235, 78, 78)
INK = (20, 20, 24)


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", wt.BYTE), ("BlendFlags", wt.BYTE),
                ("SourceConstantAlpha", wt.BYTE), ("AlphaFormat", wt.BYTE)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD), ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD), ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG), ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def _arc(d, box, start, end, color, width):
    d.arc(box, start=start, end=end, fill=color, width=width)


def enable_dpi_awareness():
    """
    必须比任何窗口都早调用。
    不设的话进程按 96DPI 虚拟化：这块 2560x1440 的屏会报告成 2048x1152，
    画出来的东西被系统再放大 1.25 倍，既发虚又和光标坐标对不上。
    """
    try:
        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return True
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # PER_MONITOR_DPI_AWARE
        return True
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return True
    except Exception:
        return False


def dpi_scale():
    try:
        dpi = user32.GetDpiForSystem()
    except Exception:
        dpi = 96
    return max(1.0, dpi / 96.0)


enable_dpi_awareness()


class Overlay:
    """动效层的门面。所有方法都可以从任意线程调用。"""

    BASE = 200              # 96DPI 下的画布边长，光标居中

    def __init__(self, enabled=True, tick=0.033):
        self.enabled = enabled
        self.tick = tick
        self.scale = dpi_scale()
        self.CANVAS = int(round(self.BASE * self.scale))
        self._lock = threading.Lock()
        self._state = "idle"          # idle | recording | transcribing | done | error
        self._state_at = 0.0
        self._level_fn = None
        self._level = 0.0
        self._thread = None
        self._hwnd = None
        self._stop = threading.Event()

    # ---------------- 对外接口

    def start(self):
        if not self.enabled or self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, daemon=True, name="vp-overlay")
        self._thread.start()

    def stop(self):
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=2.0)
        self._thread = None

    def begin_recording(self, level_fn=None):
        with self._lock:
            self._level_fn = level_fn
            self._state = "recording"
            self._state_at = time.monotonic()

    def set_state(self, state):
        with self._lock:
            self._state = state
            self._state_at = time.monotonic()

    def flash(self, state="done", hold=0.75):
        """一闪而过：done / error，之后自动隐藏。"""
        with self._lock:
            self._state = state
            self._state_at = time.monotonic()
            self._flash_hold = hold

    def hide(self):
        with self._lock:
            self._state = "idle"
            self._level_fn = None

    # ---------------- 内部

    def _cursor_pos(self):
        pt = wt.POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)

    def _run(self):
        try:
            self._create_window()
        except Exception as e:                     # 动效失败不影响主流程
            print("overlay 创建失败:", e)
            return

        hbmp, memdc, bits, screen_dc = self._make_surface()

        while not self._stop.is_set():
            t0 = time.monotonic()
            with self._lock:
                state = self._state
                since = t0 - self._state_at
                level_fn = self._level_fn
                flash_hold = getattr(self, "_flash_hold", 0.75)

            if state == "recording" and level_fn is not None:
                try:
                    raw = level_fn() or 0.0
                except Exception:
                    raw = 0.0
                self._level = self._level * 0.65 + min(1.0, raw) * 0.35

            img = None
            if state == "idle":
                if self._hwnd:
                    user32.ShowWindow(self._hwnd, SW_HIDE)
            elif state == "done" and since > flash_hold:
                with self._lock:
                    self._state = "idle"
                user32.ShowWindow(self._hwnd, SW_HIDE)
            elif state == "error" and since > flash_hold:
                with self._lock:
                    self._state = "idle"
                user32.ShowWindow(self._hwnd, SW_HIDE)
            else:
                img = self._render(state, since)

            if img is not None:
                x, y = self._cursor_pos()
                self._upload(img, x - self.CANVAS // 2, y - self.CANVAS // 2,
                             hbmp, memdc, bits, screen_dc)

            self._pump()
            dt = time.monotonic() - t0
            time.sleep(max(0.0, self.tick - dt))

        try:
            if self._hwnd:
                user32.ShowWindow(self._hwnd, SW_HIDE)
                user32.DestroyWindow(self._hwnd)
        except Exception:
            pass
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(memdc)
        user32.ReleaseDC(None, screen_dc)

    def _create_window(self):
        ex = (WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW |
              WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        self._hwnd = user32.CreateWindowExW(
            ex, "STATIC", "VoicePressOverlay", WS_POPUP,
            -10000, -10000, self.CANVAS, self.CANVAS, None, None, None, None)
        if not self._hwnd:
            raise RuntimeError("CreateWindowExW 失败")
        user32.SetWindowPos(self._hwnd, HWND_TOPMOST, -10000, -10000,
                            self.CANVAS, self.CANVAS, SWP_NOACTIVATE)

    def _make_surface(self):
        screen_dc = user32.GetDC(None)
        memdc = gdi32.CreateCompatibleDC(screen_dc)
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = self.CANVAS
        bmi.bmiHeader.biHeight = -self.CANVAS            # 自上而下
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        bits = ctypes.c_void_p()
        hbmp = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                      ctypes.byref(bits), None, 0)
        if not hbmp:
            raise RuntimeError("CreateDIBSection 失败")
        gdi32.SelectObject(memdc, hbmp)
        return hbmp, memdc, bits, screen_dc

    def _upload(self, img, x, y, hbmp, memdc, bits, screen_dc):
        rgba = np.asarray(img.convert("RGBA"), dtype=np.uint8)
        alpha = rgba[:, :, 3].astype(np.uint16)
        rgb = (rgba[:, :, :3].astype(np.uint16) * alpha[:, :, None] // 255).astype(np.uint8)
        bgra = np.dstack([rgb[:, :, 2], rgb[:, :, 1], rgb[:, :, 0],
                          rgba[:, :, 3]]).tobytes()
        ctypes.memmove(bits, bgra, len(bgra))

        pt_dst = wt.POINT(int(x), int(y))
        pt_src = wt.POINT(0, 0)
        size = wt.SIZE(self.CANVAS, self.CANVAS)
        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        user32.UpdateLayeredWindow(self._hwnd, screen_dc, ctypes.byref(pt_dst),
                                   ctypes.byref(size), memdc, ctypes.byref(pt_src),
                                   0, ctypes.byref(blend), ULW_ALPHA)
        if not user32.IsWindowVisible(self._hwnd):
            user32.ShowWindow(self._hwnd, 4)             # SW_SHOWNOACTIVATE

    def _pump(self):
        msg = wt.MSG()
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # ---------------- 绘制

    def _render(self, state, since):
        c = self.BASE / 2.0
        img = Image.new("RGBA", (self.BASE, self.BASE), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)

        if state == "recording":
            # 三圈错开的脉冲光环
            for i in range(3):
                p = (since * 0.9 + i / 3.0) % 1.0
                r = 20 + 30 * p
                a = int(120 * (1 - p) ** 1.4)
                if a > 2:
                    d.ellipse([c - r, c - r, c + r, c + r],
                              outline=BLUE + (a,), width=3)
            self._draw_bars(d, c, since)

        elif state == "transcribing":
            self._draw_badge(d, c + 34, c + 26, since, kind="spinner")

        elif state == "done":
            self._draw_badge(d, c + 34, c + 26, since, kind="done")

        elif state == "error":
            self._draw_badge(d, c + 34, c + 26, since, kind="error")

        if self.CANVAS != self.BASE:
            img = img.resize((self.CANVAS, self.CANVAS), Image.LANCZOS)
        return img

    def _draw_bars(self, d, c, since):
        """光标右侧的小音量条（跟着实际音量跳动）。"""
        level = max(0.06, min(1.0, self._level))
        bx0, bx1 = c + 24, c + 60
        by0, by1 = c - 19, c + 19
        d.rounded_rectangle([bx0, by0, bx1, by1], radius=11,
                            fill=INK + (170,), outline=(255, 255, 255, 22), width=1)
        n = 4
        bw, gap = 4, 4
        total = n * bw + (n - 1) * gap
        x = bx0 + (bx1 - bx0 - total) / 2
        for i in range(n):
            phase = math.sin(since * 7.0 + i * 1.1) * 0.10
            hh = 6 + (level + phase) * (by1 - by0 - 12) * 0.66
            hh = max(5, min(by1 - by0 - 10, hh))
            mid = (by0 + by1) / 2
            col = BLUE + (255,) if i % 2 == 0 else (130, 180, 255, 255)
            d.rounded_rectangle([x, mid - hh / 2, x + bw, mid + hh / 2],
                                radius=bw / 2, fill=col)
            x += bw + gap

    def _draw_badge(self, d, cx, cy, since, kind):
        # 弹出动画：前 140ms 从小放大
        k = min(1.0, since / 0.14)
        ease = 1 - (1 - k) ** 3
        r = 9 + 7 * ease
        color = {"spinner": BLUE, "done": GREEN, "error": RED}[kind]
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=INK + (225,))
        d.ellipse([cx - r, cy - r, cx + r, cy + r],
                  outline=color + (215,), width=2)

        if kind == "spinner":
            a0 = (since * 420) % 360
            _arc(d, [cx - 7, cy - 7, cx + 7, cy + 7], a0, a0 + 110,
                 (255, 255, 255, 240), 3)
        elif kind == "done":
            d.line([cx - 6, cy, cx - 2, cy + 4.5], fill=(255, 255, 255, 255), width=3)
            d.line([cx - 2, cy + 4.5, cx + 6, cy - 4.5], fill=(255, 255, 255, 255), width=3)
        else:
            d.line([cx - 5, cy - 5, cx + 5, cy + 5], fill=(255, 255, 255, 255), width=3)
            d.line([cx + 5, cy - 5, cx - 5, cy + 5], fill=(255, 255, 255, 255), width=3)
