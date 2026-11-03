#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
VoicePress 设置面板。

托盘菜单"设置…"会以独立进程启动这个脚本（pythonw settings.py），
所以 Tk 只在主线程里跑，不会和托盘的线程互相干扰；保存后写 config.json，
主程序发现文件变了会自动热加载。

面板只放常用项（触发手感、光标图标、动效、麦克风、开机自启）；
模型路径、采样率这类一般不用动的仍然留在 config.json 里。
"""

import copy
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "libs"))
sys.path.insert(0, HERE)

import autostart  # noqa: E402
import make_assets  # noqa: E402

# Tk 也开 DPI 感知：不做的话窗口会被系统整体放大 1.25 倍，字是糊的
make_assets.enable_dpi_awareness()

BG = "#f7f8fa"
CARD = "#ffffff"
ACCENT = "#2078ff"
MUTED = "#6b7280"
THUMB = 42


class SettingsApp:
    def __init__(self, cfg, on_save=None, defaults=None):
        self.cfg = copy.deepcopy(cfg)
        self.defaults = copy.deepcopy(defaults or {})
        self.on_save = on_save
        self.root = None
        self.presets = []
        self._cells = []
        self._imgs = []

    # ---------------- 窗口

    def run(self):
        self.root = tk.Tk()
        self.root.title("VoicePress 设置")
        self.root.configure(bg=BG)
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._set_window_icon()
        try:
            self.presets = make_assets.build_all()
        except Exception:
            self.presets = list(make_assets.PRESETS)
        self._build()
        self._center()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(400, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()
        self.root.mainloop()
        return self

    def _set_window_icon(self):
        try:
            ico = os.path.join(make_assets.ASSETS, "app.ico")
            if not os.path.isfile(ico):
                img = make_assets.draw_mic(64).resize((64, 64), make_assets.Image.LANCZOS)
                img.save(ico, sizes=[(16, 16), (32, 32), (48, 48), (64, 64)])
            if os.path.isfile(ico):
                self.root.iconbitmap(default=ico)
        except Exception:
            pass

    def _center(self):
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        x, y = max(0, (sw - w) // 2), max(0, (sh - h) // 2 - 20)
        self.root.geometry(f"+{x}+{y}")

    def close(self):
        try:
            self.root.destroy()
        except Exception:
            pass

    # ---------------- 控件

    def _section(self, title):
        outer = tk.Frame(self.root, bg=BG)
        outer.pack(fill="x", padx=14, pady=(8, 0))
        tk.Label(outer, text=title, bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 9)).pack(anchor="w", pady=(0, 3))
        card = tk.Frame(outer, bg=CARD, highlightthickness=1, highlightbackground="#e3e6ea")
        card.pack(fill="x")
        return card

    def _int_row(self, parent, label, key, lo, hi, step=1, suffix="", hint=""):
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=12, pady=5)
        tk.Label(row, text=label, bg=CARD, width=12, anchor="w").pack(side="left")
        var = tk.StringVar(value=str(self.cfg.get(key, self.defaults.get(key, 0))))
        ttk.Spinbox(row, from_=lo, to=hi, increment=step, width=6,
                    textvariable=var).pack(side="left")
        if suffix:
            tk.Label(row, text=suffix, bg=CARD, fg=MUTED).pack(side="left", padx=(4, 0))
        if hint:
            tk.Label(row, text=hint, bg=CARD, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        setattr(self, f"v_{key}", var)
        return var

    def _bool_row(self, parent, label, key, hint=""):
        var = tk.BooleanVar(value=bool(self.cfg.get(key, self.defaults.get(key, False))))
        row = tk.Frame(parent, bg=CARD)
        row.pack(fill="x", padx=12, pady=4)
        ttk.Checkbutton(row, text=label, variable=var).pack(side="left")
        if hint:
            tk.Label(row, text=hint, bg=CARD, fg=MUTED,
                     font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        setattr(self, f"v_{key}", var)
        return var

    def _build(self):
        root = self.root
        tk.Label(root, text="VoicePress 设置", bg=BG,
                 font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", padx=16, pady=(12, 0))
        tk.Label(root, text="长按左键说话 → 语音转文字 → 粘贴到光标处",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 9)).pack(anchor="w", padx=16)

        s = self._section("触发")
        self._int_row(s, "长按时长", "hold_ms", 200, 1500, 50, "ms", "按住这么久才开录")
        self._int_row(s, "拖动容差", "move_tolerance_px", 2, 40, 1, "px", "移动超过就算拖拽")
        self._int_row(s, "麦克风预热", "prebuffer_ms", 0, 800, 50, "ms", "0 = 按下即开麦")
        self._bool_row(s, "只在输入框里触发", "only_in_text_field", "桌面上长按不录音")

        s = self._section("光标与动效")
        self._bool_row(s, "录音时替换光标", "cursor_icon")
        picker = tk.Frame(s, bg=CARD)
        picker.pack(fill="x", padx=12, pady=(0, 6))
        self.v_cursor_preset = tk.StringVar(
            value=self.cfg.get("cursor_preset", make_assets.DEFAULT_PRESET))
        for i, preset in enumerate(self.presets):
            self._cells.append(self._icon_cell(picker, preset, i))
        self._bool_row(s, "显示光环/音量条动效", "overlay")
        self._bool_row(s, "提示音", "beep")

        s = self._section("麦克风")
        row = tk.Frame(s, bg=CARD)
        row.pack(fill="x", padx=12, pady=6)
        tk.Label(row, text="输入设备", bg=CARD, width=12, anchor="w").pack(side="left")
        self.v_input_device = tk.StringVar(value=self.cfg.get("input_device") or "系统默认")
        ttk.Combobox(row, textvariable=self.v_input_device,
                     values=["系统默认"] + self._list_devices(),
                     width=28, state="readonly").pack(side="left")

        s = self._section("启动与其它")
        self.v_autostart = tk.BooleanVar(value=autostart.is_enabled())
        row = tk.Frame(s, bg=CARD)
        row.pack(fill="x", padx=12, pady=4)
        ttk.Checkbutton(row, text="开机自启动", variable=self.v_autostart).pack(side="left")
        tk.Label(row, text="写入 HKCU 的 Run 项", bg=CARD, fg=MUTED,
                 font=("Microsoft YaHei UI", 8)).pack(side="left", padx=(8, 0))
        self._int_row(s, "剪贴板还原", "paste_restore_delay_ms", 100, 1000, 50, "ms",
                      "粘贴后多久还原剪贴板")

        bar = tk.Frame(root, bg=BG)
        bar.pack(fill="x", padx=14, pady=12)
        ttk.Button(bar, text="保存并应用", command=self.save).pack(side="right")
        ttk.Button(bar, text="取消", command=self.close).pack(side="right", padx=(0, 8))
        ttk.Button(bar, text="恢复默认", command=self.reset_defaults).pack(side="left")

        tk.Label(root, text="模型、采样率等高级项仍在 config.json 里（托盘菜单 → 打开配置文件）",
                 bg=BG, fg=MUTED, font=("Microsoft YaHei UI", 8)).pack(anchor="w", padx=16, pady=(0, 8))

    def _icon_cell(self, parent, preset, index):
        col, row = index % 3, index // 3
        holder = tk.Frame(parent, bg=CARD)
        holder.grid(row=row, column=col, padx=3, pady=3, sticky="nsew")

        cell = tk.Frame(holder, bg="#f2f4f7", bd=0, highlightthickness=2,
                        highlightbackground="#f2f4f7", cursor="hand2")
        cell.pack(fill="both", expand=True)

        img = None
        png = preset.get("png")
        if png and os.path.isfile(png):
            try:
                img = tk.PhotoImage(file=png)
                self._imgs.append(img)
            except Exception:
                img = None
        thumb = tk.Label(cell, image=img, bg="#f2f4f7", bd=0,
                         width=THUMB, height=THUMB) if img else \
            tk.Label(cell, text="(无)", bg="#f2f4f7", width=6, height=2)
        if img:
            thumb.configure(image=img)
        thumb.image = img
        thumb.pack(pady=(4, 0))
        name = tk.Label(cell, text=preset["label"], bg="#f2f4f7",
                        font=("Microsoft YaHei UI", 8))
        name.pack(pady=(1, 4))

        def pick(_event=None, pid=preset["id"]):
            self.v_cursor_preset.set(pid)
            for c in self._cells:
                c["refresh"]()

        for w in (cell, thumb, name):
            w.bind("<Button-1>", pick)

        entry = {"id": preset["id"], "cell": cell, "thumb": thumb, "name": name}

        def refresh():
            on = self.v_cursor_preset.get() == preset["id"]
            bg = "#e8f0ff" if on else "#f2f4f7"
            line = ACCENT if on else "#f2f4f7"
            for w in (cell, thumb, name):
                w.configure(bg=bg)
            cell.configure(highlightbackground=line)

        entry["refresh"] = refresh
        refresh()
        return entry

    def _list_devices(self):
        try:
            import sounddevice as sd
            names = []
            for d in sd.query_devices():
                if d["max_input_channels"] > 0 and d["name"] not in names:
                    names.append(d["name"])
            return names
        except Exception:
            return []

    # ---------------- 动作

    def reset_defaults(self):
        for key, var in list(vars(self).items()):
            if not key.startswith("v_") or key in ("v_autostart", "v_input_device"):
                continue
            cfg_key = key[2:]
            if cfg_key == "cursor_preset":
                var.set(make_assets.DEFAULT_PRESET)
            elif cfg_key in self.defaults:
                var.set(self.defaults[cfg_key])
        self.v_autostart.set(False)
        self.v_input_device.set("系统默认")
        for c in self._cells:
            c["refresh"]()

    def _collect(self):
        cfg = copy.deepcopy(self.cfg)

        def as_int(name, fallback):
            try:
                return int(float(getattr(self, name).get()))
            except Exception:
                return fallback

        cfg["hold_ms"] = max(100, as_int("v_hold_ms", 600))
        cfg["move_tolerance_px"] = max(1, as_int("v_move_tolerance_px", 6))
        cfg["prebuffer_ms"] = max(0, as_int("v_prebuffer_ms", 250))
        cfg["paste_restore_delay_ms"] = max(50, as_int("v_paste_restore_delay_ms", 300))
        cfg["only_in_text_field"] = bool(self.v_only_in_text_field.get())
        cfg["cursor_icon"] = bool(self.v_cursor_icon.get())
        cfg["cursor_preset"] = self.v_cursor_preset.get()
        cfg["overlay"] = bool(self.v_overlay.get())
        cfg["beep"] = bool(self.v_beep.get())
        dev = self.v_input_device.get()
        cfg["input_device"] = "" if dev == "系统默认" else dev
        return cfg

    def save(self):
        cfg = self._collect()
        ok, msg = autostart.set_enabled(bool(self.v_autostart.get()))
        if not ok:
            messagebox.showwarning("开机自启", msg, parent=self.root)
        if self.on_save is not None:
            try:
                self.on_save(cfg)
            except Exception as e:
                messagebox.showerror("保存失败", str(e), parent=self.root)
                return
        self.close()


def open_settings(cfg, on_save=None, defaults=None):
    return SettingsApp(cfg, on_save=on_save, defaults=defaults).run()


def main():
    import voicepress as vp
    cfg = vp.load_config()

    def on_save(new_cfg):
        vp.save_config(new_cfg)

    open_settings(cfg, on_save=on_save, defaults=vp.DEFAULTS)


if __name__ == "__main__":
    sys.exit(main())
