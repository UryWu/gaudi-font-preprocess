"""一次性脚本：从 ocr_tasks/*.json 把识别结果导入到 ocr_annotations.json

背景：之前 OCR 任务因并发保存 bug 部分失败，结果存在 ocr_tasks/
（任务状态文件，10 分钟 TTL 过期）但没写进 ocr_annotations.json
（用户标注持久化文件）。

用法：直接 python import_ocr_tasks.py（不需要 Flask 在跑）
"""
import json
import os
import glob
import sys


def import_one(session_dir: str) -> tuple[int, int]:
    """处理单个 session 目录。

    Returns: (imported_count, skipped_count)
    """
    hash_ = os.path.basename(session_dir)
    task_dir = os.path.join(session_dir, 'ocr_tasks')
    ann_path = os.path.join(session_dir, 'ocr_annotations.json')

    if not os.path.isdir(task_dir):
        return 0, 0

    # 读现有标注
    annotations = {}
    if os.path.exists(ann_path):
        try:
            with open(ann_path, 'r', encoding='utf-8') as f:
                annotations = json.load(f)
        except json.JSONDecodeError:
            print(f'  警告：{ann_path} 读坏，按空处理（将被修复）')
            annotations = {}

    imported = 0
    skipped = 0
    task_files = sorted(glob.glob(os.path.join(task_dir, '*.json')))

    if not task_files:
        return 0, 0

    print(f'\n[{hash_}] 找到 {len(task_files)} 个 task 文件')

    for task_file in task_files:
        try:
            with open(task_file, 'r', encoding='utf-8') as f:
                task = json.load(f)
        except Exception as e:
            print(f'  跳过（读失败）: {os.path.basename(task_file)}: {e}')
            skipped += 1
            continue

        task_id_short = os.path.basename(task_file).replace('.json', '')[:12]
        status = task.get('status')
        results = task.get('results', [])
        print(f'  task {task_id_short} status={status} results={len(results)}')

        if status != 'done':
            print(f'    (跳过，未完成)')
            skipped += len(results)
            continue

        for r in results:
            fn = r.get('filename', '')
            char = r.get('character', '')
            conf = r.get('confidence', 0)
            if not fn or not char:
                skipped += 1
                continue

            # 已有标注（用户手动改过）→ 不覆盖
            existing = annotations.get(fn)
            if existing and not isinstance(existing, dict):
                # 旧字符串格式，转对象
                existing = None
            if existing and existing.get('simplified') and existing.get('source') == 'manual':
                skipped += 1
                continue

            annotations[fn] = {
                'simplified': char,
                'conf': round(float(conf), 3),
                'source': r.get('engine', 'ocr'),
                'updated_at': task.get('finished_at', 0),  # 用任务完成时间
                'imported_from_task': task_id_short,
            }
            imported += 1

    # 写回
    with open(ann_path, 'w', encoding='utf-8') as f:
        json.dump(annotations, f, ensure_ascii=False, indent=2)

    print(f'  → 导入 {imported} 条，跳过 {skipped} 条')

    # 清掉老的 task 文件（避免下次重复导入；让 TTL 自然过期也行）
    # 不删，让它们留着——下次出问题还能溯源

    return imported, skipped


def main():
    base = sys.argv[1] if len(sys.argv) > 1 else 'data/sessions'
    if not os.path.isdir(base):
        print(f'目录不存在: {base}')
        sys.exit(1)

    total_imported = 0
    total_skipped = 0
    for entry in sorted(os.listdir(base)):
        session_dir = os.path.join(base, entry)
        if not os.path.isdir(session_dir):
            continue
        imp, skp = import_one(session_dir)
        total_imported += imp
        total_skipped += skp

    print(f'\n========== 合计 ==========')
    print(f'导入: {total_imported} 条')
    print(f'跳过: {total_skipped} 条')


if __name__ == '__main__':
    main()
