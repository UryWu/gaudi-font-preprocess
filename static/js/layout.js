/* 切割布局页面逻辑 - 支持缩放和文本框显示 */

// 状态管理
const state = {
    imageHash: null,
    imageUrl: null,
    imageWidth: 0,
    imageHeight: 0,
    canvasWidth: 0,
    canvasHeight: 0,
    originalScale: 1,  // 原始适配比例
    zoomLevel: 1,      // 缩放级别
    panOffset: { x: 0, y: 0 },  // 平移偏移
    verticalLines: [],
    horizontalLines: [],
    boxes: [],         // OCR文本框
    imageObj: null,
    selectedLine: null,
    lineType: null,
    isDragging: false,
    isPanning: false,
    lastMousePos: null,
    showTextBoxes: true,  // 是否显示文本框
    cumulativeRotation: 0,  // 手动旋转累计角度（度）
    detectionStale: false,  // 当前检测结果是否已过期（旋转后未重新识别）
    useRedLines: true,   // 切割：参与红/蓝网格
    useBlueLines: true,
    useGreenBoxes: false  // 切割：参与绿框直接切割
};

// DOM 元素
const canvas = document.getElementById('imageCanvas');
const ctx = canvas.getContext('2d');
const imageInput = document.getElementById('imageInput');
const uploadBtn = document.getElementById('uploadBtn');
const saveBtn = document.getElementById('saveBtn');
const applyBtn = document.getElementById('applyBtn');
const emptyState = document.getElementById('emptyState');
const rotateLeftBtn = document.getElementById('rotateLeftBtn');
const rotateRightBtn = document.getElementById('rotateRightBtn');
const rotateResetBtn = document.getElementById('rotateResetBtn');
const detectBtn = document.getElementById('detectBtn');
const rotationDisplay = document.getElementById('rotationDisplay');
const useRedLinesToggle = document.getElementById('useRedLinesToggle');
const useBlueLinesToggle = document.getElementById('useBlueLinesToggle');
const useGreenBoxesToggle = document.getElementById('useGreenBoxesToggle');

// 初始化
document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    resizeCanvas();
});

function setupEventListeners() {
    uploadBtn.addEventListener('click', () => imageInput.click());
    imageInput.addEventListener('change', handleImageUpload);
    saveBtn.addEventListener('click', saveCutLines);
    applyBtn.addEventListener('click', applyCut);

    // Canvas 事件
    canvas.addEventListener('mousedown', handleMouseDown);
    canvas.addEventListener('mousemove', handleMouseMove);
    canvas.addEventListener('mouseup', handleMouseUp);
    canvas.addEventListener('mouseleave', handleMouseUp);
    canvas.addEventListener('dblclick', handleDoubleClick);
    canvas.addEventListener('wheel', handleWheel, { passive: false });
    canvas.addEventListener('contextmenu', (e) => e.preventDefault());  // 禁止右键菜单

    // 窗口大小变化
    window.addEventListener('resize', resizeCanvas);

    // 手动旋转按钮
    rotateLeftBtn.addEventListener('click', () => handleRotate(+1));   // 逆时针
    rotateRightBtn.addEventListener('click', () => handleRotate(-1));  // 顺时针
    rotateResetBtn.addEventListener('click', handleResetRotation);
    detectBtn.addEventListener('click', handleDetect);

    // 切割来源勾选框
    if (useRedLinesToggle) {
        useRedLinesToggle.addEventListener('change', e => {
            state.useRedLines = e.target.checked;
            updateUI();
        });
        useBlueLinesToggle.addEventListener('change', e => {
            state.useBlueLines = e.target.checked;
            updateUI();
        });
        useGreenBoxesToggle.addEventListener('change', e => {
            state.useGreenBoxes = e.target.checked;
            updateUI();
        });
    }

    // 绿色框设置按钮 + 模态框
    const greenBoxSettingsBtn = document.getElementById('greenBoxSettingsBtn');
    if (greenBoxSettingsBtn) greenBoxSettingsBtn.addEventListener('click', openGreenBoxModal);
    initGreenBoxModal();
}

function resizeCanvas() {
    const workspace = document.querySelector('.workspace');
    canvas.width = workspace.clientWidth;
    canvas.height = workspace.clientHeight;
    state.canvasWidth = canvas.width;
    state.canvasHeight = canvas.height;

    // 计算原始适配比例
    if (state.imageObj) {
        calculateBaseScale();
        drawCanvas();
    }
}

function calculateBaseScale() {
    // 计算图片完全适配canvas的基础缩放比例
    const imgRatio = state.imageWidth / state.imageHeight;
    const canvasRatio = canvas.width / canvas.height;

    if (imgRatio > canvasRatio) {
        state.originalScale = canvas.width / state.imageWidth;
    } else {
        state.originalScale = canvas.height / state.imageHeight;
    }
}

async function handleImageUpload(e) {
    const file = e.target.files[0];
    if (!file) return;

    showLoading('正在处理图片...');

    const formData = new FormData();
    formData.append('image', file);
    // 传递纠偏开关状态
    const deskewToggle = document.getElementById('deskewToggle');
    formData.append('deskew', deskewToggle && deskewToggle.checked ? '1' : '0');

    try {
        const response = await fetch('/api/upload', {
            method: 'POST',
            body: formData
        });
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        // 更新状态
        state.imageHash = data.hash;
        state.imageWidth = data.width;
        state.imageHeight = data.height;
        state.verticalLines = data.vertical_lines || [];
        state.horizontalLines = data.horizontal_lines || [];
        state.stripHorizontalLines = data.strip_horizontal_lines || [];  // 按列分组的横向切割线
        state.boxes = data.boxes || [];
        // 初始化主集（识别完成后的原始 boxes）
        masterBoxes = state.boxes.slice();
        persistMasterBoxes();
        state.zoomLevel = 1;
        state.panOffset = { x: 0, y: 0 };

        // 保存到 localStorage 供其他页面使用
        localStorage.setItem('currentImageHash', data.hash);

        // 加载图片
        const img = new Image();
        img.onload = () => {
            state.imageObj = img;
            emptyState.classList.add('hidden');
            calculateBaseScale();
            drawCanvas();
            updateUI();
            hideLoading();

            if (data.has_saved_session) {
                showToast('已加载保存的切割线配置');
            }
            if (data.skew_angle !== undefined && Math.abs(data.skew_angle) > 0.1) {
                showToast(`已自动纠偏 ${data.skew_angle.toFixed(2)}°`);
            }
        };
        img.src = data.image_url;
        state.imageUrl = data.image_url;

        // 更新信息显示
        document.getElementById('imgWidth').textContent = data.width;
        document.getElementById('imgHeight').textContent = data.height;
        document.getElementById('imgHash').textContent = data.hash.substring(0, 12) + '...';

        // 启用按钮
        saveBtn.disabled = false;
        applyBtn.disabled = false;
        rotateLeftBtn.disabled = false;
        rotateRightBtn.disabled = false;
        rotateResetBtn.disabled = true;  // 重置仅在已旋转后才可用
        detectBtn.disabled = false;

        // 重置累计旋转（重新上传 = 全新开始）
        state.cumulativeRotation = 0;
        state.detectionStale = false;
        detectBtn.classList.remove('btn-detect-stale');
        updateRotationDisplay();

    } catch (error) {
        hideLoading();
        showToast('上传失败: ' + error.message);
        console.error(error);
    }
}

