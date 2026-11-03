# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller 打包配置。

两种产物用同一个 spec，靠环境变量切换：
  默认        → 单文件 dist/VoicePress.exe（瘦 exe，首次运行下载模型）
  VP_OFFLINE=1 → 目录 dist/VoicePress-offline/（内置模型，解压即用）
                 配合 VP_MODEL_DIR 指定要打进去的模型目录

打离线包时目录里会带上 models/<名字>/，运行时会命中 paths.BUNDLED_MODELS_DIR。
"""

import os

from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

HERE = SPECPATH
OFFLINE = os.environ.get("VP_OFFLINE") == "1"
CONSOLE = os.environ.get("VP_CONSOLE") == "1"     # 调试构建：带控制台看 traceback
NAME = os.environ.get("VP_NAME", "VoicePress")
MODEL_DIR = os.environ.get("VP_MODEL_DIR", "")

datas = [(os.path.join(HERE, "assets"), "assets")]
if MODEL_DIR and os.path.isdir(MODEL_DIR):
    datas.append((MODEL_DIR, os.path.join("models", os.path.basename(MODEL_DIR.rstrip("\\/")))))

# faster-whisper 自带 silero VAD 的 onnx 文件，不收集会报缺文件
datas += collect_data_files("faster_whisper")
datas += collect_data_files("ctranslate2")

binaries = collect_dynamic_libs("ctranslate2") + collect_dynamic_libs("onnxruntime")

hiddenimports = [
    "paths", "bootstrap", "overlay", "syscursor", "settings", "autostart",
    "make_assets", "fetch_model",
    "pynput.keyboard._win32", "pynput.mouse._win32", "pynput._util.win32",
    "pystray._win32",
    "comtypes.client", "uiautomation",
    "PIL._tkinter_finder",
    "sounddevice",
    "faster_whisper", "ctranslate2", "onnxruntime", "av", "tokenizers",
    "huggingface_hub",
] + collect_submodules("ctranslate2") + collect_submodules("faster_whisper") \
  + collect_submodules("comtypes") + collect_submodules("uiautomation")

excludes = [
    "torch", "torchaudio", "torchvision", "torchtext",
    "matplotlib", "scipy", "pandas", "IPython", "pytest", "jedi",
    "nbformat", "notebook", "sphinx", "setuptools", "pip", "wheel",
    "cv2", "tensorflow", "transformers",
]

a = Analysis(
    ["main.py"],
    pathex=[HERE],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

icon = os.path.join(HERE, "assets", "app.ico")
icon = icon if os.path.isfile(icon) else None

if OFFLINE:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name=NAME,
        debug=False, strip=False, upx=False,
        console=CONSOLE, icon=icon,
    )
    coll = COLLECT(
        exe, a.binaries, a.datas,
        strip=False, upx=False,
        name="VoicePress-offline",
    )
else:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name=NAME,
        debug=False, strip=False, upx=False,
        console=CONSOLE, icon=icon,
        # 默认解压到系统临时目录；测试时可以用 VP_TMPDIR 指到别处
        runtime_tmpdir=os.environ.get("VP_TMPDIR") or None,
    )
