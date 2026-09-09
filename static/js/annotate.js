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
    clearBtn: document.getElementById('clearBtn')
};

// OCR 状态
let ocrState = {
    taskId: null,           // 当前后台 task_id
    pollTimer: null,        // 轮询 timer
    applied: 0,             // 已填入卡片数
    highConf: 0,            // 高置信填入数
    lowConf: 0,             // 低置信填入数
    threshold: 0.5,         // 当前阈值
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

    } catch (error) {
        console.error('加载字符数据失败:', error);
        showEmptyState();
    }
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
    const imageUrl = char.processed_url || char.image_url || `/output/${state.imageHash}/${char.filename}`;
    const filename = char.filename || char.processed_filename || 'unknown';

    console.log(`创建卡片 ${index}: filename=${filename}, imageUrl=${imageUrl}`);

    card.innerHTML = `
        <div class="card-image">
            <span class="card-filename">${filename}</span>
            <img src="${imageUrl}" alt="字符 ${index + 1}">
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

    simpInput.addEventListener('input', (e) => {
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

        if (state.characters[index]) {
            state.characters[index].simplified = '';
            state.characters[index].traditional = '';
        }
    });

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

// 导出白底黑字
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

    showLoading('正在导出...');

    try {
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

        if (data.success) {
            state.exportedDir = data.output_dir;
            elements.openDirBtn.disabled = false;
            showToast(`成功导出 ${data.count} 个字符到 ${data.output_dir}`);

            // 清理中间过程文件
            try {
                const cleanResp = await fetch('/api/cleanup_intermediate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ hash: state.imageHash, keep_dir: data.output_dir })
                });
                const cleanData = await cleanResp.json();
                if (cleanData.success) {
                    console.log('中间文件清理完成:', cleanData.message);
                }
            } catch (e) {
                console.warn('清理中间文件失败:', e);
            }
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('导出失败: ' + error.message);
    }

    hideLoading();
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

    // 收集目标：未删除 + 有文件名
    const targets = state.characters
        .map((c, idx) => ({ char: c, idx, fn: c.processed_filename || c.filename }))
        .filter(t => t.fn && !t.char.deleted);

    if (targets.length === 0) {
        showToast('没有可识别的字符');
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

        // 更新进度条
        const percent = data.total > 0 ? (data.done / data.total * 100) : 0;
        elements.ocrProgressFill.style.width = percent.toFixed(1) + '%';
        elements.ocrProgressText.textContent = `${data.done}/${data.total} (${percent.toFixed(0)}%)`;

        // 应用增量结果
        for (const r of data.new_results) {
            applyOcrResult(r, targets, threshold);
        }

        // 终态
        if (data.status === 'done') {
            clearInterval(ocrState.pollTimer);
            ocrState.pollTimer = null;
            const summary = `OCR 完成：识别 ${data.done} 张，填入 ${ocrState.applied}（高置信 ${ocrState.highConf} + 低置信 ${ocrState.lowConf}）`;
            showToast(summary);
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
    if (!target) return;

    const card = document.querySelector(`.char-card[data-index="${target.idx}"]`);
    if (!card) return;

    // 只填「简化字输入框为空」的（不覆盖用户已标）
    const simpInput = card.querySelector('.simplified-input');
    if (simpInput.value) return;  // 已有标注，跳过

    if (!result.character || result.error) {
        // OCR 没识别出来，不填
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
