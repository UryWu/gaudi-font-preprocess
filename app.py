"""高迪书法字库预处理工具 - Flask主应用"""
import os
import sys
import json
import time
import uuid
import threading
import cv2
import numpy as np
from flask import Flask, render_template, request, jsonify, redirect, url_for
from werkzeug.utils import secure_filename

from config import *

# PyInstaller 打包后的资源目录
if getattr(sys, 'frozen', False):
    RESOURCE_DIR = sys._MEIPASS
else:
    RESOURCE_DIR = os.path.dirname(os.path.abspath(__file__))

from utils.image_processor import load_image, to_binary, resize_to_height, compute_hash, save_image, get_image_info, deskew
from utils.ocr_handler import detect_text_boxes
from utils.cut_analyzer import analyze_cut_lines
from utils.storage import save_session, load_session, list_sessions
from utils.empty_detector import detect_empty_slice

app = Flask(__name__,
            template_folder=os.path.join(RESOURCE_DIR, 'templates'),
            static_folder=os.path.join(RESOURCE_DIR, 'static'))
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 最大50MB

# 确保目录存在
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(OUTPUT_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)

# OpenCC 转换器模块级缓存。
# 每请求重建 opencc.OpenCC('s2t') 很重（初始化整张字典表），OCR 每张卡
# 发一次转换请求时会形成高并发 + 每请求重建 → 后端积压卡死。
# 缓存复用两个实例，转换本身在单字符/短文上毫秒级。
try:
    import opencc as _opencc
    _S2T_CONVERTER = _opencc.OpenCC('s2t')
    _T2S_CONVERTER = _opencc.OpenCC('t2s')
    print('[opencc] s2t/t2s 转换器已初始化（缓存）')
except Exception as _opencc_err:
    _opencc = None
    _S2T_CONVERTER = None
    _T2S_CONVERTER = None
    print(f'[opencc] 初始化失败（转换将原样返回）: {_opencc_err}')


# 切割产物子目录：data/sessions/<hash>/cutting_output/
# 与 scaled/ 平级——一个放 char_*.png（切割图），一个放 scaled_*.png（缩放后）
# 之前代码把 char_*.png 直接放 session 根目录，现统一到子目录避免污染 session 根
CHAR_DIR_NAME = 'cutting_output'


def char_dir(image_hash: str) -> str:
    """切割产物目录（data/sessions/<hash>/cutting_output/）"""
    return os.path.join(OUTPUT_FOLDER, image_hash, CHAR_DIR_NAME)


def _normalize_char_urls(characters, image_hash):
    """把旧 session 里存的 image_url（指向 session 根 char_*.png）重写到 cutting_output/

    迁移后 char_*.png 从 session 根挪到 cutting_output/，但老 session.json
    里 characters 的 image_url 还是旧路径 /output/<hash>/char_XXXX.png（会 404）。
    这里对每个字符做幂等改写：若 image_url 没含 /cutting_output/ 且是 char_*.png，
    就换成新路径。返回原列表（原地修改）。"""
    for c in characters or []:
        url = c.get('image_url') or ''
        fn = c.get('filename') or ''
        if url and '/cutting_output/' not in url and fn and fn.startswith('char_'):
            c['image_url'] = f'/output/{image_hash}/{CHAR_DIR_NAME}/{fn}'
    return characters


@app.route('/')
def index():
    """首页 - 重定向到切割布局页面"""
    return redirect(url_for('layout'))


@app.route('/layout')
def layout():
    """切割布局页面"""
    return render_template('layout.html')


@app.route('/adjust')
def adjust():
    """切割调整页面"""
    return render_template('adjust.html')


@app.route('/scale')
def scale():
    """缩放校正页面"""
    return render_template('scale.html')


@app.route('/annotate')
def annotate():
    """标注出图页面"""
    return render_template('annotate.html')


@app.route('/api/upload', methods=['POST'])
def upload_image():
    """上传图片并处理"""
    if 'image' not in request.files:
        return jsonify({'error': '没有上传文件'}), 400

    file = request.files['image']
    if file.filename == '':
        return jsonify({'error': '没有选择文件'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': '不支持的文件格式'}), 400

    # 保存原始文件
    filename = secure_filename(file.filename)
    original_path = os.path.join(app.config['UPLOAD_FOLDER'], f"original_{filename}")
    file.save(original_path)

    # 计算哈希
    file_hash = compute_hash(original_path)

    # 加载并处理图片
    img = load_image(original_path)
    original_height, original_width = img.shape[:2]

    # 自动纠偏（可选）
    deskew_enabled = request.form.get('deskew', '1') == '1'
    if deskew_enabled:
        img, skew_angle = deskew(img)
        if abs(skew_angle) > 0.1:
            print(f"倾斜校正: {skew_angle:.2f}°")
    else:
        skew_angle = 0.0
        print("倾斜校正: 已禁用")

    # 保存原图和纠偏后原图（手动旋转重置用）
    original_copy_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_hash}_original.png")
    save_image(img, original_copy_path)
    base_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{file_hash}_base.png")
    save_image(img, base_path)

    # 转换为二值图
    binary = to_binary(img)

    # 缩放到目标高度
    resized, scale = resize_to_height(binary, TARGET_HEIGHT)
    resized_height, resized_width = resized.shape[:2]

    # 保存处理后的图片
    processed_filename = f"{file_hash}.png"
    processed_path = os.path.join(app.config['UPLOAD_FOLDER'], processed_filename)
    save_image(resized, processed_path)

    # 检查是否有保存的会话
    session_data = load_session(file_hash, DATA_FOLDER)

    if session_data:
        # 使用保存的切割线
        # 如果会话中没有boxes，重新检测
        boxes = session_data.get('boxes', [])
        print(f"加载会话: hash={file_hash}, 会话中boxes数量={len(boxes)}")

        if not boxes:
            print("会话中无boxes，重新检测...")
            try:
                boxes = detect_text_boxes(resized)
                print(f"重新检测到boxes数量={len(boxes)}")
            except Exception as e:
                print(f"文本框检测失败: {e}")
                boxes = []

        return jsonify({
            'success': True,
            'hash': file_hash,
            'image_url': f'/static/uploads/{processed_filename}',
            'original_width': original_width,
            'original_height': original_height,
            'width': resized_width,
            'height': resized_height,
            'scale': scale,
            'skew_angle': skew_angle,
            'vertical_lines': session_data.get('vertical_lines', []),
            'horizontal_lines': session_data.get('horizontal_lines', []),
            'strip_horizontal_lines': session_data.get('strip_horizontal_lines', []),
            'boxes': boxes,
            'has_saved_session': True
        })

    # 文本框检测（使用 OpenCV 轮廓检测）
    try:
        boxes = detect_text_boxes(resized)
    except Exception as e:
        print(f"文本框检测失败: {e}")
        boxes = []

    # 分析切割线
    cut_result = analyze_cut_lines(boxes, resized_width, resized_height)

    return jsonify({
        'success': True,
        'hash': file_hash,
        'image_url': f'/static/uploads/{processed_filename}',
        'original_width': original_width,
        'original_height': original_height,
        'width': resized_width,
        'height': resized_height,
        'scale': scale,
        'skew_angle': skew_angle,
        'vertical_lines': cut_result['vertical_lines'],
        'horizontal_lines': cut_result['horizontal_lines'],
        'strip_horizontal_lines': cut_result['strip_horizontal_lines'],
        'boxes': boxes,
        'has_saved_session': False
    })


@app.route('/api/rotate', methods=['POST'])
def rotate_image():
    """手动旋转图片（增量式，不触发识别）"""
    import time
    data = request.get_json()
    image_hash = data.get('hash')
    angle = float(data.get('angle', 0))

    if not image_hash:
        return jsonify({'success': False, 'error': '缺少图片哈希'}), 400

    base_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}_base.png")
    processed_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}.png")

    if not os.path.exists(base_path):
        return jsonify({'success': False, 'error': '基准图不存在，请重新上传'}), 404

    if abs(angle) < 0.01:
        return jsonify({'success': False, 'error': '角度为零'}), 400

    # 从彩色基准图旋转（避免对二值图旋转产生灰边）
    img = load_image(base_path)
    h, w = img.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    rotated = cv2.warpAffine(
        img, M, (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(255, 255, 255)
    )

    # 覆盖彩色基准图（累积旋转）
    save_image(rotated, base_path)

    # 二值化 + 缩放，覆盖显示图
    binary = to_binary(rotated)
    resized, scale = resize_to_height(binary, TARGET_HEIGHT)
    save_image(resized, processed_path)

    print(f"手动旋转: {angle:+.2f}°, hash={image_hash}")

    return jsonify({
        'success': True,
        'image_url': f'/static/uploads/{image_hash}.png?t={int(time.time())}',
        'width': resized.shape[1],
        'height': resized.shape[0],
        'scale': scale,
        'skew_angle': angle,
    })


@app.route('/api/detect', methods=['POST'])
def detect_cut_lines():
    """对当前已处理图片重新检测文本框和切割线"""
    import time
    data = request.get_json()
    image_hash = data.get('hash')

    if not image_hash:
        return jsonify({'success': False, 'error': '缺少图片哈希'}), 400

    processed_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}.png")

    if not os.path.exists(processed_path):
        return jsonify({'success': False, 'error': '图片不存在，请重新上传'}), 404

    # 加载当前显示图（已经是二值化+缩放后的）
    img = load_image(processed_path)
    h, w = img.shape[:2]

    try:
        boxes = detect_text_boxes(img)
    except Exception as e:
        print(f"文本框检测失败: {e}")
        boxes = []

    cut_result = analyze_cut_lines(boxes, w, h)
    print(f"重新识别: hash={image_hash}, boxes={len(boxes)}")

    return jsonify({
        'success': True,
        'image_url': f'/static/uploads/{image_hash}.png?t={int(time.time())}',
        'width': w,
        'height': h,
        'vertical_lines': cut_result['vertical_lines'],
        'horizontal_lines': cut_result['horizontal_lines'],
        'strip_horizontal_lines': cut_result['strip_horizontal_lines'],
        'boxes': boxes,
    })


@app.route('/api/reset_image', methods=['POST'])
def reset_image():
    """重置为纠偏后、未手动旋转的基准状态"""
    import time
    data = request.get_json()
    image_hash = data.get('hash')

    if not image_hash:
        return jsonify({'success': False, 'error': '缺少图片哈希'}), 400

    base_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}_base.png")
    original_copy_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}_original.png")
    processed_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}.png")

    if not os.path.exists(original_copy_path):
        return jsonify({'success': False, 'error': '原图不存在，请重新上传'}), 404

    # 用原图重新覆盖基准图（丢弃手动旋转）
    import shutil
    shutil.copy(original_copy_path, base_path)

    # 重新二值化 + 缩放 + 检测
    img = load_image(base_path)
    binary = to_binary(img)
    resized, scale = resize_to_height(binary, TARGET_HEIGHT)
    save_image(resized, processed_path)

    try:
        boxes = detect_text_boxes(resized)
    except Exception as e:
        print(f"文本框检测失败: {e}")
        boxes = []

    cut_result = analyze_cut_lines(boxes, resized.shape[1], resized.shape[0])
    print(f"重置图片: hash={image_hash}")

    return jsonify({
        'success': True,
        'image_url': f'/static/uploads/{image_hash}.png?t={int(time.time())}',
        'width': resized.shape[1],
        'height': resized.shape[0],
        'scale': scale,
        'skew_angle': 0.0,
        'vertical_lines': cut_result['vertical_lines'],
        'horizontal_lines': cut_result['horizontal_lines'],
        'strip_horizontal_lines': cut_result['strip_horizontal_lines'],
        'boxes': boxes,
    })


