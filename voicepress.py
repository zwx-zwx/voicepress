#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VoicePress - 长按鼠标左键说话，松开后把语音转成文字，粘贴到当前光标所在的输入框。

触发规则：
  1. 按下鼠标左键 -> 开始预缓冲麦克风音频
  2. 按住超过 hold_ms 且鼠标没有明显移动 -> 判定为"说话"，给提示音，正式录音
  3. 松开左键 -> 转写 -> Ctrl+V 粘贴到当前焦点控件
  4. 期间鼠标移动超过 move_tolerance_px -> 判定为拖拽/选择，直接放弃

用法：
  pythonw voicepress.py            # 常驻托盘运行
  python  voicepress.py --devices  # 列出麦克风
  python  voicepress.py --focus    # 打印当前焦点控件（调试用）
  python  voicepress.py --transcribe a.wav
  python  voicepress.py --paste "测试文本"
"""

import argparse
import json
import logging
import os
import queue
import re
import sys
import threading
import time
import traceback

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import paths  # noqa: E402

paths.ensure_dirs()
paths.add_deps_to_path()

CONFIG_PATH = paths.CONFIG_PATH
LOG_PATH = paths.LOG_PATH
ASSETS_DIR = paths.ASSETS_DIR

# 依赖装在本目录下的 libs/ 里（不动系统的 conda 环境）
LIBS_DIR = os.path.join(APP_DIR, "libs")
if os.path.isdir(LIBS_DIR) and LIBS_DIR not in sys.path:
    sys.path.insert(0, LIBS_DIR)

import numpy as np  # noqa: E402

try:                                    # 动效层和光标替换都是可选增强，缺了也能用
    from overlay import Overlay
except Exception:                       # pragma: no cover
    Overlay = None
try:
    from syscursor import CursorSwitcher
except Exception:                       # pragma: no cover
    CursorSwitcher = None

MIC_CURSOR = os.path.join(ASSETS_DIR, "mic.cur")

# 默认配置：首次运行会写一份到 config.json
DEFAULTS = {
    "hold_ms": 600,                 # 长按多久算"说话"
    "move_tolerance_px": 6,         # 判定为拖拽的移动像素
    "only_in_text_field": True,     # 只在可编辑控件上触发
    "prebuffer_ms": 250,            # 按住这么久后先开麦预热，0 = 按下立刻开（麦克风图标会闪）
    "model": "large-v3",
    "device": "cuda",               # cuda / cpu
    "compute_type": "float16",      # float16 / int8_float16 / int8
    "language": "zh",
    "beam_size": 5,
    "initial_prompt": "以下是普通话的句子，请输出简体中文并带标点。",
    "sample_rate": 16000,
    "min_audio_sec": 0.35,          # 太短就当误触
    "max_record_sec": 60.0,         # 超过自动停止
    "silence_rms": 0.0035,          # 小于这个音量当作没说话
    "beep": True,
    "overlay": True,                # 光标旁边的动效指示器
    "cursor_icon": True,            # 录音时把系统光标换成麦克风
    "cursor_preset": "mic24",       # 用哪个光标样式（见 make_assets.PRESETS）
    "paste_restore_delay_ms": 300,  # 粘贴后等多久还原剪贴板
    "hf_endpoint": "https://hf-mirror.com",
    "tray": True,
}

log = logging.getLogger("voicepress")


# ---------------------------------------------------------------- utils

def clean_proxy_env():
    """
    NO_PROXY 里如果写了 [::1] 这种带方括号的 IPv6 地址，httpx 会把它当 URL 解析
    然后抛 InvalidURL，导致 huggingface_hub 连模型都下不了。这里把带方括号的条目去掉。
    """
    val = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if not val or "[" not in val:
        return
    parts = [p.strip() for p in val.split(",") if p.strip() and "[" not in p]
    clean = ",".join(parts)
    for k in [k for k in list(os.environ) if k.lower() == "no_proxy"]:
        os.environ.pop(k, None)
    os.environ["NO_PROXY"] = clean
    try:
        import ctypes
        ctypes.windll.kernel32.SetEnvironmentVariableW("NO_PROXY", clean)
        ctypes.windll.kernel32.SetEnvironmentVariableW("no_proxy", clean)
    except Exception:
        pass


def patch_comtypes():
    """
    comtypes 默认把生成的 COM 包装代码写到 %APPDATA%\\Python 或 site-packages，这里两处
    都可能不可写。提前塞一个指向项目目录的 comtypes.gen，让它把缓存写到我们自己这边。
    """
    try:
        import comtypes
    except Exception:
        return
    if getattr(comtypes, "gen", None) is not None:
        return
    import types
    cache = os.path.join(paths.DATA_DIR, "comtypes-cache")
    try:
        os.makedirs(cache, exist_ok=True)
    except Exception:
        pass
    mod = types.ModuleType("comtypes.gen")
    mod.__path__ = [cache]
    sys.modules["comtypes.gen"] = mod
    comtypes.gen = mod


def load_config():
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception:
            log.warning("config.json 读取失败，使用默认配置", exc_info=True)

    # 离线包里内置了哪个模型就用哪个（内置 small 时别去找 large-v3 下载）
    try:
        bundled_dir = paths.BUNDLED_MODELS_DIR
        if os.path.isdir(bundled_dir):
            names = [n for n in os.listdir(bundled_dir) if paths.has_model(n)]
            if names and cfg.get("model") not in names:
                cfg["model"] = names[0]
    except Exception:
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        log.warning("config.json 写入失败", exc_info=True)


def beep(kind="start"):
    try:
        import winsound
    except Exception:
        return
    seq = {
        "start": [(880, 70)],
        "ok": [(1175, 55), (1568, 70)],
        "cancel": [(400, 90)],
        "error": [(330, 120), (260, 120)],
    }.get(kind, [(880, 60)])

    def run():
        try:
            for f, d in seq:
                winsound.Beep(f, d)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def tidy(text):
    """整理转写结果：去掉多余空白、CJK 之间被插入的空格、纯标点输出。"""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"(?<=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])\s+(?=[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef])", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    if not re.search(r"[\w\u4e00-\u9fff]", t):
        return ""
    return t


def foreground_hwnd():
    """当前前台窗口句柄。"""
    try:
        import ctypes
        return ctypes.windll.user32.GetForegroundWindow()
    except Exception:
        return 0


def window_title(hwnd):
    try:
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    except Exception:
        return ""


# ---------------------------------------------------------------- 焦点检测

class FocusProbe:
    """
    单线程跑 UI Automation，带超时。
    UIA 在某些应用上会卡几百毫秒，所以全部丢给一个后台线程，主线程只等结果。
    结果按窗口句柄缓存 3 秒。
    """

    def __init__(self, cache_sec=3.0):
        self.cache_sec = cache_sec
        self._lock = threading.Lock()
        self._cache = {}          # hwnd -> (ts, editable)
        self._req = queue.Queue()
        self._thread = None
        self._started = False

    def _ensure(self):
        if self._started:
            return
        self._started = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        try:
            patch_comtypes()
            import uiautomation as auto  # noqa: F401
            try:
                auto.InitializeUIAutomationInCurrentThread()
            except Exception:
                pass
        except Exception:
            log.warning("uiautomation 不可用，only_in_text_field 将失效", exc_info=True)
            return

        while True:
            hwnd, ev = self._req.get()
            try:
                ok = self._check(hwnd)
            except Exception:
                log.debug("UIA 检测异常", exc_info=True)
                ok = None
            with self._lock:
                self._cache[hwnd] = (time.monotonic(), ok)
            ev.set()

    def _check(self, hwnd):
        import uiautomation as auto

        ctl = auto.GetFocusedControl()
        for _ in range(5):
            if ctl is None:
                return False
            name = getattr(ctl, "ControlTypeName", "") or ""
            if name == "EditControl":
                try:
                    vp = ctl.GetValuePattern()
                    if vp is not None and vp.IsReadOnly:
                        return False
                except Exception:
                    pass
                return True
            if name == "DocumentControl":
                try:
                    if not ctl.IsEnabled:
                        return False
                except Exception:
                    pass
                return True
            if name in ("ComboBoxControl",):
                return True
            try:
                ctl = ctl.GetParentControl()
            except Exception:
                return False
        return False

    def ask_async(self, hwnd=None):
        """按窗口句柄发起后台检测（按下鼠标时调用）。"""
        hwnd = hwnd or foreground_hwnd()
        self._ensure()
        if self._thread is None or not self._thread.is_alive():
            return
        try:
            self._req.put_nowait((hwnd, threading.Event()))
        except Exception:
            pass

    def cached(self, hwnd, max_age=None):
        """取缓存结果；没有就返回 None（未知）。"""
        max_age = self.cache_sec if max_age is None else max_age
        with self._lock:
            item = self._cache.get(hwnd)
        if not item:
            return None
        ts, ok = item
        if time.monotonic() - ts > max_age:
            return None
        return ok


# ---------------------------------------------------------------- 录音

class Recorder:
    """麦克风采集，内部统一重采样到 16k 单声道 float32。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._stream = None
        self._buf = []
        self._lock = threading.Lock()
        self._sr = None
        self._real_sr = None
        self._error = None
        self._peak = 0.0

    def level(self):
        """给动效层用的实时音量（0~1），顺便做一点衰减。"""
        with self._lock:
            v = self._peak
            self._peak = v * 0.55
        return min(1.0, v * 6.0)

    @property
    def error(self):
        return self._error

    def _guess_rate(self):
        import sounddevice as sd
        try:
            dev = self._device_id()
            info = sd.query_devices(dev, kind="input") if dev is not None else sd.query_devices(kind="input")
            return int(info["default_samplerate"])
        except Exception:
            return 48000

    def _device_id(self):
        """config 里的 input_device 可以是设备序号、名字片段，留空用系统默认。"""
        want = self.cfg.get("input_device")
        if want in (None, "", 0):
            return None
        import sounddevice as sd
        if isinstance(want, int):
            return want
        try:
            for i, d in enumerate(sd.query_devices()):
                if d["max_input_channels"] > 0 and str(want).lower() in d["name"].lower():
                    return i
        except Exception:
            pass
        return None

    def start(self):
        import sounddevice as sd
        import numpy as np

        self._error = None
        with self._lock:
            self._buf = []
        target = int(self.cfg["sample_rate"])
        candidates = [target]
        dev_rate = self._guess_rate()
        for r in (48000, 44100, dev_rate):
            if r not in candidates:
                candidates.append(int(r))

        last_err = None
        device = self._device_id()
        for rate in candidates:
            try:
                stream = sd.InputStream(
                    device=device,
                    samplerate=rate,
                    channels=1,
                    dtype="float32",
                    blocksize=0,
                    callback=self._cb,
                )
                stream.start()
                self._stream = stream
                self._real_sr = rate
                self._sr = target
                return True
            except Exception as e:  # 设备不支持该采样率
                last_err = e
                continue
        self._error = f"无法打开麦克风: {last_err}"
        log.error(self._error)
        return False

    def _cb(self, indata, frames, time_info, status):
        try:
            if status:
                log.debug("stream status: %s", status)
            mono = indata[:, 0]
            peak = float(np.max(np.abs(mono))) if len(mono) else 0.0
            with self._lock:
                self._buf.append(mono.copy())
                if peak > self._peak:
                    self._peak = peak
        except Exception:
            pass

    def stop(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            chunks, self._buf = self._buf, []
        if not chunks:
            return None
        import numpy as np
        audio = np.concatenate(chunks).astype(np.float32)
        if self._real_sr and self._real_sr != self._sr:
            audio = self._resample(audio, self._real_sr, self._sr)
        return audio

    @staticmethod
    def _resample(audio, src, dst):
        import numpy as np
        if src == dst or len(audio) == 0:
            return audio
        n = int(round(len(audio) * dst / float(src)))
        if n <= 0:
            return audio
        idx = np.linspace(0, len(audio) - 1, n, dtype=np.float64)
        left = np.floor(idx).astype(np.int64)
        right = np.minimum(left + 1, len(audio) - 1)
        frac = (idx - left).astype(np.float32)
        return (audio[left] * (1 - frac) + audio[right] * frac).astype(np.float32)

    def cancel(self):
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        with self._lock:
            self._buf = []


# ---------------------------------------------------------------- 转写

class Transcriber:
    def __init__(self, cfg):
        self.cfg = cfg
        self._model = None
        self._lock = threading.Lock()
        self._loading = threading.Lock()
        self._force_cpu = False

    def _load(self):
        with self._loading:
            if self._model is not None:
                return self._model
            clean_proxy_env()
            # 模型缓存放数据目录，别写到临时解压目录（只读）
            os.environ.setdefault("HF_HOME", paths.MODELS_DIR)
            endpoint = (self.cfg.get("hf_endpoint") or "").strip()
            if endpoint:
                os.environ.setdefault("HF_ENDPOINT", endpoint)
            from faster_whisper import WhisperModel
            device = self.cfg.get("device", "cuda")
            compute = self.cfg.get("compute_type", "float16")
            name = self.cfg.get("model", "large-v3")
            # 已经有下载好的模型（内置或用户下载）就用本地目录，避免每次去网上拉
            local = paths.model_dir(os.path.basename(str(name)))
            if local:
                name = local
            log.info("加载模型 %s device=%s compute=%s ...", name, device, compute)
            t0 = time.perf_counter()
            try:
                model = WhisperModel(name, device=device, compute_type=compute)
            except Exception:
                if device != "cpu":
                    log.warning("GPU 加载失败，回退 CPU/int8", exc_info=True)
                    model = WhisperModel(name, device="cpu", compute_type="int8")
                else:
                    raise
            log.info("模型就绪，用时 %.1fs", time.perf_counter() - t0)
            self._model = model
            return model

    def preload(self):
        try:
            self._load()
        except Exception:
            log.error("模型预加载失败", exc_info=True)

    @staticmethod
    def _is_cuda_error(exc):
        msg = str(exc).lower()
        for key in ("cublas", "cudnn", "cuda", "cudart", "cannot be loaded", "no kernel image"):
            if key in msg:
                return True
        return False

    def _build(self, device, compute):
        from faster_whisper import WhisperModel
        name = self.cfg.get("model", "large-v3")
        local = paths.model_dir(os.path.basename(str(name)))
        if local:
            name = local
        log.info("加载模型 %s device=%s compute=%s ...", name, device, compute)
        t0 = time.perf_counter()
        model = WhisperModel(name, device=device, compute_type=compute)
        log.info("模型就绪，用时 %.1fs", time.perf_counter() - t0)
        return model

    def _load(self):
        with self._loading:
            if self._model is not None:
                return self._model
            clean_proxy_env()
            # 模型缓存放数据目录，别写到临时解压目录（只读）
            os.environ.setdefault("HF_HOME", paths.MODELS_DIR)
            endpoint = (self.cfg.get("hf_endpoint") or "").strip()
            if endpoint:
                os.environ.setdefault("HF_ENDPOINT", endpoint)
            device = "cpu" if self._force_cpu else self.cfg.get("device", "cuda")
            compute = self.cfg.get("compute_type", "float16")
            # CPU 上跑不了 float16，掉回 CPU 时必须换成 int8，否则 ctranslate2 直接拒绝
            if device == "cpu" and compute in ("float16", "bfloat16", "int8_float16"):
                compute = "int8"
            try:
                model = self._build(device, compute)
            except Exception:
                if device != "cpu":
                    log.warning("GPU 加载失败，回退 CPU/int8", exc_info=True)
                    self._force_cpu = True
                    model = self._build("cpu", "int8")
                else:
                    raise
            self._model = model
            return model

    def _run(self, model, audio):
        cfg = self.cfg
        segments, _info = model.transcribe(
            audio,
            language=cfg.get("language") or None,
            beam_size=int(cfg.get("beam_size", 5)),
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=250),
            initial_prompt=cfg.get("initial_prompt") or None,
            condition_on_previous_text=False,
        )
        return "".join(seg.text for seg in segments)

    def transcribe(self, audio):
        import numpy as np
        if audio is None or len(audio) == 0:
            return ""
        if float(np.max(np.abs(audio))) < 1e-4:
            return ""
        model = self._load()
        with self._lock:
            t0 = time.perf_counter()
            try:
                text = self._run(model, audio)
            except Exception as e:
                # 打包版里通常不会带 cuBLAS/cuDNN，ctranslate2 会在第一次推理时才报错，
                # 这里就地降级到 CPU，不打断用户
                if model is not None and not self._force_cpu and self._is_cuda_error(e):
                    log.warning("GPU 推理失败，切回 CPU/int8 重试：%s", e)
                    self._force_cpu = True
                    self._model = None
                    model = self._load()
                    text = self._run(model, audio)
                else:
                    raise
            dt = time.perf_counter() - t0
        log.info("转写 %.2fs 音频用时 %.2fs: %s",
                 len(audio) / float(self.cfg["sample_rate"]), dt, text.strip())
        return text


