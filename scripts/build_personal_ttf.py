#!/usr/bin/env python3
"""
从「导出训练包」的字形图合成一个 TTF 字体。

用途：把本工具导出的字形图（uniXXXX.png）打包成字体文件，供
[ai-font-tool](https://github.com/gaudi1209/ai-font-tool) 当 REF_FONT 使用
（它的 generate 流程要靠这个字体知道「你的字长什么样」）。

用法::

    uv sync --extra ttf              # 首次：装可选依赖 fonttools
    python scripts/build_personal_ttf.py --session <会话hash> --out <字体路径>

    # 例：拿最近一次导出的训练包，产出到隔壁 AI 工具项目
    python scripts/build_personal_ttf.py \
        --session c556f452bf5320f5aff1ccb8947e0cf7 \
        --out "../gaudi-ai-font-tool/font/my_personal_font.ttf" \
        --family "我的书法"

    # 也可以直接指定导出目录（不限于本项目的会话布局）
    python scripts/build_personal_ttf.py --export-dir <任意目录> --out x.ttf

设计要点
--------
1. **不依赖模板字体**。用 fontTools 官方 `FontBuilder` 从零建字，直接产出
   head/hhea/maxp/OS2/post/cmap/hmtx/glyf/loca 全套合法表。
   （早期一次性版本是拿 ai-font-tool 的 default.ttf 当骨架、只替换 glyf/cmap
   来绕开手搭表的坑；FontBuilder 更干净，也不再跨项目耦合。）
2. **度量默认对齐 ai-font-tool**：unitsPerEm=2048 / ascent=1638 / descent=-409
   （它的训练页按 2048 算 metrics）。可用参数覆盖。
3. **字宽 = 整个 em 框**（advance=unitsPerEm），并让 hmtx 的 lsb 等于字形 xMin。
   这是 CJK 字体的标准做法：每个字占一个等宽方格、不会溢出自己的步进框。
   —— 早期版本有两个度量缺陷（都已修，见 git 历史）：advance 按「墨迹宽度」设置
   而墨迹却居中在 1843 宽的绘制框里（实测 advance 1317 而墨迹跨 274..1569，
   排版会重叠）；lsb 写成 0（FreeType/PIL 按 lsb 定位，字形整体左移 xMin，
   实测偏 37~125px）。
4. **字形轮廓**：PNG 二值化 → 按列扫描最长连续黑段 → 每段一个 4 点闭合矩形。
   不依赖 potrace 等外部工具。
5. **同字多样本只取第一张**（uniXXXX_01/_02 这类后缀），避免同一码点重复占位。

已知限制
--------
- **轮廓呈阶梯状**：每列笔画都是矩形，放大看边缘是一级级的。改成
  `cv2.findContours` + `approxPolyDP` 能拿到真正的轮廓（且文件更小），
  本项目已依赖 OpenCV，属于可做的改进；当前保留矩形方案是因为它不需要
  调参、任何字形都不会失败。
- **无 hinting / GSUB 等扩展表**：屏幕小字号显示会略糊，作为训练用参考字体无碍。
- 152 个字形的字体约 1.4MB，主要体积就是矩形轮廓的点数。
"""
import argparse
import os
import re
import sys

from PIL import Image
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

# 导出训练包里的字形图尺寸（见 /annotate 的「导出训练包」）
PNG_SIZE = 512

# 会话布局：data/sessions/<hash>/exported/<时间戳>/
# 时间戳目录名规则与 app.py 的 _scan_session_exports() 保持一致
TS_PATTERN = re.compile(r"^\d{8}_\d{6}$")