@app.route('/api/save_cuts', methods=['POST'])
def save_cuts():
    """保存切割线配置"""
    data = request.get_json()
    image_hash = data.get('hash')
    vertical_lines = data.get('vertical_lines', [])
    horizontal_lines = data.get('horizontal_lines', [])
    strip_horizontal_lines = data.get('strip_horizontal_lines', [])
    boxes = data.get('boxes', [])

    print(f"保存切割线: hash={image_hash}, boxes数量={len(boxes)}")

    if not image_hash:
        return jsonify({'error': '缺少图片哈希'}), 400

    session_data = {
        'hash': image_hash,
        'vertical_lines': vertical_lines,
        'horizontal_lines': horizontal_lines,
        'strip_horizontal_lines': strip_horizontal_lines,
        'boxes': boxes
    }

    if save_session(image_hash, session_data, DATA_FOLDER):
        return jsonify({'success': True})
    else:
        return jsonify({'error': '保存失败'}), 500


@app.route('/api/load_session/<image_hash>')
def api_load_session(image_hash):
    """加载会话数据"""
    session_data = load_session(image_hash, DATA_FOLDER)
    if session_data:
        return jsonify({'success': True, 'data': session_data})
    else:
        return jsonify({'success': False, 'error': '会话不存在'})


@app.route('/api/list_sessions', methods=['GET'])
def api_list_sessions():
    """
    列出所有有可用数据的会话。

    返回每个会话的元信息：字符数、是否有有效坐标、是否有 output 目录。
    前端在 /adjust 加载失败时用此端点寻找 fallback 哈希
    （场景：localStorage 记了一个失效的 hash，原图被删，导致 '图片不存在'）。

    每个 session 还包含 `exports` 字段——该 session 历次「导出训练包」产物。
    导出来源是磁盘扫描（不是 cutting.json 记录）——用户手动删 export 目录后
    API 自动不再列出，符合「手动删 = 不需要了」语义。详见 _scan_session_exports。
    """
    sessions = list_sessions(DATA_FOLDER)
    result = []
    for h in sessions:
        session_data = load_session(h, DATA_FOLDER)
        if not session_data:
            continue
        chars = session_data.get('characters', [])
        # 是否有有效坐标（x>0 或 y>0 的字符）
        has_coords = any(c.get('x', 0) > 0 or c.get('y', 0) > 0 for c in chars)
        # 是否已有 cutting_output 目录
        out_dir = char_dir(h)
        out_files = 0
        if os.path.isdir(out_dir):
            out_files = sum(1 for f in os.listdir(out_dir)
                            if f.startswith('char_') and f.endswith('.png'))
        # 原图是否还在
        upload_path = os.path.join(UPLOAD_FOLDER, f"{h}.png")
        has_upload = os.path.exists(upload_path)
        result.append({
            'hash': h,
            'char_count': len(chars),
            'has_coords': has_coords,
            'output_count': out_files,
            'has_upload': has_upload,
            'exports': _scan_session_exports(h),
        })
    # 按可用性排序：有 coords + 有 output > 有 output > 其它；同档内按字符数降序
    def score(s):
        if s['has_coords'] and s['output_count'] > 0:
            return (2, s['char_count'])
        if s['output_count'] > 0:
            return (1, s['char_count'])
        return (0, s['char_count'])
    result.sort(key=score, reverse=True)
    return jsonify({'success': True, 'sessions': result})


def _scan_session_exports(image_hash):
    """扫描 session 下 exported/ 目录，返回所有「完整训练包」子目录的元信息。

    数据来源 = 磁盘扫描：用户手动删 export 目录后 API 自动不再列出该版本，
    不维护「导出历史数据库」（避免与现实脱节）。

    过滤规则：只算 `os.path.isdir` 子目录 + 子目录里有 PNG 才算「完整导出」。
    子目录命名规范：Ymd_HMS 时间戳（如 20260910_013320），不合规范的不算
    （避免把用户手动 mkdir 的临时目录、backup/ 等算进来）。

    返回：按 mtime 倒序的 list（最新在前）：
      [{'ts': '20260910_013320',
        'path': 'G:\\...\\exported\\20260910_013320',
        'png_count': 318,
        'csv_rows': 318,
        'created_at': '2026-09-10T01:33:20'}, ...]
    """
    from datetime import datetime
    import re

    exported_dir = os.path.join(OUTPUT_FOLDER, image_hash, 'exported')
    if not os.path.isdir(exported_dir):
        return []

    ts_pattern = re.compile(r'^\d{8}_\d{6}$')
    results = []
    for entry in os.listdir(exported_dir):
        sub = os.path.join(exported_dir, entry)
        if not os.path.isdir(sub):
            continue
        if not ts_pattern.match(entry):
            continue  # 跳过非时间戳目录（如 backup/、test/）
        # 数 PNG 与 CSV 行数（CSV 取 fontlab_ 开头的那个）
        files = os.listdir(sub)
        png_count = sum(1 for f in files if f.endswith('.png'))
        csv_rows = 0
        csv_files = [f for f in files if f.startswith('fontlab_') and f.endswith('.csv')]
        if csv_files:
            csv_path = os.path.join(sub, csv_files[0])
            try:
                with open(csv_path, 'r', encoding='utf-8') as cf:
                    # 表头占一行，数据行 = 总行数 - 1
                    csv_rows = max(0, sum(1 for _ in cf) - 1)
            except OSError:
                csv_rows = 0
        results.append({
            'ts': entry,
            'path': os.path.abspath(sub),
            'png_count': png_count,
            'csv_rows': csv_rows,
            'created_at': datetime.fromtimestamp(
                os.path.getmtime(sub)).strftime('%Y-%m-%dT%H:%M:%S'),
        })
    # 按 mtime 倒序（最新在前）—— 同一 ts 内若有多个，按字典序兜底
    results.sort(key=lambda x: x['created_at'], reverse=True)
    return results


def _dedupe_contained(regions):
    """
    去重：如果一个 region 被另一个完全包含，保留较小的（更紧的字符边界）。
    绿框通常比网格单元更紧，所以"框在格子里"的情况保留框、丢弃格。
    """
    kept = []
    for r in regions:
        area_r = (r['x2'] - r['x1']) * (r['y2'] - r['y1'])
        contained_index = None
        for i, k in enumerate(kept):
            if (r['x1'] >= k['x1'] and r['y1'] >= k['y1'] and
                    r['x2'] <= k['x2'] and r['y2'] <= k['y2']):
                contained_index = i
                break
        if contained_index is not None:
            area_k = (kept[contained_index]['x2'] - kept[contained_index]['x1']) * (
                kept[contained_index]['y2'] - kept[contained_index]['y1'])
            if area_r < area_k:
                kept[contained_index] = r
            # 否则 r 被丢弃（k 较小更精确）
        else:
            kept.append(r)
    return kept


@app.route('/api/apply_cuts', methods=['POST'])
def apply_cuts():
    """应用切割，保存切割结果。支持红/蓝/绿三种来源任意组合。"""
    data = request.get_json()
    image_hash = data.get('hash')
    vertical_lines = data.get('vertical_lines', [])
    strip_horizontal_lines = data.get('strip_horizontal_lines', [])
    boxes = data.get('boxes', [])

    use_red = data.get('use_red', True)
    use_blue = data.get('use_blue', True)
    use_green = data.get('use_green', False)

    if not image_hash:
        return jsonify({'error': '缺少图片哈希'}), 400

    # 加载处理后的图片
    processed_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}.png")
    if not os.path.exists(processed_path):
        return jsonify({'error': '图片不存在'}), 404

    img = load_image(processed_path)

    # 创建输出目录
    output_dir = char_dir(image_hash)
    os.makedirs(output_dir, exist_ok=True)

    # 收集所有候选切割区域（含来源标记）
    regions = []  # [{x1, y1, x2, y2, source}, ...]

    # 来源 1：红色 × 蓝色网格
    if use_red and use_blue and strip_horizontal_lines and len(strip_horizontal_lines) > 0:
        for strip_info in strip_horizontal_lines:
            x_start = strip_info.get('x_start', 0)
            x_end = strip_info.get('x_end', 0)
            h_lines = strip_info.get('horizontal_lines', [])
            for i in range(len(h_lines) - 1):
                regions.append({
                    'x1': x_start, 'y1': h_lines[i],
                    'x2': x_end,   'y2': h_lines[i + 1],
                    'source': 'grid'
                })
    elif use_red and use_blue:
        # 兼容旧数据：使用全局横向切割线
        horizontal_lines = data.get('horizontal_lines', [])
        for i in range(len(horizontal_lines) - 1):
            for j in range(len(vertical_lines) - 1):
                regions.append({
                    'x1': vertical_lines[j], 'y1': horizontal_lines[i],
                    'x2': vertical_lines[j + 1], 'y2': horizontal_lines[i + 1],
                    'source': 'grid'
                })

    # 来源 2：绿色文本框
    if use_green and boxes:
        for box in boxes:
            regions.append({
                'x1': box['x_min'], 'y1': box['y_min'],
                'x2': box['x_max'], 'y2': box['y_max'],
                'source': 'box'
            })

    if not regions:
        return jsonify({'success': False, 'error': '没有勾选任何切割来源'}), 400

    # 重叠去重：完全包含时保留较小的（更紧的边界）
    regions = _dedupe_contained(regions)

    # 切割并保存
    cut_images = []
    for idx, r in enumerate(regions):
        # 安全裁剪到图片边界
        x1 = max(0, r['x1'])
        y1 = max(0, r['y1'])
        x2 = min(img.shape[1], r['x2'])
        y2 = min(img.shape[0], r['y2'])
        if x2 <= x1 or y2 <= y1:
            continue

        piece = img[y1:y2, x1:x2]

        # 检测是否为空切片
        is_empty, text_ratio, _ = detect_empty_slice(piece, threshold=0.05)

        # 保存
        piece_filename = f"char_{idx:04d}.png"
        piece_path = os.path.join(output_dir, piece_filename)
        save_image(piece, piece_path)

        cut_images.append({
            'index': idx,
            'filename': piece_filename,
            'image_url': f'/output/{image_hash}/{CHAR_DIR_NAME}/{piece_filename}',
            'x': x1, 'y': y1,
            'width': x2 - x1,
            'height': y2 - y1,
            'is_empty': is_empty,
            'text_ratio': round(text_ratio, 4),
            'source': r['source']  # 'grid' or 'box'
        })

    print(f"应用切割: hash={image_hash}, use_red={use_red}, use_blue={use_blue}, use_green={use_green}, "
          f"共 {len(cut_images)} 片")

    # 同步写入 session_data['characters']，避免 /adjust 重新生成不一致结果
    session_data = load_session(image_hash, DATA_FOLDER) or {'hash': image_hash}
    session_data['characters'] = cut_images
    save_session(image_hash, session_data, DATA_FOLDER)

    return jsonify({
        'success': True,
        'total_pieces': len(cut_images),
        'output_dir': output_dir,
        'pieces': cut_images
    })


