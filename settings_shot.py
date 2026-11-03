#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
以独立进程打开设置面板，截一张图检查布局，然后关掉。

这和托盘菜单里的行为一致（都是 `pythonw settings.py`）。
"""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "libs"))
sys.path.insert(0, HERE)

from PIL import ImageGrab  # noqa: E402

user32 = ctypes.windll.user32
TITLE = "VoicePress 设置"


def find_window(title, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        h = user32.FindWindowW(None, title)
        if h:
            return h
        time.sleep(0.2)
    return 0


def main():
    proc = subprocess.Popen([sys.executable, os.path.join(HERE, "settings.py")], cwd=HERE)
    hwnd = find_window(TITLE)
    print("窗口句柄:", hwnd, flush=True)
    if not hwnd:
        proc.kill()
        return 1

    time.sleep(1.2)
    rect = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    print("窗口位置:", rect.left, rect.top, rect.right, rect.bottom, flush=True)

    pad = 8
    # 不要用 GetWindowRect 裁切：跨 DPI 感知模式取到的可能是虚拟化坐标，会截掉窗口下半截。
    # 直接按"窗口左上角 + 足够大的一块"截。
    img = ImageGrab.grab()
    box = (max(0, rect.left - pad), max(0, rect.top - pad),
           min(img.width, rect.left + 640), min(img.height, rect.top + 1000))
    img.crop(box).save(os.path.join(HERE, "assets", "settings_panel.png"))
    print("截图:", box, flush=True)

    user32.PostMessageW(hwnd, 0x0010, 0, 0)          # WM_CLOSE
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
    print("已关闭", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