// 获取当前绘制参数
function getDrawParams() {
    const scale = state.originalScale * state.zoomLevel;
    const drawWidth = state.imageWidth * scale;
    const drawHeight = state.imageHeight * scale;

    // 居中位置 + 平移偏移
    const baseX = (canvas.width - drawWidth) / 2 + state.panOffset.x;
    const baseY = (canvas.height - drawHeight) / 2 + state.panOffset.y;

    return { scale, drawWidth, drawHeight, offsetX: baseX, offsetY: baseY };
}

function drawCanvas() {
    if (!state.imageObj) return;

    ctx.clearRect(0, 0, canvas.width, canvas.height);

    const { scale, drawWidth, drawHeight, offsetX, offsetY } = getDrawParams();

    // 保存绘制参数
    state.drawOffset = { x: offsetX, y: offsetY };
    state.drawScale = scale;

    // 绘制图片
    ctx.drawImage(state.imageObj, offsetX, offsetY, drawWidth, drawHeight);

    // 绘制文本框
    if (state.showTextBoxes && state.boxes.length > 0) {
        drawTextBoxes();
    }

    // 绘制切割线
    drawCutLines();
}

function drawTextBoxes() {
    const { x: offsetX, y: offsetY } = state.drawOffset;
    const scale = state.drawScale;

    ctx.strokeStyle = 'rgba(46, 204, 113, 0.6)';  // 绿色文本框
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);

    state.boxes.forEach((box, index) => {
        const x = offsetX + box.x_min * scale;
        const y = offsetY + box.y_min * scale;
        const w = box.width * scale;
        const h = box.height * scale;

        ctx.strokeRect(x, y, w, h);

        // 绘制编号（小字）
        ctx.setLineDash([]);
        ctx.fillStyle = 'rgba(46, 204, 113, 0.9)';
        const fontSize = Math.max(8, 12 / state.zoomLevel);
        ctx.font = `${fontSize}px sans-serif`;
        ctx.textAlign = 'left';
        ctx.textBaseline = 'top';
        ctx.fillText(`${index + 1}`, x + 2, y + 2);
        ctx.setLineDash([4, 4]);
    });

    ctx.setLineDash([]);
}

function drawCutLines() {
    const { x: offsetX, y: offsetY } = state.drawOffset;
    const scale = state.drawScale;

    // 绘制纵向切割线（红色）
    ctx.strokeStyle = '#e74c3c';
    ctx.lineWidth = 2 / state.zoomLevel;  // 线宽随缩放调整
    state.verticalLines.forEach(x => {
        const canvasX = offsetX + x * scale;
        ctx.beginPath();
        ctx.moveTo(canvasX, offsetY);
        ctx.lineTo(canvasX, offsetY + state.imageHeight * scale);
        ctx.stroke();
    });

    // 绘制横向切割线（蓝色）- 按列绘制，不跨列
    ctx.strokeStyle = '#3498db';
    if (state.stripHorizontalLines && state.stripHorizontalLines.length > 0) {
        // 按列绘制横向切割线
        state.stripHorizontalLines.forEach(strip => {
            const xStart = offsetX + strip.x_start * scale;
            const xEnd = offsetX + strip.x_end * scale;
            strip.horizontal_lines.forEach(y => {
                const canvasY = offsetY + y * scale;
                ctx.beginPath();
                ctx.moveTo(xStart, canvasY);
                ctx.lineTo(xEnd, canvasY);
                ctx.stroke();
            });
        });
    } else {
        // 兼容旧数据：如果没有按列分组的数据，则跨整行绘制
        state.horizontalLines.forEach(y => {
            const canvasY = offsetY + y * scale;
            ctx.beginPath();
            ctx.moveTo(offsetX, canvasY);
            ctx.lineTo(offsetX + state.imageWidth * scale, canvasY);
            ctx.stroke();
        });
    }
}

// 滚轮缩放
function handleWheel(e) {
    e.preventDefault();
    if (!state.imageObj) return;

    const pos = getCanvasPosition(e);
    const oldPos = canvasToImage(pos);

    // 缩放因子
    const zoomFactor = e.deltaY > 0 ? 0.9 : 1.1;
    const newZoomLevel = Math.max(0.1, Math.min(10, state.zoomLevel * zoomFactor));

    state.zoomLevel = newZoomLevel;

    // 重新计算位置，保持鼠标下的点不动
    const { scale, offsetX, offsetY } = getDrawParams();
    state.drawOffset = { x: offsetX, y: offsetY };
    state.drawScale = scale;

    const newPos = imageToCanvas(oldPos);
    state.panOffset.x += pos.x - newPos.x;
    state.panOffset.y += pos.y - newPos.y;

    drawCanvas();
    updateUI();
}

function imageToCanvas(imagePos) {
    const { x: offsetX, y: offsetY } = state.drawOffset || getDrawParams();
    const scale = state.drawScale || state.originalScale * state.zoomLevel;
    return {
        x: offsetX + imagePos.x * scale,
        y: offsetY + imagePos.y * scale
    };
}

function handleMouseDown(e) {
    if (!state.imageObj) return;

    const pos = getCanvasPosition(e);

    // 右键：平移
    if (e.button === 2) {
        state.isPanning = true;
        state.lastMousePos = pos;
        canvas.style.cursor = 'grabbing';
        e.preventDefault();
        return;
    }

    // 左键处理
    if (e.button !== 0) return;

    const lineInfo = findNearestLine(pos);

    if (lineInfo && e.altKey) {
        // Alt+点击删除切割线
        deleteLine(lineInfo);
    } else if (lineInfo && !e.ctrlKey) {
        // 开始拖动切割线（按住 Ctrl 时跳过，强制平移）
        state.selectedLine = lineInfo.lineIndex !== undefined ? lineInfo.lineIndex : lineInfo.index;
        state.lineType = lineInfo.type;
        state.selectedLineValue = lineInfo.yValue;
        state.selectedStripIndex = lineInfo.stripIndex;  // 存储列索引
        state.isDragging = true;
        canvas.style.cursor = lineInfo.type === 'vertical' ? 'ew-resize' : 'ns-resize';
    } else {
        // 空白处 或 Ctrl+点击任意位置：开始平移图片
        state.isPanning = true;
        state.lastMousePos = pos;
        canvas.style.cursor = 'grabbing';
    }
}

