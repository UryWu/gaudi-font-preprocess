/* 切割调整页面逻辑 */

// 状态管理
const state = {
    imageHash: null,
    characters: [],
    selectedIndices: [],
    currentAdjustIndex: null
};

// 调整 modal 是否打开（控制 Ctrl 画笔快捷键的可用性）
let adjustModalOpen = false;

// DOM 元素
const elements = {
    loadingState: document.getElementById('loadingState'),
    emptyState: document.getElementById('emptyState'),
    charGrid: document.getElementById('charGrid'),
    totalCount: document.getElementById('totalCount'),
    adjustCount: document.getElementById('adjustCount'),
    adjustInfo: document.getElementById('adjustInfo'),
    saveBtn: document.getElementById('saveBtn'),
    adjustBtn: document.getElementById('adjustBtn'),
    deleteBtn: document.getElementById('deleteSelectedBtn'),
    clearEmptyBtn: document.getElementById('clearEmptyBtn'),
    clearAllBtn: document.getElementById('clearAllBtn')
};

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    loadCutResults();
    setupEventListeners();
});

function setupEventListeners() {
    elements.saveBtn.addEventListener('click', saveAdjustments);
    elements.adjustBtn.addEventListener('click', openAdjustModal);
    if (elements.deleteBtn) elements.deleteBtn.addEventListener('click', deleteSelectedCharacters);
    if (elements.clearEmptyBtn) elements.clearEmptyBtn.addEventListener('click', handleClearEmpty);
    if (elements.clearAllBtn) elements.clearAllBtn.addEventListener('click', handleClearAll);

    // 画笔快捷键：仅在 adjust modal 打开时，按住 Ctrl 进入画笔模式
    // 颜色 / 大小直接用 modal 里的 brushColor + brushSizeInput
    document.addEventListener('keydown', (e) => {
        if (!adjustModalOpen) return;
        // 纯 Ctrl（无 Shift/Alt/Meta）才触发
        if (e.key === 'Control' && !e.shiftKey && !e.altKey && !e.metaKey) {
            if (!canvasState.brushMode) {
                canvasState.brushMode = true;
                updateBrushToggleButton();
            }
        }
        // Escape 强制退出画笔模式
        if (e.key === 'Escape' && canvasState.brushMode) {
            canvasState.brushMode = false;
            canvasState.isPainting = false;
            updateBrushToggleButton();
        }
    });
    document.addEventListener('keyup', (e) => {
        if (!adjustModalOpen) return;
        // Ctrl 松开 → 自动退出画笔模式
        if (e.key === 'Control' && canvasState.brushMode) {
            canvasState.brushMode = false;
            canvasState.isPainting = false;
            updateBrushToggleButton();
            // 强制重绘以清除残留的画笔光标圆圈（mousemove 在 keyup 时不会触发）
            redrawCanvas();
        }
    });
    // 窗口失焦时也退出（比如 Alt+Tab 切走）
    window.addEventListener('blur', () => {
        if (canvasState.brushMode) {
            canvasState.brushMode = false;
            canvasState.isPainting = false;
            updateBrushToggleButton();
        }
    });
}

// 加载切割结果
async function loadCutResults() {
    // 从 localStorage 获取当前图片哈希
    state.imageHash = localStorage.getItem('currentImageHash');

    if (!state.imageHash) {
        showEmptyState();
        return;
    }

    showLoadingState();

    try {
        const response = await fetch(`/api/get_cut_results/${state.imageHash}`);
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error || '加载切割结果失败');
        }

        if (!data.characters || data.characters.length === 0) {
            showEmptyState();
            return;
        }

        state.characters = data.characters;
        renderCharacterGrid();
        updateUI();

    } catch (error) {
        console.error('加载切割结果失败:', error);
        showToast('加载切割结果失败: ' + error.message);
        showEmptyState();
    }
}

function showLoadingState() {
    elements.loadingState.style.display = 'flex';
    elements.emptyState.style.display = 'none';
    elements.charGrid.style.display = 'none';
}

function showEmptyState() {
    elements.loadingState.style.display = 'none';
    elements.emptyState.style.display = 'flex';
    elements.charGrid.style.display = 'none';
    elements.totalCount.textContent = '0';
}

function showGridState() {
    elements.loadingState.style.display = 'none';
    elements.emptyState.style.display = 'none';
    elements.charGrid.style.display = 'grid';
}

// 渲染字符网格
function renderCharacterGrid() {
    showGridState();
    elements.charGrid.innerHTML = '';

    // 按传统顺序渲染图片，编号就是索引+1
    const traditionalOrder = getTraditionalOrder(state.characters);
    traditionalOrder.forEach((char, index) => {
        const card = createCharCard(char, index);
        elements.charGrid.appendChild(card);
    });
}

// 传统书法顺序：从上到下，从右到左
function getTraditionalOrder(characters) {
    // strip_index 大的是右边的列，应该排在前面（先读）
    // 同一列内，char_index 从小到大（从上到下）
    return [...characters].sort((a, b) => {
        if (a.strip_index !== b.strip_index) {
            return b.strip_index - a.strip_index;  // strip_index大的（右边列）排前面
        }
        return a.char_index - b.char_index;  // 同一列，char_index小的（上面）排前面
    });
}

