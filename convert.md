# 重构任务：把绘制迁移到 Web UI，打印层独立，支持 Mac + Android

## 背景

现有项目 PrintTheShot 是一个 Python 服务端，负责：
1. 接收 DE1 上传的 shot JSON
2. 用 Pillow 在服务端渲染曲线图（PNG）
3. 调系统打印（macOS/Linux 用 lpr，Windows 用 ctypes）
4. 提供 Web UI 展示历史、统计、设置

目标：把「绘制」从 Python 服务端迁移到浏览器端（Web UI），让打印层成为独立模块。
本期只实现 Mac 和 Android 两个平台，其他平台后续再说。

---

## 核心架构原则

绘制和打印必须彻底分离。

- 绘制：完全在浏览器里用 canvas 完成，服务端不再生成 PNG。
- 打印：定义一个统一的打印接口，每个平台各自实现。上层业务代码只调用接口，不知道底下是 Mac 还是 Android。

服务端退化为「数据中转 + 打印调度」，不再承担渲染职责。

---

## 目标架构

Web UI (浏览器 / WebView)
- 接收 shot JSON
- 用 canvas 绘制曲线
- 展示历史 / 统计 / 设置
- 需要打印时 → 调打印接口
        ↓ HTTP / WebSocket
Python 服务端（数据中转 + 打印调度）
- 接收上传的 JSON
- 存储历史 (index.json)
- 提供 REST API
- 打印接口（平台无关）
        ↓
Mac 打印适配 (lpr/lp)  |  Android 打印适配 (蓝牙 ESC/POS)

---

## 具体任务

### 任务 1：Web UI 端实现 Canvas 绘制

输入：shot JSON（格式与现有 sample_shots/ 一致）

输出：在 canvas 上绘制曲线图，包含：
- 压力曲线、流量曲线、温度曲线（按现有 PNG 的视觉样式）
- 坐标轴、刻度、标签
- 时间轴
- 图例（如果现有版本有）

要求：
- 纯前端 JS，不依赖服务端渲染
- 支持 Retina / 高 DPI 屏幕
- 提供 renderShotToCanvas(shotJson, canvasElement) 函数，供展示和打印复用
- 提供 canvasToBitmap(canvas) 函数，输出 1-bit 位图数据（用于打印）

参考：现有 Python 代码里 Pillow 的绘制逻辑（坐标计算、缩放、颜色映射）可以直接翻译成 JS，数学部分不变。

交付：
- web/render.js：Canvas 绘制逻辑
- web/render.test.html：一个独立测试页，加载 sample JSON 就能看到绘制结果

---

### 任务 2：定义打印接口

接口设计（服务端暴露）：

POST /api/print
Body: {
  "bitmap": "<base64 编码的 1-bit 位图>",
  "width": 384,
  "height": 600,
  "printer": "default" | "<printer_id>"
}
Response: { "success": true/false, "message": "..." }

要求：
- 服务端收到位图后，交给对应平台的打印适配器
- 服务端不关心位图怎么来的，也不做任何图像处理
- 打印适配器是一个可插拔的模块，按平台加载

---

### 任务 3：Mac 打印适配

实现方式：
- 用 lpr / lp 命令（复用现有代码）
- 把收到的位图写入临时文件（PBM 或 RAW 格式），然后调 lp -d <printer> -o raw <file>
- 或者用 CUPS 的 Custom.80x180mm 纸张设置（参考现有 README）

要求：
- 封装成 MacPrinter 类，实现统一打印接口
- 支持选择打印机（枚举 CUPS 里的打印机列表）
- 错误处理：打印机不存在、纸张不匹配、打印失败

交付：
- printers/mac_printer.py

---

### 任务 4：Android 打印适配

实现方式：
- 用蓝牙 ESC/POS 协议直接驱动打印机
- 推荐方案：在 Android 端用 Capacitor 或 Cordova 打包 Web UI，打印用原生插件
- 或者：Web UI 跑在 Android WebView 里，通过 JavaScript Bridge 调原生蓝牙打印

打印流程：
1. 扫描并配对蓝牙打印机（经典蓝牙 SPP 或 BLE）
2. 建立连接
3. 把 1-bit 位图按 ESC/POS 指令打包（GS v 0 光栅位图指令）
4. 发送字节流
5. 关闭连接（或保持长连接）