function handleMouseMove(e) {
    if (!state.imageObj) return;

    const pos = getCanvasPosition(e);

    if (state.isPanning && state.lastMousePos) {
        // 平移
        const dx = pos.x - state.lastMousePos.x;
        const dy = pos.y - state.lastMousePos.y;
        state.panOffset.x += dx;
        state.panOffset.y += dy;
        state.lastMousePos = pos;
        drawCanvas();
    } else if (state.isDragging && state.selectedLine !== null) {
        // 拖动切割线
        const imagePos = canvasToImage(pos);

        if (state.lineType === 'vertical') {
            const newX = Math.max(0, Math.min(state.imageWidth, imagePos.x));
            const roundedX = Math.round(newX);
            state.verticalLines[state.selectedLine] = roundedX;

            // 同步更新 stripHorizontalLines 中对应列的边界
            const lineIdx = state.selectedLine;
            if (state.stripHorizontalLines && state.stripHorizontalLines.length > 0) {
                // 移动 verticalLines[lineIdx] 影响 strip[lineIdx-1].x_end 和 strip[lineIdx].x_start
                if (lineIdx > 0 && lineIdx - 1 < state.stripHorizontalLines.length) {
                    state.stripHorizontalLines[lineIdx - 1].x_end = roundedX;
                }
                if (lineIdx < state.stripHorizontalLines.length) {
                    state.stripHorizontalLines[lineIdx].x_start = roundedX;
                }
            }
        } else {
            // 横向切割线 - 只更新当前列
            const newY = Math.max(0, Math.min(state.imageHeight, imagePos.y));
            const roundedY = Math.round(newY);

            // 只更新当前列的横向切割线
            if (state.stripHorizontalLines && state.selectedStripIndex !== undefined) {
                const strip = state.stripHorizontalLines[state.selectedStripIndex];
                if (strip && state.selectedLine < strip.horizontal_lines.length) {
                    strip.horizontal_lines[state.selectedLine] = roundedY;
                    strip.horizontal_lines.sort((a, b) => a - b);
                    state.selectedLine = strip.horizontal_lines.indexOf(roundedY);
                    state.selectedLineValue = roundedY;
                }
            }
        }

        drawCanvas();
        updateUI();
    } else {
        // 检查是否靠近切割线，更新光标
        if (e.ctrlKey) {
            canvas.style.cursor = 'grab';
        } else {
            const lineInfo = findNearestLine(pos);
            if (lineInfo) {
                canvas.style.cursor = lineInfo.type === 'vertical' ? 'ew-resize' : 'ns-resize';
            } else {
                canvas.style.cursor = 'grab';
            }
        }
    }
}

function handleMouseUp(e) {
    state.isDragging = false;
    state.isPanning = false;
    state.selectedLine = null;
    state.lineType = null;
    state.lastMousePos = null;
    canvas.style.cursor = 'grab';
}

function handleDoubleClick(e) {
    if (!state.imageObj) return;

    const pos = getCanvasPosition(e);
    const imagePos = canvasToImage(pos);

    // Shift+双击：添加竖向切割线
    if (e.shiftKey) {
        addLineAt('vertical', imagePos.x);
        return;
    }

    // 找到鼠标所在的列索引
    let stripIndex = 0;
    if (state.stripHorizontalLines && state.stripHorizontalLines.length > 0) {
        for (let i = 0; i < state.stripHorizontalLines.length; i++) {
            const strip = state.stripHorizontalLines[i];
            if (strip.x_start <= imagePos.x && imagePos.x <= strip.x_end) {
                stripIndex = i;
                break;
            }
        }
    }

    // 普通双击：添加横向切割线到当前列
    addHorizontalLineAt(imagePos.y, stripIndex);
}

// 在指定列添加横向切割线
function addHorizontalLineAt(y, stripIndex) {
    const roundedY = Math.round(y);

    console.log(`添加横向切割线: y=${roundedY}, stripIndex=${stripIndex}`);
    console.log(`当前列信息:`, state.stripHorizontalLines.map((s, i) =>
        `列${i}: x=${s.x_start}-${s.x_end}, 横线数=${s.horizontal_lines.length}`
    ).join(', '));

    if (state.stripHorizontalLines && state.stripHorizontalLines[stripIndex]) {
        const strip = state.stripHorizontalLines[stripIndex];
        // 检查是否已存在
        if (!strip.horizontal_lines.includes(roundedY)) {
            strip.horizontal_lines.push(roundedY);
            strip.horizontal_lines.sort((a, b) => a - b);
            console.log(`已添加到列${stripIndex}, 现有横线:`, strip.horizontal_lines);
        }
    }

    drawCanvas();
    updateUI();
    showToast(`已添加横向切割线`);
}

function getCanvasPosition(e) {
    const rect = canvas.getBoundingClientRect();
    return {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top
    };
}

function canvasToImage(canvasPos) {
    const { x: offsetX, y: offsetY } = state.drawOffset || getDrawParams();
    const scale = state.drawScale || state.originalScale * state.zoomLevel;
    return {
        x: (canvasPos.x - offsetX) / scale,
        y: (canvasPos.y - offsetY) / scale
    };
}