// 创建字符卡片
function createCharCard(char, displayIndex) {
    const card = document.createElement('div');
    card.className = 'char-card' + (char.is_empty ? ' empty' : '');
    card.dataset.index = displayIndex;
    card.dataset.charId = `${char.strip_index}_${char.char_index}`;

    // 编号（从1开始）
    const number = document.createElement('div');
    number.className = 'char-number';
    number.textContent = displayIndex + 1;

    // 图片
    const img = document.createElement('img');
    img.className = 'char-image';
    // 兼容老数据：image_url 缺失时根据 hash + filename 构造
    // 追加时间戳防止浏览器缓存重剪后的同名 PNG
    const baseUrl = char.image_url || `/output/${state.imageHash}/${char.filename}`;
    img.src = `${baseUrl}?v=${char.cache_version || 0}`;
    img.title = char.filename || '';
    img.alt = `字符 ${displayIndex + 1}`;

    // 状态标签
    const hasAdjust = (char.adjust_top || 0) > 0 || (char.adjust_bottom || 0) > 0 ||
                      (char.adjust_left || 0) > 0 || (char.adjust_right || 0) > 0;

    if (char.needs_adjust) {
        const status = document.createElement('div');
        status.className = 'char-status needs-adjust';
        status.textContent = '需调整';
        card.appendChild(status);
    } else if (hasAdjust) {
        const status = document.createElement('div');
        status.className = 'char-status adjusted';
        status.textContent = '已调整';
        card.appendChild(status);
    } else if (char.is_empty) {
        const status = document.createElement('div');
        status.className = 'char-status empty-slice';
        status.textContent = '空白';
        card.appendChild(status);
    }

    card.appendChild(number);
    card.appendChild(img);

    // 左键点击 - 选择/取消选择
    card.addEventListener('click', () => toggleSelectCard(card, displayIndex));

    // 双击 - 直接打开该字符的调整 modal（无需先选中）
    card.addEventListener('dblclick', () => {
        state.currentAdjustIndex = displayIndex;
        // 临时把 selectedIndices 设成 [this] 让 applyAdjust/saveAndNext 工作正常
        if (!state.selectedIndices.includes(displayIndex)) {
            state.selectedIndices = [displayIndex];
        }
        showAdjustModal(displayIndex);
    });

    // 右键 - 自定义菜单（删除 / 在资源管理器中打开）
    card.addEventListener('contextmenu', (e) => {
        e.preventDefault();
        showCharContextMenu(e.clientX, e.clientY, char, displayIndex);
    });

    return card;
}

// 删除字符（无 confirm，由右键菜单直接调用；同时删本地文件）
async function deleteCharacter(char, displayIndex) {
    try {
        const r = await fetch('/api/delete_characters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                filenames: [char.filename]
            })
        });
        const data = await r.json();
        if (!data.success) throw new Error(data.error);
    } catch (err) {
        showToast('删除失败: ' + err.message);
        return;
    }
    // 客户端：移除并重绘
    const index = state.characters.indexOf(char);
    if (index > -1) {
        state.characters.splice(index, 1);
    }
    renderCharacterGrid();
    updateUI();
    showToast(`已删除 ${char.filename || '第' + (displayIndex + 1) + '号字符'}`);
}

// 只刷新单个字符卡片（图 + 状态标签），不重渲其他 858 张
function reloadOneCharCard(char) {
    if (!char || !char.filename) return;
    // 找 grid 中对应的卡片
    const card = document.querySelector(`.char-card[data-char-id="${char.strip_index}_${char.char_index}"]`);
    if (!card) return;
    // 找图标签，更新 src（用最新 cache_version 重新加载）
    const img = card.querySelector('img.char-image');
    if (img) {
        const baseUrl = char.image_url || `/output/${state.imageHash}/${char.filename}`;
        img.src = `${baseUrl}?v=${char.cache_version || 0}&t=${Date.now()}`;
    }
    // 状态标签：把旧的「已调整」/「需调整」badge 移除（已调整完）
    const oldBadge = card.querySelector('.char-status');
    if (oldBadge) oldBadge.remove();
    // 重新评估是否需要 badge：adjust_* 全为 0 时已调整完
    const hasAdjust = (char.adjust_top || 0) > 0 || (char.adjust_bottom || 0) > 0 ||
                      (char.adjust_left || 0) > 0 || (char.adjust_right || 0) > 0;
    if (hasAdjust) {
        const status = document.createElement('div');
        status.className = 'char-status adjusted';
        status.textContent = '已调整';
        card.appendChild(status);
    } else if (char.is_empty) {
        const status = document.createElement('div');
        status.className = 'char-status empty-slice';
        status.textContent = '空白';
        card.appendChild(status);
    }
}

// 显示字符图片的右键菜单
function showCharContextMenu(x, y, char, displayIndex) {
    hideCharContextMenu();

    const menu = document.createElement('div');
    menu.className = 'char-context-menu';
    menu.id = 'charContextMenu';
    menu.innerHTML = `
        <div class="ctx-item danger" data-action="delete">🗑 删除</div>
        <div class="ctx-item" data-action="open-folder">📁 在文件管理器中打开</div>
    `;
    // 定位（防止溢出视口）
    menu.style.left = x + 'px';
    menu.style.top = y + 'px';
    document.body.appendChild(menu);

    // 调整位置避免溢出
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth) {
        menu.style.left = (x - rect.width) + 'px';
    }
    if (rect.bottom > window.innerHeight) {
        menu.style.top = (y - rect.height) + 'px';
    }

    // 点击菜单项
    menu.addEventListener('click', async (e) => {
        const action = e.target.dataset.action;
        hideCharContextMenu();
        if (action === 'delete') {
            deleteCharacter(char, displayIndex);
        } else if (action === 'open-folder') {
            try {
                const r = await fetch('/api/open_path', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: char.filename, hash: state.imageHash })
                });
                const data = await r.json();
                if (!data.success) throw new Error(data.error);
                showToast(`已在资源管理器中打开 ${char.filename}`);
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

// 全局点击其他地方关闭菜单
document.addEventListener('click', hideCharContextMenu, true);
document.addEventListener('scroll', hideCharContextMenu, true);

// 批量删除选中的字符
async function deleteSelectedCharacters() {
    if (state.selectedIndices.length === 0) return;

    const chars = state.characters;
    const deleteSet = new Set(state.selectedIndices);
    const toDelete = chars.filter((_, i) => deleteSet.has(i));

    if (!confirm(`确定删除 ${toDelete.length} 个字符？此操作不可撤销。`)) return;

    showLoading('正在删除...');
    try {
        const r = await fetch('/api/delete_characters', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                filenames: toDelete.map(c => c.filename)
            })
        });
        const data = await r.json();
        if (!data.success) throw new Error(data.error);

        // 客户端：移除已选字符
        state.characters = chars.filter((_, i) => !deleteSet.has(i));
        state.selectedIndices = [];
        hideLoading();
        renderCharacterGrid();
        updateUI();
        updateAdjustButton();
        showToast(`已删除 ${toDelete.length} 个字符`);
    } catch (error) {
        hideLoading();
        showToast('删除失败: ' + error.message);
    }
}

