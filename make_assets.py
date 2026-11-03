#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
生成 VoicePress 的光标图标库。

每个预设都输出两份东西：
  assets/cursors/<id>.cur   给系统用（SetSystemCursor 要真实文件）
  assets/cursors/<id>.png   给设置面板当缩略图

用法：
  python make_assets.py                 # 生成全部预设 + 兼容用的 assets/mic.cur
  python make_assets.py --size 20       # 只生成一支指定尺寸的话筒
  python make_assets.py --compare       # 额外输出与系统箭头的对比图
"""

import argparse
import ctypes
import os
import struct
import sys

from PIL import Image, ImageDraw

import paths

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = paths.ASSETS_DIR
CURSOR_DIR = os.path.join(ASSETS, "cursors")
BLUE = (32, 120, 255)

# 设置面板里可选的图标（顺序即展示顺序）
PRESETS = [
    {"id": "mic24", "label": "话筒 · 中", "style": "mic", "size": 24,
     "desc": "默认，比系统箭头小一号"},
    {"id": "mic20", "label": "话筒 · 小", "style": "mic", "size": 20,
     "desc": "最不打扰"},
    {"id": "mic28", "label": "话筒 · 大", "style": "mic", "size": 28,
     "desc": "接近箭头大小"},
    {"id": "mic_disc", "label": "话筒 · 蓝底", "style": "mic_disc", "size": 26,
     "desc": "蓝色圆底，最显眼"},
    {"id": "badge", "label": "箭头 + 红点", "style": "arrow_badge", "size": 32,
     "desc": "保持原光标，只挂个小点"},
    {"id": "arrow", "label": "不换光标", "style": "none", "size": 32,
     "desc": "只用光环动效"},
]

DEFAULT_PRESET = "mic24"


# ---------------------------------------------------------------- 绘制

def draw_mic(size=24):
    """白色话筒 + 黑色描边，和系统箭头同一套视觉语言。"""
    ss = 6
    W = size * ss
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = W / 2.0
    u = W / 24.0
    white, black = (255, 255, 255, 255), (0, 0, 0, 255)

    def glyph(color, grow):
        t = max(1, int(round(1.7 * u + 2 * grow)))
        d.rounded_rectangle([c - 2.7 * u - grow, c - 8.4 * u - grow,
                             c + 2.7 * u + grow, c - 0.6 * u + grow],
                            radius=2.7 * u + grow, fill=color)
        d.arc([c - 5.6 * u - grow, c - 4.4 * u - grow,
               c + 5.6 * u + grow, c + 3.6 * u + grow],
              start=0, end=180, fill=color, width=t)
        d.line([c, c + 3.6 * u - grow, c, c + 7.2 * u + grow], fill=color, width=t)
        d.line([c - 2.8 * u - grow, c + 7.2 * u, c + 2.8 * u + grow, c + 7.2 * u],
               fill=color, width=t)

    glyph(black, 0.85 * u)
    glyph(white, 0)
    return img.resize((size, size), Image.LANCZOS)


def draw_mic_disc(size=26):
    """蓝色实心圆 + 白色话筒，最抓眼球，也最"胖"。"""
    ss = 6
    W = size * ss
    img = Image.new("RGBA", (W, W), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    c = W / 2.0
    R = c - 0.5 * (W / size)
    d.ellipse([c - R, c - R, c + R, c + R], fill=(26, 110, 240, 255))
    u = W / 26.0
    white = (255, 255, 255, 255)
    d.rounded_rectangle([c - 3.0 * u, c - 7.4 * u, c + 3.0 * u, c + 0.6 * u],
                        radius=3.0 * u, fill=white)
    d.arc([c - 6.0 * u, c - 3.6 * u, c + 6.0 * u, c + 4.0 * u], start=0, end=180,
          fill=white, width=max(1, int(1.8 * u)))
    d.line([c, c + 4.0 * u, c, c + 7.6 * u], fill=white, width=max(1, int(1.8 * u)))
    d.line([c - 3.2 * u, c + 7.6 * u, c + 3.2 * u, c + 7.6 * u], fill=white,
           width=max(1, int(1.8 * u)))
    return img.resize((size, size), Image.LANCZOS)


def draw_arrow_badge(size=32):
    """原样箭头 + 右下角一个小蓝点，光标本体一点都不变大。"""
    src = _arrow_cursor_path()
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hotspot = (2, 2)
    if os.path.isfile(src):
        items = _read_cur(src)
        if items:
            picked = min(items, key=lambda t: abs(t[0] - size))
            arrow, hotspot = picked[4], (picked[2], picked[3])
            if arrow.size != (size, size):
                canvas = Image.new("RGBA", arrow.size, (0, 0, 0, 0))
            canvas.paste(arrow, (0, 0), arrow)

    ss = 6
    w, h = canvas.size
    d = ImageDraw.Draw(canvas)
    bx, by, r = w - 7.5, h - 7.5, 5.0
    d.ellipse([bx - r - 1, by - r - 1, bx + r + 1, by + r + 1], fill=(0, 0, 0, 235))
    d.ellipse([bx - r, by - r, bx + r, by + r], fill=(26, 110, 240, 255))
    return canvas, hotspot


def _arrow_cursor_path():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Control Panel\Cursors") as k:
            p = os.path.expandvars(str(winreg.QueryValueEx(k, "Arrow")[0]))
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "Cursors", "aero_arrow.cur")


def render_preset(preset):
    """返回 (PIL 图, 热点)；style=none 返回 (None, None)。"""
    style = preset.get("style", "mic")
    size = int(preset.get("size", 24))
    if style == "none":
        return None, None
    if style == "mic":
        return draw_mic(size), (size // 2, size // 2)
    if style == "mic_disc":
        return draw_mic_disc(size), (size // 2, size // 2)
    if style == "arrow_badge":
        return draw_arrow_badge(size)
    raise ValueError(f"未知样式: {style}")


# ---------------------------------------------------------------- 文件写入

def write_cur(path, img, hotspot):
    """
    写入 .cur：ICONDIR + ICONDIRENTRY + 32bpp 的 DIB（含 AND 掩码）。
    Pillow 只会写 .ico，所以这里按格式拼一遍。
    """
    w, h = img.size
    hx, hy = hotspot
    px = img.convert("RGBA").load()

    xor = bytearray()
    for y in range(h - 1, -1, -1):            # DIB 自下而上
        for x in range(w):
            r, g, b, a = px[x, y]
            xor += bytes((b, g, r, a))

    row_bytes = ((w + 31) // 32) * 4
    and_mask = bytearray()
    for y in range(h - 1, -1, -1):
        bits = bytearray(row_bytes)
        for x in range(w):
            if px[x, y][3] < 8:
                bits[x // 8] |= 0x80 >> (x % 8)
        and_mask += bits

    header = struct.pack("<IiiHHIIiiII", 40, w, h * 2, 1, 32, 0,
                         len(xor) + len(and_mask), 0, 0, 0, 0)
    dib = header + bytes(xor) + bytes(and_mask)
    icondir = struct.pack("<HHH", 0, 2, 1)
    entry = struct.pack("<BBBBHHII", w % 256, h % 256, 0, 0, hx, hy,
                        len(dib), 22)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(icondir + entry + dib)
    return path


def _read_cur(path):
    """读 .cur（含 PNG 压缩条目），返回 [(w, h, xhot, yhot, Image)]。"""
    import io
    data = open(path, "rb").read()
    _, _typ, count = struct.unpack("<HHH", data[:6])
    out = []
    for i in range(count):
        off = 6 + i * 16
        w, h, _cc, _res, xh, yh, nb, img_off = struct.unpack("<BBBBHHII", data[off:off + 16])
        w, h = (w or 256), (h or 256)
        blob = data[img_off:img_off + nb]
        if blob[:8] == b"\x89PNG\r\n\x1a\n":
            im = Image.open(io.BytesIO(blob)).convert("RGBA")
        else:
            _hs, bw, bh, _planes, _bpp = struct.unpack("<IiiHH", blob[:16])
            rows = abs(bh) // 2 if abs(bh) == 2 * h else abs(bh)
            px = blob[40:40 + bw * rows * 4]
            if len(px) < bw * rows * 4:
                continue
            im = Image.frombytes("RGBA", (bw, rows), px, "raw", "BGRA", 0,
                                 -1 if bh > 0 else 1)
        out.append((w, h, xh, yh, im))
    return out


# ---------------------------------------------------------------- 批量生成

def build_all(dest=CURSOR_DIR, thumb=44):
    """生成全部预设，返回 [{id,label,desc,cur,png}]。"""
    os.makedirs(dest, exist_ok=True)
    arrow_img = None
    try:
        src = _arrow_cursor_path()
        if os.path.isfile(src):
            items = _read_cur(src)
            if items:
                arrow_img = min(items, key=lambda t: abs(t[0] - 32))[4]
    except Exception:
        arrow_img = None

    built = []
    for preset in PRESETS:
        img, hotspot = render_preset(preset)
        item = dict(preset)
        cur_path = None
        if img is not None:
            cur_path = os.path.join(dest, preset["id"] + ".cur")
            write_cur(cur_path, img, hotspot)
        item["cur"] = cur_path

        # 缩略图：style=none 用系统箭头本身当预览，其它用画出来的光标
        preview = img if img is not None else arrow_img
        png_path = None
        if preview is not None:
            png_path = os.path.join(dest, preset["id"] + ".png")
            k = thumb / float(preview.width)
            big = preview.resize((thumb, thumb), Image.LANCZOS) if abs(k - 1) > 0.01 else preview
            pad = Image.new("RGBA", (thumb + 8, thumb + 8), (240, 242, 246, 255))
            pad.paste(big, ((pad.width - big.width) // 2, (pad.height - big.height) // 2), big)
            pad.save(png_path)
        item["png"] = png_path
        built.append(item)
    return built


def cursor_path(preset_id):
    """取某个预设的 .cur 路径；拿不到就退回默认话筒。"""
    if not preset_id:
        preset_id = DEFAULT_PRESET
    p = os.path.join(CURSOR_DIR, str(preset_id) + ".cur")
    if os.path.isfile(p):
        return p
    fallback = os.path.join(ASSETS, "mic.cur")
    return fallback if os.path.isfile(fallback) else p


def enable_dpi_awareness():
    u = ctypes.windll.user32
    try:
        if u.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass


def make_compare(mic_img):
    """把系统箭头和话筒并排画出来看大小（1:1 + 4 倍）。"""
    arrow_path = _arrow_cursor_path()
    if not os.path.isfile(arrow_path):
        print("找不到系统箭头，跳过对比图")
        return
    items = _read_cur(arrow_path)
    if not items:
        return
    arrow = min(items, key=lambda t: abs(t[0] - 32))[4]
    pad, gap, big = 24, 40, 128
    w = pad * 2 + arrow.width * 4 + gap + mic_img.width * 4
    h = pad * 2 + 40 + big + 30 + big
    canvas = Image.new("RGB", (w, h), (246, 246, 248))
    d = ImageDraw.Draw(canvas)
    canvas.paste(arrow, (pad, pad + 44), arrow)
    canvas.paste(mic_img, (pad + arrow.width + gap, pad + 52), mic_img)
    d.text((pad, pad + 20), "1:1 原尺寸（箭头 32px 框）", fill=(60, 60, 60))
    k = 4
    a_big = arrow.resize((arrow.width * k, arrow.height * k), Image.LANCZOS)
    m_big = mic_img.resize((mic_img.width * k, mic_img.height * k), Image.LANCZOS)
    y = pad + 40 + big + 20
    canvas.paste(a_big, (pad, y), a_big)
    canvas.paste(m_big, (pad + a_big.width + gap, y), m_big)
    d.text((pad, y - 18), "4 倍放大", fill=(60, 60, 60))
    out = os.path.join(ASSETS, "compare.png")
    canvas.save(out)
    print("对比图:", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--size", type=int, default=0, help="只生成一支指定尺寸的话筒")
    ap.add_argument("--compare", action="store_true", help="输出与系统箭头的对比图")
    args = ap.parse_args()
    enable_dpi_awareness()

    if args.size:
        size = max(16, min(64, args.size))
        img = draw_mic(size)
        os.makedirs(ASSETS, exist_ok=True)
        path = write_cur(os.path.join(ASSETS, "mic.cur"), img, (size // 2, size // 2))
        print(f"话筒 {size}px -> {path}")
        if args.compare:
            make_compare(img)
        return

    built = build_all()
    # 兼容旧版本：assets/mic.cur = 默认预设
    default = next((b for b in built if b["id"] == DEFAULT_PRESET), None)
    if default and default["cur"]:
        import shutil
        shutil.copyfile(default["cur"], os.path.join(ASSETS, "mic.cur"))
    for b in built:
        print(f"{b['id']:<10} {b['label']:<12} {b['cur'] or '(无文件)'}")
    print("图标库目录:", CURSOR_DIR)


if __name__ == "__main__":
    sys.exit(main())