@app.route('/api/get_cut_results/<image_hash>')
def get_cut_results(image_hash):
    """获取切割结果（用于切割调整页面）"""
    # 加载会话数据
    session_data = load_session(image_hash, DATA_FOLDER)
    if not session_data:
        return jsonify({'success': False, 'error': '会话不存在，请先在切割布局页面处理图片'})

    # 检查是否已有完整的切割结果（包含正确的坐标）
    characters = session_data.get('characters', [])
    has_valid_coords = characters and any(c.get('x', 0) > 0 or c.get('y', 0) > 0 for c in characters)

    output_dir = char_dir(image_hash)

    # 如果没有有效的坐标数据，需要重新生成
    if not has_valid_coords:
        processed_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}.png")
        if not os.path.exists(processed_path):
            return jsonify({'success': False, 'error': '图片不存在'})

        img = load_image(processed_path)
        os.makedirs(output_dir, exist_ok=True)

        vertical_lines = session_data.get('vertical_lines', [])
        strip_horizontal_lines = session_data.get('strip_horizontal_lines', [])

        idx = 0
        characters = []

        if strip_horizontal_lines and len(strip_horizontal_lines) > 0:
            for strip_info in strip_horizontal_lines:
                strip_index = strip_info.get('strip_index', 0)
                x_start = strip_info.get('x_start', 0)
                x_end = strip_info.get('x_end', 0)
                h_lines = strip_info.get('horizontal_lines', [])

                for i in range(len(h_lines) - 1):
                    y1 = h_lines[i]
                    y2 = h_lines[i + 1]

                    piece = img[y1:y2, x_start:x_end]
                    is_empty, text_ratio, _ = detect_empty_slice(piece, threshold=0.05)

                    piece_filename = f"char_{idx:04d}.png"
                    piece_path = os.path.join(output_dir, piece_filename)
                    save_image(piece, piece_path)

                    characters.append({
                        'index': idx,
                        'strip_index': strip_index,
                        'char_index': i,
                        'filename': piece_filename,
                        'image_url': f'/output/{image_hash}/{CHAR_DIR_NAME}/{piece_filename}',
                        'x': x_start, 'y': y1,
                        'width': x_end - x_start,
                        'height': y2 - y1,
                        'is_empty': is_empty,
                        'text_ratio': round(text_ratio, 4),
                        'needs_adjust': False
                    })
                    idx += 1
        else:
            horizontal_lines = session_data.get('horizontal_lines', [])
            for i in range(len(horizontal_lines) - 1):
                for j in range(len(vertical_lines) - 1):
                    y1 = horizontal_lines[i]
                    y2 = horizontal_lines[i + 1]
                    x1 = vertical_lines[j]
                    x2 = vertical_lines[j + 1]

                    piece = img[y1:y2, x1:x2]
                    is_empty, text_ratio, _ = detect_empty_slice(piece, threshold=0.05)

                    piece_filename = f"char_{idx:04d}.png"
                    piece_path = os.path.join(output_dir, piece_filename)
                    save_image(piece, piece_path)

                    characters.append({
                        'index': idx,
                        'strip_index': j,
                        'char_index': i,
                        'filename': piece_filename,
                        'image_url': f'/output/{image_hash}/{CHAR_DIR_NAME}/{piece_filename}',
                        'x': x1, 'y': y1,
                        'width': x2 - x1,
                        'height': y2 - y1,
                        'is_empty': is_empty,
                        'text_ratio': round(text_ratio, 4),
                        'needs_adjust': False
                    })
                    idx += 1

        # 保存切割结果到会话
        session_data['characters'] = characters
        save_session(image_hash, session_data, DATA_FOLDER)

    # 旧 session 里 image_url 可能还指向 session 根（迁移前），规范化到 cutting_output/
    _normalize_char_urls(characters, image_hash)
    return jsonify({
        'success': True,
        'characters': characters,
        'image_hash': image_hash
    })


@app.route('/api/clear_empty_chars', methods=['POST'])
def clear_empty_chars():
    """清除所有空白字符：删除磁盘文件 + 从 session 中移除"""
    import os
    data = request.get_json()
    image_hash = data.get('hash')

    if not image_hash:
        return jsonify({'success': False, 'error': '缺少图片哈希'}), 400

    session_data = load_session(image_hash, DATA_FOLDER)
    if not session_data:
        return jsonify({'success': False, 'error': '会话不存在'}), 404

    characters = session_data.get('characters', [])
    if not characters:
        return jsonify({'success': False, 'error': '没有字符数据'}), 400

    # 区分空白和非空
    kept = []
    removed_files = []
    for c in characters:
        if c.get('is_empty'):
            # 删除磁盘文件
            filename = c.get('filename')
            if filename:
                file_path = os.path.join(char_dir(image_hash), filename)
                if os.path.exists(file_path):
                    try:
                        os.remove(file_path)
                        removed_files.append(filename)
                    except Exception as e:
                        print(f"删除空白切片失败 {filename}: {e}")
        else:
            kept.append(c)

    if not removed_files:
        return jsonify({'success': False, 'error': '没有空白字符可清除'}), 400

    # 重新编号：index 重新分配，filename 也重命名（char_NNNN.png）
    # 简化处理：保留原 filename，只更新 session 和磁盘列表
    # 由于原 filename 按切割顺序排列，去除空白后序号会"跳跃"
    # 为保持连续，重命名磁盘文件
    for new_idx, c in enumerate(kept):
        old_filename = c.get('filename')
        new_filename = f"char_{new_idx:04d}.png"
        if old_filename != new_filename:
            old_path = os.path.join(char_dir(image_hash), old_filename)
            new_path = os.path.join(char_dir(image_hash), new_filename)
            if os.path.exists(old_path):
                try:
                    os.rename(old_path, new_path)
                except Exception as e:
                    print(f"重命名失败 {old_filename} -> {new_filename}: {e}")
            c['filename'] = new_filename
            c['image_url'] = f'/output/{image_hash}/{CHAR_DIR_NAME}/{new_filename}'
            c['index'] = new_idx

    # 写回 session
    session_data['characters'] = kept
    save_session(image_hash, session_data, DATA_FOLDER)

    print(f"清除空白: hash={image_hash}, 移除 {len(removed_files)} 个, 保留 {len(kept)} 个")

    return jsonify({
        'success': True,
        'removed_count': len(removed_files),
        'remaining_count': len(kept),
        'characters': kept
    })


@app.route('/api/clear_all_data', methods=['POST'])
def clear_all_data():
    """清空该图片的所有数据：删除磁盘目录 + 删除 session JSON"""
    import shutil
    data = request.get_json()
    image_hash = data.get('hash')

    if not image_hash:
        return jsonify({'success': False, 'error': '缺少图片哈希'}), 400

    # 删除 output 目录
    output_dir = char_dir(image_hash)
    if os.path.exists(output_dir):
        try:
            shutil.rmtree(output_dir)
        except Exception as e:
            print(f"删除 output 目录失败: {e}")
            return jsonify({'success': False, 'error': f'删除 output 目录失败: {e}'}), 500

    # 删除 session JSON
    session_path = os.path.join(DATA_FOLDER, f"{image_hash}.json")
    if os.path.exists(session_path):
        try:
            os.remove(session_path)
        except Exception as e:
            print(f"删除 session 失败: {e}")

    # 删除 uploads 里的原图和基准图（保留原图副本可选）
    for suffix in ['.png', '_base.png', '_original.png']:
        upload_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{image_hash}{suffix}")
        if os.path.exists(upload_path):
            try:
                os.remove(upload_path)
            except Exception as e:
                print(f"删除 upload {suffix} 失败: {e}")

    print(f"清空所有数据: hash={image_hash}")

    return jsonify({
        'success': True,
        'message': '已清空 output、session、uploads'
    })


@app.route('/api/save_adjustments', methods=['POST'])
def save_adjustments():
    """保存调整结果：实际按 adjust_top/bottom/left/right 重剪图片。

    若客户端传了 `image_data`（来自画笔编辑），则直接把 base64 PNG 写入磁盘，
    跳过基于原图的重剪逻辑（仍保留后续按 adjust_* 二次裁剪的可能性）。
    """
    import base64
    data = request.get_json()
    image_hash = data.get('hash')
    characters = data.get('characters', [])

    if not image_hash:
        return jsonify({'error': '缺少图片哈希'}), 400

    # 加载会话数据
    session_data = load_session(image_hash, DATA_FOLDER)
    if not session_data:
        return jsonify({'error': '会话不存在'}), 404

    # 按 adjust 值实际重剪 PNG 文件
    output_dir = char_dir(image_hash)
    recropped = 0
    painted = 0
    for char in characters:
        filename = char.get('filename')
        if not filename:
            continue
        fp = os.path.join(output_dir, filename)
        if not os.path.exists(fp):
            continue

        # 优先处理 image_data（画笔编辑过的整张图）
        image_data = char.get('image_data')
        if image_data:
            try:
                payload = image_data.split(',', 1)[1] if ',' in image_data else image_data
                img_bytes = base64.b64decode(payload)
                img_array = np.frombuffer(img_bytes, dtype=np.uint8)
                img = cv2.imdecode(img_array, cv2.IMREAD_UNCHANGED)
                if img is not None:
                    cv2.imwrite(fp, img)
                    h, w = img.shape[:2]
                    char['width'] = w
                    char['height'] = h
                    painted += 1
                    # 清掉一次性字段，避免污染 session
                    char.pop('image_data', None)
                else:
                    print(f"image_data 解码失败 {filename}（cv2.imdecode 返回 None），跳过")
            except Exception as e:
                print(f"image_data 解码失败 {filename}: {e}")

        a_top = int(char.get('adjust_top', 0) or 0)
        a_bottom = int(char.get('adjust_bottom', 0) or 0)
        a_left = int(char.get('adjust_left', 0) or 0)
        a_right = int(char.get('adjust_right', 0) or 0)

        # 全部为 0 则跳过裁剪
        if a_top == 0 and a_bottom == 0 and a_left == 0 and a_right == 0:
            # 重置 adjust 值（即便没裁剪也归零，标记已处理）
            char['adjust_top'] = 0
            char['adjust_bottom'] = 0
            char['adjust_left'] = 0
            char['adjust_right'] = 0
            continue

        img = cv2.imread(fp, cv2.IMREAD_UNCHANGED)
        if img is None:
            continue

        h, w = img.shape[:2]

        # 调整值语义：
        # - 正值 = 收缩（向内裁掉对应行/列像素）
        # - 负值 = 扩展（向外添加 |值| 行/列，黑色 [0,0,0] 填充）
        # 新画布尺寸公式 new_h = h - a_top - a_bottom，a_top/a_bottom 可为负
        new_h = h - a_top - a_bottom
        new_w = w - a_left - a_right

        # 边界校验：new_h/new_w 必须为正，否则跳过
        # 典型无效情况：a_top + a_bottom >= h 或 a_left + a_right >= w
        if new_h <= 0 or new_w <= 0:
            print(f"无效裁剪范围: {filename}, new_h={new_h}, new_w={new_w}，跳过")
            # 归零 adjust 值（已处理：拒绝应用）
            char['adjust_top'] = 0
            char['adjust_bottom'] = 0
            char['adjust_left'] = 0
            char['adjust_right'] = 0
            continue

        # 原图在新画布中的放置位置
        # - 收缩（a_top >= 0）：原图 row a_top 起写到新画布 row 0
        # - 扩展（a_top < 0）：原图 row 0 起写到新画布 row |a_top|，前面 |a_top| 行黑填
        top_offset = max(0, -a_top)
        left_offset = max(0, -a_left)

        # 原图读取起点（处理收缩时跳过前 a_top 行/列）
        source_top = max(0, a_top)
        source_left = max(0, a_left)

        # 实际可读取的尺寸：受原图剩余内容与新画布剩余空间双重限制
        # 例：a_top=-10, a_bottom=0, h=100 → new_h=110, top_offset=10
        #     source_top=0, source_h=min(100, 110-10)=100（读全部 100 行）
        # 例：a_top=10, a_bottom=0, h=100 → new_h=90, top_offset=0
        #     source_top=10, source_h=min(90, 90-0)=80（去掉前 10 行）
        source_h = min(h - source_top, new_h - top_offset)
        source_w = min(w - source_left, new_w - left_offset)

        # 创建黑底画布
        # - 灰度图（2D）：单通道，0 即黑
        # - BGR/BGRA（3D）：保留原通道数，0 即黑
        if img.ndim == 2:
            new_img = np.zeros((new_h, new_w), dtype=img.dtype)
        else:
            new_img = np.zeros((new_h, new_w, img.shape[2]), dtype=img.dtype)

        # 把原图对应区域贴到新画布的指定位置
        # 黑填由 np.zeros 完成；扩展区自然保持 0
        new_img[top_offset:top_offset + source_h,
                left_offset:left_offset + source_w] = \
            img[source_top:source_top + source_h,
                source_left:source_left + source_w]

        cv2.imwrite(fp, new_img)
        char['width'] = new_w
        char['height'] = new_h
        recropped += 1

        # 重剪后 adjust 值归零（已应用）
        char['adjust_top'] = 0
        char['adjust_bottom'] = 0
        char['adjust_left'] = 0
        char['adjust_right'] = 0

    # 更新字符数据
    session_data['characters'] = characters
    save_session(image_hash, session_data, DATA_FOLDER)

    if recropped or painted:
        print(f"保存调整: hash={image_hash}, 重剪 {recropped}, 绘制 {painted}")

    return jsonify({'success': True, 'recropped': recropped, 'painted': painted})


