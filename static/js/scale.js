/* 缩放校正页面逻辑
 *
 * v2 算法：所有字统一高度（默认填 0.9×512 = 460px 高），宽度按字形自然变。
 * 旧版是「×1.15」语义（只放大、不归一），导致大字符撑满画布、小字符迷你。
 * 详见 utils/scale_processor.py → scale_char + docs/任务书_页面3_缩放校正.md
 */

// 状态管理
const state = {
    imageHash: null,
    characters: [],
    processedCharacters: [],
    isProcessed: false,
    outputDir: null,
    // v2 算法参数（与 scale_processor.py 同步）
    fillRatio: 0.9,
    maxWidthRatio: 0.95,
};

// DOM 元素
const elements = {
    emptyState: document.getElementById('emptyState'),
    previewGrid: document.getElementById('previewGrid'),
    totalCount: document.getElementById('totalCount'),
    scaleSlider: document.getElementById('scaleSlider'),
    scaleValue: document.getElementById('scaleValue'),
    fillRatioSlider: document.getElementById('fillRatioSlider'),
    fillRatioValue: document.getElementById('fillRatioValue'),
    maxWidthRatioSlider: document.getElementById('maxWidthRatioSlider'),
    maxWidthRatioValue: document.getElementById('maxWidthRatioValue'),
    progressContainer: document.getElementById('progressContainer'),
    progressFill: document.getElementById('progressFill'),
    progressText: document.getElementById('progressText'),
    processBtn: document.getElementById('processBtn'),
    saveBtn: document.getElementById('saveBtn'),
    openDirBtn: document.getElementById('openDirBtn'),
    annotateBtn: document.getElementById('annotateBtn')
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadCharacters();
    setupEventListeners();
});

function setupEventListeners() {
    // 滑块事件：v2 = 目标高度倍数，100% = 填满 0.9×512 = 460px
    elements.scaleSlider.addEventListener('input', (e) => {
        elements.scaleValue.textContent = e.target.value + '%';
    });
    // 画布填满比例滑块：70-95（百分比）
    elements.fillRatioSlider.addEventListener('input', (e) => {
        const pct = parseInt(e.target.value, 10);
        state.fillRatio = pct / 100;
        elements.fillRatioValue.textContent = pct + '%';
    });
    // 宽字保护滑块：70-100（百分比）
    elements.maxWidthRatioSlider.addEventListener('input', (e) => {
        const pct = parseInt(e.target.value, 10);
        state.maxWidthRatio = pct / 100;
        elements.maxWidthRatioValue.textContent = pct + '%';
    });

    // 按钮事件
    elements.processBtn.addEventListener('click', startProcess);
    elements.saveBtn.addEventListener('click', saveResults);
    elements.openDirBtn.addEventListener('click', openDirectory);
    elements.annotateBtn.addEventListener('click', () => {
        location.href = '/annotate';
    });
}

// 传统书法顺序：从上到下，从右到左
function getTraditionalOrder(characters) {
    return [...characters].sort((a, b) => {
        if (a.strip_index !== b.strip_index) {
            return b.strip_index - a.strip_index;  // strip_index大的（右边列）排前面
        }
        return a.char_index - b.char_index;  // 同一列，char_index小的（上面）排前面
    });
}

// 构造 /api/process_scale 与 /api/save_scaled 的通用参数对象
// 集中管理方便后续调整（如新增 align/background 选项时不用到处改）
function getScaleParams() {
    return {
        scale: parseInt(elements.scaleSlider.value, 10) / 100,
        align: document.querySelector('input[name="align"]:checked')?.value || 'center',
        background: document.querySelector('input[name="background"]:checked')?.value || 'black',
        target_size: 512,
        fill_ratio: state.fillRatio,
        max_width_ratio: state.maxWidthRatio,
    };
}

