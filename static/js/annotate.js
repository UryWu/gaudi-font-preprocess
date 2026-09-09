/* 标注出图页面逻辑 */

// 状态管理
const state = {
    imageHash: null,
    characters: [],
    outputDir: null,
    exportedDir: null,
    mode: 'mixed',  // 'mixed'、'traditional' 或 'simplified'
    inputText: ''
};

// DOM 元素
const elements = {
    emptyState: document.getElementById('emptyState'),
    cardGrid: document.getElementById('cardGrid'),
    inputCount: document.getElementById('inputCount'),
    cardCount: document.getElementById('cardCount'),
    mixedInput: document.getElementById('mixedInput'),
    simplifiedInput: document.getElementById('simplifiedInput'),
    traditionalInput: document.getElementById('traditionalInput'),
    mixedMode: document.getElementById('mixedMode'),
    simplifiedMode: document.getElementById('simplifiedMode'),
    traditionalMode: document.getElementById('traditionalMode'),
    mixedInputGroup: document.getElementById('mixedInputGroup'),
    simplifiedInputGroup: document.getElementById('simplifiedInputGroup'),
    traditionalInputGroup: document.getElementById('traditionalInputGroup'),
    importBtn: document.getElementById('importBtn'),
    exportBtn: document.getElementById('exportBtn'),
    csvBtn: document.getElementById('csvBtn'),
    openDirBtn: document.getElementById('openDirBtn'),
    annotateBtn: document.getElementById('annotateBtn'),
    ocrAnnotateBtn: document.getElementById('ocrAnnotateBtn'),
    ocrConfig: document.getElementById('ocrConfig'),
    ocrThreshold: document.getElementById('ocrThreshold'),
    ocrThresholdValue: document.getElementById('ocrThresholdValue'),
    ocrProgress: document.getElementById('ocrProgress'),
    ocrProgressFill: document.getElementById('ocrProgressFill'),
    ocrProgressText: document.getElementById('ocrProgressText'),
    ocrFilterBtn: document.getElementById('ocrFilterBtn'),
    ocrFilterCount: document.getElementById('ocrFilterCount'),
    clearBtn: document.getElementById('clearBtn'),
    cardSearchBar: document.getElementById('cardSearchBar'),
    cardSearchInput: document.getElementById('cardSearchInput'),
    cardSearchCount: document.getElementById('cardSearchCount'),
    cardSearchClear: document.getElementById('cardSearchClear'),
    cardSearchHelp: document.getElementById('cardSearchHelp'),
    cardSearchTip: document.getElementById('cardSearchTip'),
    shortcutHelpModal: document.getElementById('shortcutHelpModal'),
    shortcutHelpClose: document.getElementById('shortcutHelpClose'),
};

// OCR 状态
let ocrState = {
    taskId: null,           // 当前后台 task_id
    pollTimer: null,        // 轮询 timer
    applied: 0,             // 已填入卡片数
    highConf: 0,            // 高置信填入数
    lowConf: 0,             // 低置信填入数
    threshold: 0.5,         // 当前阈值
    filterOnly: false,      // 「只看待复查」是否激活
};

// 搜索状态
let searchState = {
    query: '',              // 当前搜索词
    rangeStart: null,       // index 范围：起始（1-based）
    rangeEnd: null,         // index 范围：结束
    utfPrefix: null,        // UTF 码前缀
    charMatch: null,        // 字符匹配
    filterActive: false,    // 是否有任何过滤条件
};

// 键盘焦点状态
let keyboardState = {
    focusedIndex: -1,       // 当前键盘焦点卡片的 state.characters index
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadCharacters();
    setupEventListeners();
});

function setupEventListeners() {
    // 导入按钮
    elements.importBtn.addEventListener('click', importDirectory);

    // 导出按钮
    elements.exportBtn.addEventListener('click', exportImages);
    elements.csvBtn.addEventListener('click', exportCSV);
    elements.openDirBtn.addEventListener('click', openOutputDirectory);

    // 标注按钮
    elements.annotateBtn.addEventListener('click', startAnnotate);
    elements.ocrAnnotateBtn.addEventListener('click', ocrAutoAnnotate);
    elements.ocrFilterBtn.addEventListener('click', toggleOcrFilter);
    elements.clearBtn.addEventListener('click', clearInputs);

    // OCR 阈值滑块：实时更新显示值
    elements.ocrThreshold.addEventListener('input', (e) => {
        const v = parseInt(e.target.value, 10) / 100;
        ocrState.threshold = v;
        elements.ocrThresholdValue.textContent = v.toFixed(2);
    });

    // 繁简切换
    elements.mixedMode.addEventListener('click', () => setMode('mixed'));
    elements.simplifiedMode.addEventListener('click', () => setMode('simplified'));
    elements.traditionalMode.addEventListener('click', () => setMode('traditional'));

    // 繁简混合输入（只去标点，不转换）
    let mixedDebounceTimer;
    elements.mixedInput.addEventListener('input', (e) => {
        clearTimeout(mixedDebounceTimer);
        mixedDebounceTimer = setTimeout(() => {
            const cleaned = removePunctuation(e.target.value);
            if (cleaned !== e.target.value) {
                e.target.value = cleaned;
            }
            elements.inputCount.textContent = Array.from(cleaned).length;
        }, 300);
    });

    // 搜索框：实时过滤
    elements.cardSearchInput.addEventListener('input', (e) => {
        applySearch(e.target.value);
    });
    elements.cardSearchClear.addEventListener('click', () => {
        elements.cardSearchInput.value = '';
        applySearch('');
        elements.cardSearchInput.focus();
    });
    // 用法提示气泡：点 ? 切换显示
    elements.cardSearchHelp.addEventListener('click', (e) => {
        e.stopPropagation();
        elements.cardSearchTip.hidden = !elements.cardSearchTip.hidden;
    });
    // 外部点击关闭
    document.addEventListener('click', (e) => {
        if (!elements.cardSearchTip.hidden &&
            !elements.cardSearchTip.contains(e.target) &&
            e.target !== elements.cardSearchHelp) {
            elements.cardSearchTip.hidden = true;
        }
    });

    // 快捷键帮助 modal
    elements.shortcutHelpClose.addEventListener('click', hideShortcutHelp);
    // 点击背景关闭
    if (elements.shortcutHelpModal) {
        const backdrop = elements.shortcutHelpModal.querySelector('.shortcut-help-backdrop');
        if (backdrop) backdrop.addEventListener('click', hideShortcutHelp);
    }

    // 繁简输入联动（带去标点和防抖）
    let simpDebounceTimer;
    let tradDebounceTimer;
    let isUpdating = false;  // 防止循环更新

    elements.simplifiedInput.addEventListener('input', (e) => {
        if (isUpdating) return;
        clearTimeout(simpDebounceTimer);
        simpDebounceTimer = setTimeout(async () => {
            // 去除标点符号
            const cleaned = removePunctuation(e.target.value);
            if (cleaned !== e.target.value) {
                e.target.value = cleaned;
            }
            // 转换并更新繁体框
            if (cleaned) {
                const result = await callConvertApi('/api/convert_to_traditional', cleaned);
                if (result) {
                    isUpdating = true;
                    elements.traditionalInput.value = result;
                    elements.inputCount.textContent = Array.from(cleaned).length;
                    setTimeout(() => isUpdating = false, 50);
                }
            } else {
                isUpdating = true;
                elements.traditionalInput.value = '';
                elements.inputCount.textContent = 0;
                setTimeout(() => isUpdating = false, 50);
            }
        }, 300);
    });

    elements.traditionalInput.addEventListener('input', (e) => {
        if (isUpdating) return;
        clearTimeout(tradDebounceTimer);
        tradDebounceTimer = setTimeout(async () => {
            // 去除标点符号
            const cleaned = removePunctuation(e.target.value);
            if (cleaned !== e.target.value) {
                e.target.value = cleaned;
            }
            // 转换并更新简体框
            if (cleaned) {
                const result = await callConvertApi('/api/convert_to_simplified', cleaned);
                if (result) {
                    isUpdating = true;
                    elements.simplifiedInput.value = result;
                    elements.inputCount.textContent = Array.from(cleaned).length;
                    setTimeout(() => isUpdating = false, 50);
                }
            } else {
                isUpdating = true;
                elements.simplifiedInput.value = '';
                elements.inputCount.textContent = 0;
                setTimeout(() => isUpdating = false, 50);
            }
        }, 300);
    });
}

