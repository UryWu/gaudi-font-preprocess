# CLAUDE.md - 高迪书法字库预处理工具

## 项目概览

`gaudi-font-preprocess` 是一个面向书法爱好者的字库预处理 Flask Web 应用。用户上传手写书法长篇图片，应用自动/手动切割成单字图，统一尺寸后导出 FontLab 标准命名（`uniXXXX.bmp` / `uXXXXX.bmp`）的白底黑字素材。详见 [README.md](README.md) 与 [高迪书法字库预处理工具版本历史.md](高迪书法字库预处理工具版本历史.md)。

**主仓库**：<https://github.com/gaudi1209/gaudi-font-preprocess>
**关联项目**：<https://github.com/gaudi1209/ai-font-tool>（基于 zi2zi 的 AI 字体生成）

## 项目结构

```
gaudi-font-preprocess/
├── app.py                       # Flask 主应用，路由 + API
├── config.py                    # 路径常量、图像处理参数
├── pyproject.toml               # uv 项目定义（Python 3.10–3.13）
├── 启动.bat / 启动.ps1          # 用户启动脚本（PyInstaller 兼容）
├── templates/                   # 五个页面模板
│   ├── base.html                # 全局 layout（侧边导航 + 顶栏）
│   ├── layout.html              # 页面1：切割布局
│   ├── adjust.html              # 页面2：切割调整
│   ├── scale.html               # 页面3：缩放校正
│   └── annotate.html            # 页面4：标注出图
├── static/
│   ├── css/style.css            # 全局样式（莫兰迪配色）
│   ├── js/                      # 每页一个 JS：layout/adjust/scale/annotate + common
│   ├── uploads/                 # 用户上传原图 + 二值化图 + 基准图
│   └── images/                  # 静态图片资源
├── utils/                       # 图像/OCR/切割/存储 业务模块
│   ├── image_processor.py       # 二值化、缩放、deskew、哈希
│   ├── ocr_handler.py           # EasyOCR 文本框检测
│   ├── cut_analyzer.py          # 切割线算法（竖线 + 按列分组的横线）
│   ├── scale_processor.py       # 缩放校正
│   ├── empty_detector.py        # 空白切片检测
│   └── storage.py               # 会话 JSON 读写
├── data/sessions/{hash}.json    # 切割线 + 文本框配置（按图片哈希存）
├── output/{hash}/               # 切割字符图 + 缩放图 + 导出目录
├── docs/                        # 任务书与说明文档
│   ├── 任务书_页面1_切割布局.md
│   ├── 任务书_页面2_切割调整.md
│   ├── 任务书_页面3_缩放校正.md
│   ├── 任务书_页面4_标注出图.md
│   ├── 数据存储说明.md
│   ├── 倾斜校正说明.md
│   ├── 切割线三色说明.md
│   └── 文本框检测说明.md
└── my_font/                     # 测试样例图片
```

## 技术栈

- 后端：Python 3.10+、Flask 3.0、OpenCV 4.9、Pillow 10、EasyOCR 1.7、OpenCC、NumPy
- 前端：原生 JS + Canvas，无构建步骤（直接 `<script src="...">` 引用）
- 打包：PyInstaller（`sys.frozen` 判断源码 vs 打包环境，见 `app.py`、`config.py`）

## 常用命令

```bash
# 启动（默认 http://localhost:7500）
双击 启动.bat                     # 用户入口
python app.py                     # 直接启动

# 依赖（uv 优先，pip 也行）
uv sync
pip install -r requirements.txt
```

## 工作流（五个页面）

| # | 页面 | 模板 | 主 JS | 关键产出 |
|---|------|------|-------|----------|
| 1 | 切割布局 | `layout.html` | `static/js/layout.js` | `data/sessions/{hash}.json` |
| 2 | 切割调整 | `adjust.html` | `static/js/adjust.js` | 删除噪点/错字切片 |
| 3 | 缩放校正 | `scale.html` | `static/js/scale.js` | `output/{hash}/scaled/*.png` |
| 4 | 标注出图 | `annotate.html` | `static/js/annotate.js` | `output/{hash}/exported/{ts}/` |
| 5 | 导出 | （页面4内） | （同上） | `uniXXXX.bmp` / `uXXXXX.bmp` + CSV |

完整说明与算法/坑点见 `docs/任务书_页面N_*.md`；数据落盘规则见 `docs/数据存储说明.md`。

## 关键约定

- **图片哈希**：原图 MD5 决定 `{hash}`，同名图片复用会话（见 `utils/image_processor.py` 的 `compute_hash`）。
- **路径兼容**：所有路径经 `RESOURCE_DIR` 解析（`config.py:5`），源码运行用项目目录，打包运行用 `sys._MEIPASS`。
- **目标高度**：`TARGET_HEIGHT = 4096`（`config.py:20`），所有图片按此等比缩放后再处理。
- **CJK 扩展区**：字符处理统一用 `Array.from()` + `codePointAt(0)`，避免代理对截断（详见版本历史 2026-04-14）。
- **命名规范**：BMP 区 `uniXXXX`，扩展区 `uXXXXX`；重复字加 `_01`、`_02` 后缀。
- **三色切割线**：红=竖向列边界、蓝=横向行边界（按列独立）、绿=OCR 文本框（可直接作为切割区域）；详见 `docs/切割线三色说明.md`。
- **页面任务书**：改/扩任意页面前先读对应 `docs/任务书_页面N_*.md`，里面记了算法、坑点、API、数据格式。
- **版本历史**：每次功能/修复落地后，更新 [高迪书法字库预处理工具版本历史.md](高迪书法字库预处理工具版本历史.md)。

