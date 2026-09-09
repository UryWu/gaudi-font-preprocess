"""文本框检测 + 单字符识别

- detect_text_boxes / filter_boxes_by_size / merge_overlapping_boxes
  用于「文本框检测」（在原图上找字符位置），与切割布局配合
- recognize_character：单字符图片识别，/annotate 页 OCR 自动标注用
"""
import cv2
import numpy as np
from typing import Tuple


# === 字符识别（双引擎：PaddleOCR 优先，EasyOCR 备份） ===
# - PaddleOCR：中文专用，准确率比 EasyOCR 高 ~15-20 个百分点（实测）
# - EasyOCR：通用，依赖轻量；PaddleOCR 不可用时自动降级
#
# 切换方式：
#   - 默认 PaddleOCR（如果 paddleocr 装好了）
#   - 环境变量 OCR_ENGINE=easyocr 强制走 EasyOCR
#   - 显式传 engine= 也行

# PaddleOCR 懒加载（模型 ~100MB，首次加载慢）
_paddle_ocr = None
# EasyOCR 懒加载（模型 ~80MB）
_easyocr_reader = None

# 置信度下限：低于此值视为识别失败
OCR_CONFIDENCE_THRESHOLD = 0.2


def _get_paddle_ocr():
    """懒加载 PaddleOCR（首次 ~10-20s 含模型下载/加载）"""
    global _paddle_ocr
    if _paddle_ocr is None:
        from paddleocr import PaddleOCR
        # PaddleOCR 3.x API：
        #   - lang='ch'：中英双语模型
        #   - use_angle_cls=False：单字图不需要方向分类
        #   - show_log=False：静音
        #   - device='cpu'：环境无 CUDA 走 CPU
        #   注：3.x 移除了旧版的 use_gpu，改用 device
        kwargs = dict(
            use_angle_cls=False,
            lang='ch',
            show_log=False,
            device='cpu',
        )
        # 3.0.0+ 才支持 device 参数；老版本不支持时降级
        try:
            _paddle_ocr = PaddleOCR(**kwargs)
        except (TypeError, AssertionError) as e:
            if 'device' in str(e) or 'use_gpu' in str(e):
                # 移除 device 重试（兼容 < 3.0.0）
                kwargs.pop('device', None)
                _paddle_ocr = PaddleOCR(**kwargs)
            else:
                raise
    return _paddle_ocr


def _get_easyocr_reader():
    """懒加载 EasyOCR（首次 ~10-15s）"""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    return _easyocr_reader


def _paddleocr_available() -> bool:
    """检查 paddleocr 是否可 import（不强求可用，因为 _get_paddle_ocr 里还会触发）"""
    try:
        import paddleocr  # noqa: F401
        return True
    except ImportError:
        return False


def _select_engine(engine: str = None) -> str:
    """决定用哪个引擎。优先级：显式参数 > 环境变量 > paddleocr 可用 > easyocr"""
    if engine is None:
        import os
        engine = os.environ.get('OCR_ENGINE', '').strip().lower()
        if not engine:
            engine = 'paddleocr' if _paddleocr_available() else 'easyocr'
    if engine not in ('paddleocr', 'easyocr'):
        raise ValueError(f"未知 OCR 引擎: {engine}（仅支持 paddleocr / easyocr）")
    return engine


def recognize_character(image_path: str, engine: str = None) -> Tuple[str, float]:
    """
    识别单张字符图片，返回 (字符, 置信度 0-1)

    算法：
    1. 按 _select_engine 选引擎
    2. 调用引擎 OCR
    3. 取置信度最高的识别结果
    4. 过滤：单字符 + 置信度 >= OCR_CONFIDENCE_THRESHOLD

    Args:
        image_path: 图片文件路径（建议是缩放后的 512x512 白底黑字图）
        engine: 'paddleocr' / 'easyocr' / None（自动选）

    Returns:
        (character, confidence)。无有效结果时 character='', confidence=0.0
    """
    selected = _select_engine(engine)
    try:
        if selected == 'paddleocr':
            return _recognize_paddle(image_path)
        else:
            return _recognize_easyocr(image_path)
    except Exception as e:
        # 引擎失败 → 兜底到另一个引擎
        fallback = 'easyocr' if selected == 'paddleocr' else 'paddleocr'
        if _engine_available(fallback):
            print(f"[OCR] {selected} 失败 ({e})，降级到 {fallback}")
            try:
                if fallback == 'paddleocr':
                    return _recognize_paddle(image_path)
                else:
                    return _recognize_easyocr(image_path)
            except Exception as e2:
                print(f"[OCR] {fallback} 也失败: {e2}")
        return '', 0.0


def _engine_available(name: str) -> bool:
    """检查指定引擎是否可用（不抛异常）"""
    if name == 'paddleocr':
        try:
            import paddleocr  # noqa: F401
            return True
        except ImportError:
            return False
    if name == 'easyocr':
        try:
            import easyocr  # noqa: F401
            return True
        except ImportError:
            return False
    return False


def _recognize_paddle(image_path: str) -> Tuple[str, float]:
    """PaddleOCR 识别单张字符图。

    PaddleOCR 2.x 的 ocr() 返回 [[(bbox, (text, conf)), ...]]（单图）
    text/conf 是二元组不是单独值。"""
    ocr = _get_paddle_ocr()
    result = ocr.ocr(image_path, cls=False)
    if not result or not result[0]:
        return '', 0.0
    # result = [[(bbox, (text, conf)), ...]]
    items = result[0]
    if not items:
        return '', 0.0
    # 选置信度最高的
    best = max(items, key=lambda x: x[1][1])
    text = best[1][0].strip()
    confidence = float(best[1][1])
    if len(text) != 1:
        return '', confidence
    if confidence < OCR_CONFIDENCE_THRESHOLD:
        return '', confidence
    return text, confidence


def _recognize_easyocr(image_path: str) -> Tuple[str, float]:
    """EasyOCR 识别单张字符图（旧版实现，保留作兜底）"""
    reader = _get_easyocr_reader()
    results = reader.readtext(image_path, detail=1, paragraph=False)
    if not results:
        return '', 0.0
    best = max(results, key=lambda r: r[2])
    text = best[1].strip()
    confidence = float(best[2])
    if len(text) != 1:
        return '', confidence
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