// 调用转换API
async function callConvertApi(url, text) {
    try {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text })
        });
        const data = await response.json();
        if (data.success) {
            console.log(`转换: ${text} -> ${data.result}`);
            return data.result;
        } else {
            console.error('转换失败:', data.error);
            return null;
        }
    } catch (error) {
        console.error('转换API调用失败:', error);
        return null;
    }
}

// 去除标点符号（保留中文、英文、数字，支持CJK扩展区）
function removePunctuation(text) {
    // CJK基本区 \u4e00-\u9fff，扩展A \u3400-\u4dbf
    // 扩展B-F需要用u标志匹配补充平面（代理对范围）
    return text.replace(/[^\u3400-\u9fff\uF900-\uFAFFa-zA-Z0-9\u{20000}-\u{2FA1F}]/gu, '');
}

// 设置模式
function setMode(mode) {
    state.mode = mode;

    // 切换按钮激活状态
    elements.mixedMode.classList.toggle('active', mode === 'mixed');
    elements.simplifiedMode.classList.toggle('active', mode === 'simplified');
    elements.traditionalMode.classList.toggle('active', mode === 'traditional');

    // 切换输入区域显示
    if (mode === 'mixed') {
        elements.mixedInputGroup.style.display = '';
        elements.simplifiedInputGroup.style.display = 'none';
        elements.traditionalInputGroup.style.display = 'none';
        const cleaned = removePunctuation(elements.mixedInput.value);
        elements.inputCount.textContent = Array.from(cleaned).length;
    } else {
        elements.mixedInputGroup.style.display = 'none';
        elements.simplifiedInputGroup.style.display = '';
        elements.traditionalInputGroup.style.display = '';
        const text = mode === 'simplified' ? elements.simplifiedInput.value : elements.traditionalInput.value;
        elements.inputCount.textContent = Array.from(removePunctuation(text)).length;
    }

    // 更新卡片显示
    updateCardsDisplay();
}

// 更新卡片显示（根据繁简模式）
function updateCardsDisplay() {
    const cards = document.querySelectorAll('.char-card');
    cards.forEach(card => {
        const simplifiedRow = card.querySelector('.simplified-row');
        const traditionalRow = card.querySelector('.traditional-row');

        if (state.mode === 'simplified') {
            simplifiedRow.classList.remove('dimmed');
            simplifiedRow.classList.add('highlighted');
            traditionalRow.classList.add('dimmed');
            traditionalRow.classList.remove('highlighted');
        } else if (state.mode === 'traditional') {
            traditionalRow.classList.remove('dimmed');
            traditionalRow.classList.add('highlighted');
            simplifiedRow.classList.add('dimmed');
            simplifiedRow.classList.remove('highlighted');
        } else {
            // mixed mode - both highlighted
            simplifiedRow.classList.remove('dimmed');
            simplifiedRow.classList.add('highlighted');
            traditionalRow.classList.remove('dimmed');
            traditionalRow.classList.add('highlighted');
        }
    });
}

// 加载字符数据
async function loadCharacters() {
    state.imageHash = localStorage.getItem('currentImageHash');

    if (!state.imageHash) {
        showEmptyState();
        return;
    }

    try {
        // 尝试加载缩放校正后的数据
        let response = await fetch(`/api/get_scaled_results/${state.imageHash}`);
        let data = await response.json();

        if (!data.success || !data.characters || data.characters.length === 0) {
            // 如果没有缩放校正数据，加载原始切割结果
            response = await fetch(`/api/get_cut_results/${state.imageHash}`);
            data = await response.json();

            if (!data.success) {
                showEmptyState();
                return;
            }
        }

        // 过滤掉已删除和空字符，按传统顺序排序
        const filteredChars = data.characters.filter(c => !c.deleted && !c.is_empty);
        state.characters = getTraditionalOrder(filteredChars);
        state.outputDir = data.output_dir;

        if (state.characters.length === 0) {
            showEmptyState();
            return;
        }

        showCardGrid();
        renderCards();
        updateUI();

        // 加载之前保存的 OCR 标注并预填 input（持久化）
        // 流程：拉 server 的 ocr_annotations.json，对每张卡填 value + 加 .ocr-filled
        await loadOcrAnnotations();

    } catch (error) {
        console.error('加载字符数据失败:', error);
        showEmptyState();
    }
}

// 从服务器拉之前保存的 OCR 标注，预填到对应卡的 input
// 值可能是：旧格式字符串 "华"（兼容），或新对象 {simp, conf, source, updated_at}
async function loadOcrAnnotations() {
    try {
        const r = await fetch(`/api/get_ocr_annotations/${state.imageHash}`);
        const data = await r.json();
        if (!data.success || !data.annotations) return;
        const annotations = data.annotations;  // { "scaled_0002.png": {simplified, conf, ...}, ... }
        // 建 filename → 卡下标 映射
        // session 里 char 字段可能是 char_NNNN.png（原始切割）或
        // scaled_NNNN.png（缩放后）。OCR 标注存的 key 也是其中之一。
        // 索引时同时认两种 fn，确保任意一种都能对得上。
        const fnToIdx = {};
        state.characters.forEach((c, i) => {
            // 后端可能给 processed_filename 或 filename，OCR 标注也可能存其中之一
            for (const fn of [c.processed_filename, c.filename]) {
                if (fn) fnToIdx[fn] = i;
            }
            // 若文件名带 scaled_ 前缀（如 scaled_0002.png），也兼容对应 char_0002.png
            if (c.processed_filename && c.processed_filename.startsWith('scaled_')) {
                const charEq = c.processed_filename.replace(/^scaled_/, 'char_');
                fnToIdx[charEq] = i;
            }
            if (c.filename && c.filename.startsWith('char_')) {
                const scaledEq = c.filename.replace(/^char_/, 'scaled_');
                fnToIdx[scaledEq] = i;
            }
        });
        let count = 0;
        for (const [filename, rec] of Object.entries(annotations)) {
            if (!rec) continue;
            const idx = fnToIdx[filename];
            if (idx === undefined) continue;   // 记录对应卡不在当前列表（删除/改名），跳过
            const simplified = typeof rec === 'string' ? rec : (rec.simplified || '');
            const conf = typeof rec === 'object' ? (rec.conf || 0) : 0;
            if (!simplified) continue;
            const card = document.querySelector(`.char-card[data-index="${idx}"]`);
            if (!card) continue;
            const simpInput = card.querySelector('.simplified-input');
            if (!simpInput) continue;
            // 卡已手动填过（值非空且非 OCR）就跳过——OCR 标注不该覆盖用户输入
            if (simpInput.value && !card.classList.contains('ocr-filled')) continue;
            // 预填 + 标 OCR 标记
            simpInput.value = simplified;
            const simpUtf = card.querySelector('.simplified-utf');
            if (simpUtf) simpUtf.textContent = getUtfCode(simplified);
            card.classList.add('ocr-filled');
            state.characters[idx].simplified = simplified;
            // 恢复置信度徽章（与 OCR 跑完时同款样式）
            if (conf > 0 && !card.querySelector('.ocr-badge')) {
                const badge = document.createElement('div');
                badge.className = 'ocr-badge ' + (conf >= 0.5 ? 'high' : 'low');
                badge.textContent = `${Math.round(conf * 100)}%`;
                badge.title = `OCR 置信度 ${conf}`;
                card.appendChild(badge);
            }
            // 触发自动转繁（填繁体框）
            convertSingleToTraditional(simplified, idx);
            count++;
        }
        if (count > 0) {
            showToast(`已恢复 ${count} 张 OCR 标注（来自上次保存）`);
            updateOcrFilterButton();
        }
    } catch (err) {
        console.warn('加载 OCR 标注失败:', err);
    }
}

