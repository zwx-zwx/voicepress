#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
路径集中管理。

源码运行时一切都在项目目录里；被 PyInstaller 打成 exe 后要分两种：

  RESOURCE_DIR  打包进 exe 的只读资源（assets/、可能内置的 models/）
                单文件 exe 解压到临时目录，每次启动可能不同，不能往里写
  DATA_DIR      可写数据：config.json、日志、模型（3GB 级）

DATA_DIR 的选址原则：**别占系统盘**。
  1. 环境变量 VPR_DATA_DIR 指定则优先（便携版/调试用）
  2. 之前选过就沿用（读指针文件），避免换次盘就"模型不见了"
  3. 否则在非系统盘里挑可用空间最大的那个，建 <盘符>:\\VoicePress
  4. 实在没有可用盘，才退回 %LOCALAPPDATA%\\VoicePress
指针文件本身只有一行文本，放在 %LOCALAPPDATA%\\VoicePress\\location.txt。
"""

import ctypes
import os
import sys

FROZEN = getattr(sys, "frozen", False)
HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "VoicePress"
MIN_FREE_GB = 5.0

_RESOURCE_DIR = getattr(sys, "_MEIPASS", HERE) if FROZEN else HERE


def _localappdata():
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    return os.path.join(base, APP_NAME)


def _system_drive():
    return os.path.abspath(os.environ.get("SystemDrive", "C:") + "\\").rstrip("\\")


def _free_gb(path):
    try:
        import shutil
        return shutil.disk_usage(path).free / (1024 ** 3)
    except Exception:
        try:
            free = ctypes.c_ulonglong(0)
            total = ctypes.c_ulonglong(0)
            avail = ctypes.c_ulonglong(0)
            if ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                    ctypes.c_wchar_p(path), ctypes.byref(avail),
                    ctypes.byref(total), ctypes.byref(free)):
                return avail.value / (1024 ** 3)
        except Exception:
            pass
        return 0.0


def _is_fixed_drive(root):
    """只认本地固定盘，别把 U 盘/光驱/网络盘当成数据盘。"""
    try:
        DRIVE_FIXED = 3
        return ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)) == DRIVE_FIXED
    except Exception:
        return os.path.isdir(root)


def _candidate_drives():
    sys_drive = _system_drive().upper()
    out = []
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if not os.path.isdir(root) or root.upper().startswith(sys_drive):
            continue
        if not _is_fixed_drive(root):
            continue
        if _free_gb(root) < MIN_FREE_GB:
            continue
        out.append(root)
    # 空闲空间大的排前面
    return sorted(out, key=lambda r: _free_gb(r), reverse=True)


def _pointer_file():
    return os.path.join(_localappdata(), "location.txt")


def _read_pointer():
    try:
        with open(_pointer_file(), "r", encoding="utf-8") as f:
            p = f.read().strip()
        if p and os.path.isdir(p):
            return p
    except Exception:
        pass
    return None


def _write_pointer(path):
    try:
        os.makedirs(_localappdata(), exist_ok=True)
        with open(_pointer_file(), "w", encoding="utf-8") as f:
            f.write(path)
    except Exception:
        pass


def _pick_data_dir():
    override = os.environ.get("VPR_DATA_DIR")
    if override:
        return os.path.abspath(override)

    remembered = _read_pointer()
    if remembered:
        return remembered

    # 已有的本地数据目录（老版本装在 LOCALAPPDATA）优先，别让用户"丢模型"
    legacy = _localappdata()
    if os.path.isdir(os.path.join(legacy, "models")) or \
            os.path.isfile(os.path.join(legacy, "config.json")):
        return legacy

    for root in _candidate_drives():
        target = os.path.join(root, APP_NAME)
        try:
            os.makedirs(target, exist_ok=True)
            _write_pointer(target)
            return target
        except Exception:
            continue
    return legacy


if FROZEN:
    RESOURCE_DIR = _RESOURCE_DIR
    DATA_DIR = _pick_data_dir()
else:
    RESOURCE_DIR = HERE
    # 源码模式默认跟着项目目录，但允许 VPR_DATA_DIR 覆盖（调试/测试用）
    _src_override = os.environ.get("VPR_DATA_DIR")
    DATA_DIR = os.path.abspath(_src_override) if _src_override else HERE

CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "voicepress.log")
ASSETS_DIR = os.path.join(_RESOURCE_DIR, "assets")

# 运行时依赖（预留：将来若需要外挂运行环境）
DEPS_DIR = os.path.join(DATA_DIR, "deps")
# 模型
MODELS_DIR = os.path.join(DATA_DIR, "models")
# 打进离线包里的模型（优先用这个）
BUNDLED_MODELS_DIR = os.path.join(_RESOURCE_DIR, "models")


def ensure_dirs():
    for d in (DATA_DIR, DEPS_DIR, MODELS_DIR):
        try:
            os.makedirs(d, exist_ok=True)
        except Exception:
            pass


def add_deps_to_path():
    if os.path.isdir(DEPS_DIR) and DEPS_DIR not in sys.path:
        sys.path.insert(0, DEPS_DIR)


def has_runtime():
    """ctranslate2 的引擎是否就位（正常是打进 exe 的）。"""
    for base in (DEPS_DIR, RESOURCE_DIR):
        d = os.path.join(base, "ctranslate2")
        if os.path.isfile(os.path.join(d, "ctranslate2.dll")):
            return True
        if os.path.isdir(d) and os.path.isfile(os.path.join(d, "__init__.py")):
            return True
    return False


def model_dir(name):
    """优先用内置模型，其次用下载到数据目录的模型。返回 None 表示没有。"""
    for base in (BUNDLED_MODELS_DIR, MODELS_DIR):
        p = os.path.join(base, name)
        if os.path.isfile(os.path.join(p, "model.bin")):
            return p
    return None


def has_model(name):
    return model_dir(name) is not None
