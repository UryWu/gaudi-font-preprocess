"""缩放校正处理模块 - 字符标准化处理

功能：
1. 检测字符边框（边缘腐蚀 + 轮廓检测）
2. 字符居中（上下黑边相等，左右黑边相等）
3. 字符缩放（按目标高度归一 + 宽字保护）
4. 输出标准化（512x512）

算法版本（用于 /api/save_scaled 自动重生成判定）：
- v1：旧版「×scale」—— 只按固定倍率放大，只防爆框，不同字大小差异大
- v2：当前——「按目标高度归一」—— 所有字统一高度，宽度按字形自然变

见 docs/任务书_页面3_缩放校正.md
"""

import cv2
import numpy as np
import os
from typing import Tuple, Optional


# 算法版本号：scale_char 行为变更时 +1
# /api/save_scaled 看到 session.version < 当前值就主动重跑所有字符
#
# ⚠️ 2026-09-13 的包围盒修复（detect_char_bbox 去腐蚀，不再切掉墨迹边缘）
#    **故意没有 +1**：用户当时正在手工整理 scaled/ 目录，一升版本号下次点「保存」
#    就会按新算法把全部字符重生成、覆盖他的手工成果。
#    所以 v2 目前同时代表两种略有差异的行为：
#      v2-旧：包围盒带腐蚀 → 每个字都切掉一点墨（切墨中位 2.8%、最大 30.6%）
#      v2-新：包围盒精确 → 807/821 一个字都不切，其余只丢 1~2 像素噪点
#    要区分二者只能比对产物（例如看墨迹是否贴合裁切框），版本号本身分不出来。
#    若将来需要强制全体重生成，把这里 +1 即可。
ALGORITHM_VERSION = 2


