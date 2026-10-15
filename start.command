#!/bin/bash
# SPDX-FileCopyrightText: 2026 QuincyLeo
# SPDX-License-Identifier: GPL-3.0-or-later
# 启动照片加边框工具。可选参数：要直接打开的照片路径。
cd "$(dirname "$0")" || exit 1

if ! python3 -c "import PIL" 2>/dev/null; then
  echo "缺少 Pillow，正在安装…"
  python3 -m pip install --user --quiet Pillow || {
    echo "安装失败，请手动执行: python3 -m pip install --user Pillow"
    exit 1
  }
fi

exec python3 -u server.py "$@"
