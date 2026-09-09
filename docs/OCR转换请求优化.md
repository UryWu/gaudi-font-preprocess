# OCR 每张卡发简转繁请求导致后端卡死——优化记录

> /annotate 自动标注功能。OCR 跑到 100+ 张后，`convert_to_traditional` /
> `save_ocr_annotation` 请求骤停，`ocr_annotations.json` 里 `scaled_0107` 之后
> 的 traditional 全空（只有 worker 直写的 simplified）。
> 此文档记录优化思路与知识点，供后续网络/批量场景参考。

## 1. 现象

```
[OCR c536b3887243] [107/321] scaled_0107.png → 'L' ...
[OCR c536b3887243] [108/321] scaled_0108.png → '益' ...
...（OCR worker 后端继续识别，进度正常）
```

- OCR 前 ~100 张：每张伴随 `POST /api/convert_to_traditional 200` + `POST /api/save_ocr_annotation 200`
- ~100 张后：两个接口调用**骤停**，但 worker 识别 + 前端 poll（`GET /api/ocr_progress`）都正常
- 文件里从 `scaled_0107.png` 往后 traditional = ""（worker 直写只有 simplified）

## 2. 根因（两层叠加）

### 2.1 N+1 请求：每张卡一次简转繁

前端 `applyOcrResult`（每识别成功一张执行）会：

```js
convertSingleToTraditional(char, idx)   // ① 每张发一次 /api/convert_to_traditional
  .then(trad => saveOcrAnnotation(...)) // ② 完成后写盘
```

321 张识别 → 至多 321 次 `convert_to_traditional` + 321 次 `save_ocr_annotation`，
全部短连接并发发出。这本质是 **N+1 问题**——一条任务能用 1 次请求完成的事
拆成了 N 次。

### 2.2 OpenCC 每请求重建转换器（重头）

后端 `convert_to_traditional`：

```python
def convert_to_traditional():
    import opencc
    converter = opencc.OpenCC('s2t')   # ← 每请求都重建！
    result = converter.convert(text)
```

`opencc.OpenCC('s2t')` 初始化会加载并构建整张繁简映射（几十~几百 ms 量级），
这是**重量级一次性成本**，却放在了请求热路径上。高并发时每个请求都在重建
转换器 → 后端线程耗尽 → 新请求排队/超时 → 浏览器积压的 convert 请求一直
pending 不 resolve → 后面的 `.then(saveOcrAnnotation)` 永远不执行 → 这些卡
的传统补不上，只留下 worker 直写的 simplified。

## 3. 修复（三层）

### 3.1 后端：资源复用（模块级缓存）

`opencc.OpenCC` 实例线程安全可复用于所有请求，只初始化一次：

```python
# app.py 模块级，进程启动时初始化一次
try:
    import opencc as _opencc
    _S2T_CONVERTER = _opencc.OpenCC('s2t')
    _T2S_CONVERTER = _opencc.OpenCC('t2s')
except Exception:
    _S2T_CONVERTER = _T2S_CONVERTER = None
```

接口直接用缓存实例，单字符/短文转换毫秒级。这是**无状态服务最常见的性能
坑**：重对象放进请求里重建 vs 进程内复用。

### 3.2 前端：去掉逐张 convert+save

`applyOcrResult` 改为**只填 UI 简化字**。落盘职责明确分层：

- **simplified**：OCR worker 后端每张已直写 `ocr_annotations.json`（见
  commit 34918e1），前端无需再 save
- **traditional**：前端不实时逐张算，交给任务结束一次性批量

好处：不再制造每张 1 个 convert 的并发风暴。

### 3.3 任务结束一次性批量补 + 落盘

OCR `done` 兜底改为异步两步：

1. `loadOcrAnnotations(true)` —— 拉全量补齐 poll 漏应用的空卡；缺繁体的卡
   由 `fillMissingTraditional()` **所有简体拼成一串，1 次接口**批量转繁填框
2. `saveAllTraditional()` —— 把各卡已填繁体**批量** `POST /api/bulk_fill_traditional`
   落盘（只补 traditional 字段，保留 simplified/conf/source）

新增后端 `/api/bulk_fill_traditional/<hash>`，仅更新已有记录的传统字段。

## 4. 优化后的请求量

| | 修复前 | 修复后 |
|---|---|---|
| convert_to_traditional | 每张 1 次（~200+）| done 后 **1 次** |
| save_ocr_annotation（前端）| 每张 1 次 | 0（worker 直写）|

## 5. 思想与知识点

### 5.1 N+1 问题 → 批量化（Batching）

循环里为每个元素各发一次请求是典型 N+1。凡是「对一批同质数据做同一种
处理」，都应合并成一次请求携带整批（一条长文本 / 一组 key），服务器一次
处理再按序分发。本次：`fillMissingTraditional` 把全部缺繁简体拼串一次转，
`saveAllTraditional` 一次写全部 traditional。

### 5.2 重资源进程内复用，别放请求热路径

判断「对象可复用」三问：
- 初始化是否昂贵？（OpenCC 加载映射 → 昂贵）
- 是否有状态？（转换器无请求相关状态 → 可共享）
- 是否线程安全？（OpenCC.convert 线程安全 → 可共享）

三者满足就模块级缓存/连接池。**一个百万级调用的服务，每秒省一次重初始化
就是巨大收益。** 通用类比：数据库连接池、HTTP 客户端复用、模型预加载。

### 5.3 职责分层：谁落盘、谁展示

- **持久化**应尽量由服务端完成（worker 直写），不依赖前端「轮询到才触发」
- **前端**只管展示 + 用户交互；批量收尾（done 兜底）负责把展示内容落盘
- 避免同一数据多处写：worker 写盘 + 前端再 save = 双写冗余 + 双倍请求

### 5.4 异步 Promise 链的隐藏积压

`fetch(...).then(下一步)` 若 fetch 长期 pending（后端过载），后续 `.then`
全部排队。批量操作的「完成回调」（`.then`/`await` 后）依赖每个中间请求
先返回。**减少请求数 = 减少可积压的中间状态点**。

### 5.5 结束时一次性兜底 vs 实时每张

实时每张（逐张 convert）响应即时但并发高；结束后一次性兜底（done 批量）
并发低但中间态稍滞后。取舍：中间态是「卡片繁体框暂空、任务完成立即补」，
可接受；换来的稳定性和大幅降负载是主要收益。**小步实时 vs 批量收尾要按
中间态容忍度权衡。**

## 6. 相关 commit

- `34918e1` OCR worker 每张识别完直接写 ocr_annotations.json（不再依赖前端 poll）
- `30be0ce` fix: OCR 每张卡发简转繁请求导致 100 张后后端卡死，传统空（本次）

## 7. 验证

1. 跑完整 OCR 到 done
2. 观察后端日志：全程**不再每张**一个 `convert_to_traditional`；
   done 后**1 次**批量转换 + 落盘
3. 检查 `ocr_annotations.json`：所有 `simplified` 非空的记录
   `traditional` 也非空
4. 刷新 `/annotate`：读 json 填两框，不再触发转换请求