@app.route('/api/delete_characters', methods=['POST'])
def delete_characters():
    """批量删除指定字符：删除磁盘文件 + 从 session 中移除对应条目

    级联删除（本函数最容易漏的点）：一个「字符」在会话里有两份条目、两份文件——
      characters        : filename=char_XXXX.png     → cutting_output/char_XXXX.png
      scaled_characters : original_filename=char_XXXX, processed_filename=scaled_YYYY.png
                                                 → scaled/scaled_YYYY.png
    /adjust 删原始名（char_XXXX.png），/annotate 删缩放名（scaled_YYYY.png）。
    无论从哪个入口删，原始 + 缩放两侧的文件与条目都要一起清掉，并同步删掉
    ocr_annotations.json 里的对应标注。否则另一半会漏删——例如只删了 scaled
    文件但 scaled_characters 条目还在，/annotate 刷新又把卡片加载回来（本次 bug）。
    """
    data = request.get_json()
    image_hash = data.get('hash')
    filenames = data.get('filenames', [])

    if not image_hash or not filenames:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    session_data = load_session(image_hash, DATA_FOLDER)

    # 建 原始名 ↔ 缩放名 双向映射：scaled 条目记着 original_filename，据此倒查
    # 同名映射只取第一个（正常一个原始字只对应一份缩放输出）
    char_to_scaled = {}
    scaled_to_char = {}
    for sc in (session_data or {}).get('scaled_characters', []):
        src = (sc.get('original_filename') or '').strip()
        sp = (sc.get('processed_filename') or '').strip()
        if sp:
            scaled_to_char[sp] = src
            if src:
                char_to_scaled.setdefault(src, sp)

    # 展开真正要删的文件集合 = 用户指定的 + 级联到的另一侧
    files_to_delete = set()
    for fn in filenames:
        if not fn or '..' in fn or '/' in fn or '\\' in fn:  # 防路径穿越
            continue
        files_to_delete.add(fn)
        # 用户给缩放名 → 补删其原始切图；给原始名 → 补删它的缩放输出
        if fn in scaled_to_char and scaled_to_char[fn]:
            files_to_delete.add(scaled_to_char[fn])
        if fn in char_to_scaled:
            files_to_delete.add(char_to_scaled[fn])

    deleted = []
    for fn in files_to_delete:
        # 字符文件可能在 cutting_output/（char_*.png）或 scaled/（scaled_*.png）
        # 两个位置都试一次——前端只发文件名，路径由服务端解析
        fp = os.path.join(char_dir(image_hash), fn)
        if not os.path.exists(fp):
            scaled_fp = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled', fn)
            if os.path.exists(scaled_fp):
                fp = scaled_fp
        if os.path.exists(fp):
            try:
                os.remove(fp)
                deleted.append(fn)
            except Exception as e:
                print(f"删除 {fn} 失败: {e}")

    if session_data:
        deleted_set = set(files_to_delete)
        # 原始切图按 filename 匹配；缩放列表按 processed_filename（个别兜底 filename）
        session_data['characters'] = [
            c for c in session_data.get('characters', [])
            if (c.get('filename') or '') not in deleted_set
        ]
        session_data['scaled_characters'] = [
            c for c in session_data.get('scaled_characters', [])
            if (c.get('processed_filename') or c.get('filename') or '') not in deleted_set
        ]
        save_session(image_hash, session_data, DATA_FOLDER)

        # 同步删 ocr_annotations.json 里指向这些文件的标注记录（键=文件名）。
        # 不删的话，同名 scaled 若日后重新生成，旧标注会被 loadOcrAnnotations
        # 误套到新图对应的旧字上。
        _delete_ocr_annotations_by_keys(image_hash, files_to_delete)

    remaining = len(session_data.get('characters', [])) if session_data else 0
    scaled_remaining = len(session_data.get('scaled_characters', [])) if session_data else 0
    print(f"批量删除字符: hash={image_hash}, 删 {len(deleted)} 个文件, "
          f"characters 剩 {remaining}, scaled 剩 {scaled_remaining}")

    return jsonify({
        'success': True,
        'deleted_count': len(deleted),
        'remaining_count': remaining
    })


@app.route('/api/open_path', methods=['POST'])
def open_path():
    """在系统资源管理器中打开字符文件（Windows: explorer /select）"""
    import subprocess
    import sys
    data = request.get_json()
    filename = data.get('path', '')
    image_hash = data.get('hash', '')

    if not filename or '..' in filename or '/' in filename or '\\' in filename:
        return jsonify({'success': False, 'error': '非法文件名'}), 400

    fp = os.path.join(char_dir(image_hash), filename)
    # 缩放后的文件在 scaled/ 子目录，找不到就再试一次
    if not os.path.exists(fp):
        scaled_fp = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled', filename)
        if os.path.exists(scaled_fp):
            fp = scaled_fp
    fp = os.path.abspath(fp)
    if not os.path.exists(fp):
        return jsonify({'success': False, 'error': '文件不存在'}), 404

    try:
        if sys.platform.startswith('win'):
            # Windows: explorer /select,"<path>" 打开资源管理器并选中文件
            subprocess.Popen(['explorer', '/select,', fp])
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', '-R', fp])
        else:
            # Linux: 打开父目录
            subprocess.Popen(['xdg-open', os.path.dirname(fp)])
        return jsonify({'success': True, 'path': fp})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/process_scale', methods=['POST'])