// 选择/取消选择卡片
function toggleSelectCard(card, index) {
    card.classList.toggle('selected');

    const idx = state.selectedIndices.indexOf(index);
    if (idx > -1) {
        state.selectedIndices.splice(idx, 1);
    } else {
        state.selectedIndices.push(index);
    }

    updateAdjustButton();
}

// 更新调整按钮状态
function updateAdjustButton() {
    const hasSelection = state.selectedIndices.length > 0;
    elements.adjustBtn.disabled = !hasSelection;
    if (elements.deleteBtn) elements.deleteBtn.disabled = !hasSelection;
}

// 更新UI
function updateUI() {
    const total = state.characters.length;
    const emptyCount = state.characters.filter(c => c.is_empty).length;
    const validCount = total - emptyCount;
    const needsAdjust = state.characters.filter(c => c.needs_adjust);

    // 总数 + 空白数（如 "859（空白 201）"）
    if (emptyCount > 0) {
        elements.totalCount.textContent = `${validCount}（空白 ${emptyCount}）`;
    } else {
        elements.totalCount.textContent = `${validCount}`;
    }

    if (needsAdjust.length > 0) {
        elements.adjustCount.textContent = needsAdjust.length;
        elements.adjustInfo.style.display = 'block';
    } else {
        elements.adjustInfo.style.display = 'none';
    }
}

// Canvas 调整相关状态
const canvasState = {
    char: null,
    img: null,  // 保存加载的图片
    scale: 1,
    isDragging: false,
    dragEdge: null,  // 'top', 'bottom', 'left', 'right'
    startX: 0,
    startY: 0,
    originalTop: 0,
    originalBottom: 0,
    originalLeft: 0,
    originalRight: 0,
    // 画笔相关
    brushMode: false,         // 是否处于画笔模式（与拖拽裁剪互斥）
    cutMode: false,           // 是否处于切割模式（需点击按钮进入）
    lastCursorPos: null,      // 画笔光标的最后位置（重绘时重新画）
    isPainting: false,        // 当前是否正在画
    overlayCanvas: null,      // 离屏 canvas，记录画笔笔触
    overlayCtx: null,
    lastBrushPos: null        // 上一个画笔位置（用于画线段）
};

// 打开调整弹窗
function openAdjustModal() {
    if (state.selectedIndices.length === 0) return;

    // 从第一个选中的开始调整
    state.currentAdjustIndex = state.selectedIndices[0];
    showAdjustModal(state.currentAdjustIndex);
}

// 显示调整弹窗
function showAdjustModal(displayIndex) {
    const orderedChars = getTraditionalOrder(state.characters);
    const char = orderedChars[displayIndex];

    if (!char) return;

    // 标记 modal 开启（Ctrl 快捷键需要此状态）
    adjustModalOpen = true;

    // 创建弹窗
    let modal = document.getElementById('adjustModal');
    if (!modal) {
        modal = createAdjustModal();
        document.body.appendChild(modal);
        setupBrushControls();
    }

    // 每次打开都重置画笔/切割状态（防止上一次残留）
    canvasState.brushMode = false;
    canvasState.cutMode = false;
    canvasState.isPainting = false;
    canvasState.lastBrushPos = null;
    updateBrushToggleButton();
    updateCutToggleButton();

    // 更新弹窗内容
    document.getElementById('modalCharIndex').textContent = displayIndex + 1;
    document.getElementById('adjustTop').value = char.adjust_top || 0;
    document.getElementById('adjustBottom').value = char.adjust_bottom || 0;
    document.getElementById('adjustLeft').value = char.adjust_left || 0;
    document.getElementById('adjustRight').value = char.adjust_right || 0;

    // 加载图片到 canvas
    loadCharToCanvas(char);

    modal.classList.remove('hidden');
}

