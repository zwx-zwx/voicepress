#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""开机自启：往 HKCU\\...\\Run 里写/删一条，不改系统其它地方。"""

import os
import sys
import winreg

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "VoicePress"


def _command():
    """启动命令：exe 版直接启动自己，源码版用 pythonw（无控制台窗口）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    script = os.path.join(here, "voicepress.py")
    exe = sys.executable
    pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
    if os.path.isfile(pyw):
        exe = pyw
    return f'"{exe}" "{script}"'


def is_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            value, _ = winreg.QueryValueEx(k, NAME)
            return bool(value)
    except OSError:
        return False
    except Exception:
        return False


def set_enabled(enabled):
    """返回 (成功与否, 说明)。"""
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            if enabled:
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except FileNotFoundError:
                    pass
        return True, "已开启开机自启" if enabled else "已关闭开机自启"
    except Exception as e:
        return False, f"写入注册表失败：{e}"