## 编码风格

### 详细注释（强制）

写代码时必须给出详尽的中文注释，让读者**只看注释就能理解意图**，不需要再追变量名或外部资料。函数/类用 docstring 说明用途、参数、返回值；关键分支、魔法数字、坐标系/阈值等非显然逻辑必须用行级注释解释「**为什么这样做**」，而不只是「做了什么」。

判断标准：把代码里所有标识符、注释、docstring 全部删掉，只留空白和关键字——读者应该还能凭注释**复原出整个设计意图**。

### 行级注释密度参考

以下示例来自本项目实际模式，按此密度执行。注释要解释**意图**和**陷阱**，不重复函数名或显而易见的语法。

#### Python 示例（utils/*.py）

```python
def deskew(img, max_angle=10.0, angle_step=0.5):
    """
    自动检测并校正图片倾斜（投影轮廓法）。

    原理：对 [-max_angle, +max_angle] 范围每个角度旋转缩略图，计算水平投影
    （每行白像素数）的方差，方差最大时文字最「水平」。原理详见
    docs/倾斜校正说明.md。

    参数:
        img:        BGR 彩色图或灰度图（np.ndarray）。
        max_angle:  最大搜索角度（度），默认 10°。书法作品一般倾斜不会超过这个值。
        angle_step: 角度搜索步长（度）。0.5° 是精度与速度的折中——再细收益微小，
                    再粗会把明显倾斜漏掉。

    返回:
        (rotated_img, detected_angle_degrees)：
          - rotated_img: 旋转后的图（角度 < 0.1° 时返回原图，避免引入插值模糊）。
          - detected_angle_degrees: 检测到的角度，未校正时为 0.0。
    """
    # 统一转成单通道二值图（白字黑底），后面投影计算才有效
    if len(img.shape) == 3:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    else:
        gray = img
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # 几乎没有文字的图片（白像素 < 0.5% 或 > 99.5%）—— 跳过校正
    # 否则空白图会被旋转成奇怪角度，浪费一次插值
    white_ratio = float(np.count_nonzero(binary)) / binary.size
    if white_ratio < 0.005 or white_ratio > 0.995:
        return img, 0.0

    # 缩到最长边 1024px 再搜索：投影方差对分辨率不敏感，
    # 缩 4 倍速度提升约 16 倍，角度搜索精度足够
    h, w = binary.shape
    scale = min(1.0, 1024 / max(h, w))
    small = cv2.resize(binary, None, fx=scale, fy=scale,
                       interpolation=cv2.INTER_AREA) if scale < 1.0 else binary

    # 角度搜索：水平投影方差最大者胜出
    best_angle, best_score = 0.0, -1.0
    angles = np.arange(-max_angle, max_angle + angle_step / 2, angle_step)
    for angle in angles:
        rotated = _rotate_image(small, float(angle), border_value=0)
        projection = np.sum(rotated, axis=1) / 255.0  # 每行的白像素数
        score = float(np.var(projection))             # 方差越大 → 行间差异越大 → 越水平
        if score > best_score:
            best_score, best_angle = score, float(angle)

    # 角度 < 0.1° 时跳过旋转：避免不必要的插值模糊，省一次文件 IO
    if abs(best_angle) > 0.1:
        # 旋转原图（彩色保留），彩色背景用白色填充以匹配纸面
        if len(img.shape) == 3:
            border = (255, 255, 255)
        else:
            border = 255
        rotated_img = _rotate_image(img, best_angle, border_value=border)
    else:
        rotated_img = img
        best_angle = 0.0  # 显式归零，下游打印日志更清晰

    return rotated_img, best_angle
```

注释要点：
- **每个函数 docstring** 写「做什么 / 为什么这么做 / 参数 / 返回值」。
- **魔法数字必须解释**：1024、0.5°、0.005、0.995 都不是随便选的。
- **关键分支解释为什么**：例如「白像素 < 0.5% 跳过」，注释要写「否则空白图会被旋转成奇怪角度」。
- **跨文件引用**：指向 `docs/倾斜校正说明.md`。

#### JavaScript 示例（static/js/*.js）