// 创建调整弹窗
function createAdjustModal() {
    const modal = document.createElement('div');
    modal.id = 'adjustModal';
    modal.className = 'adjust-modal';
    modal.innerHTML = `
        <div class="modal-content">
            <button class="modal-close-x" onclick="closeAdjustModal()" title="关闭">×</button>
            <div class="preview-area">
                <canvas id="adjustCanvas"></canvas>
            </div>
            <div class="modal-sidebar">
                <div class="modal-header">
                    <h3>调整字符 - <span id="modalCharIndex">1</span></h3>
                </div>
                <div class="adjust-controls">
                    <h4>切割范围</h4>
                    <div class="control-row">
                        <label>上边距</label>
                        <input type="number" id="adjustTop" value="0" min="0">
                    </div>
                    <div class="control-row">
                        <label>下边距</label>
                        <input type="number" id="adjustBottom" value="0" min="0">
                    </div>
                    <div class="control-row">
                        <label>左边距</label>
                        <input type="number" id="adjustLeft" value="0" min="0">
                    </div>
                    <div class="control-row">
                        <label>右边距</label>
                        <input type="number" id="adjustRight" value="0" min="0">
                    </div>
                </div>
                <div class="adjust-controls">
                    <h4 style="margin-top: 12px; font-size: 13px; color: #555;">模式</h4>
                    <button type="button" id="cutToggleBtn" onclick="toggleCutMode()"
                        style="width: 100%; margin-bottom: 8px; padding: 6px; background: #f0f0f0; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 13px;">
                        ✂ 切割模式：关
                    </button>
                    <button type="button" id="brushToggleBtn" onclick="toggleBrushMode()"
                        style="width: 100%; margin-bottom: 8px; padding: 6px; background: #f0f0f0; border: 1px solid #ccc; border-radius: 4px; cursor: pointer; font-size: 13px;">
                        ✏ 画笔：关
                    </button>
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                        <label style="font-size: 12px;">颜色:</label>
                        <input type="color" id="brushColor" value="#000000" style="width: 50px; height: 28px; border: 1px solid #ccc; border-radius: 4px;">
                    </div>
                    <div style="display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px;">
                        <button type="button" class="brush-preset" data-color="#000000" style="background: #000000; color: #fff;">黑</button>
                        <button type="button" class="brush-preset" data-color="#ffffff" style="background: #ffffff; color: #000; border: 1px solid #ccc;">白</button>
                        <button type="button" class="brush-preset" data-color="#ff0000" style="background: #ff0000; color: #fff;">红</button>
                        <button type="button" class="brush-preset" data-color="#00ff00" style="background: #00ff00; color: #000;">绿</button>
                        <button type="button" class="brush-preset" data-color="#0000ff" style="background: #0000ff; color: #fff;">蓝</button>
                        <button type="button" class="brush-preset" data-color="#ffff00" style="background: #ffff00; color: #000;">黄</button>
                    </div>
                    <div style="display: flex; align-items: center; gap: 8px; margin-bottom: 8px;">
                        <label style="font-size: 12px; min-width: 40px;">大小:</label>
                        <input type="range" id="brushSizeSlider" min="1" max="100" value="5" style="flex: 1;">
                        <input type="number" id="brushSizeInput" min="1" max="100" value="5" style="width: 60px;">
                    </div>
                    <button type="button" onclick="clearBrushOverlay()"
                        style="width: 100%; padding: 6px; background: #fff5f5; border: 1px solid #e8c5c5; color: #c0392b; border-radius: 4px; cursor: pointer; font-size: 12px;">
                        清除画笔
                    </button>
                </div>
                <div class="adjust-controls">
                    <h4>操作</h4>
                    <button class="btn btn-secondary" style="width: 100%; margin-bottom: 8px;" onclick="resetAdjust()">重置</button>
                    <button class="btn btn-success" style="width: 100%; margin-bottom: 8px;" onclick="applyAdjust()">应用</button>
                    <button class="btn btn-success" style="width: 100%; margin-bottom: 8px;" onclick="confirmAdjust()">确定</button>
                    <button class="btn btn-success" style="width: 100%;" onclick="saveAndNext()">保存并下一个</button>
                </div>
            </div>
        </div>
    `;

    return modal;
}

// 加载字符到 Canvas
function loadCharToCanvas(char) {
    const canvas = document.getElementById('adjustCanvas');

    // 保存当前字符到状态
    canvasState.char = char;

    // 显示加载指示（图片可能几百 KB，Flask 渲染需要时间）
    const wrap = canvas.parentElement;
    let loadingEl = wrap.querySelector('.canvas-loading');
    if (!loadingEl) {
        loadingEl = document.createElement('div');
        loadingEl.className = 'canvas-loading';
        loadingEl.textContent = '加载中…';
        loadingEl.style.cssText = 'position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#999;font-size:14px;background:rgba(255,255,255,0.7);z-index:5;';
        wrap.style.position = 'relative';
        wrap.appendChild(loadingEl);
    }
    loadingEl.style.display = 'flex';

    // 稳定 cache key：文件名不变时复用浏览器缓存；重剪/画笔后递增版本强制刷新
    const cacheKey = `${char.image_url}?v=${char.cache_version || 0}`;
    const img = new Image();
    img.onload = () => {
        // 保存图片对象
        canvasState.img = img;

        // 设置 canvas 大小（缩小到 440 让 4 条红色边界线在 modal 内完整可见）
        const maxSize = 440;
        canvasState.scale = Math.min(maxSize / img.width, maxSize / img.height);
        canvas.width = img.width * canvasState.scale;
        canvas.height = img.height * canvasState.scale;

        // 创建画笔覆盖层 canvas（与主 canvas 同尺寸，用于记录笔触）
        canvasState.overlayCanvas = document.createElement('canvas');
        canvasState.overlayCanvas.width = canvas.width;
        canvasState.overlayCanvas.height = canvas.height;
        canvasState.overlayCtx = canvasState.overlayCanvas.getContext('2d');
        canvasState.lastBrushPos = null;

        // 绘制图片和边框
        redrawCanvas();
        loadingEl.style.display = 'none';

        // 设置canvas事件监听
        setupCanvasEvents(canvas);
    };
    img.onerror = () => {
        loadingEl.textContent = '加载失败';
    };
    img.src = cacheKey;
}

// 重绘Canvas
function redrawCanvas() {
    const canvas = document.getElementById('adjustCanvas');
    const ctx = canvas.getContext('2d');
    const char = canvasState.char;
    const img = canvasState.img;

    if (!char || !img) return;

    // 清空并重绘图片
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height);

    // 切割模式下才显示裁剪线（按按钮进入，避免与画笔冲突）
    if (canvasState.cutMode) {
        drawAdjustBox(ctx, canvas.width, canvas.height, char, canvasState.scale);
    }

    // 笔触覆盖层始终绘制（Ctrl 松开时笔触不消失，避免被原图覆盖的"假象"）
    if (canvasState.overlayCanvas) {
        ctx.drawImage(canvasState.overlayCanvas, 0, 0);
    }

    // 画笔模式下再画红色光标圈（不烘焙进图）
    if (canvasState.brushMode && canvasState.lastCursorPos) {
        drawBrushCursor(canvasState.lastCursorPos.x, canvasState.lastCursorPos.y);
    }
}