# 导出文件名：uniXXXX.png（BMP）或 uXXXXX.png（扩展区），可能带 _01/_02 去重后缀
NAME_PATTERN = re.compile(r"^(uni([0-9A-F]{4})|u([0-9A-F]{5}))(?:_\d+)?\.png$")


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="从导出的字形图合成 TTF 字体",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="提示：不知道导出目录在哪时，用 --session 指定会话 hash 即可自动找最近一次导出。",
    )

    src = p.add_argument_group("输入")
    src.add_argument("--export-dir", help="导出训练包目录（含 uniXXXX.png / uXXXXX.png）")
    src.add_argument("--session", help="会话 hash；与 --export-dir 二选一，自动取该会话的导出目录")
    src.add_argument("--export-version",
                     help="配合 --session 指定具体导出批次（时间戳目录名）；默认取最新的一次")
    src.add_argument("--data-dir", default=None,
                     help="会话数据根目录，默认 <项目根>/data/sessions")

    out = p.add_argument_group("输出")
    out.add_argument("--out", required=True, help="输出的 .ttf 路径")

    font = p.add_argument_group("字体信息")
    font.add_argument("--family", default="我的书法", help="familyName（默认 我的书法）")
    font.add_argument("--style", default="Regular", help="styleName（默认 Regular）")
    font.add_argument("--ps-name", default="MyCalligraphy-Regular", help="PostScript 名")
    font.add_argument("--font-version", default="1.0",
                      help="字体版本号（写进 name 表）")
    font.add_argument("--copyright", default="Copyright UryWu")

    metric_args = p.add_argument_group("度量")
    metric_args.add_argument("--upm", type=int, default=2048,
                             help="unitsPerEm（默认 2048，与 ai-font-tool 的 default.ttf 一致）")
    metric_args.add_argument("--ascent", type=int, default=1638)
    metric_args.add_argument("--descent", type=int, default=-409)
    metric_args.add_argument("--canvas-ratio", type=float, default=0.9,
                             help="512 画布映射到 em 的比例（默认 0.9 → 画布对应 1843/2048 单位）。"
                                  "注意映射的是整张画布而不是字形墨迹——这样字形在格子里的"
                                  "位置会被保留（例如贴角的标点不会被重新居中）")
    metric_args.add_argument("--baseline-pad-ratio", type=float, default=0.05,
                             help="字形底边与 baseline 的距离占 em 的比例（默认 0.05）")

    ink = p.add_argument_group("图像")
    ink.add_argument("--ink", choices=("auto", "dark", "bright"), default="auto",
                     help="哪一侧是笔画：dark=白底黑字（导出包的默认形态）、"
                          "bright=黑底白字；auto 按整图明暗自动判断")

    return p.parse_args(argv)


def resolve_export_dir(args):
    """把 --export-dir / --session 解析成一个确定的导出目录"""
    if args.export_dir:
        d = os.path.abspath(args.export_dir)
        if not os.path.isdir(d):
            sys.exit(f"错误：导出目录不存在 → {d}")
        return d

    if not args.session:
        sys.exit("错误：需要 --export-dir 或 --session 指定输入（-h 看用法）")

    data_dir = args.data_dir or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "sessions")
    exported = os.path.join(data_dir, args.session, "exported")
    if not os.path.isdir(exported):
        sys.exit(f"错误：该会话没有导出目录 → {exported}")

    # 只认时间戳命名的子目录（与 app.py 的 _scan_session_exports 同一规则，
    # 这样 backup/ 之类的手工目录不会被误当成一次导出）
    ts_dirs = [d for d in os.listdir(exported)
               if TS_PATTERN.match(d) and os.path.isdir(os.path.join(exported, d))]
    if not ts_dirs:
        sys.exit(f"错误：导出目录下没有时间戳批次 → {exported}")

    if args.export_version:
        if args.export_version not in ts_dirs:
            sys.exit(f"错误：没有这一批导出 → {args.export_version}"
                     f"（现有：{', '.join(sorted(ts_dirs))}）")
        chosen = args.export_version
    else:
        # 默认取最新的一批（时间戳目录名可直接字符串比较，格式固定 YYYYMMDD_HHMMSS）
        chosen = sorted(ts_dirs)[-1]

    print(f"导出批次：{chosen}（{'指定' if args.export_version else '最新'}）")
    return os.path.join(exported, chosen)


def collect_entries(export_dir):
    """
    扫描导出目录，返回 [(码点, 图片路径)]，同码点只保留第一张。

    排序后遍历，保证 `uni7684.png` 排在 `uni7684_01.png` 之前 —— 也就是取
    「无后缀那张」（同字多样本里的第一张）。
    """
    seen = set()
    entries = []
    for fn in sorted(os.listdir(export_dir)):
        m = NAME_PATTERN.match(fn)
        if not m:
            continue
        # uniXXXX → BMP 区直接就是码点；uXXXXX → 扩展区，需 +0x10000
        cp = int(m.group(2), 16) if m.group(2) else int(m.group(3), 16) + 0x10000
        if cp in seen:
            continue
        seen.add(cp)
        entries.append((cp, os.path.join(export_dir, fn)))
    entries.sort()
    return entries


