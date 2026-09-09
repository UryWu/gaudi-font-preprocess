"""文本框检测 + 单字符识别

- detect_text_boxes / filter_boxes_by_size / merge_overlapping_boxes
  用于「文本框检测」（在原图上找字符位置），与切割布局配合
- recognize_character：单字符图片识别，/annotate 页 OCR 自动标注用
"""
import cv2
import numpy as np
from typing import Tuple


# === 文本框检测（OpenCV 轮廓）===
def detect_text_boxes(img, min_area=100, max_area_ratio=0.3):
    """
    使用 OpenCV 轮廓检测识别文本框
    对于书法图片，这种方法比 OCR 更准确

    参数：
        img: 二值图（黑底白字）
        min_area: 最小文本框面积
        max_area_ratio: 最大文本框占图片面积的比例

    返回: list of dict, 每个包含 {x_min, x_max, y_min, y_max, width, height, center_x, center_y}
    """
    # 确保是二值图
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img

    # 膨胀操作，连接相邻的文字部分
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    dilated = cv2.dilate(gray, kernel, iterations=1)

    # 查找轮廓
    contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    img_area = gray.shape[0] * gray.shape[1]
    max_area = img_area * max_area_ratio

    boxes = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h

        # 过滤过大或过小的框
        if area < min_area or area > max_area:
            continue

        # 过滤太扁或太窄的框（不是正常的字）
        aspect_ratio = w / h if h > 0 else 0
        if aspect_ratio > 5 or aspect_ratio < 0.1:
            continue

        box_info = {
            'x_min': int(x),
            'x_max': int(x + w),
            'y_min': int(y),
            'y_max': int(y + h),
            'width': int(w),
            'height': int(h),
            'center_x': int(x + w / 2),
            'center_y': int(y + h / 2),
            'area': int(area)
        }
        boxes.append(box_info)

    return boxes


def filter_boxes_by_size(boxes, min_width=10, min_height=10, max_width_ratio=0.5, max_height_ratio=0.5, img_width=1, img_height=1):
    """按尺寸过滤文本框"""
    filtered = []
    for box in boxes:
        if box['width'] < min_width or box['height'] < min_height:
            continue
        if box['width'] > img_width * max_width_ratio:
            continue
        if box['height'] > img_height * max_height_ratio:
            continue
        filtered.append(box)
    return filtered


def merge_overlapping_boxes(boxes, overlap_threshold=0.5):
    """合并重叠的文本框"""
    if not boxes:
        return []

    # 按 x_min 排序
    sorted_boxes = sorted(boxes, key=lambda b: (b['x_min'], b['y_min']))

    merged = []
    used = set()

    for i, box in enumerate(sorted_boxes):
        if i in used:
            continue

        current = box.copy()
        used.add(i)

        # 查找重叠的框
        for j, other in enumerate(sorted_boxes):
            if j in used:
                continue

            # 计算重叠
            x_overlap = max(0, min(current['x_max'], other['x_max']) - max(current['x_min'], other['x_min']))
            y_overlap = max(0, min(current['y_max'], other['y_max']) - max(current['y_min'], other['y_min']))

            overlap_area = x_overlap * y_overlap
            smaller_area = min(current['width'] * current['height'], other['width'] * other['height'])

            if smaller_area > 0 and overlap_area / smaller_area > overlap_threshold:
                # 合并
                current['x_min'] = min(current['x_min'], other['x_min'])
                current['x_max'] = max(current['x_max'], other['x_max'])
                current['y_min'] = min(current['y_min'], other['y_min'])
                current['y_max'] = max(current['y_max'], other['y_max'])
                current['width'] = current['x_max'] - current['x_min']
                current['height'] = current['y_max'] - current['y_min']
                current['center_x'] = (current['x_min'] + current['x_max']) // 2
                current['center_y'] = (current['y_min'] + current['y_max']) // 2
                used.add(j)

        merged.append(current)

    return merged