// 加载字符数据
async function loadCharacters() {
    state.imageHash = localStorage.getItem('currentImageHash');

    if (!state.imageHash) {
        showEmptyState();
        return;
    }

    try {
        const response = await fetch(`/api/get_cut_results/${state.imageHash}`);
        const data = await response.json();

        if (!data.success) {
            showEmptyState();
            return;
        }

        // 只过滤「已删除」的，**保留被判为空白（is_empty）的字符**，然后按传统顺序排序。
        //
        // 为什么不再过滤 is_empty：is_empty 只是切割阶段的**自动判断**（见
        // empty_detector.detect_empty_slice），并不代表用户想丢掉它。以前这里把
        // 空字符滤掉，用户从 /adjust 切到 /scale 会发现那些图"不见了"，看起来像被
        // 自动删除——而用户并没有点过 /adjust 的「一键清除空白字符」。
        // 是否清除空白**只能由用户显式操作**（/adjust 的清除按钮），加载页面不得代为决定。
        //
        // 注意：保留 is_empty 的字符意味着它们也会进入处理/保存流程，
        // 即空切片也会生成一张 512x512 的输出图（全黑），与用户"保留"的意图一致。
        const filteredChars = data.characters.filter(c => !c.deleted);
        state.characters = getTraditionalOrder(filteredChars);

        if (state.characters.length === 0) {
            showEmptyState();
            return;
        }

        showPreviewGrid();
        updateUI();

        // 把上次保存的参数还原到控件上（只影响控件显示，不触发任何写盘）。
        // 不还原的话，用户点「处理」时用的会是页面默认值（居中），而不是他上次的设置。
        const restored = data.scale_params ? applyScaleParams(data.scale_params) : true;
        if (!restored) {
            console.warn('保存的缩放参数在本页无法完整还原（如角对齐）', data.scale_params);
            showToast(`该批次保存时用的是「${data.scale_params.align}」对齐，本页没有对应选项，` +
                      `控件只能显示默认值；要按新参数重做请点「处理」`);
        }

        // === 打开页面只做「只读预览」，绝不写盘 ===
        // 这里以前是无条件 await autoProcess()（POST /api/process_scale），
        // 而那个接口会**重写整个 scaled/ 目录** —— 于是「打开一次 /scale」就等于
        // 「用页面参数覆盖一次缩放结果」。2026-09-13 正是这样误覆盖掉了 671 个文件。
        // 现在改成：有现成的缩放结果就只读预览它们，没有就预览原始切图；
        // 想按当前参数重新生成，必须显式点「处理」按钮（startProcess）才写盘。
        await previewExistingScaled();

    } catch (error) {
        console.error('加载字符数据失败:', error);
        showEmptyState();
    }
}

/**
 * 只读预览磁盘上现有的缩放结果（**不写盘**）。
 *
 * 数据源 /api/get_scaled_results：它返回会话里保存的 scaled_characters；若该会话
 * 从未保存过缩放结果，它会退化成返回 characters（那些只有 image_url、没有
 * processed_url），所以判定条件是「有没有 processed_url」，不能只看数组长度。
 *
 *   有 → 用 scaled 图渲染预览，并把「保存 / 打开目录 / 下一步」置为可用
 *        （磁盘上确实已经有结果了，isProcessed 名副其实）
 *   无 → 退化为渲染原始切图，按钮保持禁用（还没处理过，没什么可保存）
 */
async function previewExistingScaled() {
    try {
        const r = await fetch(`/api/get_scaled_results/${state.imageHash}`);
        const d = await r.json();
        // 口径与上面的 loadCharacters 一致：**不过滤 is_empty**。
        // 这里原先带 `&& !c.is_empty`，会让"保留空白字符"的效果在刷新后又失效——
        // 处理完的空白切片要么被滤掉（用户又看到图少了），要么永远进不了预览。
        // 只保留"确实有已处理结果（processed_url）"这一个条件。
        const chars = (d.characters || []).filter(c => c.processed_url);
        if (d.success && chars.length > 0) {
            state.processedCharacters = chars;
            state.isProcessed = true;
            renderProcessedPreview(chars);
            elements.saveBtn.disabled = false;
            elements.openDirBtn.disabled = false;
            elements.annotateBtn.disabled = false;
            return;
        }
    } catch (e) {
        console.warn('读取现有缩放结果失败，退化为预览原始切图:', e);
    }
    renderOriginalPreview();
}

