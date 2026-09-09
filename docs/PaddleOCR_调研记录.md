# PaddleOCR 调研记录（2026-09）

> /annotate 页 OCR 自动标注曾尝试接 PaddleOCR（替代/补充 EasyOCR），
> 实测在当前环境（Windows + Python 3.12 + paddlepaddle 3.3.1）跑不通。
> 此文档记录排查过程、原因、未来重试的入口。

## TL;DR

- EasyOCR 在书法字上高置信率约 **34%**
- PaddleOCR 期望 **50-60%**（中文专用模型）
- 实际测试：paddlepaddle **3.3.1 on Windows CPU** 的 onednn 实现有 C++ bug
  - 错误：`NotImplementedError: ConvertPirAttribute2RuntimeAttribute not support [pir::ArrayAttribute<pir::DoubleAttribute>]]`
  - 位置：`paddle/fluid/framework/new_executor/instruction/onednn/onednn_instruction.cc:118`
  - **每张图都崩**（不是 35 张后才崩，是 init 完就崩）
- PyPI 官方 + 清华源 paddlepaddle Windows wheel **最高只到 3.3.1**，无 3.4+（修 bug 的版本）
- **结论**：当前环境无法用 PaddleOCR。决策：删双引擎代码，只留 EasyOCR。

## 详细排查时间线

### Step 1：装 paddlepaddle + paddleocr
```bash
pip install --no-deps paddlepaddle paddleocr paddlex
# 后续：手动装齐 paddlex 间接依赖
# - colorlog, prettytable, huggingface_hub, modelscope, modelscope-hub
# - aiohttp, yarl, multidict, frozenlist, aiosignal, aiohappyeyeballs
# - cryptography, pyclipper, pypdfium2, python-bidi, shapely, attrs
# 装 opencv-contrib-python==4.10.0.84 时要停 Flask（cv2.pyd 被锁）
```

### Step 2：API 兼容性问题
PaddleOCR 3.x（3.7.0）init 参数和 2.x 完全不同：
| 2.x 参数 | 3.x 状态 |
|---|---|
| `use_gpu` | ❌ 改名 `device` |
| `use_angle_cls` | ❌ 移除 |
| `show_log` | ❌ 移除，用 logger 控制 |
| `lang` | ✓ 保留 |

PaddleOCR 3.x `ocr()` 也不再接 `cls`：
```python
ocr.ocr(img, cls=False)       # 旧
ocr.ocr(img, use_textline_orientation=False)  # 新
```

### Step 3：paddlex[ocr-core] 依赖检查
paddlex 3.7.2 的 `ocr-core` extra 强依赖 `opencv-contrib-python==4.10.0.84`，
且通过 `is_dep_available` 函数验证（不是 `import cv2` 检查，是真的
查 metadata）。monkey-patch：

```python
import paddlex.utils.deps as _pdx_deps
# 1) 把 opencv-contrib-python 检查改成「opencv-python 装了就算」
for pkg_name, deps in _pdx_deps.EXTRAS['ocr-core'].items():
    if 'opencv-contrib-python' in pkg_name:
        deps[:] = ['opencv-python']
# 2) 覆盖 is_dep_available
_orig = _pdx_deps.is_dep_available
def _patched(dep):
    if 'opencv-contrib-python' in dep: return True
    return _orig(dep)
_pdx_deps.is_dep_available = _patched
# 3) 全局注入 cv2 到所有 paddlex 子模块（多模块不 import cv2，靠 contrib 的 sitecustomize 注入）
import sys
for mod_name in list(sys.modules.keys()):
    if any(mod_name.startswith(p) for p in ('paddlex.', 'paddleocr.', 'paddle.')):
        mod = sys.modules[mod_name]
        if mod and not getattr(mod, '__cv2_injected', False):
            if 'cv2' not in mod.__dict__:
                mod.cv2 = cv2
            mod.__cv2_injected = True
```

→ 成功绕过了 DependencyError，pipeline 能建。

### Step 4：onednn bug
报错（每张图都崩）：
```
NotImplementedError: (Unimplemented) ConvertPirAttribute2RuntimeAttribute
not support [pir::ArrayAttribute<pir::DoubleAttribute>]
(at ..\paddle\fluid\framework\new_executor\instruction\onednn\onednn_instruction.cc:118)
```

**关键事实**：这个错是 **C++ 层**抛的，Python 改不了。

尝试关 onednn（全部失败）：
```python
paddle.set_flags({'FLAGS_use_mkldnn': False})      # 2.x flag
paddle.set_flags({'FLAGS_use_onednn': False})      # 3.x flag
os.environ['FLAGS_use_mkldnn'] = 'False'           # env var
os.environ['FLAGS_use_onednn'] = 'False'
os.environ['PADDLE_DISABLE_ONEDNN'] = '1'
```

