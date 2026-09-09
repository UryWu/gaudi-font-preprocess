"""数据存储模块 - JSON文件存储会话数据

每批次（image_hash）的所有数据合并到 data/sessions/<hash>/ 目录：
- cutting.json：切割线 + boxes + 标注
- ocr_annotations.json：OCR 标注（前端 load 时预填 + OCR 填时存）
- ocr_tasks/<id>.json：OCR 后台任务状态（持久化跨 Flask 重启）
- scaled/、char_*.png：图片（与 OUTPUT_FOLDER 同一目录）
"""
import json
import os
from typing import Dict, Any, Optional


def session_dir(image_hash: str, data_dir: str) -> str:
    """单个 session 的目录（data_dir/<image_hash>/）"""
    return os.path.join(data_dir, image_hash)


def get_session_filepath(image_hash: str, data_dir: str) -> str:
    """获取会话 cutting.json 路径（per-session 目录）"""
    return os.path.join(session_dir(image_hash, data_dir), 'cutting.json')


def save_session(image_hash: str, data: Dict[str, Any], data_dir: str) -> bool:
    """保存到 data_dir/<image_hash>/cutting.json"""
    filepath = get_session_filepath(image_hash, data_dir)
    try:
        os.makedirs(os.path.dirname(filepath), exist_ok=True)
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"保存会话失败: {e}")
        return False


def load_session(image_hash: str, data_dir: str) -> Optional[Dict[str, Any]]:
    """加载会话数据

    优先读新 layout（<hash>/cutting.json），
    兼容旧 layout（<hash>.json）—— 供迁移期数据残留时回退。
    """
    new_path = get_session_filepath(image_hash, data_dir)
    if os.path.exists(new_path):
        try:
            with open(new_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载会话失败（新路径 {new_path}）: {e}")
            return None
    # 旧 layout 回退
    old_path = os.path.join(data_dir, f'{image_hash}.json')
    if os.path.exists(old_path):
        try:
            with open(old_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"加载会话失败（旧路径 {old_path}）: {e}")
            return None
    return None


def delete_session(image_hash: str, data_dir: str) -> bool:
    """删除整个 session 目录（图片 + 标注 + 任务）"""
    sd = session_dir(image_hash, data_dir)
    if not os.path.isdir(sd):
        return True
    try:
        import shutil
        shutil.rmtree(sd)
        return True
    except Exception as e:
        print(f"删除会话失败: {e}")
        return False


def list_sessions(data_dir: str) -> list:
    """列出所有 session（per-session 目录名）"""
    if not os.path.isdir(data_dir):
        return []
    return [d for d in os.listdir(data_dir)
            if os.path.isdir(os.path.join(data_dir, d))]
