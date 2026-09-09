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
_easyocr_reader = None


def _get_easyocr_reader():
    """获取（或初始化）EasyOCR reader。
    第一次调用耗时较长（模型下载/加载），但只发生一次。"""
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        # ch_sim 简体 + en 英文；gpu=False 走 CPU（环境无 CUDA）
        _easyocr_reader = easyocr.Reader(['ch_sim', 'en'], gpu=False, verbose=False)
    return _easyocr_reader


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

    历史：曾试过 PaddleOCR（paddlepaddle 3.3.1）作为第二引擎，期望 50-60% 高置信
    率（vs EasyOCR 34%），但 3.3.1 on Windows CPU 的 onednn 实现有 bug
    (ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>])，
    Paddle 团队在 3.4+ 修了，但 Windows wheel 一直没发 3.4+。现状：先只用 EasyOCR。
    详见 utils/ocr_handler.py git 历史 (commit 148cd94 / 99d94cd / 9aefae3 / 9f8e9aa / 5f4bc48 / 0cd52a1)。
    """
    reader = _get_easyocr_reader()
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
