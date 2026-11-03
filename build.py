#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包脚本。

  python build.py            # 只打瘦 exe（dist/VoicePress.exe）
  python build.py --offline  # 打离线完整包（dist/VoicePress-offline/ + zip）
  python build.py --all      # 两个都打

为什么要先"精简 ctranslate2"：
  conda 环境里那份 ctranslate2 目录被塞进了 3.2GB 的 CUDA 库（cuBLAS/cuDNN/cuSPARSE…），
  collect_dynamic_libs 会把它们全部打进 exe，导致产物 2.3GB。
  这里先把引擎复制一份、只留 CPU 用得到的 DLL，再让 PyInstaller 用这份干净的。
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "libs"))
sys.path.insert(0, HERE)

BUILD = os.path.join(HERE, "build")
DIST = os.path.join(HERE, "dist")
RUNTIME = os.path.join(BUILD, "rt")            # 精简后的运行环境
EMPTY = os.path.join(BUILD, "comtypes-gen")    # 给 comtypes 用的可写 gen 目录

# 这些是 CUDA 相关的大件，CPU 推理不需要，GPU 版也不该由我们分发（>2GB）
CUDA_PREFIXES = ("cublas", "cudnn", "cusparse", "cufft", "cusolver", "curand",
                 "nvrtc", "nvjitlink", "nvblas", "cudart", "nvtoolsext",
                 "libcublas", "libcudnn")


def ensure_runtime():
    """把 ctranslate2 复制成 CPU 精简版放到 build/rt（保留 converters/models/specs 等子包）。"""
    import ctranslate2
    src = os.path.dirname(ctranslate2.__file__)
    dst = os.path.join(RUNTIME, "ctranslate2")
    if os.path.isdir(dst):
        shutil.rmtree(dst)
    os.makedirs(dst, exist_ok=True)

    kept = 0
    for name in os.listdir(src):
        p = os.path.join(src, name)
        if os.path.isdir(p):
            if name == "__pycache__":
                continue
            shutil.copytree(p, os.path.join(dst, name),
                            ignore=shutil.ignore_patterns("__pycache__"))
            kept += 1
            continue
        low = name.lower()
        if low.endswith(".dll") and low.startswith(CUDA_PREFIXES):
            continue
        shutil.copy2(p, os.path.join(dst, name))
        kept += 1

    size = sum(os.path.getsize(os.path.join(r, f))
               for r, _, fs in os.walk(dst) for f in fs)
    print(f"精简 ctranslate2: {kept} 项, {size/1e6:.1f} MB -> {dst}")
    return dst


def ensure_icon():
    """assets/app.ico 是生成物，打包前确保存在。"""
    import make_assets
    ico = os.path.join(make_assets.ASSETS, "app.ico")
    if not os.path.isfile(ico):
        img = make_assets.draw_mic(64)
        img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
    make_assets.build_all()
    return ico


