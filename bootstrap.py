#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
首次运行引导（只对 exe 版生效）。

做三件事：
  1. 探测有没有可用的 NVIDIA/CUDA：有 → large-v3 + float16；没有 → small + int8
  2. 把模型从 ModelScope 下载到 %LOCALAPPDATA%\\VoicePress\\models（断点续传）
  3. 写一份匹配当前机器的 config.json

识别引擎（ctranslate2）是打进 exe 的，不用下载，所以这里只有模型这一步。
"""

import json
import os
import sys
import threading
import time

import paths

PROXY = os.environ.get("VPR_PROXY", "http://127.0.0.1:7897")

# ModelScope 上的 faster-whisper 转换版
MODEL_REPOS = {
    "large-v3": "pengzhendong/faster-whisper-large-v3",
    "medium": "pengzhendong/faster-whisper-medium",
    "small": "pengzhendong/faster-whisper-small",
}

# 各模型大致体积，用于给用户一个预期
MODEL_SIZE_MB = {"large-v3": 3100, "medium": 1500, "small": 480}

SKIP_EXT = (".md", ".gitattributes", ".jpg", ".png")


# ---------------------------------------------------------------- 探测

def detect_gpu():
    """有没有 NVIDIA 显卡（不要求装好 CUDA）。"""
    try:
        import subprocess
        flags = 0x08000000 if os.name == "nt" else 0      # CREATE_NO_WINDOW
        out = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=8,
                             creationflags=flags)
        return out.returncode == 0 and b"GPU" in out.stdout
    except Exception:
        return False


def cuda_usable():
    """ctranslate2 真能初始化 CUDA 吗（缺 cuBLAS/cuDNN 时会失败）。"""
    try:
        import ctranslate2
        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def pick_profile():
    """返回 (model_name, device, compute_type, 说明)。"""
    forced = os.environ.get("VPR_FORCE_MODEL")      # 调试/测试用
    if forced in MODEL_REPOS:
        return forced, "cpu", "int8", f"（测试指定：{forced} + CPU）"
    if detect_gpu():
        if cuda_usable():
            return "large-v3", "cuda", "float16", "检测到可用的 NVIDIA 显卡，使用 large-v3 + GPU 加速"
        return "large-v3", "cpu", "int8", (
            "检测到 NVIDIA 显卡，但系统里找不到 CUDA 运行库（cuBLAS/cuDNN），"
            "先按 CPU 模式安装；需要 GPU 加速请装 CUDA 12 + cuDNN 9 后把 config.json 里的 device 改回 cuda")
    return "small", "cpu", "int8", "没有检测到可用的 NVIDIA 显卡，使用 small 模型 + CPU（速度更快）"


# ---------------------------------------------------------------- 下载

def _session(proxy=None):
    import requests
    s = requests.Session()
    s.trust_env = False
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def _list_files(s, repo):
    r = s.get(f"https://www.modelscope.cn/api/v1/models/{repo}/repo/files",
              params={"Revision": "master", "Recursive": "true"}, timeout=30)
    r.raise_for_status()
    files = [f["Path"] for f in r.json().get("Data", {}).get("Files", [])]
    base = f"https://www.modelscope.cn/models/{repo}/resolve/master"
    return [(f, f"{base}/{f}") for f in files if not f.endswith(SKIP_EXT)]


def _download(s, name, url, dest_dir, progress=None, attempts=8):
    path = os.path.join(dest_dir, name.replace("/", os.sep))
    os.makedirs(os.path.dirname(path) or dest_dir, exist_ok=True)
    part = path + ".part"
    for attempt in range(1, attempts + 1):
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        try:
            with s.get(url, headers=headers, stream=True, timeout=(20, 60)) as r:
                if r.status_code == 416:
                    os.replace(part, path)
                    return path
                if r.status_code not in (200, 206):
                    raise RuntimeError(f"HTTP {r.status_code}")
                total = int(r.headers.get("Content-Length", 0)) + have
                mode = "ab" if (have and r.status_code == 206) else "wb"
                if mode == "wb":
                    have = 0
                done = have
                with open(part, mode) as f:
                    for chunk in r.iter_content(1 << 20):
                        if not chunk:
                            continue
                        f.write(chunk)
                        done += len(chunk)
                        if progress:
                            progress(done, total, name)
            os.replace(part, path)
            return path
        except Exception as e:
            if attempt == attempts:
                raise
            time.sleep(min(15, 2 * attempt))
    return path


def download_model(model_name, progress=None):
    """下载某个模型到 paths.MODELS_DIR，已存在则跳过。"""
    if paths.has_model(model_name):
        return paths.model_dir(model_name)
    repo = MODEL_REPOS.get(model_name)
    if not repo:
        raise RuntimeError(f"未知模型: {model_name}")

    dest = os.path.join(paths.MODELS_DIR, model_name)
    os.makedirs(dest, exist_ok=True)
    # 直连 ModelScope 最快；走本地代理反而更慢
    s = _session(None)
    files = _list_files(s, repo)
    total_bytes = sum(1 for _ in files)
    for idx, (name, url) in enumerate(files, 1):
        if progress:
            progress(0, 0, f"[{idx}/{total_bytes}] {name}")
        try:
            _download(s, name, url, dest, progress=progress)
        except Exception:
            # 直连失败再试一次代理
            s2 = _session(PROXY)
            _download(s2, name, url, dest, progress=progress)
    return dest


def is_ready():
    """运行环境 + 至少一个模型就绪。"""
    if not paths.has_runtime():
        return False
    for name in MODEL_REPOS:
        if paths.has_model(name):
            return True
    return False


# ---------------------------------------------------------------- 向导窗口

class Wizard:
    def __init__(self):
        self.root = None
        self.progress_var = None
        self.status_var = None
        self.done = threading.Event()
        self.ok = False
        self.error = None

    def run(self):
        import tkinter as tk
        from tkinter import ttk

        self.root = tk.Tk()
        self.root.title("VoicePress 首次运行")
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap(default=os.path.join(paths.ASSETS_DIR, "app.ico"))
        except Exception:
            pass

        frame = tk.Frame(self.root, padx=22, pady=18)
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="VoicePress 首次运行", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")
        tk.Label(frame, text="正在准备语音识别模型（只需一次，之后就不用了）",
                 fg="#6b7280", font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(2, 12))

        self.status_var = tk.StringVar(value="检测硬件…")
        tk.Label(frame, textvariable=self.status_var, anchor="w",
                 font=("Microsoft YaHei UI", 9)).pack(fill="x")
        self.progress_var = tk.DoubleVar(value=0)
        ttk.Progressbar(frame, variable=self.progress_var, maximum=100,
                        length=360).pack(fill="x", pady=(6, 10))
        self.detail_var = tk.StringVar(value="")
        tk.Label(frame, textvariable=self.detail_var, anchor="w", fg="#6b7280",
                 font=("Microsoft YaHei UI", 8)).pack(fill="x")

        self.root.protocol("WM_DELETE_WINDOW", lambda: None)
        threading.Thread(target=self._work, daemon=True).start()
        self.root.after(300, self._poll)
        self.root.mainloop()
        return self.ok

    def _poll(self):
        if self.done.is_set():
            try:
                self.root.destroy()
            except Exception:
                pass
            return
        self.root.after(300, self._poll)

    def _ui(self, fn):
        try:
            self.root.after(0, fn)
        except Exception:
            pass

    def _set_status(self, text):
        self._ui(lambda: self.status_var.set(text))

    def _set_detail(self, text):
        self._ui(lambda: self.detail_var.set(text))

    def _set_progress(self, pct):
        self._ui(lambda: self.progress_var.set(max(0.0, min(100.0, pct))))

    def _work(self):
        try:
            model, device, compute, note = pick_profile()
            self._set_status(f"将使用：{model} / {device}")
            self._set_detail(note)
            time.sleep(0.6)

            size = MODEL_SIZE_MB.get(model, 500)
            self._set_status(f"下载模型 {model}（约 {size} MB）…")

            def on_progress(done, total, name):
                if total:
                    pct = done * 100.0 / total
                    self._set_progress(pct)
                    self._set_detail(f"{name}  {done/1e6:.0f}/{total/1e6:.0f} MB")
                else:
                    self._set_detail(str(name))

            download_model(model, progress=on_progress)
            self._set_progress(100)
            self._set_status("写入配置…")

            cfg = {}
            if os.path.isfile(paths.CONFIG_PATH):
                try:
                    with open(paths.CONFIG_PATH, "r", encoding="utf-8") as f:
                        cfg = json.load(f)
                except Exception:
                    cfg = {}
            cfg.update({"model": model, "device": device, "compute_type": compute})
            with open(paths.CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)

            self._set_status("准备完成，正在启动…")
            time.sleep(0.5)
            self.ok = True
        except Exception as e:
            self.error = e
            self._set_status("准备失败")
            self._set_detail(f"{type(e).__name__}: {e}")
            time.sleep(3.0)
        finally:
            self.done.set()


def run_wizard():
    return Wizard().run()


def main():
    """单独跑引导：python bootstrap.py"""
    print("runtime:", paths.has_runtime(),
          "| models:", [n for n in MODEL_REPOS if paths.has_model(n)])
    ready = run_wizard()
    print("ok:", ready)
    return 0 if ready else 1


if __name__ == "__main__":
    sys.exit(main())