function findNearestLine(pos) {
    const threshold = Math.max(10, 5 / state.zoomLevel);  // 阈值随缩放调整
    const { x: offsetX, y: offsetY } = state.drawOffset || getDrawParams();
    const scale = state.drawScale || state.originalScale * state.zoomLevel;

    // 检查纵向切割线
    for (let i = 0; i < state.verticalLines.length; i++) {
        const lineX = offsetX + state.verticalLines[i] * scale;
        if (Math.abs(pos.x - lineX) < threshold) {
            return { type: 'vertical', index: i };
        }
    }

    // 检查横向切割线 - 只在鼠标所在的列中查找
    if (state.stripHorizontalLines && state.stripHorizontalLines.length > 0) {
        // 找到鼠标所在的列
        const imageX = (pos.x - offsetX) / scale;
        let currentStripIndex = -1;
        for (let i = 0; i < state.stripHorizontalLines.length; i++) {
            const strip = state.stripHorizontalLines[i];
            if (strip.x_start <= imageX && imageX <= strip.x_end) {
                currentStripIndex = i;
                break;
            }
        }

        if (currentStripIndex >= 0) {
            const strip = state.stripHorizontalLines[currentStripIndex];
            for (let i = 0; i < strip.horizontal_lines.length; i++) {
                const lineY = offsetY + strip.horizontal_lines[i] * scale;
                if (Math.abs(pos.y - lineY) < threshold) {
                    return {
                        type: 'horizontal',
                        stripIndex: currentStripIndex,
                        lineIndex: i,
                        yValue: strip.horizontal_lines[i]
                    };
                }
            }
        }
    } else {
        // 兼容旧数据
        for (let i = 0; i < state.horizontalLines.length; i++) {
            const lineY = offsetY + state.horizontalLines[i] * scale;
            if (Math.abs(pos.y - lineY) < threshold) {
                return { type: 'horizontal', index: i, yValue: state.horizontalLines[i] };
            }
        }
    }

    return null;
}

function addLineAt(type, position) {
    if (type === 'vertical') {
        const newX = Math.round(position);
        state.verticalLines.push(newX);
        state.verticalLines.sort((a, b) => a - b);

        // 更新 stripHorizontalLines 结构
        updateStripHorizontalLines();
    } else {
        const roundedY = Math.round(position);

        // 只添加到鼠标所在的那一列
        if (state.stripHorizontalLines && state.stripHorizontalLines.length > 0 && state.lastClickStripIndex !== undefined) {
            const strip = state.stripHorizontalLines[state.lastClickStripIndex];
            if (strip) {
                strip.horizontal_lines.push(roundedY);
                strip.horizontal_lines.sort((a, b) => a - b);
            }
        } else {
            // 兼容：添加到所有列
            state.horizontalLines.push(roundedY);
            state.horizontalLines.sort((a, b) => a - b);
            if (state.stripHorizontalLines) {
                state.stripHorizontalLines.forEach(strip => {
                    strip.horizontal_lines.push(roundedY);
                    strip.horizontal_lines.sort((a, b) => a - b);
                });
            }
        }
    }
    drawCanvas();
    updateUI();
    showToast(`已添加${type === 'vertical' ? '纵向' : '横向'}切割线`);
}

// 更新列结构（当竖向切割线变化时）
function updateStripHorizontalLines() {
    // 保存旧的列结构用于继承
    const oldStrips = state.stripHorizontalLines || [];

    console.log('更新列结构, 旧列数:', oldStrips.length, ', 新竖线数:', state.verticalLines.length);

    if (oldStrips.length === 0) {
        // 如果没有现有的列结构，创建默认的（只有边界线）
        const defaultHLines = [0, state.imageHeight];
        state.stripHorizontalLines = [];
        for (let i = 0; i < state.verticalLines.length - 1; i++) {
            state.stripHorizontalLines.push({
                strip_index: i,
                x_start: state.verticalLines[i],
                x_end: state.verticalLines[i + 1],
                horizontal_lines: [...defaultHLines]
            });
        }
        return;
    }

    // 为每个新的竖向区间找到重叠的原始列，合并其横向切割线
    const newStripHorizontalLines = [];
    for (let i = 0; i < state.verticalLines.length - 1; i++) {
        const xStart = state.verticalLines[i];
        const xEnd = state.verticalLines[i + 1];

        // 找到所有与新列重叠的原始列，合并它们的横向切割线
        const mergedHLines = new Set();
        mergedHLines.add(0);
        mergedHLines.add(state.imageHeight);

        for (const strip of oldStrips) {
            // 检查是否有重叠
            if (strip.x_start < xEnd && strip.x_end > xStart) {
                // 有重叠，合并横向切割线
                strip.horizontal_lines.forEach(y => mergedHLines.add(y));
            }
        }

        const hLines = Array.from(mergedHLines).sort((a, b) => a - b);

        console.log(`新列${i}: x=${xStart}-${xEnd}, 合并后横线数=${hLines.length}`);

        newStripHorizontalLines.push({
            strip_index: i,
            x_start: xStart,
            x_end: xEnd,
            horizontal_lines: hLines
        });
    }

    state.stripHorizontalLines = newStripHorizontalLines;
}

function deleteLine(lineInfo) {
    if (lineInfo.type === 'vertical') {
        if (lineInfo.index === 0 || lineInfo.index === state.verticalLines.length - 1) {
            showToast('无法删除边界线');
            return;
        }
        state.verticalLines.splice(lineInfo.index, 1);
        // 更新列结构
        updateStripHorizontalLines();
    } else {
        // 横向切割线 - 只从当前列中删除
        const yValue = lineInfo.yValue;
        if (yValue === 0 || yValue === state.imageHeight) {
            showToast('无法删除边界线');
            return;
        }

        // 只从当前列中删除
        if (lineInfo.stripIndex !== undefined && state.stripHorizontalLines) {
            const strip = state.stripHorizontalLines[lineInfo.stripIndex];
            if (strip) {
                const idx = strip.horizontal_lines.indexOf(yValue);
                if (idx !== -1) {
                    strip.horizontal_lines.splice(idx, 1);
                }
            }
        } else {
            // 兼容旧数据
            const idx = state.horizontalLines.indexOf(yValue);
            if (idx !== -1) {
                state.horizontalLines.splice(idx, 1);
            }
        }
    }
    drawCanvas();
    updateUI();
    showToast('已删除切割线');
}

function updateUI() {
    const vCount = state.verticalLines.length;
    const hCount = state.horizontalLines.length;
    const bCount = state.boxes.length;

    document.getElementById('vLineCount').textContent = vCount;
    document.getElementById('hLineCount').textContent = hCount;
    document.getElementById('boxCount').textContent = bCount;

    // 根据三色勾选状态计算预计切割
    // 三勾选或单绿框：实际去重后 ≥ max(boxes, grid)，取上界
    // 双勾选网格：= (v-1) × (h-1)
    const gridCount = Math.max(0, (vCount - 1) * (hCount - 1));
    let pieces = 0;
    const gridOn = state.useRedLines && state.useBlueLines;

    if (state.useGreenBoxes) {
        pieces = Math.max(bCount, gridOn ? gridCount : bCount);
    } else if (gridOn) {
        pieces = gridCount;
    }

    const hint = document.getElementById('totalPiecesHint');
    if (state.useGreenBoxes && gridOn) {
        hint.textContent = '（≥max(绿框,网格)）';
    } else if (state.useGreenBoxes) {
        hint.textContent = '（绿框）';
    } else if (gridOn) {
        hint.textContent = '（网格）';
    } else {
        hint.textContent = '（未勾选）';
    }
    document.getElementById('totalPieces').textContent = pieces;
}