def process_scale():
    """处理缩放校正（v2 算法：按目标高度归一）"""
    from utils.scale_processor import (
        apply_adjustments, scale_char, ALGORITHM_VERSION
    )
    import traceback

    try:
        data = request.get_json()
        image_hash = data.get('hash')
        characters = data.get('characters', [])
        # scale 现在是「目标高度倍数」：1.0 = fill_ratio * canvas 高度（默认 0.9 = 460）
        scale = float(data.get('scale', 1.0))
        align = data.get('align', 'center')
        background = data.get('background', 'black')
        target_size = data.get('target_size', 512)
        # 字符填满画布的比例（高度方向）：0.9 = 字高 460/512
        fill_ratio = float(data.get('fill_ratio', 0.9))
        # 超宽字保护的宽度上限：0.95 = 宽字符最多占 95% 画布宽
        max_width_ratio = float(data.get('max_width_ratio', 0.95))

        if not image_hash:
            return jsonify({'error': '缺少图片哈希'}), 400

        # 创建输出目录
        output_dir = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled')
        os.makedirs(output_dir, exist_ok=True)

        processed_characters = []

        for i, char in enumerate(characters):
            try:
                # 读取原始图片
                filename = char.get('filename', char.get('original_filename', ''))
                if not filename:
                    print(f"字符 {i} 缺少 filename 字段: {char}")
                    continue

                original_path = os.path.join(char_dir(image_hash), filename)
                if not os.path.exists(original_path):
                    print(f"文件不存在: {original_path}")
                    continue

                img = load_image(original_path)

                # 应用调整值（如果有）
                adjust_top = char.get('adjust_top', 0) or 0
                adjust_bottom = char.get('adjust_bottom', 0) or 0
                adjust_left = char.get('adjust_left', 0) or 0
                adjust_right = char.get('adjust_right', 0) or 0

                if adjust_top > 0 or adjust_bottom > 0 or adjust_left > 0 or adjust_right > 0:
                    img = apply_adjustments(img, adjust_top, adjust_bottom, adjust_left, adjust_right)

                # 处理缩放和居中（v2：按目标高度归一）
                processed = scale_char(
                    img, scale, target_size, align, background,
                    fill_ratio=fill_ratio, max_width_ratio=max_width_ratio,
                )

                # 保存
                output_filename = f"scaled_{i:04d}.png"
                output_path = os.path.join(output_dir, output_filename)

                if background == 'transparent':
                    cv2.imwrite(output_path, processed)
                else:
                    save_image(processed, output_path)

                processed_characters.append({
                    'index': i,
                    'original_filename': filename,
                    'processed_filename': output_filename,
                    'processed_url': f'/output/{image_hash}/scaled/{output_filename}'
                })
            except Exception as e:
                print(f"处理字符 {i} 失败: {e}")
                traceback.print_exc()
                continue

        # 把算法版本写入 session（让 /api/save_scaled 知道是否需要重跑）
        return jsonify({
            'success': True,
            'characters': processed_characters,
            'output_dir': output_dir,
            'total': len(processed_characters),
            'algorithm_version': ALGORITHM_VERSION,
        })
    except Exception as e:
        print(f"process_scale 错误: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/save_scaled', methods=['POST'])
def save_scaled():
    """保存缩放校正结果

    自动重生成机制：
    - 如果 session 里的 scale_algorithm_version < 当前 ALGORITHM_VERSION
      （或缺失，默认当 v1），说明磁盘上 scaled_*.png 是旧算法生成的。
      本次保存会主动重跑所有字符 + 覆盖 scaled 目录。
    - 如果版本已是最新，仅写元数据，不动磁盘。
    """
    from utils.scale_processor import (
        apply_adjustments, scale_char, ALGORITHM_VERSION
    )
    import traceback

    data = request.get_json()
    image_hash = data.get('hash')
    characters = data.get('characters', [])

    if not image_hash:
        return jsonify({'error': '缺少图片哈希'}), 400

    # 加载会话数据
    session_data = load_session(image_hash, DATA_FOLDER)
    if not session_data:
        return jsonify({'error': '会话不存在'}), 404

    # 取出本批次的处理参数（前端 /api/process_scale 返回时带回的）
    # 若前端没传，回退到 session 中上次保存的；都没有则用新算法默认值
    scale = float(data.get('scale', session_data.get('scale_height_multiplier', 1.0)))
    fill_ratio = float(data.get('fill_ratio', session_data.get('scale_fill_ratio', 0.9)))
    max_width_ratio = float(data.get('max_width_ratio', session_data.get('scale_max_width_ratio', 0.95)))
    align = data.get('align', session_data.get('scale_align', 'center'))
    background = data.get('background', session_data.get('scale_background', 'black'))
    target_size = int(data.get('target_size', 512))

    # === 自动重生成：旧版本算法 → 用新算法重跑所有字符 ===
    old_version = int(session_data.get('scale_algorithm_version', 1))
    need_regenerate = old_version < ALGORITHM_VERSION
    regenerated_count = 0

    if need_regenerate:
        # 优先用本次请求的 characters；缺失时回退到 session.characters
        src_chars = characters if characters else session_data.get('characters', [])
        output_dir = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled')
        os.makedirs(output_dir, exist_ok=True)
        new_processed = []
        for i, char in enumerate(src_chars):
            try:
                filename = char.get('original_filename') or char.get('filename', '')
                if not filename:
                    continue
                original_path = os.path.join(char_dir(image_hash), filename)
                if not os.path.exists(original_path):
                    print(f"save_scaled 重生成：文件不存在 {original_path}")
                    continue
                img = load_image(original_path)
                # 应用调整值
                adjust_top = char.get('adjust_top', 0) or 0
                adjust_bottom = char.get('adjust_bottom', 0) or 0
                adjust_left = char.get('adjust_left', 0) or 0
                adjust_right = char.get('adjust_right', 0) or 0
                if adjust_top > 0 or adjust_bottom > 0 or adjust_left > 0 or adjust_right > 0:
                    img = apply_adjustments(img, adjust_top, adjust_bottom, adjust_left, adjust_right)
                # 用新算法缩放
                processed = scale_char(
                    img, scale, target_size, align, background,
                    fill_ratio=fill_ratio, max_width_ratio=max_width_ratio,
                )
                output_filename = f"scaled_{i:04d}.png"
                output_path = os.path.join(output_dir, output_filename)
                if background == 'transparent':
                    cv2.imwrite(output_path, processed)
                else:
                    save_image(processed, output_path)
                new_processed.append({
                    'index': i,
                    'original_filename': filename,
                    'processed_filename': output_filename,
                    'processed_url': f'/output/{image_hash}/scaled/{output_filename}'
                })
                regenerated_count += 1
            except Exception as e:
                print(f"save_scaled 重生成字符 {i} 失败: {e}")
                traceback.print_exc()
                continue
        # 用新生成的列表替换本次的 characters（保证磁盘和元数据一致）
        characters = new_processed
        print(f"save_scaled 自动重生成：{image_hash}, v{old_version} → v{ALGORITHM_VERSION}, {regenerated_count} 个字符")

    # 保存缩放校正数据 + 算法版本
    session_data['scaled_characters'] = characters
    session_data['scale_processed'] = True
    session_data['scale_algorithm_version'] = ALGORITHM_VERSION
    # 把参数也存下来，下次 save_scaled 重生成时能拿到（前端可能不重传）
    session_data['scale_height_multiplier'] = scale
    session_data['scale_fill_ratio'] = fill_ratio
    session_data['scale_max_width_ratio'] = max_width_ratio
    session_data['scale_align'] = align
    session_data['scale_background'] = background
    save_session(image_hash, session_data, DATA_FOLDER)

    return jsonify({
        'success': True,
        'regenerated': need_regenerate,
        'regenerated_count': regenerated_count,
        'algorithm_version': ALGORITHM_VERSION,
    })


# 进程内锁：OCR 每张卡都会 POST save，多个请求并发「读→改→写」同一 json，
# 无锁会让一方读到另一方写半截的文件 → JSONDecodeError。串行化 + 原子写解决。
_ocr_annotations_lock = threading.Lock()


def _persist_one_ocr_annotation(image_hash, filename, simplified, conf=0.0, source='ocr',
                                traditional=None, now=None, keep_manual=True):
    """写/删单条标注到 ocr_annotations.json（调用方须已持有 _ocr_annotations_lock）。

    供三处共用，保证写入逻辑唯一：
      - /api/save_ocr_annotation（前端 OCR 填卡实时写）
      - OCR worker（后端每张识别完直接写，不依赖前端轮询回填）
      - import_ocr_tasks 之外的手动导入
    - simplified 非空 → 写入记录对象
    - simplified 为空 → 删除该 filename
    - keep_manual=True 时，若已有 source='manual' 的记录则跳过（保护人工标注）

    注意：此函数不自己拿锁——由调用方在 with _ocr_annotations_lock 内调用，
    因为批量场景（bulk_save）需要把多次更新合并到一次读改写里。
    """
    from datetime import datetime
    if not filename:
        return False
    path = os.path.join(DATA_FOLDER, image_hash, 'ocr_annotations.json')
    annotations = {}
    if os.path.exists(path):
        try:
            with open(path, 'r', encoding='utf-8') as f:
                annotations = json.load(f)
        except (json.JSONDecodeError, OSError):
            annotations = {}

    simplified = (simplified or '').strip()
    if not simplified:
        # 空 = 删除该卡标注
        annotations.pop(filename, None)
    else:
        existing = annotations.get(filename)
        # 保护已有人工标注（manual 标记）的卡——除非显式覆盖（keep_manual=False）
        if keep_manual and isinstance(existing, dict) and existing.get('source') == 'manual':
            return False
        # traditional 缺省时继承旧记录（worker 直写无 traditional 时保留旧的）
        if traditional is None and isinstance(existing, dict):
            traditional = existing.get('traditional', '')
        annotations[filename] = {
            'simplified': simplified,
            'traditional': traditional or '',
            'conf': round(float(conf), 3),
            'source': source,
            'updated_at': now or datetime.now().strftime('%Y-%m-%dT%H:%M:%S'),
        }

    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(annotations, f, ensure_ascii=False, indent=2)
    return True


def _delete_ocr_annotations_by_keys(image_hash, keys):
    """从 ocr_annotations.json 删除一组 key（删卡后清理该卡对应标注）。

    keys 是文件名集合（char_*.png / scaled_*.png）。只删存在的 key，全空则
    不写文件。并发安全策略同 _persist_one_ocr_annotation：进程内锁串行化
    「读→改→写」后直接写目标文件（Windows 上 os.replace 覆盖被读方打开的
    文件会 PermissionError，所以不用原子替换，见 save_ocr_annotation 的注释）。

    注意：调用此函数时不另拿锁——本函数内部自己 with _ocr_annotations_lock。
    """
    path = os.path.join(DATA_FOLDER, image_hash, 'ocr_annotations.json')
    with _ocr_annotations_lock:
        if not os.path.exists(path):
            return
        try:
            with open(path, 'r', encoding='utf-8') as f:
                annotations = json.load(f)
        except (json.JSONDecodeError, OSError):
            return  # 文件坏了就跳过清理，不让删卡接口因此失败
        removed = 0
        for k in keys:
            if k in annotations:
                del annotations[k]
                removed += 1
        if removed:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(annotations, f, ensure_ascii=False, indent=2)


@app.route('/api/save_ocr_annotation/<image_hash>', methods=['POST'])
def save_ocr_annotation(image_hash):
    """保存/删除单条 OCR 标注（持久化到 data/sessions/<hash>/ocr_annotations.json）

    请求体: { filename: str, simplified: str, conf?: float, source?: 'ocr'|'manual' }
    - filename：卡的稳定标识（scaled_0002.png / char_0000.png），作为 json key
      不用卡下标 idx——下标会随排序/删卡变化，filename 稳定且与 OCR 任务
      results 里的 filename 一致，便于任务结果直接对号导入。

    存储值：对象（更详细，刷新页面可完整恢复）：
        {"simplified":"牲","conf":0.995,"source":"ocr","updated_at":"2026-09-09T18:26:07"}
    - simplified 非空：写入（覆盖旧值）
    - simplified 为空：删除该条 —— 用户手动改了 OCR 结果的卡不应再被恢复
    - 与 cutting.json 分开存：OCR 标注是用户数据，cutting 是几何/算法状态

    并发安全：OCR 每填一张卡就 POST 一次，且前端轮询一次会连续 POST 多条。
    若直接读-改-写，请求 A 读到请求 B 写到一半的文件 → json.load 崩。
    方案：进程内锁串行化「读→改→写」——锁保证同进程内绝无并发重叠，
    所以直接写目标文件即可（读方永远在锁外等，不会看到半截文件）。
    不用 os.replace 原子替换：Windows 上 replace 覆盖「被读方打开的」
    文件会抛 PermissionError，反而不稳。
    """
    data = request.get_json() or {}
    filename = data.get('filename') or ''
    # 兼容旧字段名 char/simp（万一老前端还在发）
    simplified = data.get('simplified') or data.get('char') or data.get('simp') or ''
    conf = data.get('conf') or 0.0
    source = data.get('source') or 'ocr'
    traditional = data.get('traditional')

    if not filename:
        return jsonify({'success': False, 'error': '缺少 filename'}), 400

    # 前端 OCR 填卡时实时写（带繁体）。source='ocr'，不覆盖已有 manual 标注
    with _ocr_annotations_lock:
        _persist_one_ocr_annotation(image_hash, filename, simplified, conf, source,
                                    traditional=traditional)
    return jsonify({'success': True})


@app.route('/api/bulk_save_ocr_annotations/<image_hash>', methods=['POST'])
def bulk_save_ocr_annotations(image_hash):
    """批量保存人工标注

    请求体: { annotations: { filename: simplified, ... } }
    - 与 /api/save_ocr_annotation 一样写到 ocr_annotations.json（key=filename）
    - 但 source 标记为 'manual'（人工标注），OCR 自动写的会被这条标记区分
    - 一致并发安全（同进程内锁）

    用途：用户跑 OCR 后手动校正几张卡，点「保存标注」按钮 → 把当前页
    所有 simpInput.value 非空的卡写到磁盘。OCR 阶段已经实时写过
    'ocr' 标记的记录，这里 'manual' 是补充——不会覆盖已有 manual。
    """
    from datetime import datetime
    data = request.get_json() or {}
    incoming = data.get('annotations') or {}
    if not isinstance(incoming, dict):
        return jsonify({'success': False, 'error': 'annotations 必须是 dict'}), 400

    path = os.path.join(DATA_FOLDER, image_hash, 'ocr_annotations.json')
    with _ocr_annotations_lock:
        annotations = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    annotations = json.load(f)
            except (json.JSONDecodeError, OSError):
                print(f"[bulk_save_ocr_annotations] {image_hash} 读坏文件，按空处理")
                annotations = {}

        now = datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
        for filename, val in incoming.items():
            if not filename:
                continue
            # 值可以是：对象 {simplified, traditional}（保存标注按钮新版），
            # 或字符串简化（旧版兼容）
            if isinstance(val, dict):
                simplified = (val.get('simplified') or '').strip()
                new_trad = (val.get('traditional') or '').strip()
            else:
                simplified = (val or '').strip()
                new_trad = ''
            if not simplified:
                # 空值 = 删除（用户主动清掉这张卡的标注）
                annotations.pop(filename, None)
                continue
            existing = annotations.get(filename)
            # 注：不因 existing 是 manual 就跳过——「保存标注」就是用户显式想覆盖
            # 当前页面内容，旧 manual 记录（如曾存空 traditional）也要能更新。
            # 繁体：本次传入优先；没传则继承已有记录（OCR 已算好的繁体，别丢）
            prev_trad = existing.get('traditional', '') if isinstance(existing, dict) else ''
            annotations[filename] = {
                'simplified': simplified,
                'traditional': new_trad or prev_trad,
                'conf': existing.get('conf', 0.0) if isinstance(existing, dict) else 0.0,
                'source': 'manual',
                'updated_at': now,
            }

        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(annotations, f, ensure_ascii=False, indent=2)

    saved = sum(1 for fn in incoming if incoming.get(fn))
    print(f"[bulk_save_ocr_annotations] {image_hash}: 接收 {len(incoming)} 条, 实际写入 {saved} 条")
    return jsonify({'success': True, 'received': len(incoming), 'saved': saved})


@app.route('/api/bulk_fill_traditional/<image_hash>', methods=['POST'])
def bulk_fill_traditional(image_hash):
    """批量给已有标注记录补 traditional（仅更新繁体字段，保留 simplified/conf/source）

    用途：OCR worker 直写记录时只有 simplified（后端不做繁简转换）。
    前端 OCR done 后批量把繁体算好，POST 到这里给每张已有记录补上 traditional，
    之后刷新页面直接读 json，不再重复调转换接口。

    请求体: { traditional: { filename: "華", ... } }
    """
    data = request.get_json() or {}
    incoming = data.get('traditional') or {}
    if not isinstance(incoming, dict):
        return jsonify({'success': False, 'error': 'traditional 必须是 dict'}), 400

    path = os.path.join(DATA_FOLDER, image_hash, 'ocr_annotations.json')
    with _ocr_annotations_lock:
        annotations = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    annotations = json.load(f)
            except (json.JSONDecodeError, OSError):
                annotations = {}

        updated = 0
        for fn, trad in incoming.items():
            trad = (trad or '').strip()
            if not fn or not trad:
                continue
            existing = annotations.get(fn)
            # 只更新已有记录（OCR worker 或 manual 都行）的传统字段
            if isinstance(existing, dict) and existing.get('simplified'):
                existing['traditional'] = trad
                updated += 1

        with open(path, 'w', encoding='utf-8') as f:
            json.dump(annotations, f, ensure_ascii=False, indent=2)

    print(f"[bulk_fill_traditional] {image_hash}: 补 {updated} 张繁体")
    return jsonify({'success': True, 'updated': updated})


def _backfill_ocr_traditional(image_hash, results):
    """OCR 任务完成后，给本任务识别出的记录批量补繁体并落盘。

    服务端自主执行，不依赖前端 poll/页面是否开着。OpenCC 用模块级缓存的
    _S2T_CONVERTER（毫秒级）。这样无论用户前端是哪个版本、是否刷新，
    ocr_annotations.json 里的记录都会带上 traditional，之后读 json 直接显示。

    results: task['results']（每项含 filename/character/confidence）。
    只补「缺 traditional 且 simplified 非空」的记录，不动已有传统（用户可能已手动标）。
    """
    if not results or _S2T_CONVERTER is None:
        return 0
    path = os.path.join(DATA_FOLDER, image_hash, 'ocr_annotations.json')
    updated = 0
    with _ocr_annotations_lock:
        annotations = {}
        if os.path.exists(path):
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    annotations = json.load(f)
            except (json.JSONDecodeError, OSError):
                annotations = {}
        for r in results:
            fn = r.get('filename')
            if not fn:
                continue
            existing = annotations.get(fn)
            if isinstance(existing, dict) and existing.get('simplified') and not existing.get('traditional'):
                try:
                    existing['traditional'] = _S2T_CONVERTER.convert(existing['simplified'])
                    updated += 1
                except Exception:
                    continue
        if updated:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(annotations, f, ensure_ascii=False, indent=2)
    if updated:
        print(f"[OCR backfill] {image_hash}: 批量补 {updated} 条繁体（服务端）")
    return updated


@app.route('/api/get_ocr_annotations/<image_hash>', methods=['GET'])
def get_ocr_annotations(image_hash):
    """获取这个 session 的所有 OCR 标注

    响应: { success, annotations: {idx_str: char} }
    """
    path = os.path.join(DATA_FOLDER, image_hash, 'ocr_annotations.json')
    with _ocr_annotations_lock:
        if not os.path.exists(path):
            return jsonify({'success': True, 'annotations': {}})
        try:
            with open(path, 'r', encoding='utf-8') as f:
                annotations = json.load(f)
            return jsonify({'success': True, 'annotations': annotations})
        except Exception as e:
            print(f"[get_ocr_annotations] {image_hash} 读失败: {e}")
            return jsonify({'success': True, 'annotations': {}})


@app.route('/api/cleanup_intermediate', methods=['POST'])
def cleanup_intermediate():
    """清理中间过程文件，只保留最终导出目录

    参数:
        hash:        session 哈希（必填）
        keep_dir:    保留的本次导出目录（旧逻辑用，按时间戳子目录精确保留）
        keep_exported: bool（新增）。为 true 时不动 exported/，只清过程目录；
                       供「清理过程图」按钮用——它要清的是过程图，导出目录要保留

    清理范围：cutting_output/char_*.png、scaled/ 整个目录、ocr_tasks/ 整个目录。
    - ocr_tasks 是 OCR 任务中间状态（每张图识别结果 + 进度），已落盘到 ocr_annotations.json
      的标注不会丢；下次再 OCR 会重新生成
    - 当 keep_exported=True 时，旧 exported/ 子目录全部保留
    - 当 keep_exported=False（默认，旧行为）且给了 keep_dir：删 keep_dir 外的所有 exported 子目录
    """
    import shutil

    data = request.get_json()
    image_hash = data.get('hash')
    keep_dir = data.get('keep_dir', '')  # 保留的最终输出目录
    keep_exported = bool(data.get('keep_exported', False))

    if not image_hash:
        return jsonify({'error': '缺少图片哈希'}), 400

    base_dir = os.path.join(OUTPUT_FOLDER, image_hash)
    if not os.path.exists(base_dir):
        return jsonify({'success': True, 'message': '目录不存在，无需清理', 'deleted_count': 0})

    cleaned = []
    # 1. 清理切割后的原始文件 (char_XXXX.png 在 cutting_output/ 下)
    char_output = os.path.join(base_dir, CHAR_DIR_NAME)
    if os.path.isdir(char_output):
        for f in os.listdir(char_output):
            fpath = os.path.join(char_output, f)
            if os.path.isfile(fpath) and f.startswith('char_') and f.endswith('.png'):
                os.remove(fpath)
                cleaned.append(f)
    # 2. 遍历 session 根的其他目录
    for f in os.listdir(base_dir):
        fpath = os.path.join(base_dir, f)
        if os.path.isdir(fpath):
            dirname = f.lower()
            # 清理 scaled 目录（过程图）
            if dirname == 'scaled':
                shutil.rmtree(fpath)
                cleaned.append(f'{f}/ (整个目录)')
            # 清理 ocr_tasks 目录（OCR 任务中间状态；标注结果已落 ocr_annotations.json 不丢）
            elif dirname == 'ocr_tasks':
                shutil.rmtree(fpath)
                cleaned.append(f'{f}/ (整个目录)')
            # 处理 exported 目录
            elif dirname == 'exported':
                if keep_exported:
                    # 「清理过程图」按钮场景：导出目录全部保留
                    continue
                # 旧行为：删 keep_dir 外的所有旧导出
                for sub in os.listdir(fpath):
                    sub_path = os.path.join(fpath, sub)
                    if os.path.isdir(sub_path) and sub_path != keep_dir:
                        shutil.rmtree(sub_path)
                        cleaned.append(f'{f}/{sub}/ (旧导出)')

    return jsonify({
        'success': True,
        'cleaned': cleaned,
        'deleted_count': len(cleaned),
        'message': f'已清理 {len(cleaned)} 项中间文件'
    })


@app.route('/api/open_directory', methods=['POST'])
def open_directory():
    """打开目录"""
    import subprocess
    import platform

    data = request.get_json()
    path = data.get('path', '')

    if not path or not os.path.exists(path):
        return jsonify({'error': '目录不存在'}), 400

    try:
        system = platform.system()
        if system == 'Windows':
            os.startfile(path)
        elif system == 'Darwin':  # macOS
            subprocess.run(['open', path])
        else:  # Linux
            subprocess.run(['xdg-open', path])
        return jsonify({'success': True})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# 静态文件服务 - 输出目录
@app.route('/output/<path:filename>')
def serve_output(filename):
    """提供输出文件访问（强缓存 1 年：文件名带 hash+序号，唯一）"""
    from flask import send_from_directory
    response = send_from_directory(OUTPUT_FOLDER, filename)
    response.headers['Cache-Control'] = 'public, max-age=31536000, immutable'
    return response


# ==================== 标注出图 API ====================

@app.route('/api/get_scaled_results/<image_hash>')
def get_scaled_results(image_hash):
    """获取缩放校正后的结果"""
    session_data = load_session(image_hash, DATA_FOLDER)
    if not session_data:
        return jsonify({'success': False, 'error': '会话不存在'})

    # 优先获取缩放校正后的数据
    characters = session_data.get('scaled_characters', [])
    if not characters:
        # 如果没有缩放校正数据，返回原始切割结果
        characters = session_data.get('characters', [])

    output_dir = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled')

    # 若无 scaled_characters 走了 characters 兜底，需规范化旧 image_url
    # （scaled_characters 用的是 processed_url 字段，normalize 不会误伤）
    _normalize_char_urls(characters, image_hash)

    return jsonify({
        'success': True,
        'characters': characters,
        'output_dir': output_dir
    })


@app.route('/api/convert_to_traditional', methods=['POST'])
def convert_to_traditional():
    """简体转繁体（用模块级缓存的 OpenCC 实例，不每请求重建）"""
    data = request.get_json() or {}
    text = data.get('text', '')
    if not text:
        return jsonify({'success': True, 'result': ''})

    if _S2T_CONVERTER is None:
        return jsonify({'success': True, 'result': text})  # opencc 未初始化，原样返回

    try:
        result = _S2T_CONVERTER.convert(text)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        print(f"简转繁错误: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/convert_to_simplified', methods=['POST'])
def convert_to_simplified():
    """繁体转简体（用模块级缓存的 OpenCC 实例，不每请求重建）"""
    data = request.get_json() or {}
    text = data.get('text', '')
    if not text:
        return jsonify({'success': True, 'result': ''})

    if _T2S_CONVERTER is None:
        return jsonify({'success': True, 'result': text})  # opencc 未初始化，原样返回

    try:
        result = _T2S_CONVERTER.convert(text)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        print(f"繁转简错误: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/export_annotated', methods=['POST'])
def export_annotated():
    """启动异步导出任务，返回 task_id

    329 张反色+保存可能要 5-10s，sync 会让请求挂死。改成后台线程 +
    进度轮询（与 /api/ocr_start 模式一致）。客户端轮询 /api/export_progress/<id>。
    """
    from datetime import datetime

    data = request.get_json()
    image_hash = data.get('hash')
    annotations = data.get('annotations', [])
    mode = data.get('mode', 'traditional')

    if not image_hash:
        return jsonify({'success': False, 'error': '缺少图片哈希'}), 400
    if not annotations:
        return jsonify({'success': False, 'error': '没有标注数据，请先标注字符'}), 400

    # 创建带时间戳的输出目录（同步，确保客户端拿到 task_id 时目录已存在）
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    export_dir = os.path.join(OUTPUT_FOLDER, image_hash, 'exported', timestamp)
    os.makedirs(export_dir, exist_ok=True)

    task_id = uuid.uuid4().hex[:12]
    with _export_tasks_lock:
        _export_tasks[task_id] = {
            'status': 'running',
            'total': len(annotations),
            'done': 0,
            'count': 0,           # 成功导出数
            'errors': [],        # 错误列表
            'output_dir': export_dir,
            'new_errors': [],    # 增量错误（轮询取走后清空）
            'error': None,
            'finished_at': 0.0,
            'started_at': time.time(),
        }

    def worker():
        task = _export_tasks[task_id]
        char_counts = {}
        # CSV 行缓存：{filename, unicode, character, simplified, traditional}
        # source_map：{源文件名(ann.filename): 导出名}（只有 ann 带 filename 才记）
        csv_rows = []
        source_map = {}
        try:
            for ann in annotations:
                err = None
                png_filename = None  # 记录本次成功写入的导出文件名（用于 CSV/source_map）
                try:
                    # 1. 尝试 ann.filename 走 scaled/ 子目录（最常见路径）
                    src_path = None
                    if ann.get('filename') and ann['filename'].startswith('scaled_'):
                        sp = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled',
                                           ann['filename'])
                        if os.path.exists(sp):
                            src_path = sp
                        else:
                            # 兼容老数据：session 根
                            sp2 = os.path.join(OUTPUT_FOLDER, image_hash, ann['filename'])
                            if os.path.exists(sp2):
                                src_path = sp2
                    # 2. 尝试 ann.filename 是 char_*.png（/adjust 删卡时也走这条）
                    if not src_path and ann.get('filename'):
                        sp = os.path.join(char_dir(image_hash), ann['filename'])
                        if not os.path.exists(sp):
                            sp = os.path.join(OUTPUT_FOLDER, image_hash, ann['filename'])
                        if os.path.exists(sp):
                            src_path = sp
                    # 3. 尝试用 index 推断 scaled 文件（保底，序号与 index 一致时）
                    if not src_path:
                        sp = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled',
                                           f"scaled_{ann['index']:04d}.png")
                        if os.path.exists(sp):
                            src_path = sp
                    # 4. 尝试 original_filename（同样 cutting_output/ 优先）
                    if not src_path and ann.get('original_filename'):
                        sp = os.path.join(char_dir(image_hash), ann['original_filename'])
                        if not os.path.exists(sp):
                            sp = os.path.join(OUTPUT_FOLDER, image_hash, ann['original_filename'])
                        if os.path.exists(sp):
                            src_path = sp

                    if not src_path:
                        err = f"找不到文件: index={ann['index']}"
                    else:
                        char = ann.get('character', '')
                        if not char:
                            err = f"没有字符: index={ann['index']}"
                        else:
                            # 读取 + 反色
                            img = load_image(src_path)
                            inverted = cv2.bitwise_not(img)
                            # 命名：uniXXXX / uXXXXX + 重复后缀（与 export_csv 共享规则）
                            code = ord(char[0])
                            if char in char_counts:
                                char_counts[char] += 1
                                suffix = f"_{char_counts[char]:02d}"
                            else:
                                char_counts[char] = 0
                                suffix = ""
                            if code > 0xFFFF:
                                png_filename = f"u{code:05X}{suffix}.png"
                            else:
                                png_filename = f"uni{code:04X}{suffix}.png"
                            output_path = os.path.join(export_dir, png_filename)
                            save_image(inverted, output_path)
                            # 同步累积 CSV 行 + source_map 映射（导出 PNG 后立刻收，
                            # 后续 write 阶段一次落盘；命名和计数与 export_csv 完全一致）
                            csv_rows.append([
                                png_filename,
                                f"U+{code:04X}" if code <= 0xFFFF else f"U+{code:05X}",
                                char,
                                ann.get('simplified', ''),
                                ann.get('traditional', '')
                            ])
                            if ann.get('filename'):
                                source_map[ann['filename']] = png_filename
                except Exception as e:
                    err = f"导出失败 index={ann.get('index')}: {e}"

                # 写结果
                with _export_tasks_lock:
                    task['done'] += 1
                    if err is None:
                        task['count'] += 1
                    else:
                        task['errors'].append(err)
                        task['new_errors'].append(err)

            # PNG 全部写完 → 同子目录落 CSV + source_map（与 PNG 同时间戳子目录，
            # 原子化三件套）。ai-font-tool 训练脚本按子目录的 source_map 加载样本图。
            # 出错也不抛：CSV 是附属产物，PNG 才是主产物
            if csv_rows:
                ts_name = os.path.basename(export_dir)  # 时间戳子目录名
                csv_path = os.path.join(export_dir, f"fontlab_{ts_name}.csv")
                map_path = os.path.join(export_dir, f"fontlab_{ts_name}.source_map.json")
                try:
                    import csv
                    with open(csv_path, 'w', newline='', encoding='utf-8') as cf:
                        w = csv.writer(cf)
                        w.writerow(['filename', 'unicode', 'character',
                                    'simplified', 'traditional'])
                        w.writerows(csv_rows)
                    if source_map:
                        with open(map_path, 'w', encoding='utf-8') as mf:
                            json.dump(source_map, mf, ensure_ascii=False, indent=2)
                    with _export_tasks_lock:
                        task['csv_path'] = csv_path
                        task['mapping_path'] = map_path
                except Exception as ce:
                    print(f"[export_annotated] CSV 写入失败: {ce}")
                    with _export_tasks_lock:
                        task['csv_error'] = str(ce)

            with _export_tasks_lock:
                task['status'] = 'done'
                task['finished_at'] = time.time()
            elapsed = time.time() - task['started_at']
            print(f"导出任务完成: {task_id}, {task['count']}/{task['total']} 成功, "
                  f"CSV {len(csv_rows)} 行, {elapsed:.1f}s")
        except Exception as e:
            print(f"导出任务异常: {task_id}, {e}")
            import traceback
            traceback.print_exc()
            with _export_tasks_lock:
                task['status'] = 'error'
                task['error'] = str(e)
                task['finished_at'] = time.time()

    threading.Thread(target=worker, daemon=True).start()
    print(f"导出任务启动: {task_id}, hash={image_hash}, {len(annotations)} 张")

    return jsonify({
        'success': True,
        'task_id': task_id,
        'total': len(annotations),
        'output_dir': export_dir,
    })


@app.route('/api/export_progress/<task_id>', methods=['GET'])
def export_progress(task_id):
    """轮询导出任务进度 + 增量错误"""
    with _export_tasks_lock:
        task = _export_tasks.get(task_id)
        if not task:
            return jsonify({'status': 'not_found'})
        new_errors = task['new_errors']
        task['new_errors'] = []
        return jsonify({
            'status': task['status'],
            'done': task['done'],
            'total': task['total'],
            'count': task['count'],
            'new_errors': new_errors,
            'errors': task['errors'],
            'output_dir': task['output_dir'],
            'csv_path': task.get('csv_path'),
            'mapping_path': task.get('mapping_path'),
            'csv_row_count': task.get('count'),  # PNG 成功数 == CSV 行数
            'csv_error': task.get('csv_error'),
            'error': task.get('error'),
        })


# 导出任务存储 + 清理（与 OCR 任务一致）
_export_tasks = {}
_export_tasks_lock = threading.Lock()
_EXPORT_TASK_TTL = 600


def _export_task_cleanup():
    while True:
        time.sleep(60)
        cutoff = time.time() - _EXPORT_TASK_TTL
        with _export_tasks_lock:
            stale = [tid for tid, t in _export_tasks.items()
                     if t.get('finished_at', 0) < cutoff]
            for tid in stale:
                _export_tasks.pop(tid, None)


threading.Thread(target=_export_task_cleanup, daemon=True).start()


@app.route('/api/export_csv', methods=['POST'])
def export_csv():
    """导出 FontLab CSV 格式

    每个字符样本一行。同字重复样本加 _01/_02 后缀区分，规则与 /api/export_annotated
    的图片命名一致（README 承诺「重复字符自动加后缀区分」）。若漏了后缀，CSV 会出现
    大量同名 uniXXXX.png——下游把 label 对到图片文件时，同一汉字的多个样本互相覆盖
    （本次 bug：319 行只有 152 个唯一文件名，见版本历史 2026-09-09）。

    注解带 filename（源切图 scaled_*.png / char_*.png）时，顺手写一份同时间戳的
    <csv>.source_map.json：{源文件名: 导出文件名}，供下游把样本图与 label 对号入座。
    """
    import csv
    from datetime import datetime

    data = request.get_json()
    image_hash = data.get('hash')
    annotations = data.get('annotations', [])

    if not image_hash or not annotations:
        return jsonify({'error': '缺少参数'}), 400

    # 创建输出文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_filename = f"fontlab_{timestamp}.csv"
    csv_path = os.path.join(OUTPUT_FOLDER, image_hash, 'exported', csv_filename)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    # 同行字计数（与 export_annotated 同一套：首样本无后缀，第二个起 _01、_02…）。
    # 计数用字符串 char 做 key；名用其首个码点（python 单 str 直接含扩展区码点，无代理对问题）
    char_counts = {}
    source_map = {}
    rows = []
    for ann in annotations:
        char = (ann.get('primary') or '').strip()
        if not char:
            continue
        code = ord(char)
        if char in char_counts:
            char_counts[char] += 1
            suffix = f"_{char_counts[char]:02d}"
        else:
            char_counts[char] = 0
            suffix = ""
        if code > 0xFFFF:
            png_name = f"u{code:05X}{suffix}.png"
        else:
            png_name = f"uni{code:04X}{suffix}.png"
        rows.append([
            png_name,
            f"U+{code:04X}" if code <= 0xFFFF else f"U+{code:05X}",
            char,
            ann.get('simplified', ''),
            ann.get('traditional', '')
        ])
        # 源文件名 → 导出名 映射（仅当注解带着源文件名才记）
        if ann.get('filename'):
            source_map[ann['filename']] = png_name

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['filename', 'unicode', 'character', 'simplified', 'traditional'])
        writer.writerows(rows)

    # 源文件名 → 导出名 映射文件（非必需，下游要交叉验证才用得上）
    mapping_path = None
    if source_map:
        mapping_filename = f"fontlab_{timestamp}.source_map.json"
        mapping_path = os.path.join(OUTPUT_FOLDER, image_hash, 'exported', mapping_filename)
        with open(mapping_path, 'w', encoding='utf-8') as mf:
            json.dump(source_map, mf, ensure_ascii=False, indent=2)

    return jsonify({
        'success': True,
        'output_path': csv_path,
        'mapping_path': mapping_path,
        'row_count': len(rows)
    })


