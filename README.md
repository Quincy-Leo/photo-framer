<!-- SPDX-FileCopyrightText: 2026 QuincyLeo -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# photo-framer

[English](README.en.md) · 简体中文

为照片添加带有拍摄信息的边框，适合发布和展示。支持实时预览、单张导出和文件夹批量处理。

![界面](assets/example-ui.png)

## 功能

- **模糊背景**：在照片周围扩展模糊背景，并在底部展示厂商、型号和拍摄参数。
- **信息底框**：在照片下方添加白色或黑色信息栏，展示拍摄参数、镜头、相机型号和日期时间。
- **自动填充**：拍摄信息默认读取 EXIF，也可以逐项编辑。Nikon、DJI、FUJIFILM 和 Apple 支持显示厂商 logo。
- **导出**：支持 PNG 和 JPEG。照片主体保持原始构图，不缩放、不裁切。

![模糊背景，一行](assets/example-one-line.jpg)

![模糊背景，两行](assets/example-two-line.jpg)

![信息底框](assets/example-bottom-border.jpg)

## 使用

### 启动

需要 Python 3。启动脚本会在缺少 Pillow 时自动安装。

```bash
cd photo-framer
./start.command
```

也可以在启动时直接打开一张照片：

```bash
./start.command photo.jpg
```

启动后浏览器会打开本地界面，关闭终端即可退出。也可以直接运行：

```bash
python3 server.py
```

### 操作步骤

1. 将照片拖入窗口，或点击“打开…”选择照片。
2. 在“外观”中选择边框类型并调整边框、圆角、阴影、模糊和颜色等选项。
3. 在“文字”中调整排版、字号和文字内容。文字默认跟随 EXIF，也可以手动填写。
4. 选择 PNG 或 JPEG，点击“导出当前照片”。

设置会自动保留；点击“重置”可恢复默认值。

单张导出会保存到浏览器的默认下载目录，文件名为 `<原名>_framed.png` 或 `<原名>_framed.jpg`。

“批量处理整个文件夹…”会使用当前设置处理文件夹中的图片，并将结果保存到该文件夹下的 `framed/` 目录。已有同名文件不会被覆盖。

快捷键：`⌘O` 打开照片，`⌘S` 导出当前照片。

## 格式与平台

支持 JPEG、PNG、TIFF、WebP、AVIF 等常见图片格式。HEIC/HEIF 需要额外安装：

```bash
python3 -m pip install --user pillow-heif
```

文件选择器和批量处理使用 macOS 原生对话框。其他平台可以通过拖拽打开单张照片，暂不支持上述两项功能。

没有 EXIF 的照片不会自动生成拍摄信息，可以在“文字”中手动填写。

## 许可证

[GPL-3.0-or-later](LICENSE)
