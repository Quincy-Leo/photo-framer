<!-- SPDX-FileCopyrightText: 2026 QuincyLeo -->
<!-- SPDX-License-Identifier: GPL-3.0-or-later -->
# photo-framer

[English](README.en.md) · 简体中文

为照片添加一个美观的信息展示边框。画质保持完全无损。

![界面](assets/example-ui.png)

两种边框，在「外观 · 边框类型」里切换：

- **模糊背景**：照片放在自己的模糊放大版上，四周留白，底部排厂商型号和拍摄参数。六个滑块：边框宽度、圆角、阴影、模糊度、字号、文字明暗。
- **信息底框**：照片四边不留白，下方加一条白色或黑色的单色底框 —— 左边是 logo、拍摄参数、镜头型号，右边是相机型号、日期时间。三个滑块加一个开关：底框宽度、LOGO 大小、字号、底框颜色。

文字都按 EXIF 自动填充，也可以逐项手改。Nikon、DJI、FUJIFILM、Apple 的厂商名显示为矢量 logo。照片的像素、色彩描述和 EXIF 原样保留。

三种出图效果，都由 `assets/example.JPG` 生成（README中展示图缩小了尺寸）：

模糊背景，一行：

![一行](assets/example-one-line.jpg)

模糊背景，两行：

![两行](assets/example-two-line.jpg)

信息底框：

![信息底框](assets/example-bottom-border.jpg)

## 启动

```bash
cd photo-framer
./start.command              # 或 python3 server.py
./start.command photo.jpg    # 直接打开某张照片
```

浏览器会自动打开 `http://127.0.0.1:8787/?t=…`。关掉终端即退出。

只依赖 Pillow（≥ 10）。`start.command` 会在缺失时自动装。目前的字体路径与原生文件选择器是 macOS 专用的，换平台需要改 `framer.py` 里 `FontBook` 的字体常量和 `server.py` 里的 `native_pick()`。

浏览器预览使用长边 1024 的小图 JPEG 代理（约 40 ms，拖滑块跟手），导出永远从磁盘上的原图重新渲染。

## 已知边界

- **16-bit TIFF 会被截成 8-bit。** Pillow 读 16 bit/通道的 TIFF 时直接丢低字节，这是上游限制。工具会在信息栏用黄字提示。8-bit 的 JPEG/PNG/WebP 不受影响。
- **HEIC/HEIF 需要额外装一个包**：`pip3 install --user pillow-heif`，装完重启即可识别，不装会给出明确提示。
- **原图没有 ICC 时**按 sRGB 处理（输出也不写 ICC）。信息栏会标出来。
- **照片的 EXIF 被剥掉时**（很多修图 App 和分享渠道会把 Make/Model/光圈快门全删掉）文字自动填充会是空的，信息栏用黄字提示，手填即可。
- 边框、文字、阴影的颜色是直接写在原图的数值空间里的。如果原图是 Display P3，白就是 P3 白。

## 参数

参数会存在 localStorage，下次打开还在。`重置` 回到默认。

### 信息底框

| 控件 | 范围 | 说明 |
|---|---|---|
| 底框颜色 | 白 / 黑，默认白 | 文字颜色跟着自动取黑或白 |
| 底框宽度 | 0.5 – 5.0 ×，步进 0.1 | 1.0 是调好的基础宽度（照片宽度的 9.25%） |
| LOGO 大小 | 0.5 – 5.0 ×，步进 0.1 | 和文字上下平齐后不再变大，要更大得调大底框宽度 |
| 字号 | 0.5 – 5.0 ×，步进 0.1 | 两行一起缩放；排满整行后不再变大 |

四个文字框对应底框的四个位置：拍摄参数在左上、镜头型号在左下、相机型号在右上、日期时间在右下。清空哪一个，哪一行就不显示。尼康 Z 系列机身的 `Z` 会按尼康自己的写法显示。

没有 logo 的厂商（佳能、索尼等），左边直接从拍摄参数开始排。

### 模糊背景

六个滑块，外加文字内容和一个排版开关。更细的比例已经按视觉效果调好，不暴露出来。