def detect_char_bbox(image: np.ndarray) -> Tuple[int, int, int, int]:
    """
    检测字符的边界框（精确覆盖全部墨迹，不切边）

    算法变更（2026-09-13）：原实现是「先 3×3 腐蚀 → 阈值 127 → 找轮廓」。
    腐蚀会把抗锯齿/较淡的笔画压到 127 以下而**整条消失**，于是包围盒比真实墨迹
    小一圈，裁到它就把字最外圈的笔画切掉了。实测某会话 821 个字**无一例外**全部
    中招（切墨中位 2.8%、最大 30.6%）。

    现在改为：纯阈值求墨迹 → 连通域按像素数剔除极小噪点 → 取联合包围盒。
    腐蚀原本想解决的「去噪点」改由连通域过滤承担，不再误伤笔画。

    为什么用连通域像素数而不是 cv2.contourArea：contourArea 量的是「围起来的
    面积」，一条 1 像素宽的细笔画围出的面积接近 0，会被误判成噪点丢掉。

    Args:
        image: 输入图片（二值图，黑底白字）

    Returns:
        (x, y, width, height) 字符边界框
    """
    if image is None or image.size == 0:
        return 0, 0, 0, 0

    # 确保是灰度图
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # 二值化：纯阈值，**不腐蚀**（腐蚀正是「切边」的元凶）
    _, binary = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)

    # 连通域统计（connectivity=8：斜向相接的笔画算同一块）
    # stats 的第 4 列是该连通域的**像素数**（不是围起来的面积）
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n <= 1:
        # 只有背景 → 没有墨迹；沿用原行为「整图当包围盒」
        return 0, 0, gray.shape[1], gray.shape[0]

    areas = stats[1:, 4]
    # 去噪点：丢掉「不足 4 像素」或「不到最大连通域 0.5%」的孤立小块。
    # 用相对阈值是为了适配不同分辨率；0.5% 既足以滤掉噪点，又不会误删真笔画
    keep = [i + 1 for i, a in enumerate(areas) if a >= max(4, areas.max() * 0.005)]
    if not keep:
        keep = [1 + int(np.argmax(areas))]

    ys, xs = np.where(np.isin(labels, keep))
    x0, y0 = int(xs.min()), int(ys.min())
    x1, y1 = int(xs.max()), int(ys.max())
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def center_char_in_canvas(
    image: np.ndarray,
    target_size: int = 512,
    align: str = 'center',
    background: str = 'black'
) -> np.ndarray:
    """
    将字符居中放置在指定尺寸的画布中

    Args:
        image: 输入图片（二值图，黑底白字）
        target_size: 目标尺寸（正方形）
        align: 对齐方式 ('center', 'top', 'baseline')
        background: 背景方式 ('black', 'transparent')

    Returns:
        处理后的图片
    """
    if image is None or image.size == 0:
        # 返回空画布
        if background == 'transparent':
            return np.zeros((target_size, target_size, 4), dtype=np.uint8)
        else:
            return np.zeros((target_size, target_size), dtype=np.uint8)

    # 确保是灰度图
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # 检测字符边界框
    x, y, w, h = detect_char_bbox(gray)

    if w == 0 or h == 0:
        # 没有检测到字符，返回空画布
        if background == 'transparent':
            return np.zeros((target_size, target_size, 4), dtype=np.uint8)
        else:
            return np.zeros((target_size, target_size), dtype=np.uint8)

    # 裁剪字符区域
    char_region = gray[y:y+h, x:x+w]

    # 创建目标画布
    if background == 'transparent':
        canvas = np.zeros((target_size, target_size, 4), dtype=np.uint8)
        is_rgba = True
    else:
        canvas = np.zeros((target_size, target_size), dtype=np.uint8)
        is_rgba = False

    # 计算放置位置（居中）
    # 上下黑边相等，左右黑边相等
    left_padding = (target_size - w) // 2
    top_padding = (target_size - h) // 2

    # 根据对齐方式调整
    if align == 'top':
        top_padding = 10  # 顶部留少量边距
    elif align == 'baseline':
        # 基线对齐：底部对齐
        top_padding = target_size - h - 10

    # 确保不越界
    right_padding = target_size - left_padding - w
    bottom_padding = target_size - top_padding - h

    if left_padding < 0:
        left_padding = 0
    if top_padding < 0:
        top_padding = 0

    # 放置字符到画布
    end_x = min(left_padding + w, target_size)
    end_y = min(top_padding + h, target_size)

    char_w = end_x - left_padding
    char_h = end_y - top_padding

    if is_rgba:
        # 透明背景：将白色字符复制到 RGBA
        canvas[top_padding:end_y, left_padding:end_x, 0] = char_region[:char_h, :char_w]
        canvas[top_padding:end_y, left_padding:end_x, 1] = char_region[:char_h, :char_w]
        canvas[top_padding:end_y, left_padding:end_x, 2] = char_region[:char_h, :char_w]
        # Alpha 通道：白色部分不透明，黑色部分透明
        _, alpha = cv2.threshold(char_region[:char_h, :char_w], 127, 255, cv2.THRESH_BINARY)
        canvas[top_padding:end_y, left_padding:end_x, 3] = alpha
    else:
        canvas[top_padding:end_y, left_padding:end_x] = char_region[:char_h, :char_w]

    return canvas