def glyph_name(cp):
    """FontLab 命名规范：BMP 区 uniXXXX，扩展区 uXXXXX"""
    return f"uni{cp:04X}" if cp <= 0xFFFF else f"u{cp - 0x10000:05X}"


def load_ink_mask(png_path, ink_mode):
    """
    读图并返回「笔画 = True」的布尔矩阵。

    ink_mode：dark=白底黑字（导出包默认形态，笔画是暗的）、bright=黑底白字。
    auto 时按整图平均明暗判断——导出包是白底黑字（均值偏亮），而 /scale 的
    中间产物是黑底白字（均值偏暗），两种都常见，自动判能少踩一次坑。
    """
    img = Image.open(png_path).convert("L")
    if img.size != (PNG_SIZE, PNG_SIZE):
        img = img.resize((PNG_SIZE, PNG_SIZE))

    if ink_mode == "auto":
        # 取均值即可：白底黑字整图偏亮，黑底白字偏暗。阈值 127 对两种情况都够稳
        avg = sum(img.getdata()) / float(PNG_SIZE * PNG_SIZE)
        ink_mode = "dark" if avg > 127 else "bright"

    # 用 256 项查找表做二值化（point 传 LUT 是 C 层实现，比传 lambda 快很多）
    if ink_mode == "dark":          # 白底黑字：暗的是笔画
        lut = [1] * 129 + [0] * 127
    else:                           # 黑底白字：亮的是笔画
        lut = [0] * 128 + [1] * 128
    return img.point(lut)


def build_glyph(mask, upm, canvas_units, baseline_pad):
    """
    把笔画掩码转成一个 TrueType 字形。

    做法：按列扫描，每列里每段连续笔画生成一个 4 点闭合矩形。不用 potrace
    这类描边工具，代价是轮廓呈阶梯状（见模块 docstring 的「已知限制」）。

    坐标系：图片左上角为原点、y 向下；字形坐标 y 向上、baseline 为 0。
    水平方向把**画布**（不是墨迹）居中并映射到 em 上——这样字形在格子里的
    相对位置会被保留（贴角的标点不会被挪到中间）。
    """
    px = mask.load()
    size = PNG_SIZE
    scale = canvas_units / float(size)

    # 先求墨迹包围盒（后续所有坐标都相对它算，这样字形贴着画布边也没关系）
    xs, ys = [], []
    for x in range(size):
        for y in range(size):
            if px[x, y]:
                xs.append(x)
                ys.append(y)
    if not xs:
        return None, 0   # 整图无笔画

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    ink_w = (x_max - x_min + 1) * scale

    # em 框内水平居中
    x_offset = (upm - ink_w) / 2.0

    pen = TTGlyphPen(None)
    for x in range(size):
        y = 0
        while y < size:
            while y < size and px[x, y] == 0:
                y += 1
            y_start = y
            while y < size and px[x, y] == 1:
                y += 1
            if y > y_start:
                x_left = (x - x_min) * scale + x_offset
                x_right = x_left + scale
                # 图片 y 向下 → 字形 y 向上：底边从 baseline_pad 起算
                y_bottom = baseline_pad + (y_start - y_min) * scale
                y_top = baseline_pad + (y - y_min) * scale
                pen.moveTo((x_left, y_bottom))
                pen.lineTo((x_right, y_bottom))
                pen.lineTo((x_right, y_top))
                pen.lineTo((x_left, y_top))
                pen.closePath()

    return pen.glyph()