| 控件 | 范围 | 说明 |
|---|---|---|
| 边框宽度 | 0.1 – 5.0 ×，步进 0.1 | 1.0 是调好的基础宽度（短边的 6.72%） |
| 圆角 | 0 – 100 | 0 是直角 |
| 阴影 | 0 – 100，默认 60 | 0 是没有阴影；浓度和扩散一起走 |
| 模糊度 | 3 – 100，默认 25 | 高斯模糊；刻度 ×0.12% 就是半径占短边的比例 |
| 字号 | 0.5 – 2.5 × | 两行一起缩放；1.0 是调好的基础字号 |
| 文字明暗 | 0 – 100，或自动 | 见下 |

### 边框

上、左、右三边等宽。下边框 = 文字高度 + 一个边框宽度，这一个边框宽度平分到文字上下，所以**文字在下边框里垂直居中，而文字上下的留白加起来正好等于其他三边的宽度**。

字号调大、或从两行切到一行，下边框会自动跟着变，不用手动配合。

以上长度数据都按照照片规格自动计算调整。

### 背景模糊

背景是原图自己放大 1.15 倍后居中裁切的版本，做**高斯模糊**。滑块刻度 × 0.12% 就是高斯半径占照片短边的比例 —— 默认 25 对应 3%（3450×2760 的照片约 83 px），下限 3 对应 0.36%。

## 文字

以下是**模糊背景**的文字规则；信息底框的四个文字框见上面的「信息底框」。厂商 logo、中文、文字明暗两种边框通用。

固定两组内容：**厂商 型号**（之间两个空格），和**焦距 | 光圈 | 快门 | ISO**（用 `|` 分隔）。曝光补偿不为 0 时会自动再接一项 `+0.3EV`；为 0 就不显示。默认两行，也可以切一行：

```
一行          NIKON  z5_2    35.0mm | f/5.6 | 1/1250 | ISO320 | +0.3EV

两行（默认）      NIKON  z5_2
       35.0mm | f/5.6 | 1/1250 | ISO320 | +0.3EV
```

厂商和型号**按 EXIF 里的原样显示**，不做大小写转换：EXIF 写 `Nikon` 就显示 `Nikon`，写 `FUJIFILM` 就显示 `FUJIFILM`。只有三处清理：厂商名里的公司后缀（`NIKON CORPORATION` → `NIKON`）、型号里重复的品牌前缀（`FUJIFILM X-T5` → `X-T5`），和尼康机身的代数写法（`Z50_2` → `Z50II`，机身上印的是后者）。四家厂商的名字会换成 logo，见下一节。

### 厂商 logo

Nikon、DJI、FUJIFILM、Apple 这四家，标题里的厂商名会显示成厂商 logo；其余厂商照常显示文字。

logo 是**矢量**的（SVG 放在 `assets/logos/`），每次渲染按当前需要的像素高度现场填充。大小和位置按排版规则对齐：

- 高度 = 旁边型号文字的**大写字高 × 1.2**（`framer.MARK_HEIGHT`）。
- logo 和文字画进同一张蒙版，所以**明暗、透明度、反向衬底全都和文字同步** —— 亮背景下文字变黑，logo 也跟着变黑。

> 声明：各个品牌对自家logo的颜色和使用场景有明确规定，本项目仅用于照片信息显示，非赢利非商用。

厂商信息由 `framer.resolve_mark()` 信息决定，EXIF 优先：

1. 先看 EXIF 的 `Make` 字段（清理过的厂商名，`NIKON CORPORATION` → `NIKON`）。
2. `Make` 为空就退回 `Model`。尼康和佳能会把品牌名重复写进 `Model`（`NIKON D850`）。

添加新厂商logo只需在 `logos.py` 的 `MARKS` 里加一行（SVG 文件名 + 三个对齐参数：高度、基线下沉、横向拉伸）。

### 字号

字号 = 照片短边 × 一个百分比 × 字号倍率，不是固定像素。一行和两行各有自己调好的比例：

| | 主标题 | 副标题 |
|---|---|---|
| 一行（默认） | 短边 × 3.38% | 短边 × 2.93% |
| 两行 | 短边 × 4.42% | 短边 × 2.86% |

换算成实际像素（一行、字号 1.0×）：

| 照片短边 | 主标题 | 副标题 |
|---|---|---|
| 2000 px | 68 px | 59 px |
| 4160 px | 141 px | 122 px |
| 6000 px | 203 px | 176 px |

### 中文

SF Pro 不含中文字形，所以标题里打中文会是一排占位符。本工具会逐字检测主字体有没有这个字形（渲染出来跟一个任何字体都没定义的码位比对），缺的字自动换 Hiragino Sans GB（W3/W6 对应常规/半粗），中英混排时两种字形共享同一条基线。