// 绘制调整框（始终显示）
function drawAdjustBox(ctx, canvasWidth, canvasHeight, char, scale) {
    const top = (char.adjust_top || 0) * scale;
    const bottom = (char.adjust_bottom || 0) * scale;
    const left = (char.adjust_left || 0) * scale;
    const right = (char.adjust_right || 0) * scale;

    // 绘制裁剪区域外的遮罩
    ctx.fillStyle = 'rgba(0, 0, 0, 0.5)';

    // 上边遮罩
    if (top > 0) {
        ctx.fillRect(0, 0, canvasWidth, top);
    }
    // 下边遮罩
    if (bottom > 0) {
        ctx.fillRect(0, canvasHeight - bottom, canvasWidth, bottom);
    }
    // 左边遮罩
    if (left > 0) {
        ctx.fillRect(0, top, left, canvasHeight - top - bottom);
    }
    // 右边遮罩
    if (right > 0) {
        ctx.fillRect(canvasWidth - right, top, right, canvasHeight - top - bottom);
    }

    // 计算边框位置
    const x = left;
    const y = top;
    const w = canvasWidth - left - right;
    const h = canvasHeight - top - bottom;

    // 绘制裁剪边框（绿色更明显）
    ctx.strokeStyle = '#2ecc71';
    ctx.lineWidth = 3;
    ctx.setLineDash([]);
    ctx.strokeRect(x, y, w, h);

    // 四条可拖动的边已关闭（用户要求不显示）
    // 如需恢复，取消注释以下代码
    // ctx.fillStyle = '#e74c3c';
    // const edgeThickness = 6;
    // ctx.fillRect(x, Math.max(0, y - edgeThickness/2), w, edgeThickness);
    // ctx.fillRect(x, Math.min(canvasHeight - edgeThickness, y + h - edgeThickness/2), w, edgeThickness);
    // ctx.fillRect(Math.max(0, x - edgeThickness/2), y, edgeThickness, h);
    // ctx.fillRect(Math.min(canvasWidth - edgeThickness, x + w - edgeThickness/2), y, edgeThickness, h);
    //
    // const handleSize = 14;
    // ctx.fillStyle = '#e74c3c';
    // drawCornerHandle(ctx, x, y, handleSize, 'top-left');
    // drawCornerHandle(ctx, x + w, y, handleSize, 'top-right');
    // drawCornerHandle(ctx, x, y + h, handleSize, 'bottom-left');
    // drawCornerHandle(ctx, x + w, y + h, handleSize, 'bottom-right');
}

// 绘制角落手柄
function drawCornerHandle(ctx, cx, cy, size, position) {
    ctx.beginPath();
    ctx.arc(cx, cy, size/2, 0, Math.PI * 2);
    ctx.fill();
}

// 设置Canvas事件
function setupCanvasEvents(canvas) {
    // 移除旧的事件监听器
    canvas.onmousedown = null;
    canvas.onmousemove = null;
    canvas.onmouseup = null;
    canvas.onmouseleave = null;

    canvas.onmousedown = handleMouseDown;
    canvas.onmousemove = handleMouseMove;
    canvas.onmouseup = handleMouseUp;
    canvas.onmouseleave = (e) => {
        handleMouseUp(e);
        // 鼠标离开：清除画笔光标（避免红圈残留）
        if (canvasState.brushMode) {
            redrawCanvas();
        }
    };
}

// 将鼠标坐标转换为canvas内部坐标
function getCanvasCoords(e) {
    const canvas = e.target;
    const rect = canvas.getBoundingClientRect();

    // 计算缩放比例（CSS尺寸 vs Canvas内部尺寸）
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    // 转换坐标
    const x = (e.clientX - rect.left) * scaleX;
    const y = (e.clientY - rect.top) * scaleY;

    return { x, y, rect };
}

// 检测鼠标在哪个边上（上下左右四条边）
function getEdgeAtPosition(x, y) {
    const char = canvasState.char;
    const scale = canvasState.scale;
    const threshold = 30; // 增大检测阈值

    const top = (char.adjust_top || 0) * scale;
    const bottom = (char.adjust_bottom || 0) * scale;
    const left = (char.adjust_left || 0) * scale;
    const right = (char.adjust_right || 0) * scale;

    const canvas = document.getElementById('adjustCanvas');
    const boxLeft = left;
    const boxTop = top;
    const boxRight = canvas.width - right;
    const boxBottom = canvas.height - bottom;

    // 上边：y 在 boxTop 附近（带 threshold），x 在 boxLeft~boxRight 之间
    if (y >= boxTop - threshold && y <= boxTop + threshold &&
        x >= boxLeft && x <= boxRight) {
        return 'top';
    }
    // 下边：y 在 boxBottom 附近，x 在 boxLeft~boxRight 之间
    if (y >= boxBottom - threshold && y <= boxBottom + threshold &&
        x >= boxLeft && x <= boxRight) {
        return 'bottom';
    }
    // 左边：x 在 boxLeft 附近，y 在 boxTop~boxBottom 之间
    if (x >= boxLeft - threshold && x <= boxLeft + threshold &&
        y >= boxTop && y <= boxBottom) {
        return 'left';
    }
    // 右边：x 在 boxRight 附近，y 在 boxTop~boxBottom 之间
    if (x >= boxRight - threshold && x <= boxRight + threshold &&
        y >= boxTop && y <= boxBottom) {
        return 'right';
    }

    return null;
}

// 鼠标按下
function handleMouseDown(e) {
    const { x, y } = getCanvasCoords(e);

    // 画笔模式下：直接进入绘画状态，不响应裁剪边
    if (canvasState.brushMode) {
        canvasState.isPainting = true;
        paintAt(x, y);
        return;
    }

    // 切割模式：拖动裁剪边调整 trim
    if (canvasState.cutMode) {
        const edge = getEdgeAtPosition(x, y);
        if (edge) {
            canvasState.isDragging = true;
            canvasState.dragEdge = edge;
            canvasState.startX = x;
            canvasState.startY = y;
            canvasState.originalTop = canvasState.char.adjust_top || 0;
            canvasState.originalBottom = canvasState.char.adjust_bottom || 0;
            canvasState.originalLeft = canvasState.char.adjust_left || 0;
            canvasState.originalRight = canvasState.char.adjust_right || 0;
        }
    }
}