# === 字符识别（RapidOCR）===
# RapidOCR = PaddleOCR 的 ONNX 移植版，绕开 paddlepaddle onednn bug
# 优势：
#   - 不依赖 paddlepaddle（pip install rapidocr-onnxruntime 即用）
#   - 模型走 onnxruntime，没有 Paddle 3.3.1 Windows 的 onednn C++ bug
#   - 速度比 EasyOCR 快 3-4 倍（0.3s/张 vs 1.5s/张）
#   - 中文准确率与 PaddleOCR 持平（>=0.99 高置信）
#
# 第一次调用会下载模型（det + cls + rec 三套共 ~50MB），下载后全局缓存
_rapidocr_engine = None


def _get_rapidocr():
    """懒加载 RapidOCR（首次 ~10s 含模型下载）"""
    global _rapidocr_engine
    if _rapidocr_engine is None:
        from rapidocr_onnxruntime import RapidOCR
        # Rec.lang='ch'：中英双语识别模型
        # Det.lang='ch'：中文检测模型
        # use_det=True / use_cls=True / use_rec=True：全流程
        # intra_op_num_threads：ONNX 推理线程数（默认 = CPU 核数，显式设更稳）
        _rapidocr_engine = RapidOCR(
            params={
                'Rec.lang': 'ch',
                'Det.lang': 'ch',
                'Det.use_dilation': False,
                'Det.box_thresh': 0.3,
                'intra_op_num_threads': 8,   # 多线程并行
                'inter_op_num_threads': 4,
            }
        )
    return _rapidocr_engine


# 置信度下限：低于此值视为识别失败（不填入标注）
# RapidOCR 在清晰图上经常 0.95+，这个阈值实际很少触发
OCR_CONFIDENCE_THRESHOLD = 0.2


def recognize_character(image_path: str) -> Tuple[str, float]:
    """
    识别单张字符图片，返回 (字符, 置信度 0-1)

    算法：
    1. RapidOCR 走全流程（检测+识别）
    2. 取置信度最高的结果
    3. 过滤：单字符 + 置信度 >= 0.2

    Args:
        image_path: 图片文件路径（建议是缩放后的 512x512 白底黑字图）

    Returns:
        (character, confidence)。无有效结果时 character='', confidence=0.0

    历史：
    - 早期用 EasyOCR（1.5s/张，34% 高置信）
    - 试过 PaddleOCR（paddlepaddle 3.3.1 onednn C++ bug 跑不通）
    - 现在用 RapidOCR（PaddleOCR 的 ONNX 移植，0.3s/张，~50% 高置信）
    - 详见 docs/PaddleOCR_调研记录.md
    """
    engine = _get_rapidocr()
    # RapidOCR 调用约定：直接传文件路径，返回 ((results, elapse),) 或 ((None, None),)
    # 其中 results = [[bbox, text, confidence], ...]
    output = engine(image_path)
    # 解包：output 可能是 ([results, elapse],) 或 (None,) 或其他
    if not output or output[0] is None:
        return '', 0.0
    results = output[0]
    if not results:
        return '', 0.0

    # 选置信度最高的
    best = max(results, key=lambda r: r[2])
    text = best[1].strip()
    confidence = float(best[2])

    # 校验 1：必须是单字符
    if len(text) != 1:
        return '', confidence
    # 校验 2：必须落在 CJK 基本平面（U+4E00–U+9FFF）或扩展 A（U+3400–U+4DBF）
    # 原因：RapidOCR mobile 模型对书法字（草书/异体字）经常误识别成英文/数字
    # （X/L/T/J/7/2/A/b 等），过滤掉避免污染标注
    code = ord(text[0])
    is_cjk = (0x4E00 <= code <= 0x9FFF) or (0x3400 <= code <= 0x4DBF)
    if not is_cjk:
        return '', confidence
    # 校验 3：置信度下限
    if confidence < OCR_CONFIDENCE_THRESHOLD:
        return '', confidence
    return text, confidence