→ 全部无效，C++ 编译时决定走 onednn 路径，runtime 关不掉。

**注意**：控制台打印的 `I... onednn_context.cc:81] oneDNN v3.6.2` 是 init log（onednn linked），
不代表一定 used。但崩溃堆栈明确指向 `onednn_instruction.cc:118`，说明这次执行走了 onednn。

### Step 5：升 paddlepaddle
```bash
pip install --upgrade paddlepaddle
# 或：pip install paddlepaddle==3.8.0
```

PyPI 官方返回可用版本：
```
2.6.2, 3.0.0b2, 3.0.0rc0, 3.0.0rc1, 3.0.0, 3.1.0, 3.1.1,
3.2.0, 3.2.1, 3.2.2, 3.3.0, 3.3.1
```

**最高 3.3.1**。3.4+ 在 Windows 上没发 wheel。

## PaddlePaddle 3.4+ Windows wheel 状态

GitHub issue tracker 上 Paddle 团队已知有这个 onednn bug，但 3.4+ 主要给
Linux + Mac + GPU 发布，**Windows CPU wheel 长期未更新**。这是 PaddlePaddle
项目的一个长期问题（GitHub Discussions / Issues 里多次提及）。

## 当前决策

用户决策：**D — 等官方修 bug 后再启用 PaddleOCR**。

代码已经清理为只用 EasyOCR：
- `utils/ocr_handler.py`：单引擎（EasyOCR only）
- `app.py`：删了顶部 onednn env var 块
- 删除 `test_paddle_ocr.py`

## 重试入口

如果以后 PaddlePaddle 发了带 onednn fix 的 Windows wheel，可以这么重试：

```bash
# 1. 升 paddlepaddle（届时 wheel 应该会有 3.4+）
pip install --upgrade paddlepaddle

# 2. revert 之前的 PaddleOCR 试错 commit（git 历史里）
git log --oneline -- utils/ocr_handler.py
# 找：148cd94 / 99d94cd / 9aefae3 / 9f8e9aa / 5f4bc48 / 0cd52a1
# 用 git revert <hash> 一个个恢复，或 git reset 到那之前的 commit

# 3. 跑 test 验证
.venv/Scripts/python.exe -c "from utils.ocr_handler import recognize_character; print(recognize_character('output/<hash>/scaled/scaled_0002.png'))"
# 期望返回 ('华', 0.98, 'paddleocr') 而不是 NotImplementedError
```

## 未来可能的 OCR 选项

如果不想等 Paddle：
1. **PaddleOCR GPU 版** — 用 CUDA 跑可能绕过 onednn bug（需 NVIDIA 卡）
2. **PaddleOCR Linux** — Linux wheel 可能有 3.4+
3. **专训模型** — 用自己的字体训练专用模型（数据准备成本高，但准确率最高）
4. **换其他 OCR**：
   - **RapidOCR**（PaddleOCR 的 ONNX 版，无 onednn 依赖）：`pip install rapidocr-onnxruntime`
   - **Tesseract**（老牌，但中文差）
   - **云 API**（百度/腾讯 OCR，按量计费）

其中 **RapidOCR** 最值得关注——它是 PaddleOCR 的 ONNX 移植版，
不依赖 paddlepaddle，模型直接走 onnxruntime，**没有 onednn bug**。
准确率与 PaddleOCR 相当。装一下试试可能就能解决问题。

```bash
pip install rapidocr-onnxruntime
# 中文模型自动下载 ~50MB
```

待后续评估。

## 相关 git commits

清理后的提交：
- `d0f8376` refactor: OCR 只用 EasyOCR，删 PaddleOCR 双引擎代码（本步骤）

被清理的 PaddleOCR 试错 commit（已 git revert 友好）：
- `148cd94` OCR 加 PaddleOCR 引擎（双引擎、隐式可选）
- `99d94cd` PaddleOCR 3.x 兼容（不再传 use_angle_cls / show_log）
- `9aefae3` PaddleOCR 3.3.1 Windows onednn bug
- `9f8e9aa` Paddle 3.x onednn 关不掉——加 env var + 双 flag 名
- `5f4bc48` OCR 结果带 engine 标签
- `0cd52a1` PaddleOCR 3.x ocr() 不再接 cls 参数

如需重试 PaddleOCR：`git log --oneline --reverse -- utils/ocr_handler.py` 找最早 PaddleOCR 相关 commit，用 `git revert` 逐个反向应用。