async function saveCutLines() {
    if (!state.imageHash) return;

    showLoading('保存中...');

    console.log('保存切割线, boxes数量:', state.boxes ? state.boxes.length : 0);

    try {
        const response = await fetch('/api/save_cuts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                vertical_lines: state.verticalLines,
                horizontal_lines: state.horizontalLines,
                strip_horizontal_lines: state.stripHorizontalLines,
                boxes: state.boxes
            })
        });
        const data = await response.json();

        if (data.success) {
            showToast('切割线配置已保存');
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('保存失败: ' + error.message);
    }

    hideLoading();
}

async function applyCut() {
    if (!state.imageHash) return;

    if (!state.useRedLines && !state.useBlueLines && !state.useGreenBoxes) {
        showToast('请至少勾选一个切割来源');
        return;
    }

    showLoading('正在切割图片...');

    try {
        const response = await fetch('/api/apply_cuts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                hash: state.imageHash,
                vertical_lines: state.verticalLines,
                horizontal_lines: state.horizontalLines,
                strip_horizontal_lines: state.stripHorizontalLines,
                boxes: state.boxes,
                use_red: state.useRedLines,
                use_blue: state.useBlueLines,
                use_green: state.useGreenBoxes,
            })
        });
        const data = await response.json();

        if (data.success) {
            showToast(`切割完成！共 ${data.total_pieces} 个片段`);
            // 跳转到切割调整页面
            setTimeout(() => {
                window.location.href = '/adjust';
            }, 1000);
        } else {
            throw new Error(data.error);
        }
    } catch (error) {
        showToast('切割失败: ' + error.message);
    }

    hideLoading();
}

// 重置视图
function resetView() {
    state.zoomLevel = 1;
    state.panOffset = { x: 0, y: 0 };
    drawCanvas();
}

// 切换文本框显示
function toggleTextBoxes() {
    state.showTextBoxes = !state.showTextBoxes;
    drawCanvas();
}

// 手动旋转按钮处理（仅旋转，不触发识别）
async function handleRotate(delta) {
    if (!state.imageHash) return;
    if (state.isRotating || state.isDetecting) return;

    state.isRotating = true;
    setRotateButtonsDisabled(true);
    showLoading('正在旋转...');

    try {
        const response = await fetch('/api/rotate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hash: state.imageHash, angle: delta })
        });
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        state.cumulativeRotation += delta;
        // 旋转后检测结果过期，标记 stale 并清空旧数据
        markDetectionStale();

        state.imageWidth = data.width;
        state.imageHeight = data.height;
        state.zoomLevel = 1;
        state.panOffset = { x: 0, y: 0 };

        const img = new Image();
        img.onload = () => {
            state.imageObj = img;
            calculateBaseScale();
            drawCanvas();
            updateUI();
            updateRotationDisplay();
            hideLoading();
            showToast(`已旋转 ${delta > 0 ? '+' : ''}${delta}°（累计 ${state.cumulativeRotation}°，请点识别）`);
        };
        img.onerror = () => {
            hideLoading();
            showToast('图片加载失败');
        };
        img.src = data.image_url;
    } catch (error) {
        hideLoading();
        showToast('旋转失败: ' + error.message);
        console.error(error);
    } finally {
        state.isRotating = false;
        setRotateButtonsDisabled(false);
    }
}

// 手动重置旋转（恢复为纠偏后状态，不触发识别）
async function handleResetRotation() {
    if (!state.imageHash) return;
    if (state.isRotating || state.isDetecting) return;
    if (state.cumulativeRotation === 0) return;

    state.isRotating = true;
    setRotateButtonsDisabled(true);
    showLoading('正在重置...');

    try {
        const response = await fetch('/api/reset_image', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hash: state.imageHash })
        });
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        state.cumulativeRotation = 0;
        // 重置后也是新图像，需要重新检测
        markDetectionStale();

        state.imageWidth = data.width;
        state.imageHeight = data.height;
        state.zoomLevel = 1;
        state.panOffset = { x: 0, y: 0 };

        const img = new Image();
        img.onload = () => {
            state.imageObj = img;
            calculateBaseScale();
            drawCanvas();
            updateUI();
            updateRotationDisplay();
            hideLoading();
            showToast('已重置为纠偏后状态，请点识别');
        };
        img.onerror = () => {
            hideLoading();
            showToast('图片加载失败');
        };
        img.src = data.image_url;
    } catch (error) {
        hideLoading();
        showToast('重置失败: ' + error.message);
        console.error(error);
    } finally {
        state.isRotating = false;
        setRotateButtonsDisabled(false);
    }
}

// 识别按钮：对当前旋转重新检测文本框和切割线
async function handleDetect() {
    if (!state.imageHash) return;
    if (state.isRotating || state.isDetecting) return;

    state.isDetecting = true;
    setRotateButtonsDisabled(true);
    detectBtn.disabled = true;
    showLoading('正在识别文本框和切割线...');

    try {
        const response = await fetch('/api/detect', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ hash: state.imageHash })
        });
        const data = await response.json();

        if (!data.success) {
            throw new Error(data.error);
        }

        state.verticalLines = data.vertical_lines || [];
        state.horizontalLines = data.horizontal_lines || [];
        state.stripHorizontalLines = data.strip_horizontal_lines || [];
        state.boxes = data.boxes || [];
        // 同步主集（识别后=未过滤的原始集）
        masterBoxes = state.boxes.slice();
        persistMasterBoxes();
        state.detectionStale = false;
        detectBtn.classList.remove('btn-detect-stale');

        drawCanvas();
        updateUI();
        hideLoading();
        showToast(`识别完成：${state.boxes.length} 个文本框`);
    } catch (error) {
        hideLoading();
        showToast('识别失败: ' + error.message);
        console.error(error);
    } finally {
        state.isDetecting = false;
        setRotateButtonsDisabled(false);
        detectBtn.disabled = false;
    }
}

function updateRotationDisplay() {
    rotationDisplay.textContent = `${state.cumulativeRotation}°`;
    rotateResetBtn.disabled = state.cumulativeRotation === 0;
}

function markDetectionStale() {
    state.detectionStale = true;
    state.verticalLines = [];
    state.horizontalLines = [];
    state.stripHorizontalLines = [];
    state.boxes = [];
    detectBtn.classList.add('btn-detect-stale');
}

function setRotateButtonsDisabled(disabled) {
    rotateLeftBtn.disabled = disabled;
    rotateRightBtn.disabled = disabled;
    rotateResetBtn.disabled = disabled || state.cumulativeRotation === 0;
}