// 鼠标移动
function handleMouseMove(e) {
    const { x, y, rect } = getCanvasCoords(e);
    const canvas = e.target;

    // 画笔模式优先
    if (canvasState.isPainting && canvasState.brushMode) {
        paintAt(x, y);
        return;
    }

    if (canvasState.isDragging) {
        const dx = x - canvasState.startX;
        const dy = y - canvasState.startY;
        const scale = canvasState.scale;

        // 根据拖动的边更新调整值
        switch (canvasState.dragEdge) {
            case 'top':
                canvasState.char.adjust_top = Math.max(0, canvasState.originalTop + dy / scale);
                document.getElementById('adjustTop').value = Math.round(canvasState.char.adjust_top);
                break;
            case 'bottom':
                canvasState.char.adjust_bottom = Math.max(0, canvasState.originalBottom - dy / scale);
                document.getElementById('adjustBottom').value = Math.round(canvasState.char.adjust_bottom);
                break;
            case 'left':
                canvasState.char.adjust_left = Math.max(0, canvasState.originalLeft + dx / scale);
                document.getElementById('adjustLeft').value = Math.round(canvasState.char.adjust_left);
                break;
            case 'right':
                canvasState.char.adjust_right = Math.max(0, canvasState.originalRight - dx / scale);
                document.getElementById('adjustRight').value = Math.round(canvasState.char.adjust_right);
                break;
        }

        // 重绘canvas
        redrawCanvas();
    } else {
        // 更新光标样式（仅在裁剪模式下有意义）
        if (canvasState.brushMode) {
            // 画笔模式：隐藏系统光标，绘制自定义圆形指示
            canvas.style.cursor = 'none';
            redrawCanvas();
            drawBrushCursor(x, y);
            canvasState.lastCursorPos = { x, y };
            return;
        }
        // 切割模式：边缘可拖动时才显示 resize 光标
        if (canvasState.cutMode) {
            const edge = getEdgeAtPosition(x, y);
            if (edge === 'top' || edge === 'bottom') {
                canvas.style.cursor = 'ns-resize';
            } else if (edge === 'left' || edge === 'right') {
                canvas.style.cursor = 'ew-resize';
            } else {
                canvas.style.cursor = 'crosshair';
            }
        } else {
            // 非画笔 + 非切割 → 默认光标
            canvas.style.cursor = 'default';
        }
    }
}

// 鼠标释放
function handleMouseUp(e) {
    if (canvasState.isPainting) {
        canvasState.isPainting = false;
        canvasState.lastBrushPos = null;
        return;
    }
    canvasState.isDragging = false;
    canvasState.dragEdge = null;
}

// 绘制画笔圆形光标（画在主 canvas，与 overlay 分开 → 不会烘焙进图）
function drawBrushCursor(x, y) {
    const canvas = document.getElementById('adjustCanvas');
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    const sizeInput = document.getElementById('brushSizeInput');
    if (!sizeInput) return;
    const imgSize = parseInt(sizeInput.value, 10) || 1;
    const radius = (imgSize * canvasState.scale) / 2;
    ctx.save();
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.strokeStyle = '#e74c3c';
    ctx.lineWidth = 2;
    ctx.stroke();
    // 中心点
    ctx.beginPath();
    ctx.arc(x, y, 1.5, 0, Math.PI * 2);
    ctx.fillStyle = '#e74c3c';
    ctx.fill();
    ctx.restore();
}

// 在画笔覆盖层上画一个点（自动补点连线，避免快速移动时出现间断）
function paintAt(x, y) {
    const overlayCtx = canvasState.overlayCtx;
    if (!overlayCtx) return;

    const colorInput = document.getElementById('brushColor');
    const sizeInput = document.getElementById('brushSizeInput');
    const color = colorInput ? colorInput.value : '#000000';
    const imgSize = sizeInput ? Math.max(1, parseInt(sizeInput.value) || 5) : 5;
    // 图片坐标 → canvas 坐标：× scale
    const size = imgSize * canvasState.scale;

    overlayCtx.fillStyle = color;
    overlayCtx.beginPath();

    if (canvasState.lastBrushPos) {
        // 在上一个点和当前点之间补点画小圆，避免快速移动出现断点
        const dx = x - canvasState.lastBrushPos.x;
        const dy = y - canvasState.lastBrushPos.y;
        const dist = Math.sqrt(dx * dx + dy * dy);
        const step = Math.max(1, size / 2);
        const steps = Math.max(1, Math.ceil(dist / step));
        for (let i = 0; i <= steps; i++) {
            const t = i / steps;
            const px = canvasState.lastBrushPos.x + dx * t;
            const py = canvasState.lastBrushPos.y + dy * t;
            overlayCtx.moveTo(px, py);
            overlayCtx.arc(px, py, size / 2, 0, Math.PI * 2);
        }
    } else {
        overlayCtx.moveTo(x, y);
        overlayCtx.arc(x, y, size / 2, 0, Math.PI * 2);
    }
    overlayCtx.fill();

    canvasState.lastBrushPos = { x, y };
    redrawCanvas();
}

// 切换画笔模式（开关）
function toggleBrushMode() {
    canvasState.brushMode = !canvasState.brushMode;
    canvasState.isPainting = false;
    canvasState.lastBrushPos = null;
    updateBrushToggleButton();
    redrawCanvas();
}