# ---------------------------------------------------------------- 粘贴

def paste_text(text, restore_delay_ms=300):
    import pyperclip
    from pynput.keyboard import Controller, Key

    old = None
    try:
        old = pyperclip.paste()
    except Exception:
        old = None

    try:
        pyperclip.copy(text)
    except Exception as e:
        log.error("写剪贴板失败: %s", e)
        return False

    time.sleep(0.06)
    kb = Controller()
    try:
        with kb.pressed(Key.ctrl):
            kb.press("v")
            kb.release("v")
    except Exception as e:
        log.error("发送 Ctrl+V 失败: %s", e)
        return False

    time.sleep(max(0.05, restore_delay_ms / 1000.0))
    try:
        if old is not None:
            pyperclip.copy(old)
    except Exception:
        pass
    return True


# ---------------------------------------------------------------- 主逻辑

class VoicePress:
    def __init__(self, cfg):
        self.cfg = cfg
        self.enabled = True
        self.recorder = Recorder(cfg)
        self.transcriber = Transcriber(cfg)
        self.probe = FocusProbe() if cfg.get("only_in_text_field") else None
        self._lock = threading.Lock()
        self._pressed_at = 0.0
        self._press_pos = None
        self._moved = False
        self._pending = None          # 长按判定定时器
        self._pre = None              # 麦克风预热定时器
        self._recording = False
        self._session_hwnd = 0
        self._max_timer = None
        self._listener = None
        self._work = queue.Queue()
        self._worker = None

        self.ui = None
        if cfg.get("overlay", True) and Overlay is not None:
            self.ui = Overlay(True)
        self.cursors = None
        if cfg.get("cursor_icon", True) and CursorSwitcher is not None:
            self.cursors = CursorSwitcher(self._cursor_path(), True)

    # -- 图标预设
    def _cursor_path(self):
        try:
            import make_assets
            return make_assets.cursor_path(self.cfg.get("cursor_preset"))
        except Exception:
            return MIC_CURSOR

    def apply_config(self, cfg):
        """设置面板保存后热更新，不用重启。"""
        self.cfg = cfg
        self.recorder.cfg = cfg
        self.transcriber.cfg = cfg

        if cfg.get("only_in_text_field"):
            if self.probe is None:
                self.probe = FocusProbe()
        else:
            self.probe = None

        want_overlay = bool(cfg.get("overlay")) and Overlay is not None
        if want_overlay and self.ui is None:
            self.ui = Overlay(True)
            self.ui.start()
        elif not want_overlay and self.ui is not None:
            self.ui.hide()
            self.ui.stop()
            self.ui = None

        want_cursor = bool(cfg.get("cursor_icon")) and CursorSwitcher is not None
        path = self._cursor_path()
        if want_cursor:
            if self.cursors is None:
                self.cursors = CursorSwitcher(path, True)
            else:
                self.cursors.mic_path = path
                self.cursors.enabled = True
        elif self.cursors is not None:
            self.cursors.restore()
            self.cursors = None

        log.info("配置已应用: hold=%sms 光标=%s 动效=%s 输入框限定=%s",
                 cfg.get("hold_ms"), cfg.get("cursor_preset") if want_cursor else "关",
                 cfg.get("overlay"), cfg.get("only_in_text_field"))

    # -- 视觉反馈
    def _visual_recording(self):
        if self.cursors is not None:
            try:
                self.cursors.set_mic()
            except Exception:
                log.debug("切换光标失败", exc_info=True)
        if self.ui is not None:
            self.ui.begin_recording(self.recorder.level)

    def _visual_transcribing(self):
        if self.cursors is not None:
            try:
                self.cursors.restore()
            except Exception:
                log.debug("还原光标失败", exc_info=True)
        if self.ui is not None:
            self.ui.set_state("transcribing")

    def _visual_flash(self, state="done", hold=0.75):
        if self.cursors is not None:
            try:
                self.cursors.restore()
            except Exception:
                pass
        if self.ui is not None:
            self.ui.flash(state, hold)

    # -- 生命周期
    def start(self):
        from pynput import mouse
        if self.cursors is not None:
            try:
                self.cursors.restore()          # 清掉上次异常退出的残留
            except Exception:
                pass
        if self.ui is not None:
            self.ui.start()
        self._worker = threading.Thread(target=self._work_loop, daemon=True)
        self._worker.start()
        self._listener = mouse.Listener(on_click=self._on_click, on_move=self._on_move)
        self._listener.start()
        log.info("VoicePress 已启动 (hold=%sms, only_in_text_field=%s)",
                 self.cfg["hold_ms"], self.cfg["only_in_text_field"])

    def stop(self):
        try:
            if self._listener:
                self._listener.stop()
        except Exception:
            pass
        self._cancel_pending()
        self.recorder.cancel()
        if self.cursors is not None:
            try:
                self.cursors.restore()
            except Exception:
                pass
        if self.ui is not None:
            self.ui.hide()
            self.ui.stop()
        self._work.put(None)
        log.info("VoicePress 已退出")

    # -- 鼠标事件
    def _on_move(self, x, y):
        with self._lock:
            if self._press_pos is None or self._recording:
                return
            dx = x - self._press_pos[0]
            dy = y - self._press_pos[1]
            if dx * dx + dy * dy > self.cfg["move_tolerance_px"] ** 2:
                self._moved = True
            moved = self._moved
        if moved:
            self._cancel_pending()
            self.recorder.cancel()

    def _on_click(self, x, y, button, pressed):
        from pynput.mouse import Button
        if button != Button.left:
            return
        if pressed:
            self._on_press(x, y)
        else:
            self._on_release(x, y)

    def _on_press(self, x, y):
        if not self.enabled:
            return
        self._cancel_pending()
        with self._lock:
            self._pressed_at = time.monotonic()
            self._press_pos = (x, y)
            self._moved = False
            self._recording = False
            self._session_hwnd = foreground_hwnd()

        if self.probe is not None:
            self.probe.ask_async(self._session_hwnd)

        pb = float(self.cfg.get("prebuffer_ms", 250))
        if pb <= 0:
            self._open_mic()
        else:
            t = threading.Timer(pb / 1000.0, self._early_open)
            t.daemon = True
            with self._lock:
                self._pre = t
            t.start()

        t = threading.Timer(max(0.05, float(self.cfg["hold_ms"]) / 1000.0), self._maybe_record)
        t.daemon = True
        with self._lock:
            self._pending = t
        t.start()

    def _early_open(self):
        """按住一会儿了还没松手，先把麦克风打开预热，等真正判定通过时开头不会丢字。"""
        with self._lock:
            if self._press_pos is None or self._moved or self._recording:
                return
            if self.recorder._stream is not None:
                return
        self._open_mic()

    def _open_mic(self):
        if not self.recorder.start():
            log.warning("%s", self.recorder.error)
            return False
        return True

    def _maybe_record(self):
        with self._lock:
            if self._recording or self._moved or self._press_pos is None:
                return
        if self.probe is not None:
            hwnd = self._session_hwnd or foreground_hwnd()
            verdict = self.probe.cached(hwnd)
            if verdict is False:
                log.debug("焦点不是输入框，忽略长按")
                self.recorder.cancel()
                return
        if self.recorder._stream is None:
            if not self._open_mic():
                if self.cfg.get("beep", True):
                    beep("error")
                return
        with self._lock:
            if self._recording or self._moved or self._press_pos is None:
                return
            self._recording = True
        if self.cfg.get("beep", True):
            beep("start")
        self._visual_recording()
        t = threading.Timer(float(self.cfg["max_record_sec"]), self._auto_stop)
        t.daemon = True
        with self._lock:
            self._max_timer = t
        t.start()
        log.debug("开始录音")

    def _auto_stop(self):
        log.info("达到最长录音时间，自动停止")
        self._finish(transcribe=True)

    def _on_release(self, x, y):
        self._cancel_pending()
        with self._lock:
            recording = self._recording
            self._press_pos = None
            self._recording = False
        if recording:
            self._finish(transcribe=True)
        else:
            self.recorder.cancel()

    def _cancel_pending(self):
        with self._lock:
            t, self._pending = self._pending, None
            pre, self._pre = self._pre, None
        for timer in (t, pre):
            if timer is not None:
                timer.cancel()
        with self._lock:
            mt, self._max_timer = self._max_timer, None
        if mt is not None:
            mt.cancel()

    def _finish(self, transcribe=True):
        self._cancel_pending()
        audio = self.recorder.stop()
        if audio is None or len(audio) < self.cfg["sample_rate"] * float(self.cfg["min_audio_sec"]):
            log.debug("音频太短，忽略")
            if self.cfg.get("beep", True):
                beep("cancel")
            self._visual_flash("error", 0.5)
            return
        if transcribe:
            self._visual_transcribing()
            self._work.put(audio)

    # -- 后台队列：串行转写 + 粘贴
    def _work_loop(self):
        while True:
            audio = self._work.get()
            if audio is None:
                return
            try:
                text = tidy(self.transcriber.transcribe(audio))
                if not text:
                    log.debug("转写结果为空，忽略")
                    self._visual_flash("error", 0.6)
                    continue
                time.sleep(0.05)
                ok = paste_text(text, int(self.cfg.get("paste_restore_delay_ms", 300)))
                if ok:
                    self._visual_flash("done", 0.7)
                    if self.cfg.get("beep", True):
                        beep("ok")
                else:
                    self._visual_flash("error", 0.7)
            except Exception:
                log.error("处理失败", exc_info=True)
                self._visual_flash("error", 0.8)
                if self.cfg.get("beep", True):
                    beep("error")