@app.route('/api/import_characters', methods=['POST'])
def import_characters():
    """从目录导入字符图片"""
    import hashlib

    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': '没有文件'}), 400

    # 计算目录哈希
    dir_path = os.path.dirname(files[0].filename) if files[0].filename else ''
    dir_hash = hashlib.md5(dir_path.encode()).hexdigest()[:16]

    # 创建输出目录
    output_dir = os.path.join(OUTPUT_FOLDER, dir_hash)
    os.makedirs(output_dir, exist_ok=True)

    characters = []
    idx = 0

    for file in files:
        if file.filename.lower().endswith(('.png', '.jpg', '.jpeg')):
            # 保存文件
            filename = f"char_{idx:04d}.png"
            filepath = os.path.join(output_dir, filename)
            file.save(filepath)

            characters.append({
                'index': idx,
                'filename': filename,
                'image_url': f'/output/{dir_hash}/{filename}'
            })
            idx += 1

    # 保存会话数据
    session_data = {
        'hash': dir_hash,
        'characters': characters,
        'imported': True
    }
    save_session(dir_hash, session_data, DATA_FOLDER)

    return jsonify({
        'success': True,
        'hash': dir_hash,
        'characters': characters,
        'output_dir': output_dir
    })


# === OCR 自动标注（异步：后台线程 + 进度轮询）===
# EasyOCR 在 CPU 上 ~1.5s/张，329 张 ≈ 8 分钟，sync 会让请求超时
# 设计：start 启动后台线程 → 返回 task_id → 前端轮询 progress
#       每次轮询返回增量 results（已填入数据库的），前端增量应用
# 准确率参考：50 张样本里 34% 高置信 (>=0.5)，书法字 OCR 本身不可靠
# 客户端默认只填入 >=0.5 置信度的结果，低置信度留给用户手动确认

