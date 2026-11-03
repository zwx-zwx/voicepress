#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
打包入口（PyInstaller 用这个当 entry）。

单独拆一个文件，是为了让 exe 里 `import voicepress` 拿到的是同一个模块实例，
而不是把 __main__ 再当模块执行一遍。
"""

import sys

import voicepress


if __name__ == "__main__":
    sys.exit(voicepress.main())