# ---------------------------------------------------------------- 托盘

def make_icon_image(active=True):
    from PIL import Image, ImageDraw
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    fg = (255, 255, 255, 255)
    bg = (32, 120, 255, 255) if active else (120, 120, 120, 255)
    d.ellipse((2, 2, size - 2, size - 2), fill=bg)
    # 话筒
    d.rounded_rectangle((24, 14, 40, 38), radius=8, fill=fg)
    d.arc((18, 26, 46, 46), start=0, end=180, fill=fg, width=4)
    d.line((32, 46, 32, 52), fill=fg, width=4)
    d.line((24, 52, 40, 52), fill=fg, width=4)
    return img


def watch_config(vp, interval=1.5):
    """盯着 config.json：设置面板（独立进程）或手动改文件保存后，自动热加载。"""
    def mtime():
        try:
            return os.path.getmtime(CONFIG_PATH)
        except Exception:
            return 0.0

    last = mtime()
    while True:
        time.sleep(interval)
        try:
            m = mtime()
            if not m or m == last:
                continue
            last = m
            cfg = load_config()
            if cfg != vp.cfg:
                vp.apply_config(cfg)
                log.info("检测到配置文件变更，已热加载")
        except Exception:
            log.warning("热加载配置失败", exc_info=True)


def run_tray(vp):
    import pystray

    def toggle_enabled(icon, item):
        vp.enabled = not vp.enabled
        icon.icon = make_icon_image(vp.enabled)
        icon.update_menu()
        log.info("已%s", "启用" if vp.enabled else "暂停")

    def toggle_scope(icon, item):
        vp.cfg["only_in_text_field"] = not vp.cfg.get("only_in_text_field", True)
        vp.probe = FocusProbe() if vp.cfg["only_in_text_field"] else None
        save_config(vp.cfg)
        icon.update_menu()
        log.info("只在输入框触发 = %s", vp.cfg["only_in_text_field"])

    def open_config(icon, item):
        try:
            os.startfile(CONFIG_PATH)
        except Exception:
            log.warning("打开配置文件失败", exc_info=True)

    def quit_app(icon, item):
        icon.stop()
        vp.stop()

    def open_settings_panel(icon, item):
        try:
            import subprocess
            if paths.FROZEN:
                # exe 版：用自己 + --settings 起一个新进程（同样独立主线程跑 Tk）
                subprocess.Popen([sys.executable, "--settings"])
            else:
                exe = sys.executable
                pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
                if os.path.isfile(pyw):
                    exe = pyw
                subprocess.Popen([exe, os.path.join(APP_DIR, "settings.py")], cwd=APP_DIR)
        except Exception:
            log.error("打开设置面板失败", exc_info=True)

    menu = pystray.Menu(
        pystray.MenuItem(lambda i: "状态：" + ("运行中" if vp.enabled else "已暂停"), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("设置…", open_settings_panel, default=True),
        pystray.MenuItem("暂停 / 恢复", toggle_enabled),
        pystray.MenuItem("只在输入框触发", toggle_scope,
                         checked=lambda i: bool(vp.cfg.get("only_in_text_field"))),
        pystray.MenuItem("打开配置文件", open_config),
        pystray.MenuItem("打开日志", lambda i, it: os.startfile(LOG_PATH)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", quit_app),
    )
    icon = pystray.Icon("VoicePress", make_icon_image(vp.enabled), "VoicePress 长按左键说话", menu)
    icon.run()


# ---------------------------------------------------------------- CLI 工具

def cmd_devices():
    import sounddevice as sd
    print(sd.query_devices())
    try:
        print("\n默认输入设备:", sd.query_devices(kind="input")["name"])
    except Exception as e:
        print("默认输入设备查询失败:", e)


def cmd_focus():
    patch_comtypes()
    auto = __import__("uiautomation")
    ctl = auto.GetFocusedControl()
    for i in range(6):
        if ctl is None:
            break
        print(f"{'  ' * i}{ctl.ControlTypeName}  name={ctl.Name!r} class={ctl.ClassName!r} "
              f"automationId={ctl.AutomationId!r}")
        ctl = ctl.GetParentControl()
    hwnd = foreground_hwnd()
    print("前台窗口:", hwnd, window_title(hwnd))


def cmd_transcribe(path):
    cfg = load_config()
    import wave
    import numpy as np

    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        ch = w.getnchannels()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    if ch > 1:
        data = data.reshape(-1, ch).mean(axis=1)
    if sr != cfg["sample_rate"]:
        data = Recorder._resample(data, sr, cfg["sample_rate"])
    tr = Transcriber(cfg)
    t0 = time.perf_counter()
    text = tr.transcribe(data)
    print(f"[{time.perf_counter() - t0:.2f}s] {tidy(text)}")


def cmd_paste(text):
    cfg = load_config()
    print("3 秒内请点到要粘贴的地方...")
    time.sleep(3)
    ok = paste_text(text, int(cfg.get("paste_restore_delay_ms", 300)))
    print("粘贴" + ("成功" if ok else "失败"))


def setup_logging(to_file=True):
    handlers = []
    if to_file:
        try:
            from logging.handlers import RotatingFileHandler
            handlers.append(RotatingFileHandler(LOG_PATH, maxBytes=2 << 20, backupCount=2,
                                                encoding="utf-8"))
        except Exception:
            pass
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler(sys.stderr))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers or [logging.NullHandler()],
    )


