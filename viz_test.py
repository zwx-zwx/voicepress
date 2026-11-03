#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
动效层自测：把各个状态画出来并截图，同时验证光标替换有没有生效。

  python viz_test.py            # 生成 viz_rec.png / viz_trans.png / viz_done.png
"""

import ctypes
import ctypes.wintypes as wt
import math
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "libs"))
sys.path.insert(0, HERE)

from PIL import ImageGrab  # noqa: E402

from overlay import Overlay  # noqa: E402
from syscursor import CursorSwitcher  # noqa: E402

user32 = ctypes.windll.user32


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("flags", wt.DWORD), ("hCursor", wt.HANDLE),
                ("ptScreenPos", wt.POINT)]


def cursor_handle():
    ci = CURSORINFO()
    ci.cbSize = ctypes.sizeof(ci)
    user32.GetCursorInfo(ctypes.byref(ci))
    return ci.hCursor


def snap(name, cx, cy, pad=170):
    time.sleep(0.35)
    img = ImageGrab.grab(all_screens=True)
    box = (max(0, cx - pad), max(0, cy - pad), cx + pad, cy + pad)
    img.crop(box).save(os.path.join(HERE, name))
    print("saved", name)


def main():
    x, y = 1000, 560
    user32.SetCursorPos(x, y)
    time.sleep(0.3)

    ov = Overlay(True)
    ov.start()
    time.sleep(0.4)

    state = {"t": 0.0}

    def level():
        state["t"] += 0.06
        return 0.35 + 0.35 * abs(math.sin(state["t"]))

    ov.begin_recording(level)
    time.sleep(0.9)
    snap("viz_rec.png", x, y)

    ov.set_state("transcribing")
    snap("viz_trans.png", x, y)

    ov.flash("done", 5)
    snap("viz_done.png", x, y)

    ov.hide()
    time.sleep(0.3)
    ov.stop()

    # 光标替换验证：换回去前后句柄会变
    cs = CursorSwitcher(os.path.join(HERE, "assets", "mic.cur"), True)
    before = cursor_handle()
    ok = cs.set_mic()
    time.sleep(0.2)
    during = cursor_handle()
    cs.restore()
    time.sleep(0.2)
    after = cursor_handle()
    print(f"cursor swap ok={ok} before={before} during={during} after={after}")
    print("RESULT:", "PASS" if (ok and during != before) else "FAIL")
    return 0


if __name__ == "__main__":
    sys.exit(main())