/**
 * 把会话里保存的缩放参数还原到页面控件上。
 *
 * 为什么需要：「处理」（startProcess）读的是**控件当前值**。不先还原，用户点
 * 「处理」时用的就是页面默认值（居中、100% 等），而不是他上次保存的设置——
 * 那等于一按就把上次的成果换成另一套参数。
 *
 * 注意：本函数**只改控件状态，不触发任何写盘**。
 *
 * @param {object} p 服务端返回的 scale_params（见 app.py 的 _get_scale_params）
 * @returns {boolean} true = 参数已完整还原；false = 有参数在本页找不到对应控件
 *                    （例如标点角对齐 bottom-left），控件只能显示默认值，
 *                    调用方会 toast 提醒用户
 */
function applyScaleParams(p) {
    let ok = true;

    if (typeof p.scale === 'number') {
        const v = Math.round(p.scale * 100);
        elements.scaleSlider.value = v;
        elements.scaleValue.textContent = v + '%';
    }
    if (typeof p.fill_ratio === 'number') {
        state.fillRatio = p.fill_ratio;
        const v = Math.round(p.fill_ratio * 100);
        elements.fillRatioSlider.value = v;
        elements.fillRatioValue.textContent = v + '%';
    }
    if (typeof p.max_width_ratio === 'number') {
        state.maxWidthRatio = p.max_width_ratio;
        const v = Math.round(p.max_width_ratio * 100);
        elements.maxWidthRatioSlider.value = v;
        elements.maxWidthRatioValue.textContent = v + '%';
    }

    // 单选框：本页只有 center / top / baseline 三种对齐，没有角对齐
    //（top-left / top-right / bottom-left / bottom-right —— 那是给标点贴角用的）。
    // 保存的值若不在本页选项里，就**一个都不勾**并返回 false，让调用方跳过自动处理。
    const al = document.querySelector(`input[name="align"][value="${p.align}"]`);
    if (al) {
        al.checked = true;
    } else {
        ok = false;
    }
    const bg = document.querySelector(`input[name="background"][value="${p.background}"]`);
    if (bg) {
        bg.checked = true;
    } else {
        ok = false;
    }
    return ok;
}

// 【已删除】autoProcess()：原先在页面加载时自动 POST /api/process_scale（会重写
// 整个 scaled/ 目录）。它与下面的 startProcess() 逻辑几乎重复，只差一条进度文字，
// 而「打开页面就写盘」正是 2026-09-13 误覆盖 671 个文件的直接原因（见版本历史）。
// 现在写盘只发生在用户显式点击「处理」（startProcess）时，故整个函数移除；
// 若将来需要「打开即处理」，请复用 startProcess 而不要恢复这条自动路径。

function showEmptyState() {
    elements.emptyState.style.display = 'flex';
    elements.previewGrid.style.display = 'none';
    elements.totalCount.textContent = '0';
}

function showPreviewGrid() {
    elements.emptyState.style.display = 'none';
    elements.previewGrid.style.display = 'grid';
}

function updateUI() {
    elements.totalCount.textContent = state.characters.length;
}

// 渲染原始预览
function renderOriginalPreview() {
    elements.previewGrid.innerHTML = '';

    state.characters.forEach((char, index) => {
        const card = createPreviewCard(char, index);
        elements.previewGrid.appendChild(card);
    });
}