def run_pyinstaller(offline=False, model_dir=None, debug=False):
    ensure_runtime()
    ensure_icon()
    os.makedirs(EMPTY, exist_ok=True)

    env = dict(os.environ)
    env["TMP"] = env["TEMP"] = os.path.join(BUILD, "tmp")
    os.makedirs(env["TMP"], exist_ok=True)
    if offline:
        env["VP_OFFLINE"] = "1"
        if model_dir:
            env["VP_MODEL_DIR"] = model_dir
    else:
        env.pop("VP_OFFLINE", None)
    if debug:
        env["VP_CONSOLE"] = "1"
        env["VP_NAME"] = "VoicePress-debug"
    else:
        env.pop("VP_CONSOLE", None)
        env.pop("VP_NAME", None)
    # 让 PyInstaller 能 import 到项目的 libs 和精简运行环境
    env["PYTHONPATH"] = os.pathsep.join([RUNTIME, os.path.join(HERE, "libs"), HERE])

    # conda 把 tcl86t.dll / libssl / sqlite3 这些放在 Library\bin，不放进 PATH 就解析不到，
    # 打出来的包里 tkinter、ssl 会缺 DLL（设置面板直接起不来）
    extra = []
    py_root = os.path.dirname(sys.executable)
    for sub in ("Library\\bin", "DLLs", "Library\\mingw-w64\\bin"):
        p = os.path.join(py_root, sub)
        if os.path.isdir(p):
            extra.append(p)
    if extra:
        env["PATH"] = os.pathsep.join(extra + [env.get("PATH", "")])
        print("构建 PATH 追加:", extra)

    code = f"""
import sys, os, types
sys.path.insert(0, r'{RUNTIME}')
sys.path.insert(0, os.path.join(r'{HERE}', 'libs'))
sys.path.insert(0, r'{HERE}')
# comtypes 默认往 %APPDATA%\\Python 写缓存，这台机器没权限；先塞一个可写的 gen
try:
    import comtypes
    m = types.ModuleType('comtypes.gen')
    m.__path__ = [r'{EMPTY}']
    sys.modules['comtypes.gen'] = m
    comtypes.gen = m
except Exception as e:
    print('comtypes shim failed:', e)
from PyInstaller.__main__ import run
run(['voicepress.spec', '--noconfirm', '--distpath', r'{DIST}',
     '--workpath', r'{BUILD}', '--log-level', 'WARN'])
"""
    t0 = time.time()
    print("PyInstaller 开始构建" + ("（离线包）" if offline else "（瘦 exe）") + " ...")
    proc = subprocess.run([sys.executable, "-c", code], cwd=HERE, env=env,
                          capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(out.strip().splitlines()[-15:])
    print(tail)
    print(f"构建耗时 {time.time()-t0:.0f}s, 退出码 {proc.returncode}")
    return proc.returncode == 0


def report():
    if os.path.isfile(os.path.join(DIST, "VoicePress.exe")):
        mb = os.path.getsize(os.path.join(DIST, "VoicePress.exe")) / 1e6
        print(f"瘦 exe: dist/VoicePress.exe  {mb:.1f} MB")
    d = os.path.join(DIST, "VoicePress-offline")
    if os.path.isdir(d):
        total = sum(os.path.getsize(os.path.join(r, f))
                    for r, _, fs in os.walk(d) for f in fs)
        print(f"离线包目录: dist/VoicePress-offline/  {total/1e6:.1f} MB")
    z = os.path.join(DIST, "VoicePress-offline.zip")
    if os.path.isfile(z):
        print(f"离线包 zip: dist/VoicePress-offline.zip  {os.path.getsize(z)/1e6:.1f} MB")


def zip_offline():
    src = os.path.join(DIST, "VoicePress-offline")
    out = os.path.join(DIST, "VoicePress-offline.zip")
    if not os.path.isdir(src):
        print("没有离线目录，跳过压缩")
        return
    if os.path.isfile(out):
        os.remove(out)
    print("压缩离线包 ...")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for root, _, files in os.walk(src):
            for f in files:
                p = os.path.join(root, f)
                z.write(p, os.path.relpath(p, DIST))
    print(f"zip 完成: {os.path.getsize(out)/1e6:.1f} MB")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--debug", action="store_true", help="带控制台的调试构建")
    args = ap.parse_args()

    if args.debug:
        ok = run_pyinstaller(offline=False, debug=True)
        report()
        return 0 if ok else 1

    ok = True
    if not args.offline or args.all:
        ok = run_pyinstaller(offline=False) and ok
    if args.offline or args.all:
        model = os.path.join(HERE, "models", "small")
        if not os.path.isdir(model):
            print("缺少 models/small，先跑：python fetch_model.py --repo pengzhendong/faster-whisper-small --dest models\\small")
            return 2
        ok = run_pyinstaller(offline=True, model_dir=model) and ok
        zip_offline()
    report()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
