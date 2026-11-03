#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
长按录音时把系统光标换成麦克风图标。

Windows 只能整张替换系统光标表（SetSystemCursor），而且按 ID 替换，
所以这里把最常用的几种（箭头、I 型、手型、十字）一起换掉——
不然你正指着输入框（I 型光标）时长按，是看不到麦克风的。

退出 / 异常时按注册表里记录的方案还原，程序启动时也会先还原一次，
避免上次被强杀后光标一直停在麦克风上。
"""

import ctypes
import ctypes.wintypes as wt
import os

user32 = ctypes.windll.user32
user32.LoadImageW.restype = ctypes.c_void_p
user32.LoadImageW.argtypes = [wt.HINSTANCE, wt.LPCWSTR, wt.UINT, ctypes.c_int,
                              ctypes.c_int, wt.UINT]

IMAGE_CURSOR = 2
LR_LOADFROMFILE = 0x00000010

OCR_NORMAL = 32512
OCR_IBEAM = 32513
OCR_CROSS = 32515
OCR_HAND = 32649

# 注册表里 "Control Panel\Cursors" 的键名 -> 系统光标 ID
REGISTRY_MAP = {
    "Arrow": OCR_NORMAL,
    "IBeam": OCR_IBEAM,
    "Crosshair": OCR_CROSS,
    "Hand": OCR_HAND,
}

# 注册表读不到时的兜底文件
FALLBACK = {
    OCR_NORMAL: r"aero_arrow.cur",
    OCR_IBEAM: r"beam_r.cur",
    OCR_CROSS: r"cross_r.cur",
    OCR_HAND: r"aero_link.cur",
}

CURSOR_DIR = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Cursors")


def _scheme_paths():
    """从当前光标方案里读出每种光标的原始文件（用于还原）。"""
    paths = {}
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as k:
            for name, cid in REGISTRY_MAP.items():
                try:
                    value = winreg.QueryValueEx(k, name)[0]
                except OSError:
                    continue
                value = os.path.expandvars(str(value))
                if os.path.isfile(value):
                    paths[cid] = value
    except Exception:
        pass
    for cid, fname in FALLBACK.items():
        paths.setdefault(cid, os.path.join(CURSOR_DIR, fname))
    return paths


def restore_system_cursors():
    """
    让 Windows 重载整套光标方案。这比逐个 SetSystemCursor 回文件可靠得多，
    也是光标卡住时的急救办法（命令行 --restore-cursor 走的就是这里）。
    """
    try:
        if user32.SystemParametersInfoW(0x0057, 0, None, 0x02):   # SPI_SETCURSORS | SPIF_SENDCHANGE
            return True
    except Exception:
        pass
    for cid, path in _scheme_paths().items():
        h = CursorSwitcher._load(path)
        if h:
            user32.SetSystemCursor(h, cid)
    return False


class CursorSwitcher:
    def __init__(self, mic_path, enabled=True):
        self.mic_path = mic_path
        self.enabled = enabled and os.path.isfile(mic_path)
        self._is_mic = False

    @staticmethod
    def _load(path):
        if not os.path.isfile(path):
            return None
        h = user32.LoadImageW(None, path, IMAGE_CURSOR, 0, 0, LR_LOADFROMFILE)
        return h or None

    def set_mic(self):
        if not self.enabled or self._is_mic:
            return False
        ok = False
        for cid in REGISTRY_MAP.values():
            h = self._load(self.mic_path)
            if not h:
                break
            # SetSystemCursor 会接管这个句柄，所以每次都要重新 Load
            if user32.SetSystemCursor(h, cid):
                ok = True
        self._is_mic = ok
        return ok

    def restore(self):
        """把系统光标还原成当前方案里的原样。"""
        if not self.enabled:
            return
        restore_system_cursors()
        self._is_mic = False
