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

        // 过滤掉已删除和空字符，然后按传统顺序排序
        const filteredChars = data.characters.filter(c => !c.deleted && !c.is_empty);
        state.characters = getTraditionalOrder(filteredChars);

        if (state.characters.length === 0) {
            showEmptyState();
            return;
        }

        showPreviewGrid();
        updateUI();

        // 自动进行默认居中处理（v2 算法）
        // 若磁盘上已有旧版 scaled_*.png，这里也会覆盖（用户刷新页面即自动迁移）
        await autoProcess();

    } catch (error) {
        console.error('加载字符数据失败:', error);
        showEmptyState();
    }
}

// 自动处理（页面加载时使用默认参数；v2 = 高度归一）
async function autoProcess() {
    if (state.characters.length === 0) {
        return;
    }

    const params = getScaleParams();
    showProgress();
    elements.progressText.textContent = '正在按高度归一...';

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

            // 渲染处理后的预览
            renderProcessedPreview(data.characters);

            // 启用按钮
            elements.saveBtn.disabled = false;
            elements.openDirBtn.disabled = false;
            elements.annotateBtn.disabled = false;
        } else {
            renderOriginalPreview();
            console.error('自动处理失败:', data.error);
        }
    } catch (error) {
        renderOriginalPreview();
        console.error('自动处理失败:', error);
    }

    hideProgress();
}

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
    card.className = 'preview-card';

    const number = document.createElement('div');
    number.className = 'preview-number';
    number.textContent = index + 1;

    const img = document.createElement('img');
    img.src = char.image_url;
    img.alt = `字符 ${index + 1}`;

    card.appendChild(number);
    card.appendChild(img);

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
        card.className = 'preview-card';

        const number = document.createElement('div');
        number.className = 'preview-number';
        number.textContent = index + 1;

        const img = document.createElement('img');
        img.src = char.processed_url + '?t=' + Date.now(); // 添加时间戳避免缓存
        img.alt = `字符 ${index + 1}`;

        card.appendChild(number);
        card.appendChild(img);
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