// ============== 绿色框过滤设置 ==============

// 保存原始（未过滤）的 boxes，用于重置
let masterBoxes = [];

// 直方图竖线可拖动
function initAreaHistogramDrag(canvas) {
    if (!canvas) return;
    let dragging = null;  // 'min' / 'max' / null

    canvas.style.cursor = 'crosshair';

    function getLineX(value) {
        const padding = 4;
        const W = canvas.width;
        const areas = masterBoxes.map(b => b.width * b.height).filter(a => a > 0);
        if (areas.length === 0) return 0;
        const minA = Math.max(1, Math.min(...areas));
        const maxA = Math.max(...areas);
        const logMin = Math.log(minA);
        const logMax = Math.log(maxA);
        const BINS = 30;
        const chartW = W - padding * 2;
        const barW = chartW / BINS;
        const logStep = (logMax - logMin) / BINS;
        const x = padding + ((Math.log(value) - logMin) / logStep) * barW;
        return { x, minA, maxA };
    }

    function xToValue(x) {
        const padding = 4;
        const W = canvas.width;
        const areas = masterBoxes.map(b => b.width * b.height).filter(a => a > 0);
        if (areas.length === 0) return 0;
        const minA = Math.max(1, Math.min(...areas));
        const maxA = Math.max(...areas);
        const logMin = Math.log(minA);
        const logMax = Math.log(maxA);
        const BINS = 30;
        const chartW = W - padding * 2;
        const barW = chartW / BINS;
        const logStep = (logMax - logMin) / BINS;
        const value = Math.exp(logMin + (x - padding) / barW * logStep);
        return Math.round(Math.max(1, Math.min(maxA, value)));
    }

    canvas.addEventListener('mousedown', (e) => {
        const rect = canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const minV = parseInt(document.getElementById('minAreaInput').value, 10) || 0;
        const maxV = parseInt(document.getElementById('maxAreaInput').value, 10) || 0;
        const minX = getLineX(minV).x;
        const maxX = getLineX(maxV).x;
        // 选更近的那条（阈值 30px）
        const distMin = Math.abs(x - minX);
        const distMax = Math.abs(x - maxX);
        const THRESHOLD = 30;
        if (Math.min(distMin, distMax) > THRESHOLD) return;
        dragging = distMin <= distMax ? 'min' : 'max';
        e.preventDefault();
    });

    document.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        const rect = canvas.getBoundingClientRect();
        const x = Math.max(0, Math.min(canvas.width, e.clientX - rect.left));
        const value = xToValue(x);
        const targetId = dragging === 'min' ? 'minAreaInput' : 'maxAreaInput';
        const input = document.getElementById(targetId);
        const sliderId = dragging === 'min' ? 'minAreaSlider' : 'maxAreaSlider';
        input.value = value;
        document.getElementById(sliderId).value = value;
        // 维持 min <= max
        const minV = parseInt(document.getElementById('minAreaInput').value, 10);
        const maxV = parseInt(document.getElementById('maxAreaInput').value, 10);
        if (dragging === 'min' && minV > maxV) {
            document.getElementById('maxAreaInput').value = minV;
            document.getElementById('maxAreaSlider').value = minV;
        } else if (dragging === 'max' && maxV < minV) {
            document.getElementById('minAreaInput').value = maxV;
            document.getElementById('minAreaSlider').value = maxV;
        }
        updateFilterStats();
        renderAreaHistogram();
    });

    document.addEventListener('mouseup', () => {
        dragging = null;
    });
}

function initGreenBoxModal() {
    const modal = document.getElementById('greenBoxModal');
    const dialog = document.getElementById('greenBoxDialog');
    const header = document.getElementById('greenBoxHeader');
    if (!modal || !dialog || !header) return;

    // 关闭按钮（点 × 或取消）
    modal.querySelectorAll('[data-close="modal-close"]').forEach(btn => {
        btn.addEventListener('click', () => { modal.style.display = 'none'; });
    });

    // 拖动：从 header 拖动整个 dialog
    initModalDrag(dialog, header);

    // 直方图竖线可拖动
    const histogram = document.getElementById('areaHistogram');
    if (histogram) initAreaHistogramDrag(histogram);

    // 8 个方向的 resize 手柄
    modal.querySelectorAll('.resize-handle').forEach(handle => {
        const dirs = Array.from(handle.classList)
            .find(c => ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'].includes(c.replace('resize-handle.', '').replace('handle-', '')))
            || handle.classList[1];  // 第二类名是方向
        initModalResize(dialog, handle, handle.classList[1]);
    });

    // 模式切换
    modal.querySelectorAll('input[name="filterMode"]').forEach(radio => {
        radio.addEventListener('change', updateFilterMode);
    });

    // 滑块与输入框联动（每个滑块 id 对应同名 input）
    const pairs = [
        ['minAreaSlider', 'minAreaInput'],
        ['maxAreaSlider', 'maxAreaInput'],
        ['minWSlider', 'minWInput'],
        ['maxWSlider', 'maxWInput'],
        ['minHSlider', 'minHInput'],
        ['maxHSlider', 'maxHInput'],
    ];
    pairs.forEach(([sliderId, inputIdId]) => {
        const slider = document.getElementById(sliderId);
        const input = document.getElementById(inputIdId);
        if (!slider || !input) return;
        const sync = (source) => {
            return () => {
                const val = Math.max(0, parseInt(source.value, 10) || 0);
                if (source === slider) {
                    input.value = val;
                } else {
                    slider.value = val;
                }
                updateFilterStats();
                // 面积滑块变化时重绘直方图（min/max 边界线）
                if (sliderId.startsWith('minArea') || sliderId.startsWith('maxArea') ||
                    sliderId.startsWith('minW') || sliderId.startsWith('maxW')) {
                    renderAreaHistogram();
                }
            };
        };
        slider.addEventListener('input', sync(slider));
        input.addEventListener('input', sync(input));
    });

    // 重置 / 应用 按钮
    document.getElementById('filterResetBtn').addEventListener('click', resetGreenBoxFilter);
    document.getElementById('filterApplyBtn').addEventListener('click', applyGreenBoxFilter);
}

// 拖动：从 header 拖动整个 dialog
function initModalDrag(dialog, handle) {
    let startX, startY, origLeft, origTop;
    handle.addEventListener('mousedown', (e) => {
        if (e.target.tagName === 'BUTTON') return;  // 不要拦截关闭按钮
        e.preventDefault();
        startX = e.clientX;
        startY = e.clientY;
        const rect = dialog.getBoundingClientRect();
        origLeft = rect.left;
        origTop = rect.top;
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
    function onMove(e) {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        const newLeft = Math.max(0, origLeft + dx);
        const newTop = Math.max(0, origTop + dy);
        dialog.style.left = newLeft + 'px';
        dialog.style.top = newTop + 'px';
        dialog.style.right = 'auto';  // 解除 right 锚定，让 left/top 完全控制
    }
    function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
    }
}

// Resize：根据手柄方向调整 dialog 的 left/top/width/height
function initModalResize(dialog, handle, dir) {
    let startX, startY, origLeft, origTop, origW, origH;
    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        e.stopPropagation();  // 不冒泡到 header 的拖动
        startX = e.clientX;
        startY = e.clientY;
        const rect = dialog.getBoundingClientRect();
        origLeft = rect.left;
        origTop = rect.top;
        origW = rect.width;
        origH = rect.height;
        document.addEventListener('mousemove', onMove);
        document.addEventListener('mouseup', onUp);
    });
    function onMove(e) {
        const dx = e.clientX - startX;
        const dy = e.clientY - startY;
        const minW = 360, minH = 320;
        let newLeft = origLeft, newTop = origTop;
        let newW = origW, newH = origH;
        if (dir.includes('e')) newW = Math.max(minW, origW + dx);
        if (dir.includes('s')) newH = Math.max(minH, origH + dy);
        if (dir.includes('w')) {
            newW = Math.max(minW, origW - dx);
            newLeft = origLeft + (origW - newW);
        }
        if (dir.includes('n')) {
            newH = Math.max(minH, origH - dy);
            newTop = origTop + (origH - newH);
        }
        dialog.style.left = newLeft + 'px';
        dialog.style.top = newTop + 'px';
        dialog.style.width = newW + 'px';
        dialog.style.height = newH + 'px';
        dialog.style.right = 'auto';
        dialog.style.bottom = 'auto';
    }
    function onUp() {
        document.removeEventListener('mousemove', onMove);
        document.removeEventListener('mouseup', onUp);
    }
}