// 创建预览卡片
function createPreviewCard(char, index) {
    const card = document.createElement('div');
    // 空白切片加 .is-empty 标记（样式见 scale.html）：
    // 现在保留它们进入流程，但预览图是一张全黑图、与"字没写出来"无法区分，
    // 所以给个角标提示"这张被自动判为空白"，要不要清由用户到 /adjust 决定
    card.className = 'preview-card' + (char.is_empty ? ' is-empty' : '');

    const number = document.createElement('div');
    number.className = 'preview-number';
    number.textContent = index + 1;

    const img = document.createElement('img');
    img.src = char.image_url;
    img.alt = `字符 ${index + 1}`;

    card.appendChild(number);
    card.appendChild(img);

    if (char.is_empty) {
        const badge = document.createElement('div');
        badge.className = 'preview-empty-badge';
        badge.textContent = '空白';
        badge.title = `此切片被自动判为空白（text_ratio=${char.text_ratio ?? '?'}）。` +
                      `已保留，如需清除请到「切割调整」页操作`;
        card.appendChild(badge);
    }

    return card;
}

// 开始处理（用户主动点按钮）
async function startProcess() {
    if (state.characters.length === 0) {
        showToast('没有可处理的字符');
        return;
    }

    const params = getScaleParams();
    elements.processBtn.disabled = true;
    showProgress();

    try {
        const response = await fetch('/api/process_scale', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                characters: state.characters,
                ...params
            })
        });

        const data = await response.json();

        if (data.success) {
            state.processedCharacters = data.characters;
            state.outputDir = data.output_dir;
            state.isProcessed = true;

            renderProcessedPreview(data.characters);

            elements.saveBtn.disabled = false;
            elements.openDirBtn.disabled = false;
            elements.annotateBtn.disabled = false;

            showToast('处理完成');
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('处理失败: ' + error.message);
    }

    elements.processBtn.disabled = false;
    hideProgress();
}

// 渲染处理后的预览
function renderProcessedPreview(characters) {
    elements.previewGrid.innerHTML = '';

    characters.forEach((char, index) => {
        const card = document.createElement('div');
        // 空白切片的标记与 createPreviewCard 保持一致（保留但标出来）
        card.className = 'preview-card' + (char.is_empty ? ' is-empty' : '');

        const number = document.createElement('div');
        number.className = 'preview-number';
        number.textContent = index + 1;

        const img = document.createElement('img');
        img.src = char.processed_url + '?t=' + Date.now(); // 添加时间戳避免缓存
        img.alt = `字符 ${index + 1}`;

        card.appendChild(number);
        card.appendChild(img);

        if (char.is_empty) {
            const badge = document.createElement('div');
            badge.className = 'preview-empty-badge';
            badge.textContent = '空白';
            card.appendChild(badge);
        }

        elements.previewGrid.appendChild(card);
    });
}

// 显示进度
function showProgress() {
    elements.progressContainer.classList.add('visible');
    elements.progressFill.style.width = '0%';
    elements.progressText.textContent = '处理中...';
}

function hideProgress() {
    elements.progressContainer.classList.remove('visible');
}

function updateProgress(current, total) {
    const percent = Math.round((current / total) * 100);
    elements.progressFill.style.width = percent + '%';
    elements.progressText.textContent = `处理中... ${current}/${total}`;
}

// 保存结果
// 服务端拿到这次请求后，如果 session.scale_algorithm_version < 当前值，会主动重跑所有字符 + 覆盖 scaled 目录。
// 所以即使 scale 参数没变，第一次保存（算法迁移）也会触发重生成；后续保存无副作用。
async function saveResults() {
    if (!state.isProcessed) {
        showToast('请先处理');
        return;
    }

    const params = getScaleParams();
    showLoading('保存中...');

    try {
        const response = await fetch('/api/save_scaled', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                characters: state.processedCharacters,
                ...params
            })
        });

        const data = await response.json();

        if (data.success) {
            if (data.regenerated) {
                showToast(`已自动重新处理 ${data.regenerated_count} 个字符（v${data.algorithm_version} 算法）`);
            } else {
                showToast('保存成功');
            }
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('保存失败: ' + error.message);
    }

    hideLoading();
}

// 打开目录
async function openDirectory() {
    if (!state.outputDir) {
        showToast('请先处理');
        return;
    }

    try {
        const response = await fetch('/api/open_directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: state.outputDir })
        });

        const data = await response.json();

        if (data.success) {
            showToast('已打开目录');
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('打开目录失败: ' + error.message);
    }
}
