"""高迪书法字库预处理工具 - Flask主应用"""
import os
import sys
import json
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
        # 是否已有 output 目录
        out_dir = os.path.join(OUTPUT_FOLDER, h)
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
    output_dir = os.path.join(OUTPUT_FOLDER, image_hash)
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
            'image_url': f'/output/{image_hash}/{piece_filename}',
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

    output_dir = os.path.join(OUTPUT_FOLDER, image_hash)

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
                        'image_url': f'/output/{image_hash}/{piece_filename}',
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
                        'image_url': f'/output/{image_hash}/{piece_filename}',
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
                file_path = os.path.join(OUTPUT_FOLDER, image_hash, filename)
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
            old_path = os.path.join(OUTPUT_FOLDER, image_hash, old_filename)
            new_path = os.path.join(OUTPUT_FOLDER, image_hash, new_filename)
            if os.path.exists(old_path):
                try:
                    os.rename(old_path, new_path)
                except Exception as e:
                    print(f"重命名失败 {old_filename} -> {new_filename}: {e}")
            c['filename'] = new_filename
            c['image_url'] = f'/output/{image_hash}/{new_filename}'
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
    output_dir = os.path.join(OUTPUT_FOLDER, image_hash)
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
    output_dir = os.path.join(OUTPUT_FOLDER, image_hash)
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
    """批量删除指定字符：删除磁盘文件 + 从 session 中移除对应条目"""
    data = request.get_json()
    image_hash = data.get('hash')
    filenames = data.get('filenames', [])

    if not image_hash or not filenames:
        return jsonify({'success': False, 'error': '缺少参数'}), 400

    deleted = []
    for fn in filenames:
        if not fn or '..' in fn or '/' in fn or '\\' in fn:  # 防路径穿越
            continue
        fp = os.path.join(OUTPUT_FOLDER, image_hash, fn)
        if os.path.exists(fp):
            try:
                os.remove(fp)
                deleted.append(fn)
            except Exception as e:
                print(f"删除 {fn} 失败: {e}")

    # 更新 session characters 字段：移除这些
    session_data = load_session(image_hash, DATA_FOLDER)
    if session_data and 'characters' in session_data:
        deleted_set = set(deleted)
        session_data['characters'] = [
            c for c in session_data['characters']
            if c.get('filename') not in deleted_set
        ]
        save_session(image_hash, session_data, DATA_FOLDER)

    remaining = len(session_data.get('characters', [])) if session_data else 0
    print(f"批量删除字符: hash={image_hash}, 删除 {len(deleted)} 个, 剩余 {remaining} 个")

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

    fp = os.path.join(OUTPUT_FOLDER, image_hash, filename)
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
    """处理缩放校正"""
    from utils.scale_processor import apply_adjustments, scale_char
    import traceback

    try:
        data = request.get_json()
        image_hash = data.get('hash')
        characters = data.get('characters', [])
        scale = data.get('scale', 1.15)
        align = data.get('align', 'center')
        background = data.get('background', 'black')
        target_size = data.get('target_size', 512)

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

                original_path = os.path.join(OUTPUT_FOLDER, image_hash, filename)
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

                # 处理缩放和居中
                processed = scale_char(img, scale, target_size, align, background)

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

        return jsonify({
            'success': True,
            'characters': processed_characters,
            'output_dir': output_dir,
            'total': len(processed_characters)
        })
    except Exception as e:
        print(f"process_scale 错误: {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/save_scaled', methods=['POST'])
def save_scaled():
    """保存缩放校正结果"""
    data = request.get_json()
    image_hash = data.get('hash')
    characters = data.get('characters', [])

    if not image_hash:
        return jsonify({'error': '缺少图片哈希'}), 400

    # 加载会话数据
    session_data = load_session(image_hash, DATA_FOLDER)
    if not session_data:
        return jsonify({'error': '会话不存在'}), 404

    # 保存缩放校正数据
    session_data['scaled_characters'] = characters
    session_data['scale_processed'] = True
    save_session(image_hash, session_data, DATA_FOLDER)

    return jsonify({'success': True})


@app.route('/api/cleanup_intermediate', methods=['POST'])
def cleanup_intermediate():
    """清理中间过程文件，只保留最终导出目录"""
    import shutil

    data = request.get_json()
    image_hash = data.get('hash')
    keep_dir = data.get('keep_dir', '')  # 保留的最终输出目录

    if not image_hash:
        return jsonify({'error': '缺少图片哈希'}), 400

    base_dir = os.path.join(OUTPUT_FOLDER, image_hash)
    if not os.path.exists(base_dir):
        return jsonify({'success': True, 'message': '目录不存在，无需清理'})

    cleaned = []
    # 清理切割后的原始文件 (char_XXXX.png)
    for f in os.listdir(base_dir):
        fpath = os.path.join(base_dir, f)
        if os.path.isfile(fpath) and f.startswith('char_') and f.endswith('.png'):
            os.remove(fpath)
            cleaned.append(f)
        elif os.path.isdir(fpath):
            dirname = f.lower()
            # 清理 scaled 目录
            if dirname == 'scaled':
                shutil.rmtree(fpath)
                cleaned.append(f'{f}/ (整个目录)')
            # 清理旧的 exported 目录中非 keep_dir 的
            elif dirname == 'exported':
                for sub in os.listdir(fpath):
                    sub_path = os.path.join(fpath, sub)
                    if os.path.isdir(sub_path) and sub_path != keep_dir:
                        shutil.rmtree(sub_path)
                        cleaned.append(f'{f}/{sub}/ (旧导出)')

    return jsonify({
        'success': True,
        'cleaned': cleaned,
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

    return jsonify({
        'success': True,
        'characters': characters,
        'output_dir': output_dir
    })


@app.route('/api/convert_to_traditional', methods=['POST'])
def convert_to_traditional():
    """简体转繁体"""
    try:
        import opencc
        data = request.get_json()
        text = data.get('text', '')

        if not text:
            return jsonify({'success': True, 'result': ''})

        # 使用 OpenCC 转换：简体 -> 繁体
        converter = opencc.OpenCC('s2t')
        result = converter.convert(text)

        print(f"简转繁: '{text}' -> '{result}'")
        return jsonify({'success': True, 'result': result})
    except ImportError as e:
        print(f"OpenCC未安装: {e}")
        return jsonify({'success': True, 'result': text})
    except Exception as e:
        print(f"简转繁错误: {e}")
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/convert_to_simplified', methods=['POST'])
def convert_to_simplified():
    """繁体转简体"""
    try:
        import opencc
        data = request.get_json()
        text = data.get('text', '')

        if not text:
            return jsonify({'success': True, 'result': ''})

        # 使用 OpenCC 转换：繁体 -> 简体
        converter = opencc.OpenCC('t2s')
        result = converter.convert(text)

        print(f"繁转简: '{text}' -> '{result}'")
        return jsonify({'success': True, 'result': result})
    except ImportError as e:
        print(f"OpenCC未安装: {e}")
        return jsonify({'success': True, 'result': text})
    except Exception as e:
        print(f"繁转简错误: {e}")
        return jsonify({'success': False, 'error': str(e)})
    except ImportError:
        # 如果没有安装 OpenCC，尝试使用内置映射
        return jsonify({'success': True, 'result': text})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/export_annotated', methods=['POST'])
def export_annotated():
    """导出标注后的图片（白底黑字，UTF-8命名）"""
    from datetime import datetime

    data = request.get_json()
    image_hash = data.get('hash')
    annotations = data.get('annotations', [])
    mode = data.get('mode', 'traditional')

    print(f"导出请求: hash={image_hash}, annotations数量={len(annotations)}, mode={mode}")

    if not image_hash:
        return jsonify({'success': False, 'error': '缺少图片哈希'}), 400

    if not annotations:
        return jsonify({'success': False, 'error': '没有标注数据，请先标注字符'}), 400

    # 创建带时间戳的输出目录
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    export_dir = os.path.join(OUTPUT_FOLDER, image_hash, 'exported', timestamp)
    os.makedirs(export_dir, exist_ok=True)
    print(f"导出目录: {export_dir}")

    count = 0
    errors = []

    # 用于跟踪重复字符
    char_counts = {}

    for ann in annotations:
        try:
            # 源文件路径 - 尝试多个可能的位置
            src_path = None

            # 1. 尝试原始文件名
            if ann.get('filename'):
                src_path = os.path.join(OUTPUT_FOLDER, image_hash, ann['filename'])
                if not os.path.exists(src_path):
                    src_path = None

            # 2. 尝试 scaled 目录
            if not src_path:
                scaled_name = f"scaled_{ann['index']:04d}.png"
                src_path = os.path.join(OUTPUT_FOLDER, image_hash, 'scaled', scaled_name)
                if not os.path.exists(src_path):
                    src_path = None

            # 3. 尝试原始 char 文件名
            if not src_path and ann.get('original_filename'):
                src_path = os.path.join(OUTPUT_FOLDER, image_hash, ann['original_filename'])
                if not os.path.exists(src_path):
                    src_path = None

            if not src_path:
                errors.append(f"找不到文件: index={ann['index']}")
                continue

            print(f"处理文件: {src_path}")

            # 读取图片
            img = load_image(src_path)

            # 反色：黑底白字 -> 白底黑字
            inverted = cv2.bitwise_not(img)

            # 生成文件名：uniXXXX.png，重复字加后缀
            char = ann.get('character', '')
            if not char:
                errors.append(f"没有字符: index={ann['index']}")
                continue

            code = ord(char)

            # 检查是否是重复字符，添加后缀
            if char in char_counts:
                char_counts[char] += 1
                suffix = f"_{char_counts[char]:02d}"
            else:
                char_counts[char] = 0
                suffix = ""

            # 文件名：BMP用uniXXXX，扩展区用uXXXXX
            if code > 0xFFFF:
                filename = f"u{code:05X}{suffix}.png"
            else:
                filename = f"uni{code:04X}{suffix}.png"

            # 保存
            output_path = os.path.join(export_dir, filename)
            save_image(inverted, output_path)
            print(f"已保存: {output_path}")
            count += 1

        except Exception as e:
            error_msg = f"导出失败 index={ann.get('index')}: {e}"
            print(error_msg)
            errors.append(error_msg)
            continue

    print(f"导出完成: 成功 {count} 个, 错误 {len(errors)} 个")

    return jsonify({
        'success': True,
        'count': count,
        'output_dir': export_dir,
        'errors': errors
    })


@app.route('/api/export_csv', methods=['POST'])
def export_csv():
    """导出 FontLab CSV 格式"""
    import csv
    from datetime import datetime

    data = request.get_json()
    image_hash = data.get('hash')
    annotations = data.get('annotations', [])
    mode = data.get('mode', 'traditional')

    if not image_hash or not annotations:
        return jsonify({'error': '缺少参数'}), 400

    # 创建输出文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    csv_filename = f"fontlab_{timestamp}.csv"
    csv_path = os.path.join(OUTPUT_FOLDER, image_hash, 'exported', csv_filename)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    with open(csv_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['filename', 'unicode', 'character', 'simplified', 'traditional'])

        for ann in annotations:
            char = ann.get('primary', '')
            if char:
                code = ord(char)
                if code > 0xFFFF:
                    png_name = f"u{code:05X}.png"
                else:
                    png_name = f"uni{code:04X}.png"
                writer.writerow([
                    png_name,
                    f"U+{code:04X}" if code <= 0xFFFF else f"U+{code:05X}",
                    char,
                    ann.get('simplified', ''),
                    ann.get('traditional', '')
                ])

    return jsonify({
        'success': True,
        'output_path': csv_path
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