function openGreenBoxModal() {
    const modal = document.getElementById('greenBoxModal');
    const dialog = document.getElementById('greenBoxDialog');
    if (!modal || !dialog) return;

    // 只在首次打开时初始化主集（避免被过滤后的 state.boxes 覆盖）
    // 这样放宽过滤条件时，仍能从完整主集中筛选
    if (masterBoxes.length === 0) {
        masterBoxes = state.boxes.slice();
        persistMasterBoxes();
    }

    // 自适应滑块范围
    autoAdjustRanges();
    updateFilterStats();

    // 初始化位置和大小（仅首次，之后保持用户调整的位置）
    if (!dialog.dataset.initialized) {
        const w = 560;
        const h = 453;
        const headerH = document.querySelector('.header').getBoundingClientRect().height;
        // 右边距离屏幕右边 20px，顶部距离 header 底边紧贴
        dialog.style.left = (window.innerWidth - w - 20) + 'px';
        dialog.style.top = headerH + 'px';
        dialog.style.width = w + 'px';
        dialog.style.height = h + 'px';
        dialog.dataset.initialized = '1';
    }

    modal.style.display = 'block';
}

function updateFilterMode() {
    const mode = document.querySelector('input[name="filterMode"]:checked').value;
    document.getElementById('filterModeArea').style.display = mode === 'area' ? 'block' : 'none';
    document.getElementById('filterModeDims').style.display = mode === 'dims' ? 'block' : 'none';
    updateFilterStats();
}

// 自动调整滑块范围（基于当前 boxes 的实际值）
function autoAdjustRanges() {
    const boxes = state.boxes || [];
    if (boxes.length === 0) return;

    let minArea = Infinity, maxArea = 0;
    let minW = Infinity, maxW = 0;
    let minH = Infinity, maxH = 0;
    for (const b of boxes) {
        const area = b.width * b.height;
        if (area < minArea) minArea = area;
        if (area > maxArea) maxArea = area;
        if (b.width < minW) minW = b.width;
        if (b.width > maxW) maxW = b.width;
        if (b.height < minH) minH = b.height;
        if (b.height > maxH) maxH = b.height;
    }
    // 滑块上下限 = 数据实际范围（用户要求"在当前分布里面"）
    const areaLo = Math.max(1, minArea);
    const areaHi = maxArea;
    const wLo = Math.max(1, minW);
    const wHi = maxW;
    const hLo = Math.max(1, minH);
    const hHi = maxH;

    // 设置上下限
    setRangeBounds('minAreaSlider', 'minAreaInput', areaLo, areaHi);
    setRangeBounds('maxAreaSlider', 'maxAreaInput', areaLo, areaHi);
    setRangeBounds('minWSlider', 'minWInput', wLo, wHi);
    setRangeBounds('maxWSlider', 'maxWInput', wLo, wHi);
    setRangeBounds('minHSlider', 'minHInput', hLo, hHi);
    setRangeBounds('maxHSlider', 'maxHInput', hLo, hHi);

    // 优先加载上次保存的设置
    const saved = loadGreenBoxSettings();
    if (saved) {
        // 模式
        const modeRadio = document.querySelector(`input[name="filterMode"][value="${saved.mode || 'area'}"]`);
        if (modeRadio) {
            modeRadio.checked = true;
            updateFilterMode();
        }
        // 数值（先 clamp 到当前上下限）
        const setVal = (id, v) => {
            const el = document.getElementById(id);
            if (!el) return;
            const lo = parseInt(el.min, 10) || 0;
            const hi = parseInt(el.max, 10) || v;
            el.value = Math.max(lo, Math.min(hi, v));
        };
        setVal('minAreaInput', saved.minArea);
        setVal('maxAreaInput', saved.maxArea);
        setVal('minWInput',   saved.minW);
        setVal('maxWInput',   saved.maxW);
        setVal('minHInput',   saved.minH);
        setVal('maxHInput',   saved.maxH);
    } else {
        // 默认全选（范围 = 数据范围，不过滤）
        const setVal = (id, v) => { document.getElementById(id).value = v; };
        setVal('minAreaInput', areaLo);
        setVal('maxAreaInput', areaHi);
        setVal('minWInput', wLo);
        setVal('maxWInput', wHi);
        setVal('minHInput', hLo);
        setVal('maxHInput', hHi);
    }

    // 同步滑块显示（input → slider）
    ['minArea', 'maxArea', 'minW', 'maxW', 'minH', 'maxH'].forEach(k => {
        const input = document.getElementById(`${k}Input`);
        const slider = document.getElementById(`${k}Slider`);
        if (input && slider) slider.value = input.value;
    });

    // 渲染面积直方图（辅助选择阈值）
    renderAreaHistogram();
}