# 全局 task 存储：{task_id: {status, results, total, done, error}}
# task 在 done/errored 后保留 10 分钟供前端最终拉取，然后清掉
# 持久化：每个 task 同步存盘到 data/sessions/<hash>/ocr_tasks/<id>.json，Flask 重启不丢
#   - worker 每张图处理完写一次（300ms/图，磁盘写 <10ms，可接受）
#   - 启动时扫描所有 session 目录的 ocr_tasks/，running 状态标为 interrupted
#     （worker 线程死了，但已完成的结果都还在）
_ocr_tasks = {}
_ocr_tasks_lock = threading.Lock()
_OCR_TASK_TTL = 600  # 10 分钟


def _ocr_task_dir(image_hash: str) -> str:
    """单个 session 的 OCR task 目录"""
    return os.path.join(DATA_FOLDER, image_hash, 'ocr_tasks')


def _save_ocr_task(task_id, task):
    """把单个 task 状态写到磁盘（持锁拷贝字段，写盘在锁外避免阻塞读）

    task 文件只是「进度快照 + 断点」，真正的标注已由 worker 实时写进
    ocr_annotations.json。所以这里写失败不能中断 worker——打印警告跳过
    即可（下一次完成还会重写）。Windows 上偶发 PermissionError（文件
    短暂被读方/另一实例占住），重试几次能跳过绝大多数。
    """
    image_hash = task.get('image_hash', '')
    if not image_hash:
        return  # 没有 image_hash 没法定位目录
    with _ocr_tasks_lock:
        snapshot = {
            'task_id': task_id,
            'status': task['status'],
            'total': task['total'],
            'done': task['done'],
            'results': list(task.get('results', [])),
            'new_results': list(task.get('new_results', [])),
            'error': task.get('error'),
            'finished_at': task.get('finished_at', 0.0),
            'started_at': task.get('started_at', 0.0),
            'image_hash': image_hash,
            'use_scaled': task.get('use_scaled', True),
            'threshold': task.get('threshold', 0.5),
        }
    path = os.path.join(_ocr_task_dir(image_hash), f'{task_id}.json')
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # 短重试：Windows 偶发共享冲突，等 50ms 再试
        for attempt in range(3):
            try:
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(snapshot, f, ensure_ascii=False)
                return True
            except PermissionError:
                if attempt < 2:
                    time.sleep(0.05)
                else:
                    raise
    except OSError as e:
        # 快照写失败不致命（标注已实时落盘），记录即可，勿中断 worker
        print(f"[ocr_task] 快照写盘失败（忽略，不影响已保存的标注）: {e}")
        return False
    return True


