#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
把 faster-whisper-large-v3 的权重抓成本地模型目录。

不用 huggingface_hub：它在这台机器上（httpx + 本地代理）会 SSL EOF；而且 huggingface.co
在直连时不通、走代理只有 0.3~0.9MB/s。ModelScope 直连能到 13MB/s 左右，所以默认走那边。

用法：
  python fetch_model.py                    # 默认 -> models/large-v3
  python fetch_model.py --source hf        # 走 huggingface（需要代理）
  python fetch_model.py --source modelscope --repo pengzhendong/faster-whisper-large-v3
"""

import argparse
import os
import sys
import time

import requests

PROXY = os.environ.get("VPR_PROXY", "http://127.0.0.1:7897")
DEFAULT_MS = "pengzhendong/faster-whisper-large-v3"
DEFAULT_HF = "Systran/faster-whisper-large-v3"
SKIP = (".md", ".gitattributes", "README", ".jpg", ".png")


def session(proxy):
    s = requests.Session()
    s.trust_env = False                     # 绕开 NO_PROXY=[::1] 之类的坑
    if proxy:
        s.proxies = {"http": proxy, "https": proxy}
    return s


def list_modelscope(s, repo):
    r = s.get(f"https://www.modelscope.cn/api/v1/models/{repo}/repo/files",
              params={"Revision": "master", "Recursive": "true"}, timeout=30)
    r.raise_for_status()
    files = [f["Path"] for f in r.json().get("Data", {}).get("Files", [])]
    base = f"https://www.modelscope.cn/models/{repo}/resolve/master"
    return [(f, f"{base}/{f}") for f in files]


def list_hf(s, repo):
    r = s.get(f"https://huggingface.co/api/models/{repo}", timeout=30)
    r.raise_for_status()
    files = [f["rfilename"] for f in r.json().get("siblings", [])]
    base = f"https://huggingface.co/{repo}/resolve/main"
    return [(f, f"{base}/{f}") for f in files]


def download(s, name, url, dest_dir, attempts=10):
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
                    raise RuntimeError(f"http {r.status_code}")
                total = int(r.headers.get("Content-Length", 0))
                mode = "ab" if (have and r.status_code == 206) else "wb"
                if mode == "wb":
                    have = 0
                total += have
                t0 = time.time()
                done = have
                with open(part, mode) as f:
                    for chunk in r.iter_content(1 << 20):
                        if chunk:
                            f.write(chunk)
                            done += len(chunk)
                            if total:
                                sp = (done - have) / max(1e-6, time.time() - t0) / 1e6
                                print(f"\r  {name}: {done*100.0/total:5.1f}% "
                                      f"{done/1e6:7.1f}/{total/1e6:.1f}MB {sp:5.1f}MB/s",
                                      end="", flush=True)
            print()
            os.replace(part, path)
            return path
        except Exception as e:
            print(f"\n  {name}: 第 {attempt} 次失败 {type(e).__name__}: {str(e)[:100]}")
            if attempt == attempts:
                raise
            time.sleep(min(20, 2 * attempt))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["modelscope", "hf"], default="modelscope")
    ap.add_argument("--repo", default=None)
    ap.add_argument("--dest", default=os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                                   "models", "large-v3"))
    ap.add_argument("--proxy", default=PROXY)
    args = ap.parse_args()

    os.makedirs(args.dest, exist_ok=True)
    repo = args.repo or (DEFAULT_MS if args.source == "modelscope" else DEFAULT_HF)
    s = session(args.proxy if args.source == "hf" else None)
    items = list_modelscope(s, repo) if args.source == "modelscope" else list_hf(s, repo)
    items = [(n, u) for n, u in items if not n.endswith(SKIP)]
    print(f"{args.source}:{repo} -> {args.dest}")
    print("文件:", [n for n, _ in items])
    for name, url in items:
        t = time.time()
        p = download(s, name, url, args.dest)
        print(f"OK {name} ({os.path.getsize(p)/1e6:.1f}MB, {time.time()-t:.1f}s)")
    print("模型目录就绪:", args.dest)


if __name__ == "__main__":
    sys.exit(main())