def single_instance():
    """已经有一个在跑就直接退出，避免开两份导致每次说话录两遍。"""
    try:
        import ctypes
        h = ctypes.windll.kernel32.CreateMutexW(None, False, "VoicePress_SingleInstance")
        if ctypes.windll.kernel32.GetLastError() == 183:   # ERROR_ALREADY_EXISTS
            return False, h
        return True, h
    except Exception:
        return True, None


def main():
    ap = argparse.ArgumentParser(description="长按左键说话 -> 转文字 -> 粘贴")
    ap.add_argument("--devices", action="store_true", help="列出麦克风设备")
    ap.add_argument("--focus", action="store_true", help="打印当前焦点控件")
    ap.add_argument("--transcribe", metavar="WAV", help="转写一个 wav 文件")
    ap.add_argument("--paste", metavar="TEXT", help="测试粘贴路径")
    ap.add_argument("--no-tray", action="store_true", help="不开托盘（前台跑）")
    ap.add_argument("--restore-cursor", action="store_true", help="强制还原系统光标（卡住时用）")
    ap.add_argument("--settings", action="store_true", help="单独打开设置面板")
    ap.add_argument("--preload", action="store_true", help="启动后台预加载模型")
    args = ap.parse_args()

    setup_logging()
    clean_proxy_env()

    if args.devices:
        return cmd_devices()
    if args.focus:
        return cmd_focus()
    if args.transcribe:
        return cmd_transcribe(args.transcribe)
    if args.paste:
        return cmd_paste(args.paste)
    if getattr(args, "restore_cursor", False):
        try:
            from syscursor import restore_system_cursors
            ok = restore_system_cursors()
            print("系统光标已重载" + ("" if ok else "（回退方式完成）"))
        except Exception as e:
            print("还原失败:", e)
        return
    if args.settings:
        import settings as settings_mod
        settings_mod.main()
        return

    ok, _mutex = single_instance()
    if not ok:
        log.warning("已经有一个 VoicePress 在运行，本次退出")
        print("VoicePress 已经在运行了")
        return 0

    # exe 版：模型没下来过就先走一次向导（VPR_FORCE_SETUP=1 可在源码模式下复现向导，便于测试）
    force_setup = os.environ.get("VPR_FORCE_SETUP") == "1"
    if paths.FROZEN or force_setup:
        try:
            import bootstrap
            if force_setup or not bootstrap.is_ready():
                if not bootstrap.run_wizard():
                    log.warning("首次运行初始化未完成")
                    return 0
        except Exception:
            log.error("首次运行引导失败", exc_info=True)

    cfg = load_config()
    if not os.path.exists(CONFIG_PATH):
        save_config(cfg)

    vp = VoicePress(cfg)
    vp.start()
    threading.Thread(target=watch_config, args=(vp,), daemon=True, name="vp-watch").start()

    if args.preload or cfg.get("preload", True):
        threading.Thread(target=vp.transcriber.preload, daemon=True).start()

    if args.no_tray:
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            vp.stop()
        return

    run_tray(vp)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.error("启动失败", exc_info=True)
        traceback.print_exc()
        sys.exit(1)
