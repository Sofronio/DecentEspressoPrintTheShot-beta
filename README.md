# PrintTheShot Beta

> ⚠️ **测试状态说明**:本项目仅完成了软件层面的开发与模拟数据测试,**尚未连接真实 DECENT 咖啡机(DE1)与热敏打印机进行实机验证**。上传端点的数据格式基于真实 DE1 导出的 JSON,打印链路沿用原版的 lpr/CUPS 方案,但实机打印效果与插件交互仍需真机确认。

## 截图

**Web 管理界面**

![Web 管理界面](screenshots/webui.png)

**打印效果**(模拟数据渲染,供排版参考)

![打印效果](screenshots/print_sample_printed.png)

---

DECENT 咖啡机冲泡数据打印服务器的**轻量重构版**。兼容原版(DecentEspressoPrintTheShot)的插件、上传端点和管理界面。

## 特性对照(Beta vs v1.6)

| 特性 | v1.6 | Beta |
|---|---|---|
| 依赖 | matplotlib + numpy + pillow | **仅 pillow** |
| 中文字体 | 依赖系统字体(常缺) | **内置 Noto Sans CJK**(跨平台一致) |
| 打包体积 | ~80MB | ~30MB |
| 启动速度 | ~2.5s(matplotlib 导入) | ~0.3s |
| Web 管理界面 | 内嵌 HTML 字符串 | **独立模板 + 中英双语** |
| 历史数据 | 重启即丢 | **持久化(index.json),重启不丢** |
| 数据浏览 | 列表 | **日期筛选 + 前后日切换 + 翻页(9/18/36 每页)** |
| 统计 | 无 | **三列分布:日期 / 冲煮方案 / 豆子** |
| 大图/下载 | 无 | **点图看大图,JSON/PNG 一键下载** |
| 插件分发 | 单一下载 | **本地版 + GitHub 版 + TXT 版(蓝牙发送)** |
| 在线更新 | 无 | **服务自更新(自动备份)** |
| 三平台打包 | 手动 | **GitHub Actions 自动构建** |
| 已知缺陷修复 | 空 `by_weight` 崩溃;同秒上传撞车 | 已修复 |

## 快速开始

```bash
# 源码运行(仅需 pillow)
pip install -r scripts/requirements.txt
python print_the_shot_server.py            # 默认端口 8000
```

打开 `http://localhost:8000` 使用管理界面。

## 使用指南(Web 界面)

- **状态卡**:运行状态、接收数据数、打印开关、豆子信息开关
- **最近数据**:默认显示今天的冲泡记录,支持
  - 日期下拉 + ◀ ▶ 前后日切换
  - 每页 9 / 18 / 36 条(记忆选择),翻页浏览
  - 点缩略图**查看大图**;每张卡片可**打印 / 下载 JSON / 下载 PNG**
  - 卡片标题 = `豆子 - 冲煮方案`
- **统计数据**(一行三列):
  - 按日期分布:围绕日均值的发散条形图(蓝=高于均值,橙=低于均值)
  - 冲煮方案分布、豆子分布
- **上传区**:拖拽 JSON 文件即可手动触发渲染 + 打印
- **插件区**:本地版(匹配当前版本)/ GitHub 最新版 / **TXT 版**(蓝牙发送到平板时安卓端常拒绝 .tcl 扩展名,`tcl.txt` 可正常传输)
- **服务更新**:检查更新 → 从 GitHub 更新(自动备份旧版到 `backup/`)

## 端到端部署(DE1 → 打印)

1. 在运行本服务的电脑上启动服务器,记下本机 IP(启动横幅会显示)
2. 从管理界面**插件区**下载插件(`plugin.tcl` 或 TXT 版改名)
3. 将插件复制到 DE1 平板的 SD 卡:`/de1plus/plugins/print_the_shot/plugin.tcl`
4. 重启 DE1App,进入 设置 → 插件 → Print The Shot,配置:
   - 服务器网址:`你的电脑IP:8000`(例如 `192.168.1.100:8000`)
   - 服务器端点:`upload`
   - 启用 HTTP
5. 萃取完成后数据自动上传 → 图表自动渲染 → 自动打印

## 仅渲染测试(不启动服务器)

```bash
python print_the_shot_server.py --render shot.json out.png
# 同时生成 out_print.bmp(打印用二值图)
```

