"""文本框检测 + 单字符识别

- detect_text_boxes / filter_boxes_by_size / merge_overlapping_boxes
  用于「文本框检测」（在原图上找字符位置），与切割布局配合
- recognize_character：单字符图片识别，/annotate 页 OCR 自动标注用
"""
import cv2
import numpy as np
from typing import Tuple


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
        _rapidocr_engine = RapidOCR(
            params={
                'Rec.lang': 'ch',
                'Det.lang': 'ch',
                'Det.use_dilation': False,  # 单字图不需要膨胀
                'Det.box_thresh': 0.3,     # 降低检测阈值（白底黑字图）
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
    # 校验 2：置信度下限
    if confidence < OCR_CONFIDENCE_THRESHOLD:
        return '', confidence
    return text, confidence