// 根据当前 brushMode 刷新按钮显示
function updateBrushToggleButton() {
    const btn = document.getElementById('brushToggleBtn');
    if (!btn) return;
    if (canvasState.brushMode) {
        btn.textContent = '✏ 画笔：开';
        btn.style.background = '#4a90a4';
        btn.style.color = '#fff';
        btn.style.borderColor = '#4a90a4';
    } else {
        btn.textContent = '✏ 画笔：关';
        btn.style.background = '#f0f0f0';
        btn.style.color = '';
        btn.style.borderColor = '#ccc';
    }
}

// 切换切割模式（需点击按钮进入，避
// 免默认与画笔冲突；off 状态下不可拖动裁剪线）
function toggleCutMode() {
    canvasState.cutMode = !canvasState.cutMode;
    updateCutToggleButton();
    redrawCanvas();
}

function updateCutToggleButton() {
    const btn = document.getElementById('cutToggleBtn');
    if (!btn) return;
    if (canvasState.cutMode) {
        btn.textContent = '✂ 切割模式：开';
        btn.style.background = '#e74c3c';
        btn.style.color = '#fff';
        btn.style.borderColor = '#e74c3c';
    } else {
        btn.textContent = '✂ 切割模式：关';
        btn.style.background = '#f0f0f0';
        btn.style.color = '';
        btn.style.borderColor = '#ccc';
    }
}

// 检测覆盖层是否真有非透明像素（用户是否真的画过）
function hasPaintStrokes() {
    if (!canvasState.overlayCtx || !canvasState.overlayCanvas) return false;
    const w = canvasState.overlayCanvas.width;
    const h = canvasState.overlayCanvas.height;
    if (w === 0 || h === 0) return false;
    try {
        const data = canvasState.overlayCtx.getImageData(0, 0, w, h).data;
        for (let i = 3; i < data.length; i += 4) {
            if (data[i] > 0) return true;
        }
    } catch (e) {
        // getImageData 在画布被污染时会抛错；这种情况当作有笔触处理，让服务器端兜底
        return true;
    }
    return false;
}

// 把画笔覆盖层烘焙到主 canvas 上，并清空覆盖层
function bakeOverlayToImage() {
    const canvas = document.getElementById('adjustCanvas');
    if (!canvas || !canvasState.overlayCanvas) return;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(canvasState.overlayCanvas, 0, 0);
    canvasState.overlayCtx.clearRect(
        0, 0,
        canvasState.overlayCanvas.width,
        canvasState.overlayCanvas.height
    );
}

// 清除画笔覆盖层
function clearBrushOverlay() {
    if (!canvasState.overlayCtx) return;
    canvasState.overlayCtx.clearRect(
        0, 0,
        canvasState.overlayCanvas.width,
        canvasState.overlayCanvas.height
    );
    redrawCanvas();
}

// 绑定画笔相关控件（颜色预设 / 大小滑杆 ↔ 数字输入）
function setupBrushControls() {
    const colorInput = document.getElementById('brushColor');
    const sizeSlider = document.getElementById('brushSizeSlider');
    const sizeNumber = document.getElementById('brushSizeInput');

    // 颜色预设按钮
    document.querySelectorAll('.brush-preset').forEach(btn => {
        btn.addEventListener('click', () => {
            const c = btn.getAttribute('data-color');
            if (!c) return;
            if (colorInput) colorInput.value = c;
            document.querySelectorAll('.brush-preset').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
        });
    });

    // 默认高亮黑色预设
    const defaultPreset = document.querySelector('.brush-preset[data-color="#000000"]');
    if (defaultPreset) defaultPreset.classList.add('active');

    // 滑杆 ↔ 数字输入 双向同步
    if (sizeSlider && sizeNumber) {
        sizeSlider.addEventListener('input', () => {
            sizeNumber.value = sizeSlider.value;
        });
        sizeNumber.addEventListener('input', () => {
            let v = parseInt(sizeNumber.value);
            if (isNaN(v)) v = 1;
            v = Math.max(1, Math.min(100, v));
            sizeSlider.value = v;
        });
    }

    // 自定义颜色时取消预设高亮
    if (colorInput) {
        colorInput.addEventListener('input', () => {
            document.querySelectorAll('.brush-preset').forEach(b => b.classList.remove('active'));
        });
    }
}

// 关闭调整弹窗
function closeAdjustModal() {
    const modal = document.getElementById('adjustModal');
    if (modal) {
        modal.classList.add('hidden');
    }
    // 关闭时清画笔/切割模式
    canvasState.brushMode = false;
    canvasState.cutMode = false;
    canvasState.isPainting = false;
    updateBrushToggleButton();
    updateCutToggleButton();
    adjustModalOpen = false;
    // 清除选择状态
    state.selectedIndices = [];
    state.currentAdjustIndex = null;
    // 刷新字符网格显示
    renderCharacterGrid();
    updateAdjustButton();
}

// 重置调整
function resetAdjust() {
    if (canvasState.char) {
        canvasState.char.adjust_top = 0;
        canvasState.char.adjust_bottom = 0;
        canvasState.char.adjust_left = 0;
        canvasState.char.adjust_right = 0;
    }
    document.getElementById('adjustTop').value = 0;
    document.getElementById('adjustBottom').value = 0;
    document.getElementById('adjustLeft').value = 0;
    document.getElementById('adjustRight').value = 0;
    redrawCanvas();
}

// 应用调整
function applyAdjust() {
    // canvasState.char 和 orderedChars[state.currentAdjustIndex] 应该是同一个引用
    // 拖动时已经直接修改了 canvasState.char，所以这里只需要同步输入框的值
    if (canvasState.char) {
        // 从输入框获取最终值（用户可能手动输入）
        canvasState.char.adjust_top = parseInt(document.getElementById('adjustTop').value) || 0;
        canvasState.char.adjust_bottom = parseInt(document.getElementById('adjustBottom').value) || 0;
        canvasState.char.adjust_left = parseInt(document.getElementById('adjustLeft').value) || 0;
        canvasState.char.adjust_right = parseInt(document.getElementById('adjustRight').value) || 0;
        canvasState.char.needs_adjust = false;
        // 调整值已变（即使是 0→0 也要标记，因为重新打开 modal 会重画），触发重渲染
        canvasState.char.cache_version = (canvasState.char.cache_version || 0) + 1;
    }

    // 重新加载 canvas（cache_key 用 cache_version，复用浏览器缓存）
    redrawCanvas();
    showToast('调整已应用');
}