// localStorage 读写
// 渲染面积分布直方图（辅助用户选择阈值）
// X 轴 = 数据实际范围，对数分箱避免被超大值主导
function renderAreaHistogram() {
    const canvas = document.getElementById('areaHistogram');
    if (!canvas || masterBoxes.length === 0) return;
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    ctx.clearRect(0, 0, W, H);

    // 数据范围（与 autoAdjustRanges 一致）
    const areas = masterBoxes.map(b => b.width * b.height).filter(a => a > 0);
    if (areas.length === 0) return;
    const minA = Math.max(1, Math.min(...areas));
    const maxA = Math.max(...areas);
    if (maxA <= minA) return;

    const BINS = 30;
    // 对数分箱：[minA, maxA] 等比分在 log 空间
    const logMin = Math.log(minA);
    const logMax = Math.log(maxA);
    const logStep = (logMax - logMin) / BINS;
    const bins = new Array(BINS).fill(0);
    const binEdges = new Array(BINS + 1);
    for (let i = 0; i <= BINS; i++) {
        binEdges[i] = Math.exp(logMin + i * logStep);
    }
    for (const a of areas) {
        let idx = Math.floor((Math.log(a) - logMin) / logStep);
        if (idx >= BINS) idx = BINS - 1;
        if (idx < 0) idx = 0;
        bins[idx]++;
    }
    const maxCount = Math.max(...bins, 1);

    // 当前过滤区间
    const minV = parseInt(document.getElementById('minAreaInput').value, 10) || minA;
    const maxV = parseInt(document.getElementById('maxAreaInput').value, 10) || maxA;

    // 画柱子
    const padding = 4;
    const chartW = W - padding * 2;
    const chartH = H - padding * 2;
    const barW = chartW / BINS;
    for (let i = 0; i < BINS; i++) {
        const x = padding + i * barW;
        const h = bins[i] / maxCount * chartH;
        const y = H - padding - h;
        const binStart = binEdges[i];
        const binEnd = binEdges[i + 1];
        const inRange = binEnd >= minV && binStart <= maxV;
        ctx.fillStyle = inRange ? '#4a90a4' : '#c8d0d8';
        ctx.fillRect(x + 1, y, Math.max(1, barW - 2), h);
    }

    // 标注：min 边界线（绿色，过滤掉面积小于此值的框）
    ctx.strokeStyle = '#27ae60';
    ctx.lineWidth = 2;
    if (minV > minA && minV < maxA) {
        const xMin = padding + ((Math.log(minV) - logMin) / logStep) * barW;
        ctx.beginPath(); ctx.moveTo(xMin, 0); ctx.lineTo(xMin, H); ctx.stroke();
    }
    // 标注：max 边界线（红色，过滤掉面积大于此值的框）
    ctx.strokeStyle = '#e74c3c';
    if (maxV > minA && maxV < maxA) {
        const xMax = padding + ((Math.log(maxV) - logMin) / logStep) * barW;
        ctx.beginPath(); ctx.moveTo(xMax, 0); ctx.lineTo(xMax, H); ctx.stroke();
    }
}

function saveGreenBoxSettings() {
    const get = id => parseInt(document.getElementById(id).value, 10) || 0;
    const mode = document.querySelector('input[name="filterMode"]:checked').value;
    const data = {
        mode,
        minArea: get('minAreaInput'),
        maxArea: get('maxAreaInput'),
        minW: get('minWInput'),
        maxW: get('maxWInput'),
        minH: get('minHInput'),
        maxH: get('maxHInput'),
    };
    localStorage.setItem('greenBoxSettings', JSON.stringify(data));
}

function loadGreenBoxSettings() {
    try {
        const s = localStorage.getItem('greenBoxSettings');
        return s ? JSON.parse(s) : null;
    } catch (e) {
        return null;
    }
}

// 主集（未过滤）按 imageHash 持久化
function masterKey() {
    return 'greenBoxMaster_' + (state.imageHash || 'default');
}
function persistMasterBoxes() {
    try {
        localStorage.setItem(masterKey(), JSON.stringify(masterBoxes));
    } catch (e) {
        console.warn('masterBoxes 持久化失败：', e);
    }
}
function loadMasterBoxes() {
    try {
        const s = localStorage.getItem(masterKey());
        return s ? JSON.parse(s) : null;
    } catch (e) {
        return null;
    }
}

function setRangeBounds(sliderId, inputId, min, max) {
    const s = document.getElementById(sliderId);
    const i = document.getElementById(inputId);
    if (s) { s.min = min; s.max = max; }
    if (i) { i.min = min; }
}

// 当前过滤逻辑：基于模式 + 输入值过滤 masterBoxes
function getCurrentFilterPredicate() {
    const mode = document.querySelector('input[name="filterMode"]:checked').value;
    if (mode === 'area') {
        const minA = parseInt(document.getElementById('minAreaInput').value, 10) || 0;
        const maxA = parseInt(document.getElementById('maxAreaInput').value, 10) || Infinity;
        return b => {
            const a = b.width * b.height;
            return a >= minA && a <= maxA;
        };
    } else {
        const minW = parseInt(document.getElementById('minWInput').value, 10) || 0;
        const maxW = parseInt(document.getElementById('maxWInput').value, 10) || Infinity;
        const minH = parseInt(document.getElementById('minHInput').value, 10) || 0;
        const maxH = parseInt(document.getElementById('maxHInput').value, 10) || Infinity;
        return b => b.width >= minW && b.width <= maxW && b.height >= minH && b.height <= maxH;
    }
}

function updateFilterStats() {
    if (masterBoxes.length === 0) return;
    const pred = getCurrentFilterPredicate();
    const kept = masterBoxes.filter(pred).length;
    document.getElementById('filterTotalCount').textContent = masterBoxes.length;
    document.getElementById('filterKeptCount').textContent = kept;
    document.getElementById('filterRemovedCount').textContent = masterBoxes.length - kept;
}

function applyGreenBoxFilter() {
    const pred = getCurrentFilterPredicate();
    const filtered = masterBoxes.filter(pred);
    state.boxes = filtered;
    saveGreenBoxSettings();
    drawCanvas();
    updateUI();
    // 不自动关闭模态，用户可看到对比效果
    showToast(`已过滤：保留 ${filtered.length} / ${masterBoxes.length} 个绿框`);
}

function resetGreenBoxFilter() {
    // 重置 = 直接调用开始识别逻辑
    handleDetect();
}
