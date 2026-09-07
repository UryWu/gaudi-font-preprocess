"""图像处理模块 - 二值化、缩放、哈希计算"""
import cv2
import numpy as np
import hashlib
from PIL import Image


def load_image(filepath):
    """加载图片（支持中文路径）"""
    # 使用 numpy 读取文件，避免中文路径问题
    with open(filepath, 'rb') as f:
        img_array = np.frombuffer(f.read(), dtype=np.uint8)
    img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"无法加载图片: {filepath}")
    return img


def to_binary(img):
    """转换为二值图（黑底白字）"""
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    # 使用大津法自动阈值，THRESH_BINARY_INV 反转为黑底白字
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    return binary


def resize_to_height(img, target_height=4096):
    """等比缩放到目标高度"""
    h, w = img.shape[:2]
    if h == target_height:
        return img, 1.0

    scale = target_height / h
    new_w = int(w * scale)
    resized = cv2.resize(img, (new_w, target_height), interpolation=cv2.INTER_AREA)
    return resized, scale


def _rotate_image(img, angle, border_value=255):
    """围绕中心旋转图片"""
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(
        img, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=border_value
    )


def deskew(img, max_angle=10.0, angle_step=0.5):
    """
    自动检测并校正图片倾斜（投影轮廓法）

    算法：对 [-max_angle, +max_angle] 范围每个角度旋转缩略图，
    计算水平投影（每行白像素数）的方差，方差最大时文字最水平。

    参数:
        img: BGR 彩色图或灰度图
        max_angle: 最大搜索角度（度）
        angle_step: 角度搜索步长（度）

    返回:
        (rotated_img, detected_angle_degrees)
    """
    # 灰度 + 二值化（白字黑底）
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 检测是否几乎没有文字（白像素比例 < 0.5% 或 > 99.5%）—— 直接跳过
    white_ratio = float(np.count_nonzero(binary)) / binary.size
    if white_ratio < 0.005 or white_ratio > 0.995:
        return img, 0.0

    # 缩到最长边 1024px 以加速
    h, w = binary.shape
    scale = min(1.0, 1024 / max(h, w))
    small = cv2.resize(binary, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA) if scale < 1.0 else binary

    # 角度搜索：水平投影方差最大者胜出
    best_angle, best_score = 0.0, -1.0
    angles = np.arange(-max_angle, max_angle + angle_step / 2, angle_step)
    for angle in angles:
        rotated = _rotate_image(small, float(angle), border_value=0)
        projection = np.sum(rotated, axis=1) / 255.0
        score = float(np.var(projection))
        if score > best_score:
            best_score, best_angle = score, float(angle)

    # 旋转原图（彩色）。角度 < 0.1° 时跳过旋转
    if abs(best_angle) > 0.1:
        if len(img.shape) == 3:
            border = (255, 255, 255)
        else:
            border = 255
        rotated_img = _rotate_image(img, best_angle, border_value=border)
    else:
        rotated_img = img
        best_angle = 0.0

    return rotated_img, best_angle


def compute_hash(filepath):
    """计算文件MD5哈希"""
    hash_md5 = hashlib.md5()
    with open(filepath, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            hash_md5.update(chunk)
    return hash_md5.hexdigest()


def save_image(img, filepath):
    """保存图片（支持中文路径）"""
    # 使用 imencode 避免中文路径问题
    ext = filepath.rsplit('.', 1)[-1]
    success, img_encoded = cv2.imencode(f'.{ext}', img)
    if success:
        with open(filepath, 'wb') as f:
            f.write(img_encoded.tobytes())
    else:
        raise ValueError(f"无法保存图片: {filepath}")


def get_image_info(filepath):
    """获取图片信息"""
    img = load_image(filepath)
    h, w = img.shape[:2]
    file_hash = compute_hash(filepath)
    return {
        'width': w,
        'height': h,
        'hash': file_hash,
        'filepath': filepath
    }