样例数据在 `sample_shots/`。想生成一个月(约 1670 条)的模拟数据:

```bash
python scripts/generate_test_data.py 31 50   # 天数 每天杯数
```

## 更新机制

- **检查更新**:管理界面「服务更新」卡片 → 检查更新,对比本地/远程版本
- **从 GitHub 更新**:下载仓库 ZIP → 校验内容 → **自动备份旧版到 `backup/<时间戳>/`** → 替换服务器程序/网页模板/插件 → **重启服务器生效**
- 打包版不支持在线自更新(程序在 exe 内),请从 GitHub Releases 下载新安装包

## 打包(单文件可执行)

| 平台 | 命令 |
|---|---|
| macOS | `./scripts/build_macos.sh` → `dist/PrintTheShot` + `.app` |
| Linux | `./scripts/build_linux.sh` → `dist/PrintTheShot` |
| Windows | `scripts\build_windows.bat` → `dist\PrintTheShot.exe` |

**推荐**:直接打 tag 推送到 GitHub,Actions 自动构建三平台二进制并挂到 Releases:

```bash
git tag v2.0-beta.1 && git push origin v2.0-beta.1
```

公开仓库的 GitHub Actions **完全免费**,无需本地分发安装包。

## CLI 参数

| 参数 | 说明 |
|---|---|
| `--port N` | 监听端口(默认 8000) |
| `--render json [png]` | 仅渲染图表,不启动服务器 |
| `--no-print` | 启动时禁用自动打印 |

## 目录结构

```
print_the_shot_server.py    # 主程序(HTTP + 渲染 + 打印 + 更新)
web/index.html              # 管理界面模板({{LANG}}/{{VERSION}} 占位)
fonts/                      # 内置字体(Noto Sans CJK SC, SIL OFL)
plugin/plugin.tcl           # DE1 插件(与原版兼容)
scripts/                    # 打包脚本 + PyInstaller spec + 测试数据生成器
sample_shots/               # 样例数据
screenshots/                # 文档截图
.github/workflows/          # 三平台 CI
shots_data/                 # 运行时生成:上传数据 + index.json(历史索引)
shots_images/               # 运行时生成:图表 PNG
backup/                     # 运行时生成:更新前的备份
```

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/upload?machine_id=...` | JSON/multipart 上传,自动渲染+打印 |
| GET | `/api/status` | 服务器状态 |
| GET | `/api/shots[?date=YYYY-MM-DD]` | 数据列表(按日期筛选),含可用日期 |
| GET | `/api/stats` | 统计(总量/日期分布/方案/豆子) |
| GET | `/api/queue` | 打印队列 |
| GET | `/api/settings` `/api/language` | 设置与语言 |
| POST | `/api/print` | 手动打印 `{filename}` |
| POST | `/api/settings/beaninfo` `/api/settings/print` | 开关豆子信息/打印 |
| POST | `/api/language` | 切换语言 |
| DELETE | `/api/queue` | 清空队列 |
| GET | `/images/*.png` `/download/json/*` | 图片与 JSON 下载 |
| GET | `/plugin/plugin.tcl` `/plugin/plugin.tcl.txt` | 插件下载(TXT 版供蓝牙) |
| GET | `/api/update/check` | 检查更新 |
| POST | `/api/update` `/api/plugin/update` | 更新服务/插件 |

## 打印

- **macOS/Linux**:走 `lpr`/`lp`(CUPS),需配置 80mm 热敏打印机,纸张 `Custom.80x180mm`
- **Windows**:纯 ctypes 调用系统打印 API(无 pywin32 依赖),使用系统默认打印机

## 故障排查

| 现象 | 处理 |
|---|---|
| 图表不生成 | 检查 `shots_data/` 是否有上传的 JSON;日志会打印错误 |
| 打印没反应 | `echo test \| lp` 验证 CUPS;确认打印机纸张设为 80x180mm |
| 历史数据丢了 | 检查 `shots_data/index.json` 是否存在(上传后自动写入) |
| 更新后没变化 | 更新需要**重启服务器**才生效 |
| 端口被占用 | `--port` 换端口 |
| 网页空白 | 强制刷新(Cmd+Shift+R);F12 看控制台报错 |

## 许可

GPLv3(与原版一致);内置字体 Noto Sans CJK 为 SIL Open Font License 1.1,可自由再分发。