// 保存/删除一条 OCR 标注到服务器（fire-and-forget）
// - filename：卡的稳定标识（scaled_0002.png / char_0000.png），作 json key
// - simplified：识别出的简体字
// - simplified 非空：写详细记录 { simplified, conf, source, updated_at }
// - simplified 为空：删除该条（用户接管这张卡，刷新不恢复旧 OCR 值）
function saveOcrAnnotation(filename, simplified, opts) {
    if (!state.imageHash || !filename) return;
    const body = { filename, simplified: simplified || '' };
    if (simplified && opts) {
        if (opts.conf !== undefined) body.conf = opts.conf;
        if (opts.source) body.source = opts.source;
    }
    fetch(`/api/save_ocr_annotation/${state.imageHash}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body)
    }).catch(err => console.warn('保存 OCR 标注失败:', err));
}

// 传统书法顺序：从上到下，从右到左
function getTraditionalOrder(characters) {
    return [...characters].sort((a, b) => {
        if (a.strip_index !== b.strip_index) {
            return b.strip_index - a.strip_index;
        }
        return a.char_index - b.char_index;
    });
}

function showEmptyState() {
    elements.emptyState.style.display = 'flex';
    elements.cardGrid.style.display = 'none';
    elements.cardCount.textContent = '0';
}

function showCardGrid() {
    elements.emptyState.style.display = 'none';
    elements.cardGrid.style.display = 'grid';
}

function updateUI() {
    const activeCount = state.characters.filter(c => !c.deleted).length;
    elements.cardCount.textContent = activeCount;
    elements.annotateBtn.disabled = activeCount === 0;
    elements.exportBtn.disabled = activeCount === 0;
    elements.csvBtn.disabled = activeCount === 0;
    // OCR 按钮：有字符时启用（无 task 正在跑时）
    elements.ocrAnnotateBtn.disabled = activeCount === 0 || !!ocrState.taskId;
    // OCR 配置面板：只在有字符时显示
    if (elements.ocrConfig) {
        elements.ocrConfig.style.display = activeCount > 0 ? 'flex' : 'none';
    }
    // 搜索栏：有字符才显示
    if (elements.cardSearchBar) {
        elements.cardSearchBar.style.display = activeCount > 0 ? 'flex' : 'none';
    }
    // 同步 OCR 过滤按钮的可见性 + 计数
    updateOcrFilterButton();
}

// 更新「只看待复查」按钮的可见性、计数
// - 有 OCR 填入的卡时显示按钮
// - 计数 = 当前 DOM 中 .ocr-filled 的卡片数
// - 如果过滤激活，隐藏其它卡
function updateOcrFilterButton() {
    const ocrFilled = elements.cardGrid.querySelectorAll('.char-card.ocr-filled').length;
    elements.ocrFilterCount.textContent = ocrFilled;
    // 有 OCR 填入的卡时才显示按钮
    elements.ocrFilterBtn.style.display = ocrFilled > 0 ? '' : 'none';
    // 如果过滤激活 + 计数变 0，自动关闭过滤
    if (ocrState.filterOnly && ocrFilled === 0) {
        ocrState.filterOnly = false;
    }
    elements.ocrFilterBtn.classList.toggle('active', ocrState.filterOnly);
    // 应用过滤
    applyOcrFilter();
}

// 切换「只看待复查」过滤
function toggleOcrFilter() {
    ocrState.filterOnly = !ocrState.filterOnly;
    elements.ocrFilterBtn.classList.toggle('active', ocrState.filterOnly);
    applyOcrFilter();
}

// 应用过滤：filterOnly 时，非 OCR 卡加 .filter-hidden
function applyOcrFilter() {
    if (!elements.cardGrid) return;
    const cards = elements.cardGrid.querySelectorAll('.char-card');
    cards.forEach(card => {
        const isOcrFilled = card.classList.contains('ocr-filled');
        const shouldHide = ocrState.filterOnly && !isOcrFilled;
        card.classList.toggle('filter-hidden', shouldHide);
    });
}

// 渲染卡片
function renderCards() {
    elements.cardGrid.innerHTML = '';

    console.log('渲染卡片, 字符数量:', state.characters.length);

    state.characters.forEach((char, index) => {
        const card = createCharCard(char, index);
        elements.cardGrid.appendChild(card);
    });

    updateCardsDisplay();
    console.log('卡片渲染完成');
}

// 创建字符卡片
function createCharCard(char, index) {
    const card = document.createElement('div');
    card.className = 'char-card';
    card.dataset.index = index;

    // 右键 - 自定义菜单（删除 / 在资源管理器中打开）
    // 见 docs/调整字符画笔.md 中类似设计；这里保留标注页的「软删除」UX（卡变灰 + 已删除遮罩），
    // 但同步删除本地文件 + session 条目，避免下次 loadCharacters 时把已删的又拉回来
    card.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        showCharContextMenu(e.clientX, e.clientY, char, index);
    });

    // 获取图片URL
    // 加 ?t=Date.now() 时间戳强制绕开浏览器缓存——见 static/js/scale.js:287 同款做法。
    // 否则 /output/<path:filename> 路由的 Cache-Control: max-age=31536000, immutable
    // 会让浏览器一直拿旧的图：磁盘上 scaled_*.png 被 process_scale / save_scaled 覆盖后，
    // 用户在 /annotate 看到的图与右键「打开图片位置」打开的文件内容不一致。
    const imageUrl = (char.processed_url || char.image_url || `/output/${state.imageHash}/cutting_output/${char.filename}`) + '?t=' + Date.now();
    const filename = char.filename || char.processed_filename || 'unknown';

    console.log(`创建卡片 ${index}: filename=${filename}, imageUrl=${imageUrl}`);

    card.innerHTML = `
        <div class="card-image">
            <span class="card-filename">${filename}</span>
            <img src="${imageUrl}" alt="字符 ${index + 1}" loading="lazy" decoding="async">
        </div>
        <div class="card-info">
            <div class="card-row simplified-row">
                <label>简体:</label>
                <input type="text" class="simplified-input" data-index="${index}">
                <span class="utf-code simplified-utf"></span>
            </div>
            <div class="card-row traditional-row">
                <label>繁体:</label>
                <input type="text" class="traditional-input" data-index="${index}">
                <span class="utf-code traditional-utf"></span>
            </div>
        </div>
    `;

    // 输入事件
    const simpInput = card.querySelector('.simplified-input');
    const tradInput = card.querySelector('.traditional-input');
    const simpUtf = card.querySelector('.simplified-utf');
    const tradUtf = card.querySelector('.traditional-utf');
    // 这张卡的稳定标识：scaled_0002.png（缩放后）或 char_0000.png（原始切割）
    // ocr_annotations.json 的 key 用这个 filename，不随卡排序变化
    const cardFilename = char.processed_filename || char.filename;

    simpInput.addEventListener('input', (e) => {
        // 用户手动编辑这张卡 → 解除「OCR 填的」状态 + 删掉已存标注
        // 否则刷新页面会从 ocr_annotations 恢复旧 OCR 值，覆盖用户的手改
        if (card.classList.contains('ocr-filled')) {
            card.classList.remove('ocr-filled');
            saveOcrAnnotation(cardFilename, '');   // simp='' → 服务端删除该条
        }

        // 提取第一个完整Unicode码点（支持CJK扩展区代理对）
        const chars = Array.from(e.target.value);
        const char = chars[0] || '';
        if (e.target.value !== char) e.target.value = char;
        simpUtf.textContent = char ? getUtfCode(char) : '';
        state.characters[index].simplified = char;

        // 自动转换繁体
        if (char) {
            convertSingleToTraditional(char, index);
        }
    });

    tradInput.addEventListener('input', (e) => {
        const chars = Array.from(e.target.value);
        const char = chars[0] || '';
        if (e.target.value !== char) e.target.value = char;
        tradUtf.textContent = char ? getUtfCode(char) : '';
        state.characters[index].traditional = char;

        // 自动转换简体
        if (char) {
            convertSingleToSimplified(char, index);
        }
    });

    return card;
}

// 右键删除字符（无 confirm，由右键菜单直接调用；同时删本地文件 + session 条目）
// 与 adjust.js 的 deleteCharacter 类似，但标注页保留「软删除」UX：卡片变灰 + 「已删除」遮罩
// 标记 state.characters[index].deleted = true 避免 loadCharacters 的 !c.deleted 过滤把它再拉回来
//
// 注意：标注页加载的是 scaled_characters（来自 /api/get_scaled_results），
// 字段是 processed_filename 而非 filename——必须回退。
async function deleteCharacter(char, index) {
    // 兼容两种数据：原始切割（filename）vs 缩放后（processed_filename）
    const filename = char.processed_filename || char.filename;
    if (!filename) {
        showToast('删除失败：字符无文件名');
        return;
    }
    try {
        const r = await fetch('/api/delete_characters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                filenames: [filename]
            })
        });
        const data = await r.json();
        if (!data.success) throw new Error(data.error);
    } catch (err) {
        showToast('删除失败: ' + err.message);
        return;
    }
    // 客户端：标记为已删除（保留卡片但变灰 + 「已删除」遮罩）
    // card.deleted = true 让 loadCharacters 的 filter 跳过它，下次刷新页面就不会出现
    state.characters[index].deleted = true;
    const card = document.querySelector(`.char-card[data-index="${index}"]`);
    if (card) {
        card.classList.add('card-deleted');
        card.style.opacity = '0.3';
        card.style.pointerEvents = 'none';
    }
    updateUI();
    showToast(`已删除 ${filename}`);
}

// 显示字符图片的右键菜单
// 见 static/css/style.css .char-context-menu（与 adjust.js 共用同一组 CSS）
function showCharContextMenu(x, y, char, index) {
    // 先关闭已有菜单，避免多个同时出现
    hideCharContextMenu();

    const menu = document.createElement('div');
    menu.className = 'char-context-menu';
    menu.id = 'charContextMenu';
    menu.innerHTML = `
        <div class="ctx-item danger" data-action="delete">🗑 删除此字符</div>
        <div class="ctx-item" data-action="open-folder">📁 打开图片位置</div>
    `;
    // 定位：用 clientX/Y + position:fixed，菜单跟随光标
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    document.body.appendChild(menu);

    // 边界保护：菜单可能溢出视口右下角 → 改为向左/上展开
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        menu.style.left = (x - rect.width) + 'px';
    }
    if (rect.bottom > window.innerHeight) {
        menu.style.top = (y - rect.height) + 'px';
    }

    // 点击菜单项：分发到删除/打开
    // 兼容两种数据：原始切割（filename）vs 缩放后（processed_filename）
    menu.addEventListener('click', async (e) => {
        const action = e.target.dataset.action;
        hideCharContextMenu();
        if (action === 'delete') {
            deleteCharacter(char, index);
        } else if (action === 'open-folder') {
            const filename = char.processed_filename || char.filename;
            if (!filename) {
                showToast('打开失败：字符无文件名');
                return;
            }
            try {
                const r = await fetch('/api/open_path', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: filename, hash: state.imageHash })
                });
                const data = await r.json();
                if (!data.success) throw new Error(data.error);
                showToast(`已在资源管理器中打开 ${filename}`);
            } catch (err) {
                showToast('打开失败: ' + err.message);
            }
        }
    });
}

function hideCharContextMenu() {
    const existing = document.getElementById('charContextMenu');
    if (existing) existing.remove();
}

// 全局点击/滚动关闭菜单：useCapture=true 抢在卡片其他 click 之前触发
// 否则点菜单项自己的 click 会先被 hideCharContextMenu 关掉，菜单项逻辑不会执行
document.addEventListener('click', hideCharContextMenu, true);
document.addEventListener('scroll', hideCharContextMenu, true);

// 获取UTF编码（支持CJK扩展区等补充平面字符）
function getUtfCode(char) {
    const code = char.codePointAt(0);
    const hexLen = code > 0xFFFF ? 5 : 4;
    return 'U+' + code.toString(16).toUpperCase().padStart(hexLen, '0');
}

// 单字转换
async function convertSingleToTraditional(char, index) {
    try {
        const response = await fetch('/api/convert_to_traditional', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: char })
        });

        const data = await response.json();
        console.log(`单字简转繁: ${char} -> ${data.result}`);
        if (data.success && data.result) {
            const card = document.querySelector(`.char-card[data-index="${index}"]`);
            if (card) {
                const tradInput = card.querySelector('.traditional-input');
                const tradUtf = card.querySelector('.traditional-utf');
                tradInput.value = data.result;
                tradUtf.textContent = getUtfCode(data.result);
                state.characters[index].traditional = data.result;
            }
        }
    } catch (error) {
        console.error('转换失败:', error);
    }
}

async function convertSingleToSimplified(char, index) {
    try {
        const response = await fetch('/api/convert_to_simplified', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ text: char })
        });

        const data = await response.json();
        console.log(`单字繁转简: ${char} -> ${data.result}`);
        if (data.success && data.result) {
            const card = document.querySelector(`.char-card[data-index="${index}"]`);
            if (card) {
                const simpInput = card.querySelector('.simplified-input');
                const simpUtf = card.querySelector('.simplified-utf');
                simpInput.value = data.result;
                simpUtf.textContent = getUtfCode(data.result);
                state.characters[index].simplified = data.result;
            }
        }
    } catch (error) {
        console.error('转换失败:', error);
    }
}

// 开始标注
function startAnnotate() {
    let text;
    if (state.mode === 'mixed') {
        text = elements.mixedInput.value;
    } else if (state.mode === 'simplified') {
        text = elements.simplifiedInput.value;
    } else {
        text = elements.traditionalInput.value;
    }

    if (!text) {
        showToast('请先输入文字');
        return;
    }

    // 去除标点符号
    text = removePunctuation(text);

    if (!text) {
        showToast('输入的文字不包含有效字符');
        return;
    }

    const chars = Array.from(text);  // 用Array.from正确处理代理对（CJK扩展区）
    const cards = document.querySelectorAll('.char-card');
    let charIdx = 0;

    cards.forEach((card, index) => {
        if (state.characters[index] && state.characters[index].deleted) return;  // 跳过已删除
        if (charIdx < chars.length) {
            const char = chars[charIdx];

            if (state.mode === 'mixed') {
                // 繁简混合：直接标注，不做转换
                const simpInput = card.querySelector('.simplified-input');
                const simpUtf = card.querySelector('.simplified-utf');
                const tradInput = card.querySelector('.traditional-input');
                const tradUtf = card.querySelector('.traditional-utf');
                simpInput.value = char;
                simpUtf.textContent = getUtfCode(char);
                tradInput.value = char;
                tradUtf.textContent = getUtfCode(char);
                state.characters[index].simplified = char;
                state.characters[index].traditional = char;
            } else if (state.mode === 'simplified') {
                const simpInput = card.querySelector('.simplified-input');
                const simpUtf = card.querySelector('.simplified-utf');
                simpInput.value = char;
                simpUtf.textContent = getUtfCode(char);
                state.characters[index].simplified = char;
                convertSingleToTraditional(char, index);
            } else {
                const tradInput = card.querySelector('.traditional-input');
                const tradUtf = card.querySelector('.traditional-utf');
                tradInput.value = char;
                tradUtf.textContent = getUtfCode(char);
                state.characters[index].traditional = char;
                convertSingleToSimplified(char, index);
            }
            charIdx++;
        }
    });

    showToast('标注完成');
}

// 清空输入
function clearInputs() {
    elements.mixedInput.value = '';
    elements.simplifiedInput.value = '';
    elements.traditionalInput.value = '';
    elements.inputCount.textContent = 0;

    // 清空所有卡片的标注
    // 注意：OCR 填过的卡带 .ocr-filled 类（棕色色条 + 置信度徽章），
    // value 清掉后 class 留着会造成「有 class 但 value 空」的脏状态。
    // 这里一并清掉，让「清空」是真的完全清空。
    const cards = document.querySelectorAll('.char-card');
    cards.forEach((card, index) => {
        const simpInput = card.querySelector('.simplified-input');
        const tradInput = card.querySelector('.traditional-input');
        const simpUtf = card.querySelector('.simplified-utf');
        const tradUtf = card.querySelector('.traditional-utf');

        simpInput.value = '';
        tradInput.value = '';
        simpUtf.textContent = '';
        tradUtf.textContent = '';
        // 删 OCR 填入标记（棕色色条 + 置信度徽章）
        card.classList.remove('ocr-filled');
        // 删 .filter-hidden（清空同时清掉搜索/OCR-only 过滤）
        card.classList.remove('filter-hidden');

        if (state.characters[index]) {
            state.characters[index].simplified = '';
            state.characters[index].traditional = '';
        }
    });

    // 清空搜索框 + 「只看待复查」filter
    if (elements.cardSearchInput) elements.cardSearchInput.value = '';
    searchState.query = '';
    searchState.filterActive = false;
    ocrState.filterOnly = false;
    elements.ocrFilterBtn.classList.remove('active');
    if (elements.ocrFilterCount) elements.ocrFilterCount.textContent = '0';

    showToast('已清空');
}

// 导入目录
async function importDirectory() {
    // 使用Electron的dialog或创建一个文件选择器
    const input = document.createElement('input');
    input.type = 'file';
    input.webkitdirectory = true;

    input.onchange = async (e) => {
        const files = Array.from(e.target.files);
        if (files.length === 0) return;

        showLoading('正在导入...');

        const formData = new FormData();
        files.forEach(file => {
            formData.append('files', file);
        });

        try {
            const response = await fetch('/api/import_characters', {
                method: 'POST',
                body: formData
            });

            const data = await response.json();

            if (data.success) {
                state.characters = data.characters;
                state.outputDir = data.output_dir;
                state.imageHash = data.hash;

                localStorage.setItem('currentImageHash', state.imageHash);

                showCardGrid();
                renderCards();
                updateUI();

                showToast(`成功导入 ${data.characters.length} 个字符`);
            } else {
                throw new Error(data.error);
            }
        } catch (error) {
            showToast('导入失败: ' + error.message);
        }

        hideLoading();
    };

    input.click();
}

// 导出白底黑字（异步：启动 task → 轮询 progress → 清理中间文件）
// 329 张反色+保存需要 5-10s，sync 会让请求挂死。改成后台线程 + 进度条（与 OCR 一致）。
async function exportImages() {
    if (state.characters.length === 0) {
        showToast('没有可导出的字符');
        return;
    }

    // 收集标注数据
    const annotations = [];
    state.characters.forEach((char, index) => {
        const card = document.querySelector(`.char-card[data-index="${index}"]`);
        if (card) {
            const primaryChar = state.mode === 'simplified'
                ? card.querySelector('.simplified-input').value
                : state.mode === 'traditional'
                    ? card.querySelector('.traditional-input').value
                    : card.querySelector('.simplified-input').value || card.querySelector('.traditional-input').value;

            if (primaryChar) {
                annotations.push({
                    index: index,
                    filename: char.filename || char.processed_filename,
                    character: primaryChar,
                    simplified: card.querySelector('.simplified-input').value,
                    traditional: card.querySelector('.traditional-input').value
                });
            }
        }
    });

    if (annotations.length === 0) {
        showToast('请先标注字符');
        return;
    }

    // 禁用导出按钮 + 显示进度
    elements.exportBtn.disabled = true;
    elements.ocrProgress.style.display = 'flex';
    elements.ocrProgressFill.style.width = '0%';
    elements.ocrProgressText.textContent = `准备导出 ${annotations.length} 张…`;

    let taskId = null;
    let pollTimer = null;
    let outputDir = null;

    const reset = () => {
        if (pollTimer) { clearInterval(pollTimer); pollTimer = null; }
        elements.ocrProgress.style.display = 'none';
        elements.exportBtn.disabled = false;
    };

    try {
        // 1. 启动导出任务
        const response = await fetch('/api/export_annotated', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                annotations: annotations,
                mode: state.mode
            })
        });
        const data = await response.json();
        if (!data.success) throw new Error(data.error);
        taskId = data.task_id;
        outputDir = data.output_dir;

        // 2. 轮询进度
        pollTimer = setInterval(async () => {
            try {
                const r = await fetch(`/api/export_progress/${taskId}`);
                const p = await r.json();
                if (p.status === 'not_found') {
                    showToast('导出任务丢失（可能服务器重启）');
                    reset();
                    return;
                }
                const percent = p.total > 0 ? (p.done / p.total * 100) : 0;
                elements.ocrProgressFill.style.width = percent.toFixed(1) + '%';
                elements.ocrProgressText.textContent = `导出 ${p.done}/${p.total} (${percent.toFixed(0)}%)`;

                if (p.status === 'done') {
                    clearInterval(pollTimer);
                    pollTimer = null;
                    state.exportedDir = p.output_dir || outputDir;
                    elements.openDirBtn.disabled = false;
                    const errCount = (p.errors || []).length;
                    showToast(`导出完成：${p.count} 张${errCount ? `（${errCount} 个错误）` : ''}`);

                    // 3. 清理中间文件
                    try {
                        const cleanResp = await fetch('/api/cleanup_intermediate', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ hash: state.imageHash, keep_dir: p.output_dir || outputDir })
                        });
                        const cleanData = await cleanResp.json();
                        if (cleanData.success) console.log('中间文件清理完成:', cleanData.message);
                    } catch (e) {
                        console.warn('清理中间文件失败:', e);
                    }
                    reset();
                } else if (p.status === 'error') {
                    clearInterval(pollTimer);
                    pollTimer = null;
                    showToast('导出失败: ' + (p.error || '未知错误'));
                    reset();
                }
            } catch (err) {
                console.warn('导出轮询失败:', err);
                // 网络抖动：继续轮询
            }
        }, 1000);
    } catch (error) {
        showToast('导出失败: ' + error.message);
        reset();
    }
}

// 导出CSV
async function exportCSV() {
    if (state.characters.length === 0) {
        showToast('没有可导出的字符');
        return;
    }

    // 收集标注数据
    const annotations = [];
    state.characters.forEach((char, index) => {
        const card = document.querySelector(`.char-card[data-index="${index}"]`);
        if (card) {
            const simpChar = card.querySelector('.simplified-input').value;
            const tradChar = card.querySelector('.traditional-input').value;

            if (simpChar || tradChar) {
                annotations.push({
                    index: index,
                    filename: char.filename || char.processed_filename,
                    simplified: simpChar,
                    traditional: tradChar,
                    primary: state.mode === 'simplified' ? simpChar : state.mode === 'traditional' ? tradChar : (simpChar || tradChar)
                });
            }
        }
    });

    if (annotations.length === 0) {
        showToast('请先标注字符');
        return;
    }

    showLoading('正在导出CSV...');

    try {
        const response = await fetch('/api/export_csv', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                annotations: annotations,
                mode: state.mode
            })
        });

        const data = await response.json();

        if (data.success) {
            showToast(`成功导出CSV到 ${data.output_path}`);
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('导出失败: ' + error.message);
    }

    hideLoading();
}

// 打开输出目录
async function openOutputDirectory() {
    const dirPath = state.exportedDir || state.outputDir;

    if (!dirPath) {
        showToast('请先导出');
        return;
    }

    try {
        const response = await fetch('/api/open_directory', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: dirPath })
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

// === OCR 自动标注 ===
// 流程：点击按钮 → POST /api/ocr_start 启动后台 task →
//      轮询 /api/ocr_progress/<id> 拿增量结果 → 逐个填入空字段
// 设计原则：
// - 只填入「简化字输入框为空」的卡片（不覆盖用户已标的）
// - 置信度低于阈值的结果不填（用户可在滑块上调阈值）
// - 填过的卡加 .ocr-filled class（左侧棕条）+ .ocr-badge 显示置信度
// - 任务耗时 1.5s/张 × 329 张 ≈ 8min，必须用进度条

async function ocrAutoAnnotate() {
    if (ocrState.taskId) {
        showToast('OCR 任务正在进行中');
        return;
    }

    // 拉取已保存的 OCR 标注，跳过「已识别过」的卡（不重复识别）
    // 判据：ocr_annotations.json 的 key（filename）里有该卡的 fn
    // = 之前 OCR 识别过并保存了 → 不再重复识别
    let knownFns = new Set();
    try {
        const kr = await fetch(`/api/get_ocr_annotations/${state.imageHash}`);
        const kd = await kr.json();
        if (kd.success && kd.annotations) {
            knownFns = new Set(Object.keys(kd.annotations));
        }
    } catch (err) { /* 拉不到就当无已知标注 */ }

    // 收集目标：未删除 + 有文件名 + 没被 OCR 识别过（跳过已有记录）
    const targets = state.characters
        .map((c, idx) => ({ char: c, idx, fn: c.processed_filename || c.filename }))
        .filter(t => t.fn && !t.char.deleted && !knownFns.has(t.fn));

    const skipped = state.characters.length - targets.length;
    if (skipped > 0) {
        showToast(`已跳过 ${skipped} 张已识别的字符（不再重复 OCR），待识别 ${targets.length} 张`);
    }

    if (targets.length === 0) {
        showToast(skipped > 0 ? '所有字符都已识别过，无需重复 OCR' : '没有可识别的字符');
        return;
    }

    // 用 scaled 图（识别率更高）—— 如果有任何一个字符有 processed_filename，就用 scaled
    const useScaled = state.characters.some(c => c.processed_filename);
    const threshold = ocrState.threshold;

    // 重置状态 + 启动进度条
    ocrState.applied = 0;
    ocrState.highConf = 0;
    ocrState.lowConf = 0;
    ocrState.taskId = null;

    elements.ocrAnnotateBtn.disabled = true;
    elements.ocrProgress.style.display = 'flex';
    elements.ocrProgressFill.style.width = '0%';
    elements.ocrProgressText.textContent = '启动 OCR 引擎…（首次约 15s）';

    try {
        const response = await fetch('/api/ocr_start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                filenames: targets.map(t => t.fn),
                use_scaled: useScaled,
                threshold: threshold,
            })
        });
        const data = await response.json();
        if (!data.success) throw new Error(data.error);

        ocrState.taskId = data.task_id;
        showToast(`OCR 任务已启动，共 ${data.total} 张，约需 ${Math.round(data.total * 1.5 / 60)} 分钟`);

        // 开始轮询
        ocrState.pollTimer = setInterval(() => pollOcrProgress(targets, threshold), 2000);
    } catch (err) {
        showToast('OCR 启动失败: ' + err.message);
        ocrReset();
    }
}

// 轮询进度 + 增量应用结果
async function pollOcrProgress(targets, threshold) {
    if (!ocrState.taskId) return;

    try {
        const response = await fetch(`/api/ocr_progress/${ocrState.taskId}`);
        const data = await response.json();

        if (data.status === 'not_found') {
            showToast('OCR 任务丢失（可能服务器重启）');
            ocrReset();
            return;
        }
        if (data.status === 'interrupted') {
            // 服务端从磁盘恢复了部分结果（worker 线程死了但已存盘的结果还在）
            // 继续让下面循环应用 new_results，然后提示用户重跑
            showToast(`服务器中断，已恢复 ${data.done}/${data.total} 张结果（剩余可手动重跑 OCR）`);
        }

        // 更新进度条
        const percent = data.total > 0 ? (data.done / data.total * 100) : 0;
        elements.ocrProgressFill.style.width = percent.toFixed(1) + '%';
        elements.ocrProgressText.textContent = `${data.done}/${data.total} (${percent.toFixed(0)}%)`;

        // 应用增量结果
        for (const r of data.new_results) {
            applyOcrResult(r, targets, threshold);
        }
        // 每批应用完后刷新「只看待复查」按钮的计数 + 过滤状态
        updateOcrFilterButton();

        // 终态
        if (data.status === 'done') {
            clearInterval(ocrState.pollTimer);
            ocrState.pollTimer = null;
            const summary = `OCR 完成：识别 ${data.done} 张，填入 ${ocrState.applied}（高置信 ${ocrState.highConf} + 低置信 ${ocrState.lowConf}）`;
            showToast(summary);
            // 任务完成后给个提示，建议用户切换到「只看待复查」模式复查
            if (ocrState.applied > 0 && !ocrState.filterOnly) {
                setTimeout(() => showToast('💡 提示：点上方「只看待复查」可只显示 OCR 填入的卡片'), 1500);
            }
            ocrReset();
        } else if (data.status === 'error') {
            clearInterval(ocrState.pollTimer);
            ocrState.pollTimer = null;
            showToast('OCR 任务失败: ' + (data.error || '未知错误'));
            ocrReset();
        }
    } catch (err) {
        console.warn('OCR 轮询失败:', err);
        // 网络抖动：继续轮询，不立即终止
    }
}

// 把单条 OCR 结果应用到对应卡片
function applyOcrResult(result, targets, threshold) {
    // 找到对应的卡
    const target = targets.find(t => t.fn === result.filename);
    if (!target) {
        console.warn(`[OCR DEBUG] 跳过 ${result.filename}: targets 中找不到（可能 session-picker 切了）`);
        return;
    }

    const card = document.querySelector(`.char-card[data-index="${target.idx}"]`);
    if (!card) {
        console.warn(`[OCR DEBUG] 跳过 ${result.filename}: 找不到 idx=${target.idx} 的卡（DOM 里没？）`);
        return;
    }

    // 只填「用户标过」的卡（不覆盖）。但允许覆盖旧的 OCR 结果
    // 判断方法：value 非空 且 没有 .ocr-filled class → 用户手标的
    // 没 value → 空卡，可填
    // 有 .ocr-filled → 之前是 OCR 填的，可重填（重跑 OCR 会覆盖）
    const simpInput = card.querySelector('.simplified-input');
    if (simpInput.value && !card.classList.contains('ocr-filled')) {
        console.log(`[OCR DEBUG] 跳过 ${result.filename}: 已被用户标过 ('${simpInput.value}')`);
        return;
    }

    if (!result.character || result.error) {
        console.log(`[OCR DEBUG] 跳过 ${result.filename}: 识别失败/空 (engine=${result.engine}, conf=${result.confidence})`);
        return;
    }

    // 应用：填入简化字 + 触发繁简转换 + UI 标记
    simpInput.value = result.character;
    card.querySelector('.simplified-utf').textContent = getUtfCode(result.character);
    state.characters[target.idx].simplified = result.character;
    card.classList.add('ocr-filled');

    // 置信度徽章
    const isHigh = result.confidence >= threshold;
    const badge = document.createElement('div');
    badge.className = 'ocr-badge ' + (isHigh ? 'high' : 'low');
    badge.textContent = `${(result.confidence * 100).toFixed(0)}%`;
    badge.title = `OCR 置信度 ${result.confidence}（阈值 ${threshold}）`;
    card.appendChild(badge);

    // 自动转繁体
    convertSingleToTraditional(result.character, target.idx);

    ocrState.applied++;
    if (isHigh) ocrState.highConf++; else ocrState.lowConf++;
    console.log(`[OCR DEBUG] 填入 ${result.filename} → '${result.character}' (conf=${result.confidence}, ${result.engine}, idx=${target.idx})`);

    // 持久化到服务器（fire-and-forget，不阻塞 UI）
    // key = target.fn（scaled_0002.png 稳定标识），带 conf/source
    saveOcrAnnotation(target.fn, result.character, {
        conf: result.confidence,
        source: result.engine || 'ocr'
    });
}

// 清理 OCR 状态（任务完成 / 失败 / 取消时）
function ocrReset() {
    ocrState.taskId = null;
    if (ocrState.pollTimer) {
        clearInterval(ocrState.pollTimer);
        ocrState.pollTimer = null;
    }
    if (elements.ocrProgress) {
        elements.ocrProgress.style.display = 'none';
    }
    updateUI();
}

// === 搜索 / 过滤 ===
// 支持三种过滤（可组合）：
//   1. 字符：输入「中」匹配所有卡（simplified 或 traditional 含「中」字）
//   2. UTF 码：输入「U+4E2D」或「4E2D」匹配 U+4E2D 的卡
//   3. Index 范围：输入「1-50」或「42」匹配 1-50 或第 42 张
// 多条件用空格分隔（AND 关系）
//
// 例：
//   "中"            → 含「中」字的卡
//   "4E2D"          → U+4E2D
//   "1-50"          → 前 50 张
//   "中 1-50"       → 前 50 张里含「中」字的
function applySearch(query) {
    searchState.query = (query || '').trim();
    searchState.rangeStart = null;
    searchState.rangeEnd = null;
    searchState.utfPrefix = null;
    searchState.charMatch = null;
    searchState.filterActive = false;

    if (!searchState.query) {
        // 无搜索：清掉所有搜索高亮 + 隐藏样式
        applySearchFilter();
        updateSearchCount();
        return;
    }

    // 解析每个 token
    const tokens = searchState.query.split(/\s+/);
    for (const tok of tokens) {
        // index 范围: 1-50
        const rangeMatch = tok.match(/^(\d+)\s*-\s*(\d+)$/);
        if (rangeMatch) {
            searchState.rangeStart = parseInt(rangeMatch[1], 10);
            searchState.rangeEnd = parseInt(rangeMatch[2], 10);
            searchState.filterActive = true;
            continue;
        }
        // 单 index: 42
        const indexMatch = tok.match(/^\d+$/);
        if (indexMatch) {
            const n = parseInt(tok, 10);
            searchState.rangeStart = n;
            searchState.rangeEnd = n;
            searchState.filterActive = true;
            continue;
        }
        // UTF 码: U+4E2D 或 4E2D
        const utfMatch = tok.match(/^(?:U\+|u\+|u)?([0-9a-fA-F]{4,5})$/);
        if (utfMatch) {
            searchState.utfPrefix = utfMatch[1].toUpperCase();
            searchState.filterActive = true;
            continue;
        }
        // 字符：直接用
        searchState.charMatch = tok;
        searchState.filterActive = true;
    }

    applySearchFilter();
    updateSearchCount();
}

// 应用搜索过滤到所有卡
function applySearchFilter() {
    if (!elements.cardGrid) return;
    const cards = elements.cardGrid.querySelectorAll('.char-card');
    cards.forEach(card => {
        const idx = parseInt(card.dataset.index, 10);
        const char = state.characters[idx];
        if (!char) return;
        const visible = isCharMatchSearch(char, idx);
        // 注意：search 过滤用 .filter-hidden，OCR 过滤也用 .filter-hidden
        // 两者是 AND 关系——只要任一隐藏就隐藏
        if (searchState.filterActive && !visible) {
            card.classList.add('filter-hidden');
        } else {
            // 不被搜索隐藏；检查 OCR 过滤
            const isOcrFilled = card.classList.contains('ocr-filled');
            const ocrHidden = ocrState.filterOnly && !isOcrFilled;
            card.classList.toggle('filter-hidden', ocrHidden);
        }
    });
}

// 判断某字符卡是否匹配当前搜索条件
function isCharMatchSearch(char, idx) {
    if (!searchState.filterActive) return true;
    const displayIdx = idx + 1;  // 1-based

    // index 范围
    if (searchState.rangeStart != null) {
        if (displayIdx < searchState.rangeStart || displayIdx > searchState.rangeEnd) {
            return false;
        }
    }
    // UTF 码
    if (searchState.utfPrefix) {
        const code = (char.simplified || char.traditional || '').codePointAt(0);
        if (code === undefined) return false;
        const hex = code.toString(16).toUpperCase().padStart(code > 0xFFFF ? 5 : 4, '0');
        if (!hex.startsWith(searchState.utfPrefix)) return false;
    }
    // 字符
    if (searchState.charMatch) {
        const s = char.simplified || '';
        const t = char.traditional || '';
        if (!s.includes(searchState.charMatch) && !t.includes(searchState.charMatch)) {
            return false;
        }
    }
    return true;
}

// 更新搜索计数显示
function updateSearchCount() {
    if (!elements.cardSearchCount) return;
    const total = state.characters.length;
    if (!searchState.filterActive) {
        elements.cardSearchCount.textContent = `${total} 张`;
        return;
    }
    const visible = state.characters.filter((c, i) => isCharMatchSearch(c, i)).length;
    elements.cardSearchCount.textContent = `${visible}/${total}`;
}

// === 键盘快捷键 ===
// 设计：监听 document keydown，根据当前焦点分发
//   - 焦点在 input/textarea：除 Esc 外不拦截（让用户正常打字）
//   - 焦点在搜索框：/ 重新聚焦，Esc 清空
//   - 其它情况：方向键移动卡焦点，Enter 编辑，/ 搜索，? 帮助，Esc 取消
//
// 卡焦点：state.characters 的 index（不是 grid 位置）
//   移动逻辑：方向键按 grid 列数（CSS grid auto-fill 估算）跳转
document.addEventListener('keydown', (e) => {
    // 帮助 modal 打开时：Esc 关闭，其它不拦截
    if (elements.shortcutHelpModal && !elements.shortcutHelpModal.hidden) {
        if (e.key === 'Escape') {
            e.preventDefault();
            hideShortcutHelp();
        }
        return;
    }

    const tag = (e.target && e.target.tagName) || '';
    const inTextInput = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target && e.target.isContentEditable);
    const inSearchInput = e.target === elements.cardSearchInput;

    // 搜索框内：Esc 清空搜索，/ 不再二次聚焦
    if (inSearchInput) {
        if (e.key === 'Escape') {
            e.preventDefault();
            elements.cardSearchInput.value = '';
            applySearch('');
            elements.cardSearchInput.blur();
        }
        return;
    }

    // 文本输入：只拦截 Esc
    if (inTextInput) {
        return;
    }

    // 方向键：移动卡焦点
    if (e.key === 'ArrowUp' || e.key === 'ArrowDown' ||
        e.key === 'ArrowLeft' || e.key === 'ArrowRight') {
        e.preventDefault();
        moveKeyboardFocus(e.key);
        return;
    }

    // Enter：焦点卡 → 编辑第一个输入框
    if (e.key === 'Enter' && !e.shiftKey && !e.ctrlKey && !e.altKey && !e.metaKey) {
        if (keyboardState.focusedIndex >= 0) {
            e.preventDefault();
            focusCardInput(keyboardState.focusedIndex);
        }
        return;
    }

    // /：聚焦搜索框
    if (e.key === '/') {
        e.preventDefault();
        elements.cardSearchInput.focus();
        elements.cardSearchInput.select();
        return;
    }

    // ?：显示帮助（shift+/ 在大多数键盘上）
    if (e.key === '?' || (e.key === '/' && e.shiftKey)) {
        e.preventDefault();
        showShortcutHelp();
        return;
    }

    // Esc：清空搜索（如有）+ 清焦点
    if (e.key === 'Escape') {
        if (searchState.query) {
            elements.cardSearchInput.value = '';
            applySearch('');
        }
        if (keyboardState.focusedIndex >= 0) {
            clearKeyboardFocus();
        }
        return;
    }
});

// 移动键盘焦点
// grid 是 CSS auto-fill minmax(180px, 1fr)，实际列数 = container.width / 180
// 用 grid.offsetWidth 估算（去掉 padding 16px*2）
function moveKeyboardFocus(direction) {
    if (state.characters.length === 0) return;
    const cols = getGridColumnCount();
    const cur = keyboardState.focusedIndex;
    let next = cur;
    if (cur < 0) {
        // 还没焦点：定位到第一个可见卡
        next = firstVisibleIndex();
    } else {
        if (direction === 'ArrowLeft')  next = Math.max(0, cur - 1);
        if (direction === 'ArrowRight') next = Math.min(state.characters.length - 1, cur + 1);
        if (direction === 'ArrowUp')    next = Math.max(0, cur - cols);
        if (direction === 'ArrowDown')  next = Math.min(state.characters.length - 1, cur + cols);
    }
    setKeyboardFocus(next);
}

// 估算 grid 列数
function getGridColumnCount() {
    if (!elements.cardGrid) return 5;
    const w = elements.cardGrid.clientWidth;
    // minmax(180px, 1fr) + gap 15px
    return Math.max(1, Math.floor((w + 15) / (180 + 15)));
}

// 第一个未被隐藏的卡 index
function firstVisibleIndex() {
    for (let i = 0; i < state.characters.length; i++) {
        const card = elements.cardGrid.querySelector(`.char-card[data-index="${i}"]`);
        if (card && !card.classList.contains('filter-hidden')) return i;
    }
    return 0;
}

// 下一个可见卡（用于焦点移动后跳过隐藏卡）
function nextVisibleIndex(target) {
    if (target < 0) target = 0;
    // 先尝试 target，再向后扫
    for (let i = target; i < state.characters.length; i++) {
        const card = elements.cardGrid.querySelector(`.char-card[data-index="${i}"]`);
        if (card && !card.classList.contains('filter-hidden')) return i;
    }
    // 向前扫
    for (let i = target - 1; i >= 0; i--) {
        const card = elements.cardGrid.querySelector(`.char-card[data-index="${i}"]`);
        if (card && !card.classList.contains('filter-hidden')) return i;
    }
    return -1;
}

// 设置键盘焦点
function setKeyboardFocus(idx) {
    // 跳过隐藏的卡
    const visible = nextVisibleIndex(idx);
    if (visible < 0) return;
    clearKeyboardFocus();
    keyboardState.focusedIndex = visible;
    const card = elements.cardGrid.querySelector(`.char-card[data-index="${visible}"]`);
    if (card) {
        card.classList.add('keyboard-focused');
        // 滚到可见
        card.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'nearest' });
    }
}

// 清除键盘焦点
function clearKeyboardFocus() {
    if (keyboardState.focusedIndex < 0) return;
    const card = elements.cardGrid.querySelector(`.char-card[data-index="${keyboardState.focusedIndex}"]`);
    if (card) card.classList.remove('keyboard-focused');
    keyboardState.focusedIndex = -1;
}

// 焦点卡 → 聚焦第一个 input
function focusCardInput(idx) {
    const card = elements.cardGrid.querySelector(`.char-card[data-index="${idx}"]`);
    if (!card) return;
    const input = card.querySelector('.simplified-input');
    if (input) {
        input.focus();
        input.select();
    }
}

// 卡片被点击时同步键盘焦点（让方向键从点击的卡开始）
// 通过事件代理加在 grid 上
if (elements.cardGrid) {
    elements.cardGrid.addEventListener('click', (e) => {
        const card = e.target.closest('.char-card');
        if (!card) return;
        const idx = parseInt(card.dataset.index, 10);
        if (!isNaN(idx)) {
            // 不调 setKeyboardFocus（避免 click 抢方向键焦点），但记录
            keyboardState.focusedIndex = idx;
        }
    });
}

// 显示 / 隐藏快捷键帮助
function showShortcutHelp() {
    if (elements.shortcutHelpModal) elements.shortcutHelpModal.hidden = false;
}
function hideShortcutHelp() {
    if (elements.shortcutHelpModal) elements.shortcutHelpModal.hidden = true;
}