```javascript
/**
 * 计算水平切割线总数（含图片上下边界 0 和 imageHeight）。
 *
 * 用于页面1「切割线统计」面板的「横向切割线 N 条」显示。
 * 注意：水平线按列分组存储（strip_horizontal_lines），所以要累加所有列。
 *
 * @returns {number} 所有列的水平线总数（含边界）
 */
function countTotalHorizontalLines() {
    // state.stripHorizontalLines 是数组，每个元素是一条竖条
    // 字段: { strip_index, x_start, x_end, horizontal_lines: [...] }
    let total = 0;
    for (const strip of state.stripHorizontalLines) {
        // horizontal_lines 已包含 0 和 imageHeight（前端 addCutLine 时强制塞入，
        // 见 layout.js 的 addHorizontalLine 函数），不需要再 +2
        total += strip.horizontal_lines.length;
    }
    return total;
}

/**
 * 添加一条竖向切割线（Shift+双击触发）。
 *
 * 调用方：layout.js 的 handleDoubleClick 检测到 Shift 键时调用。
 * 见 docs/任务书_页面1_切割布局.md → 列结构更新算法。
 *
 * @param {number} x  切割线的 x 坐标（图像坐标系，非 Canvas 屏幕坐标）
 */
function addVerticalLine(x) {
    // 边界夹紧：切割线必须在 [0, imageWidth] 范围内
    // 否则后续按 x 切片时会数组越界（见 docs/数据存储说明.md → 容易犯的错误 → 边界溢出）
    x = Math.max(0, Math.min(state.imageWidth, x));

    // 去重：相同 x 已有竖线就不重复添加
    // 否则拖动时会出现两条重合线，删除逻辑会乱
    if (state.verticalLines.includes(x)) {
        return;
    }

    state.verticalLines.push(x);
    state.verticalLines.sort((a, b) => a - b);  // 必须保持有序，按列分组才正确

    // 新增竖线 → 列结构变化 → 重新构建 stripHorizontalLines
    // 这里不能直接 push 新列的空 horizontal_lines，否则旧横线会丢
    rebuildStripHorizontalLines();

    redrawCanvas();        // 重绘红/蓝/绿三色切割线
    updateCutStats();      // 刷新右侧统计面板
}

/**
 * 重新构建 stripHorizontalLines（竖向切割线变化后调用）。
 *
 * 见 docs/任务书_页面1_切割布局.md → 列结构更新算法。
 *
 * 关键点：删除竖线时不能只继承一个旧列的横线，否则其他列的横线会丢失。
 * 必须合并所有与新列 x 范围重叠的旧列的横线，再去重排序。
 */
function rebuildStripHorizontalLines() {
    const newStrips = [];

    // 按竖线把图切成竖条，每个竖条一个 strip
    for (let i = 0; i < state.verticalLines.length - 1; i++) {
        const xStart = state.verticalLines[i];
        const xEnd = state.verticalLines[i + 1];

        // 收集与新竖条 x 范围重叠的所有旧列的横线
        const mergedHLines = new Set([0, state.imageHeight]);  // 始终包含上下边界
        for (const oldStrip of state.stripHorizontalLines) {
            // 重叠判定：两个区间有交集
            // 注意用 < 不是 <=，避免相邻列重复计入边界
            const overlap = oldStrip.x_start < xEnd && oldStrip.x_end > xStart;
            if (overlap) {
                for (const y of oldStrip.horizontal_lines) {
                    mergedHLines.add(y);
                }
            }
        }

        newStrips.push({
            strip_index: i,
            x_start: xStart,
            x_end: xEnd,
            // 转成排序数组，Set 本身不保证顺序
            horizontal_lines: Array.from(mergedHLines).sort((a, b) => a - b)
        });
    }

    state.stripHorizontalLines = newStrips;
}
```

注释要点：
- **JSDoc 写清用途、参数、调用场景**，让调用方一眼看出该不该用这个函数。
- **关键 bug 防御处**写引用：`# 见 docs/任务书_页面1_切割布局.md → 列结构更新算法`，便于回溯。
- **魔法数字 / 坐标方向**逐个解释：例如 `imageWidth` 是图像坐标不是屏幕坐标、`includes` 去重防什么坑。
- **state 字段语义**：每个字段在 `static/js/layout.js:3-29` 已逐项注释，新增字段也必须照做。

### 新增需求时的注释写法

若一段代码对应 `docs/任务书_页面N_*.md` 的某条算法或坑点，在关键位置加一行引用：

```python
# 见 docs/任务书_页面1_切割布局.md → 列结构更新算法
```

```javascript
// 见 docs/任务书_页面4_标注出图.md → 容易犯的错误 → 输入循环更新
```

便于读者从代码跳回设计文档、从设计文档定位到代码。

## 调试入口

- 用户操作触发的所有文件落盘位置：`docs/数据存储说明.md` 有完整清单。
- 页面1 调试：Canvas 上三种颜色叠加显示，浏览器 DevTools 看 `state` 与控制台日志。
- 倾斜校正算法：`utils/image_processor.py:51` 的 `deskew` 函数（投影轮廓法）。

## 父级规则

本项目还受上级 `g:\Projects\projects_ai\CLAUDE.md` 约束（减少常见 LLM 编码错误的行为准则）。上级规则优先，但项目级约定（如本文件的任务书引用、详细注释）在项目内优先。