要求：
- 封装成 AndroidPrinter，实现统一打印接口
- 处理 Android 12+ 的蓝牙权限（BLUETOOTH_CONNECT、BLUETOOTH_SCAN）
- 处理后台服务保活（前台服务 + 通知）
- 支持打印机选择（保存已配对设备列表）

推荐库：
- Capacitor 插件：@capacitor-community/bluetooth-le 或现成的 ESC/POS 插件
- 如果自己写原生：Android BluetoothAdapter + BluetoothSocket + ESC/POS 指令拼装

交付：
- Android 原生打印模块（Java/Kotlin 或 Capacitor 插件）
- 对应的 JS 调用封装

---

### 任务 5：服务端重构

改动：
- 删除 Pillow 渲染逻辑（render 相关代码）
- 删除 --render CLI 选项，或改为「只输出 JSON 给前端」
- 保留：上传接收、历史存储、统计 API、Web UI 模板
- 新增：/api/print 端点，接收位图并调度打印
- 打印适配器按平台加载（sys.platform 判断）

要求：
- 服务端启动时检测平台，加载对应的打印适配器
- 如果没有可用适配器，打印接口返回明确错误
- 保持现有 API 兼容（/upload、/api/status、/api/shots 等不变）

---

### 任务 6：Web UI 重构

改动：
- 移除对服务端 PNG 的依赖（不再从 /images/*.png 加载）
- 用 Canvas 直接绘制曲线
- 历史列表：每个 shot 用 Canvas 缩略图展示
- 大图查看：点击缩略图，用大 Canvas 展示
- 打印按钮：调 /api/print，传当前 Canvas 的位图数据

要求：
- 保持现有 UI 布局和交互（日期筛选、分页、统计）
- 中英文双语支持不变
- 响应式，适配平板和手机

---

## 目录结构调整（建议）

print_the_shot_server.py       # 主服务（数据中转 + 打印调度）
printers/
  __init__.py                  # 打印适配器加载逻辑
  base.py                      # 统一打印接口定义
  mac_printer.py               # Mac 实现
  android_printer.py           # Android 实现（或桥接到原生）
web/
  index.html                   # Web UI 模板
  render.js                    # Canvas 绘制逻辑
  app.js                       # UI 交互逻辑
  style.css
plugin/
  plugin.tcl                   # DE1 插件（不变）
sample_shots/                  # 示例数据
shots_data/                    # 运行时数据

---

## 验收标准

1. Mac 端：
   - 打开 Web UI，能看到历史 shot 的 Canvas 缩略图
   - 点击某个 shot，能看到大图
   - 点打印，能通过 lpr 打印到配置的 80mm 热敏打印机
   - 服务端不再生成 PNG 文件

2. Android 端：
   - Web UI 在 Android WebView / Capacitor 里正常运行
   - 能扫描并连接蓝牙热敏打印机
   - 点打印，能通过蓝牙 ESC/POS 打印出曲线图
   - 后台服务能保持运行（前台服务 + 通知）

3. 通用：
   - 服务端启动开销明显下降（不再加载 Pillow）
   - 同一份 Canvas 绘制代码在 Mac 和 Android 上渲染结果一致

---

## 注意事项

- 不要一次做太多：先完成 Mac 端，跑通「Canvas 绘制 → 打印」闭环，再动 Android。
- 打印接口要早定义：接口定好后，Mac 和 Android 可以并行开发。
- 位图格式统一：建议用 1-bit 单色位图，宽度 384（80mm 打印机常见宽度），高度根据内容自适应。
- 保留回滚能力：改动前打个 tag，方便对比新旧版本。
- Agent 提示：让 Agent 先读现有 Python 代码，理解 Pillow 的绘制逻辑，再翻译成 JS。数学部分（坐标、缩放）可以直接复用，API 调用需要重写。

---

## 给 Agent 的额外提示

- 现有代码里 print_the_shot_server.py 的渲染逻辑是核心参考，重点看 Pillow 的 ImageDraw 调用。
- Canvas 的 2D API 和 Pillow 的 ImageDraw 概念相似，翻译成本低。
- ESC/POS 位图指令格式：GS v 0 m xL xH yL yH d1...dk，其中 m=0 表示正常模式，xL/xH 是宽度低/高字节，yL/yH 是高度低/高字节，d1...dk 是位图数据。
- 如果 Android 端用 Capacitor，打印插件可以用现成的，不要从零写蓝牙协议栈。
- 先写测试：用 sample_shots/ 里的 JSON，在浏览器里验证 Canvas 绘制结果和现有 PNG 一致，再继续。