def main(argv=None):
    args = parse_args(argv)

    export_dir = resolve_export_dir(args)
    entries = collect_entries(export_dir)
    if not entries:
        sys.exit(f"错误：目录里没找到 uniXXXX.png / uXXXXX.png → {export_dir}")
    print(f"输入目录：{export_dir}")
    print(f"唯一字符数：{len(entries)}")

    upm = args.upm
    canvas_units = int(upm * args.canvas_ratio)
    baseline_pad = int(upm * args.baseline_pad_ratio)

    # .notdef 必须有：字形 0 号、cmap 的 0 也指向它
    glyph_order = [".notdef"]
    glyphs = {".notdef": TTGlyphPen(None).glyph()}
    cmap = {0: ".notdef"}

    skipped = []
    for cp, path in entries:
        name = glyph_name(cp)
        mask = load_ink_mask(path, args.ink)
        glyph = build_glyph(mask, upm, canvas_units, baseline_pad)
        if glyph is None:
            skipped.append(os.path.basename(path))
            continue
        glyphs[name] = glyph
        glyph_order.append(name)
        cmap[cp] = name

    if skipped:
        print(f"跳过 {len(skipped)} 张空白图：{', '.join(skipped[:5])}"
              + (" …" if len(skipped) > 5 else ""))
    if len(glyphs) <= 1:
        sys.exit("错误：所有图都是空白，没生成任何字形")

    print(f"生成字形：{len(glyphs) - 1} 个（+ .notdef）")

    # === 建字体 ===
    fb = FontBuilder(upm, isTTF=True)
    fb.setupGlyphOrder(glyph_order)
    fb.setupCharacterMap(cmap)
    fb.setupGlyf(glyphs)    # 这一步会算好每个字形的包围盒（xMin/xMax/yMin/yMax）

    # hmtx：advance 统一取整个 em 框（CJK 字体每字占一个等宽方格，墨迹已居中其中，
    # 不会溢出步进框）；**lsb 必须等于该字形的 xMin**（TrueType 惯例）。
    # 把 lsb 写成 0 会让 FreeType/PIL 这类渲染器按 lsb 定位，字形整体左移 xMin ——
    # 实测：lsb=0 时 PIL 渲染的墨迹左边界恒等于笔位（本例偏了 37~125px），
    # 而 lsb==xMin 的 default.ttf 墨迹正好落在 xMin 处。
    metrics = {}
    for name in glyph_order:
        g = glyphs[name]
        metrics[name] = (upm, getattr(g, "xMin", 0) or 0)
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=args.ascent, descent=args.descent)
    fb.setupNameTable({
        "familyName": args.family,
        "styleName": args.style,
        "psName": args.ps_name,
        "fullName": f"{args.family} {args.style}",
        "version": args.font_version,
        "copyright": args.copyright,
    })
    fb.setupOS2(
        sTypoAscender=args.ascent,
        sTypoDescender=args.descent,
        usWinAscent=args.ascent,
        usWinDescent=abs(args.descent),
    )
    # keepGlyphNames 默认 True → post 写 format 2.0，保留 uniXXXX 字形名。
    # 若写成 format 3.0，字体里就只剩 glyph00001 这类编号名了
    fb.setupPost()
    fb.setupMaxp()

    out_path = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fb.save(out_path)
    print(f"已写出：{out_path}（{os.path.getsize(out_path):,} bytes）")

    # === 回读校验：写完立刻用 fontTools 重新加载一遍，确认是合法字体 ===
    verify(out_path)
    return 0


def verify(ttf_path):
    """回读校验：字形数、cmap、命名、度量是否都符合预期"""
    from fontTools.ttLib import TTFont
    f = TTFont(ttf_path)
    order = f.getGlyphOrder()
    names_ok = all(n == ".notdef" or n.startswith(("uni", "u")) for n in order)
    # lsb 必须等于 xMin，否则渲染器（FreeType/PIL 等）会把字形整体左移，见 main 里的说明
    lsb_bad = [n for n in order
               if f["glyf"][n].numberOfContours > 0 and f["hmtx"][n][1] != f["glyf"][n].xMin]
    print("\n校验（重新加载该字体）：")
    print(f"  family / style : {f['name'].getBestFamilyName()} / {f['name'].getBestSubFamilyName()}")
    print(f"  unitsPerEm     : {f['head'].unitsPerEm}")
    print(f"  ascent/descent : {f['hhea'].ascent} / {f['hhea'].descent}")
    print(f"  字形数         : {len(order)}（含 .notdef）")
    print(f"  cmap 码点数    : {len(f['cmap'].tables[0].cmap)}")
    print(f"  字形名规范     : {'✓' if names_ok else '✗'}（前几个：{', '.join(order[:4])}）")
    print(f"  字宽           : {sorted({f['hmtx'][n][0] for n in order[1:]})}")
    print(f"  lsb == xMin    : {'✓' if not lsb_bad else '✗ 异常字形 ' + ', '.join(lsb_bad[:3])}")


if __name__ == "__main__":
    sys.exit(main())
