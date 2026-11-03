#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
端到端自测：模拟"长按左键 -> 录音 -> 转写 -> 粘贴"整条链路。

用一个 Tk 窗口当粘贴目标（比记事本可靠：商店版记事本从脚本启动会直接退出）。

  1. 弹一个输入框窗口并放到前台
  2. 用 pynput 在窗口中模拟按住左键 1.3 秒（超过 hold_ms）
  3. 同时用 winsound 外放一段中文 TTS，走"扬声器 -> 麦克风"的真实声学回路
  4. 松开后等它转写、Ctrl+V 粘贴
  5. 直接读 Tk 控件内容

用法: python e2e_test.py [wav路径]
"""

import ctypes
import os
import sys
import threading
import time
import tkinter as tk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "libs"))
sys.path.insert(0, HERE)

import voicepress as vp  # noqa: E402

WAV = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "test_zh.wav")
RESULT = {"ok": False, "text": ""}


def main():
    cfg = vp.load_config()
    cfg["only_in_text_field"] = False       # 自测窗口不是 EditControl，跳过焦点检查
    cfg["beep"] = True
    if os.environ.get("VPR_NO_CURSOR") == "1":   # 自测时可选：不动系统光标
        cfg["cursor_icon"] = False

    root = tk.Tk()
    root.title("VoicePress 自测 - 长按左键说话")
    root.geometry("760x320+380+240")
    tk.Label(root, text="下面这块会被自动粘贴，别手动输入", anchor="w").pack(fill="x", padx=8, pady=(8, 2))
    txt = tk.Text(root, font=("Microsoft YaHei", 14), wrap="word")
    txt.pack(fill="both", expand=True, padx=8, pady=8)
    root.attributes("-topmost", True)
    root.update()

    hwnd = int(ctypes.windll.user32.GetParent(root.winfo_id()))
    ctypes.windll.user32.SetForegroundWindow(hwnd)
    txt.focus_force()
    root.update()

    print("窗口就绪 hwnd =", hwnd)
    print("预加载模型 ...")
    app = vp.VoicePress(cfg)
    app.start()
    app.transcriber.preload()

    def worker():
        time.sleep(1.0)
        from pynput.mouse import Button, Controller
        m = Controller()
        cx = root.winfo_rootx() + root.winfo_width() // 2
        cy = root.winfo_rooty() + root.winfo_height() // 2
        m.position = (cx, cy)
        time.sleep(0.3)
        print(f"模拟长按左键 @ ({cx},{cy}) ...")
        m.press(Button.left)

        def play():
            import winsound
            try:
                winsound.PlaySound(WAV, winsound.SND_FILENAME)
            except Exception as e:
                print("外放失败:", e)

        threading.Thread(target=play, daemon=True).start()
        time.sleep(7.0)
        m.release(Button.left)
        print("已松开，等待转写/粘贴 ...")

    threading.Thread(target=worker, daemon=True).start()

    deadline = time.time() + 40

    def poll():
        content = txt.get("1.0", "end-1c").strip()
        if content:
            RESULT["text"] = content
            RESULT["ok"] = ("设置" in content or "字体" in content)
            root.after(200, root.destroy)
            return
        if time.time() > deadline:
            root.destroy()
            return
        root.after(500, poll)

    root.after(1500, poll)
    root.mainloop()

    app.stop()
    print("粘贴到的内容:", repr(RESULT["text"]))
    print("RESULT:", "PASS" if RESULT["ok"] else "FAIL")
    return 0 if RESULT["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