def scale_char(
    image: np.ndarray,
    scale: float = 1.0,
    target_size: int = 512,
    align: str = 'center',
    background: str = 'black',
    fill_ratio: float = 0.9,
    max_width_ratio: float = 0.95,
) -> np.ndarray:
    """
    缩放字符并输出标准化尺寸（按目标高度归一）

    算法说明（v2）：
        1. 检测字符 bbox (w, h)
        2. 目标高度 = target_size * fill_ratio * scale
        3. scale_factor = target_h / h（按高度归一）
        4. 宽字保护：若 new_w > target_size * max_width_ratio，再按宽压（保持比例）
        5. 居中放在 512x512 画布

    与 v1（×1.15 + 仅防爆框）区别：v1 让大字符大、小字符小；v2 让所有字统一高度。

    Args:
        image: 输入图片
        scale: 目标高度倍数（0.5–1.5，默认 1.0）。
               1.0 = 填满 fill_ratio（默认 0.9×512 = 460px 高）；
               1.5 = 1.35×512 = 691，但会被 fill_ratio 上限压回 460。
               注：旧版叫「缩放比例」= 「字 × 1.15」，语义已变。
        target_size: 目标画布尺寸
        align: 对齐方式。
            'center'（默认）居中；'top' 贴上边；'baseline' 贴下边。
            另有四个角对齐：'top-left' / 'top-right' / 'bottom-left' / 'bottom-right'，
            贴边留白同为 2% 画布宽（512 → 10px）。角对齐是给标点用的——
            「；」要靠左下、「“」靠右上、「”」靠左上，居中反而不符合字库排版。
        background: 背景方式
        fill_ratio: 字符填满画布的比例（默认 0.9 = 460/512）
        max_width_ratio: 超宽字保护的宽度上限（默认 0.95）

    Returns:
        处理后的图片
    """
    if image is None or image.size == 0:
        return center_char_in_canvas(image, target_size, align, background)

    # 确保是灰度图
    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image.copy()

    # 检测字符边界框
    x, y, w, h = detect_char_bbox(gray)

    if w == 0 or h == 0:
        return center_char_in_canvas(None, target_size, align, background)

    # 裁剪字符区域
    char_region = gray[y:y+h, x:x+w]

    # === 新算法（v2）：按目标高度归一 ===
    # 目标高度 = canvas * fill_ratio * scale（scale 是用户滑块）
    # 例：target_size=512, fill=0.9, scale=1.0 → target_h = 460
    # 例：scale=1.5 → target_h = 691，但仍然受 fill_ratio 实际意义约束
    #     （即实际生效高度不超过 fill_ratio * target_size）
    target_h_raw = target_size * fill_ratio * scale
    target_h = min(target_h_raw, target_size * fill_ratio)  # 高度上限：fill_ratio * target_size

    # scale_factor = 目标高度 / 实际高度 —— 统一所有字的高度
    scale_factor = target_h / h if h > 0 else 1.0
    new_h = int(round(h * scale_factor))
    new_w = int(round(w * scale_factor))

    # 宽字保护：归一化后宽度超过 max_width_ratio * canvas 时按宽再压一次
    # 例：fill=0.9, scale=1.0, max_w=0.95 → 宽字符最多占 95% 画布宽
    max_w_px = target_size * max_width_ratio
    if new_w > max_w_px:
        ratio = max_w_px / new_w
        new_w = int(round(new_w * ratio))
        new_h = int(round(new_h * ratio))

    # 缩放字符
    if new_w > 0 and new_h > 0:
        scaled_char = cv2.resize(char_region, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    else:
        scaled_char = char_region

    # 创建目标画布
    if background == 'transparent':
        canvas = np.zeros((target_size, target_size, 4), dtype=np.uint8)
        is_rgba = True
    else:
        canvas = np.zeros((target_size, target_size), dtype=np.uint8)
        is_rgba = False

    # 计算放置位置
    # pad = 贴边时的最小留白，沿用 top / baseline 一直使用的 2% 画布宽（512 → 10px）
    pad = max(5, int(target_size * 0.02))

    # 水平：默认居中；'*-left' / '*-right' 系列贴左右边
    # （标点如「；」要靠左下角、「“」靠右上角，居中反而不对）
    if align.endswith('-left'):
        left_padding = pad
    elif align.endswith('-right'):
        left_padding = target_size - new_w - pad
    else:
        left_padding = (target_size - new_w) // 2

    # 垂直：默认居中；'top' / 'top-*' 贴上边，'baseline' / 'bottom-*' 贴下边
    if align == 'top' or align.startswith('top-'):
        top_padding = pad
    elif align == 'baseline' or align.startswith('bottom'):
        top_padding = target_size - new_h - pad
    else:
        top_padding = (target_size - new_h) // 2

    # 确保不越界
    left_padding = max(0, left_padding)
    top_padding = max(0, top_padding)

    end_x = min(left_padding + new_w, target_size)
    end_y = min(top_padding + new_h, target_size)

    char_w = end_x - left_padding
    char_h = end_y - top_padding

    if is_rgba:
        # 透明背景
        canvas[top_padding:end_y, left_padding:end_x, 0] = scaled_char[:char_h, :char_w]
        canvas[top_padding:end_y, left_padding:end_x, 1] = scaled_char[:char_h, :char_w]
        canvas[top_padding:end_y, left_padding:end_x, 2] = scaled_char[:char_h, :char_w]
        _, alpha = cv2.threshold(scaled_char[:char_h, :char_w], 127, 255, cv2.THRESH_BINARY)
        canvas[top_padding:end_y, left_padding:end_x, 3] = alpha
    else:
        canvas[top_padding:end_y, left_padding:end_x] = scaled_char[:char_h, :char_w]

    return canvas


def process_character(
    image_path: str,
    output_path: str,
    scale: float = 1.0,
    target_size: int = 512,
    align: str = 'center',
    background: str = 'black',
    fill_ratio: float = 0.9,
    max_width_ratio: float = 0.95,
) -> bool:
    """
    处理单个字符图片（v2 算法：按目标高度归一）

    Args:
        image_path: 输入图片路径
        output_path: 输出图片路径
        scale: 目标高度倍数（0.5–1.5，默认 1.0）
        target_size: 目标尺寸
        align: 对齐方式
        background: 背景方式
        fill_ratio: 字符填满画布的比例（默认 0.9）
        max_width_ratio: 超宽字保护的宽度上限（默认 0.95）

    Returns:
        是否成功
    """
    try:
        # 读取图片
        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            return False

        # 处理（v2 算法）
        result = scale_char(
            image, scale, target_size, align, background,
            fill_ratio=fill_ratio, max_width_ratio=max_width_ratio,
        )

        # 保存
        cv2.imwrite(output_path, result)

        return True
    except Exception as e:
        print(f"处理字符失败: {e}")
        return False


def apply_adjustments(
    image: np.ndarray,
    adjust_top: int = 0,
    adjust_bottom: int = 0,
    adjust_left: int = 0,
    adjust_right: int = 0
) -> np.ndarray:
    """
    应用调整值裁剪图片

    Args:
        image: 输入图片
        adjust_top: 上边距调整
        adjust_bottom: 下边距调整
        adjust_left: 左边距调整
        adjust_right: 右边距调整

    Returns:
        裁剪后的图片
    """
    if image is None or image.size == 0:
        return image

    # 确保所有参数都是整数
    adjust_top = int(adjust_top or 0)
    adjust_bottom = int(adjust_bottom or 0)
    adjust_left = int(adjust_left or 0)
    adjust_right = int(adjust_right or 0)

    h, w = image.shape[:2]

    # 计算裁剪区域
    y1 = adjust_top
    y2 = h - adjust_bottom
    x1 = adjust_left
    x2 = w - adjust_right

    # 确保不越界
    y1 = max(0, y1)
    y2 = min(h, y2)
    x1 = max(0, x1)
    x2 = min(w, x2)

    if y2 <= y1 or x2 <= x1:
        return image

    return image[y1:y2, x1:x2]


def process_character_with_adjust(
    image_path: str,
    output_path: str,
    adjust_top: int = 0,
    adjust_bottom: int = 0,
    adjust_left: int = 0,
    adjust_right: int = 0,
    scale: float = 1.0,
    target_size: int = 512,
    align: str = 'center',
    background: str = 'black',
    fill_ratio: float = 0.9,
    max_width_ratio: float = 0.95,
) -> bool:
    """
    处理单个字符图片（带调整值）

    Args:
        image_path: 输入图片路径
        output_path: 输出图片路径
        adjust_top/bottom/left/right: 调整值
        scale: 目标高度倍数（0.5–1.5，默认 1.0）。
               详见 scale_char 的 scale 参数说明。
        target_size: 目标尺寸
        align: 对齐方式
        background: 背景方式
        fill_ratio: 字符填满画布的比例（默认 0.9）
        max_width_ratio: 超宽字保护的宽度上限（默认 0.95）

    Returns:
        是否成功
    """
    try:
        # 读取图片
        image = cv2.imread(image_path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            return False

        # 应用调整值
        adjusted = apply_adjustments(
            image, adjust_top, adjust_bottom, adjust_left, adjust_right
        )

        # 处理（v2 算法：按目标高度归一）
        result = scale_char(
            adjusted, scale, target_size, align, background,
            fill_ratio=fill_ratio, max_width_ratio=max_width_ratio,
        )

        # 保存
        cv2.imwrite(output_path, result)

        return True
    except Exception as e:
        print(f"处理字符失败: {e}")
        return False