// 确定：提交当前输入框的调整值，标记为「已调整」，并触发 session 保存
async function confirmAdjust() {
    if (!canvasState.char) return;

    // 从输入框读取最终值（用户可能手动输入）
    canvasState.char.adjust_top = parseInt(document.getElementById('adjustTop').value) || 0;
    canvasState.char.adjust_bottom = parseInt(document.getElementById('adjustBottom').value) || 0;
    canvasState.char.adjust_left = parseInt(document.getElementById('adjustLeft').value) || 0;
    canvasState.char.adjust_right = parseInt(document.getElementById('adjustRight').value) || 0;
    canvasState.char.needs_adjust = false;
    // 调整值变了（即使是 0→0），标记 cache_version 触发 grid 重新加载
    canvasState.char.cache_version = (canvasState.char.cache_version || 0) + 1;

    // 把画笔覆盖层烘焙到主 canvas，并把整张画好的图作为 image_data 发到服务器
    // 只有真正画过任何像素才发送，避免无谓地增大 payload
    if (hasPaintStrokes()) {
        bakeOverlayToImage();
        const canvas = document.getElementById('adjustCanvas');
        try {
            // 临时开启画笔模式抑制框线绘制（不重绘——否则刚烤的笔触会被擦掉）
            const prevBrushMode = canvasState.brushMode;
            canvasState.brushMode = true;
            canvasState.char.image_data = canvas.toDataURL('image/png');
            canvasState.brushMode = prevBrushMode;
            redrawCanvas();  // 恢复显示（笔触已在 image_data 中，display 不再需要）
        } catch (e) {
            console.error('toDataURL 失败:', e);
            showToast('画笔内容编码失败，仍保存其他调整');
        }
    }

    // 烘焙后关闭画笔模式，重绘（不再画覆盖层）
    canvasState.brushMode = false;
    canvasState.isPainting = false;
    canvasState.lastBrushPos = null;
    updateBrushToggleButton();
    redrawCanvas();

    // 触发 session 保存（与页面「保存」按钮走同一个接口）
    showLoading('保存调整...');
    try {
        const response = await fetch('/api/save_adjustments', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                characters: state.characters
            })
        });
        const data = await response.json();
        hideLoading();
        if (data.success) {
            showToast('已保存调整');
            // 只更新当前修改的卡片图片（不重渲全部 859 张）
            reloadOneCharCard(canvasState.char);
            // 关闭弹窗并刷新网格（让「已调整」badge 出现）
            closeAdjustModal();
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        hideLoading();
        showToast('保存失败: ' + error.message);
    }
}

// 保存并下一个
async function saveAndNext() {
    applyAdjust();

    // 找到下一个需要调整的
    const currentIndex = state.selectedIndices.indexOf(state.currentAdjustIndex);
    if (currentIndex < state.selectedIndices.length - 1) {
        state.currentAdjustIndex = state.selectedIndices[currentIndex + 1];
        showAdjustModal(state.currentAdjustIndex);
    } else {
        closeAdjustModal();
        showToast('所有选中字符已调整完成');
    }
}

// 保存调整结果
async function saveAdjustments() {
    if (!state.imageHash) {
        showToast('没有可保存的数据');
        return;
    }

    showLoading('保存中...');

    try {
        const response = await fetch('/api/save_adjustments', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                characters: state.characters
            })
        });
        const data = await response.json();

        if (data.success) {
            showToast('保存成功');
            // 更新显示
            renderCharacterGrid();
            updateUI();
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('保存失败: ' + error.message);
    }

    hideLoading();
}

// 一键清除空白字符
async function handleClearEmpty() {
    if (!state.imageHash) return;
    const emptyCount = state.characters.filter(c => c.is_empty).length;
    if (emptyCount === 0) {
        showToast('当前没有空白字符');
        return;
    }
    if (!confirm(`确定要删除 ${emptyCount} 张空白字符吗？\n（磁盘文件 + session 都会同步）`)) return;

    showLoading('正在清除空白字符...');
    try {
        const r = await fetch('/api/clear_empty_chars', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hash: state.imageHash })
        });
        const data = await r.json();
        if (!data.success) throw new Error(data.error);

        state.characters = data.characters;
        // 重新排序后 strip_index / char_index 可能不再连续，但本页只用于展示
        renderCharacterGrid();
        updateUI();
        hideLoading();
        showToast(`已清除 ${data.removed_count} 个空白，剩余 ${data.remaining_count} 个`);
    } catch (error) {
        hideLoading();
        showToast('清除失败: ' + error.message);
    }
}

// 清空所有数据
async function handleClearAll() {
    if (!state.imageHash) return;
    if (!confirm(`确定要清空这张图片的全部数据吗？\n\n将删除：\n• output 目录（含所有切割字符图）\n• session 配置 JSON\n• uploads 原图和基准图\n\n此操作不可撤销！`)) return;

    showLoading('正在清空所有数据...');
    try {
        const r = await fetch('/api/clear_all_data', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hash: state.imageHash })
        });
        const data = await r.json();
        if (!data.success) throw new Error(data.error);

        hideLoading();
        showToast('已清空所有数据，2秒后返回切割布局');
        // 清空 localStorage hash 并跳转
        localStorage.removeItem('currentImageHash');
        setTimeout(() => { window.location.href = '/layout'; }, 2000);
    } catch (error) {
        hideLoading();
        showToast('清空失败: ' + error.message);
    }
}
