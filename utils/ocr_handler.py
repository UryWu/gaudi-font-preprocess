"""文本框检测 + 单字符识别

- detect_text_boxes / filter_boxes_by_size / merge_overlapping_boxes
  用于「文本框检测」（在原图上找字符位置），与切割布局配合
- recognize_character：单字符图片识别，/annotate 页 OCR 自动标注用
"""
import cv2
import numpy as np
from typing import Tuple


# === 字符识别（EasyOCR）===
# 懒加载：第一次调用时初始化 reader（含模型下载/加载，~15s）
# 之后用全局缓存，避免每张图都重载
_ocr_reader = None


def _get_ocr_reader():
    """获取（或初始化）EasyOCR reader。
    第一次调用耗时较长（模型下载/加载），但只发生一次。"""
    global _ocr_reader
    if _ocr_reader is None:
        import easyocr
        # ch_sim 简体 + en 英文；gpu=False 走 CPU（环境无 CUDA）
        _ocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    return _ocr_reader


# 置信度下限：低于此值视为识别失败（不填入标注）
# 0.2 是经验值——书法字经常被识别成"似是而非"的字，0.2 是个保守阈值
OCR_CONFIDENCE_THRESHOLD = 0.2


def recognize_character(image_path: str) -> Tuple[str, float]:
    """
    识别单张字符图片，返回 (字符, 置信度 0-1)

    算法：
    1. EasyOCR readtext 拿所有 text region
    2. 取置信度最高的那个
    3. 过滤：单字符 + 置信度 >= 0.2

    Args:
        image_path: 图片文件路径（建议是缩放后的 512x512 白底黑字图，识别率最高）

    Returns:
        (character, confidence)。无有效结果时 character='', confidence=0.0
    """
    reader = _get_ocr_reader()
    # paragraph=False 让 EasyOCR 返回每个 region；单字符图通常就 1 个 region
    # detail=1 返回 (bbox, text, confidence) 三元组
    results = reader.readtext(image_path, detail=1, paragraph=False)
    if not results:
        return '', 0.0

    # 选置信度最高的
    best = max(results, key=lambda r: r[2])
    text = best[1].strip()
    confidence = float(best[2])

    # 校验 1：必须是单字符（不接受 "ab" 这种多字符结果）
    if len(text) != 1:
        return '', confidence
    # 校验 2：置信度下限
    if confidence < OCR_CONFIDENCE_THRESHOLD:
        return '', confidence
    return text, confidence


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