def _load_ocr_tasks_on_startup():
    """Flask 启动时从磁盘加载所有 task 到内存

    关键处理：之前 status=running 的 task 说明 server 被杀时还在跑，
    worker 线程已死，无法恢复处理。但已完成的结果都还在磁盘上。
    这里把它标为 'interrupted'，前端 poll 时能看到部分结果 + 这个状态。
    """
    if not os.path.isdir(DATA_FOLDER):
        return
    now = time.time()
    loaded = 0
    # 扫描每个 session 目录的 ocr_tasks/
    for session_dir_name in os.listdir(DATA_FOLDER):
        session_path = os.path.join(DATA_FOLDER, session_dir_name)
        if not os.path.isdir(session_path):
            continue
        ocr_dir = os.path.join(session_path, 'ocr_tasks')
        if not os.path.isdir(ocr_dir):
            continue
        for fn in os.listdir(ocr_dir):
            if not fn.endswith('.json'):
                continue
            path = os.path.join(ocr_dir, fn)
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                task_id = data.get('task_id')
                if not task_id:
                    continue
                # 之前是 running → 标 interrupted
                if data.get('status') == 'running':
                    data['status'] = 'interrupted'
                    data['error'] = '服务器中断，worker 线程已死，请重新开始'
                # 过期清理
                elif data.get('finished_at', 0) < now - _OCR_TASK_TTL:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
                    continue
                with _ocr_tasks_lock:
                    _ocr_tasks[task_id] = data
                loaded += 1
            except Exception as e:
                print(f"[OCR 启动] 加载 task {fn} 失败: {e}")
    if loaded:
        print(f"[OCR 启动] 从磁盘恢复 {loaded} 个 task")


# 启动时立即加载
_load_ocr_tasks_on_startup()


def _ocr_task_cleanup():
    """定期清理过期的 OCR task（内存 + 磁盘），避免泄漏"""
    while True:
        time.sleep(60)
        cutoff = time.time() - _OCR_TASK_TTL
        with _ocr_tasks_lock:
            stale = [tid for tid, t in _ocr_tasks.items()
                     if t.get('finished_at', 0) < cutoff]
            for tid in stale:
                task = _ocr_tasks.pop(tid, None)
                # 同步删盘上文件（task 存于 data/sessions/<hash>/ocr_tasks/<id>.json）
                if task:
                    img_hash = task.get('image_hash', '')
                    if img_hash:
                        path = os.path.join(_ocr_task_dir(img_hash), f'{tid}.json')
                        try:
                            os.remove(path)
                        except OSError:
                            pass


# 启动清理线程（daemon=True，主进程退出时自动结束）
threading.Thread(target=_ocr_task_cleanup, daemon=True).start()


@app.route('/api/ocr_start', methods=['POST'])
def ocr_start():
    """启动 OCR 批量识别任务（后台执行），返回 task_id

    请求体: { hash, filenames: [...], use_scaled: bool, threshold: float }
    响应: { success, task_id, total }
    """
    from utils.ocr_handler import recognize_character

    data = request.get_json()
    image_hash = data.get('hash')
    filenames = data.get('filenames', [])
    use_scaled = data.get('use_scaled', True)
    threshold = float(data.get('threshold', 0.5))

    if not image_hash or not filenames:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    task_id = uuid.uuid4().hex[:12]
    with _ocr_tasks_lock:
        _ocr_tasks[task_id] = {
            'status': 'running',
            'total': len(filenames),
            'done': 0,
            'results': [],       # 累积结果（每完成一张 append）
            'new_results': [],   # 增量结果（上次轮询后新完成的），轮询后会清空
            'error': None,
            'finished_at': 0.0,
            'started_at': time.time(),
            # 持久化元数据（重启后这些字段不存，前端恢复时拿不到原参数）
            'image_hash': image_hash,
            'use_scaled': use_scaled,
            'threshold': threshold,
        }
    # 初始存盘（让 task_id 落盘，client poll 时一定能找到）
    _save_ocr_task(task_id, _ocr_tasks[task_id])

    def worker():
        """后台 OCR 工作线程——每识别完一张就放入 new_results 供前端拉取"""
        task = _ocr_tasks[task_id]
        try:
            print(f"[OCR {task_id}] 开始处理 {len(filenames)} 张 (threshold={threshold}, use_scaled={use_scaled})")
            for fn in filenames:
                t0 = time.time()
                # 路径解析（与 /api/open_path 一致：先 scaled/，再 cutting_output/，回退 session 根）
                if not fn or '..' in fn or '/' in fn or '\\' in fn:
                    result = {'filename': fn, 'character': '', 'confidence': 0, 'error': '非法文件名'}
                else:
                    if use_scaled:
                        fp = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled', fn)
                        if not os.path.exists(fp):
                            fp = os.path.join(char_dir(image_hash), fn)
                        if not os.path.exists(fp):
                            fp = os.path.join(OUTPUT_FOLDER, image_hash, fn)
                    else:
                        fp = os.path.join(char_dir(image_hash), fn)
                        if not os.path.exists(fp):
                            fp = os.path.join(OUTPUT_FOLDER, image_hash, fn)
                    if not os.path.exists(fp):
                        result = {'filename': fn, 'character': '', 'confidence': 0, 'error': '文件不存在'}
                    else:
                        try:
                            char, conf = recognize_character(fp)
                            result = {
                                'filename': fn,
                                'character': char,
                                'confidence': round(conf, 3),
                                'engine': 'rapidocr',
                                'above_threshold': conf >= threshold and len(char) == 1,
                            }
                        except Exception as e:
                            print(f"[OCR {task_id}] {fn} 失败: {e}")
                            result = {'filename': fn, 'character': '', 'confidence': 0, 'error': str(e), 'engine': 'rapidocr'}

                # 后端直接持久化到 ocr_annotations.json（不依赖前端轮询回填）
                # 关键：即使前端刷新/关页停止 poll，识别结果也已落盘，刷新页面
                # 时 loadOcrAnnotations 能直接恢复。只有识别出字符才写（空白跳过）。
                # 持久化失败不中断识别主流程——前端 applyOcrResult 还有 POST 兜底。
                if result.get('character'):
                    try:
                        with _ocr_annotations_lock:
                            _persist_one_ocr_annotation(
                                image_hash, fn, result['character'],
                                conf=result.get('confidence', 0),
                                source=result.get('engine', 'ocr'),
                            )
                    except OSError as _e:
                        print(f"[OCR {task_id}] 标注写盘失败（忽略，前端会兜底）: {_e}")

                # 写结果（持锁更新）
                with _ocr_tasks_lock:
                    task['results'].append(result)
                    task['new_results'].append(result)
                    task['done'] += 1
                # 每张图同步存盘（300ms/图，磁盘写 <10ms，可接受）
                # 这样 Flask 重启也不丢已完成的结果，前端 poll 还能拿到
                _save_ocr_task(task_id, task)
                # 每张图打一行进度（控制台能实时看到）
                elapsed_one = time.time() - t0
                char_disp = result.get('character') or '(空)'
                conf_disp = result.get('confidence', 0)
                engine_disp = result.get('engine', '?')
                print(f"[OCR {task_id}] [{task['done']}/{len(filenames)}] {fn} → '{char_disp}' (conf={conf_disp:.3f}, {elapsed_one*1000:.0f}ms, {engine_disp})")
            # 完成
            with _ocr_tasks_lock:
                task['status'] = 'done'
                task['finished_at'] = time.time()
            _save_ocr_task(task_id, task)  # 最终存盘
            # 服务端自主补繁体：任务结束时对识别出的记录批量简转繁并落盘，
            # 不依赖前端。之后刷新页面读 ocr_annotations.json 即带 traditional。
            try:
                _backfill_ocr_traditional(image_hash, task.get('results', []))
            except Exception as _bf_err:
                print(f"[OCR {task_id}] 补繁体失败（忽略）: {_bf_err}")
            elapsed = time.time() - task['started_at']
            recognized = sum(1 for r in task['results'] if r.get('character'))
            print(f"[OCR {task_id}] ✓ 任务完成: {recognized}/{len(filenames)} 识别成功, "
                  f"{elapsed:.1f}s ({elapsed/len(filenames)*1000:.0f}ms/张, rapidocr)")
        except Exception as e:
            print(f"[OCR {task_id}] 任务异常: {e}")
            import traceback
            traceback.print_exc()
            with _ocr_tasks_lock:
                task['status'] = 'error'
                task['error'] = str(e)
                task['finished_at'] = time.time()
            _save_ocr_task(task_id, task)  # 异常也存盘

    threading.Thread(target=worker, daemon=True).start()

    return jsonify({
        'success': True,
        'task_id': task_id,
        'total': len(filenames),
    })


@app.route('/api/ocr_progress/<task_id>', methods=['GET'])
def ocr_progress(task_id):
    """轮询 OCR 任务进度 + 增量结果

    响应: { status, done, total, new_results, error }
    - status: 'running' | 'done' | 'error' | 'interrupted' | 'not_found'
      interrupted：服务器中断后启动时加载的旧 task（worker 死了但部分结果还在）
    - new_results: 上次轮询后新完成的结果（前端拉取后会被清空）
    """
    with _ocr_tasks_lock:
        task = _ocr_tasks.get(task_id)
        if not task:
            return jsonify({'status': 'not_found'})
        # 取走 new_results（消费者模式）
        new_results = task['new_results']
        task['new_results'] = []
        return jsonify({
            'status': task['status'],
            'done': task['done'],
            'total': task['total'],
            'new_results': new_results,
            'error': task.get('error'),
        })


if __name__ == '__main__':
    import webbrowser
    import threading

    port = 7500
    url = f'http://localhost:{port}'

    def open_browser():
        import time
        time.sleep(1.5)
        webbrowser.open(url)

    if getattr(sys, 'frozen', False):
        print(f'高迪书法字库预处理工具已启动')
        print(f'访问地址: {url}')
        print(f'按 Ctrl+C 停止服务')
        threading.Thread(target=open_browser, daemon=True).start()
        app.run(host='0.0.0.0', port=port, debug=False)
    else:
        app.run(debug=True, host='0.0.0.0', port=port)