### 字距

统一加了 0.03em。Pillow 本身没有字距参数，所以是逐字排的，但纯西文时每个字的位置仍然取自整串的 `getlength`，字体自己的 kerning 没有被压平。

### 文字明暗

默认自动：取文字正下方那块背景的平均亮度，暗背景给**纯白**，亮背景给**纯黑**（阈值是 luma 0.55）。文字背后还有一层反向的柔和衬底（白字配暗衬、黑字配亮衬），所以压在细碎的模糊背景上也读得清。

拖动滑块即接管为手动，可以停在任意灰度；点右边的 `自动` 切回。自动模式下滑块会停在它选的那一端，所以看得见它判断成了什么。

## 输出位置

**单张导出交给浏览器下载**，落到浏览器自己的下载目录。文件名是 `<原名>_framed.png`。

传给浏览器的是渲染好的字节流，`Blob` 下载是逐字节拷贝 —— 中间没有任何一步重新编码。

**批量**是唯一写磁盘的路径：输出到所选文件夹下的 `framed/`，同名文件自动加 `-2`、`-3` 后缀，不覆盖。

## 批量

`批量处理整个文件夹…` 用当前参数处理一个目录下所有图片。确认框会列出来源、输出目录、格式，并明确说明文字是跟 EXIF 还是用手改的那份。ICC 和 EXIF 逐张保留。处理中可以取消。

## 快捷键

`⌘O` 打开照片 · `⌘S` 导出当前照片 · 照片也可以直接拖进窗口

## 测试

```bash
python3 tests/test_lossless.py   # 渲染内核：逐位一致性、边框规则、信息底框、EXIF/EV、字距、中文回退、厂商 logo、自动配色、极端参数
python3 tests/test_server.py     # HTTP 层：鉴权、预览、下载式导出、批量、keep-alive 分帧
```

`test_lossless.py` 用高频噪声图做素材 —— 任何一次重采样都会立刻暴露。边框那条规则（文字上下留白之和 == 其他三边宽度）、模糊下限、0 EV 不显示、自动配色只给纯黑或纯白、字号按比例缩放、中文走回退字体，都有断言守着。两个脚本都不需要外部素材，自己造图。

## 文件

| | |
|---|---|
| `framer.py` | 渲染内核。纯 Pillow，可单独 import 用在脚本里 |
| `logos.py` | 厂商 logo：SVG 路径解析 + 按需栅格化，同样只依赖 Pillow |
| `server.py` | 本地 HTTP 服务，只监听 127.0.0.1，所有 `/api/` 调用校验一次性 token |
| `static/` | 界面 |
| `assets/logos/` | 四家厂商的矢量 logo（SVG） |
| `assets/` | README 用的界面截图和出图示例，以及生成它们的原图 `example.JPG` |

`framer.py` 可以直接当库用：

```python
import framer
framer.render_file("a.jpg", framer.Params(border=1.5, radius=40, one_line=True), "png")
framer.render_file("a.jpg", framer.Params(style="bar", bar_color="black"), "png")
```

## 安全

服务只绑定 `127.0.0.1`，每次启动生成一次性 token，校验 `Host` 与 `Origin`，所以浏览器里的其他页面无法驱动它。但它确实能读写当前用户权限内的任意路径（文件选择器就是这么工作的）—— 它是个本地工具，不要把端口暴露到外网。

拖进窗口的照片会被复制一份到仓库下的 `.tmp/`（而不是系统临时目录，这样缓存不会散到别处），启动时清理自己复制进去的、超过一天的旧文件 —— 只清理自己生成的那些，放在同一个目录里的其他文件不会被动。`.gitignore` 保证它不进提交。想彻底清掉，`rm -rf .tmp`。

## 许可

GNU General Public License v3.0 或更高版本。完整条款见 [LICENSE](LICENSE)（gnu.org 的原文，未作任何改动）。

例外：`assets/logos/` 里的四个 SVG 来自 [Simple Icons](https://simpleicons.org)，按 CC0-1.0 提供（每个文件旁边的 `.license` 写明了出处）。logo 本身是各厂商的注册商标，这里只用来标注这张照片是谁家的相机拍的。

```
Copyright (C) 2026 QuincyLeo

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <https://www.gnu.org/licenses/>.
```
