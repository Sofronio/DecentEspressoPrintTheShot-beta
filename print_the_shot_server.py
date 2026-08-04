#!/usr/bin/env python3
"""
PrintTheShot Beta - lightweight server
======================================
Improvements over v1.6:
  * No matplotlib/numpy — charts drawn directly with PIL (ImageDraw)
  * Bundled Noto Sans CJK font — consistent output everywhere, no system font discovery
  * Web UI moved to a standalone template (web/index.html)
  * Smaller/faster PyInstaller packages; automated 3-platform builds via GitHub Actions

Dependencies: pillow only
Run: python print_the_shot_server.py            (default port 8000)
     python print_the_shot_server.py --render shot.json out.png   (render only, for testing)
"""

import os
import sys
import json
import time
import platform
import threading
import subprocess
import argparse
import http.server
import socketserver
import urllib.parse
from datetime import datetime
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

VERSION = "2.0-beta"
DATA_DIR = "shots_data"
IMAGE_DIR = "shots_images"
PRINT_ENABLED = True
BEAN_INFO_ENABLED = True
MAX_USERS = 5
received_shots = []
shots_lock = threading.Lock()
print_jobs = []  # 内存打印队列 / in-memory print queue
server_start_time = datetime.now()

# ---------------------------------------------------------------------------
# 资源路径:兼容源码运行与 PyInstaller 打包运行 Resource paths: work both from source and from PyInstaller bundles
# ---------------------------------------------------------------------------
def resource_path(rel):
    if getattr(sys, "frozen", False):
        base = sys._MEIPASS
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, rel)

FONT_PATH = resource_path(os.path.join("fonts", "NotoSansCJKsc-Regular.otf"))
WEB_INDEX = resource_path(os.path.join("web", "index.html"))
PLUGIN_TCL = resource_path(os.path.join("plugin", "plugin.tcl"))  # bundle内(只读)
PLUGIN_GITHUB_URL = "https://raw.githubusercontent.com/Sofronio/DecentEspressoPrintTheShot/main/plugin/plugin.tcl"
RAW_SERVER_URL = "https://raw.githubusercontent.com/Sofronio/DecentEspressoPrintTheShot/main/print_the_shot_server.py"
GITHUB_ZIP_URL = "https://codeload.github.com/Sofronio/DecentEspressoPrintTheShot/zip/refs/heads/main"


def _version_key(v):
    """'2.0-beta' -> (2, 0);用于比较远端与本地版本"""
    import re
    m = re.match(r"(\d+)\.(\d+)", v or "")
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def perform_update(zip_url, base_dir, lang="zh"):
    """从GitHub仓库ZIP更新整个服务:下载→校验→备份→替换。
    返回 (success, message)。base_dir = 服务器运行目录(源码模式=仓库根)。"""
    import urllib.request, zipfile, io, shutil
    try:
        req = urllib.request.Request(zip_url, headers={"User-Agent": "PrintTheShotBeta"})
        with urllib.request.urlopen(req, timeout=120) as r:
            zdata = r.read()
        zf = zipfile.ZipFile(io.BytesIO(zdata))
        names = zf.namelist()
        root = names[0].split("/")[0] if names else ""

        def get(rel):
            for n in names:
                if n == f"{root}/{rel}" or n.endswith("/" + rel):
                    return zf.read(n)
            return None

        new_server = get("print_the_shot_server.py")
        new_web = get("web/index.html")
        new_plugin = get("plugin/plugin.tcl")
        # 校验 Validation
        if not new_server or b"def main()" not in new_server or b"PrintTheShot" not in new_server:
            return False, ("下载内容异常,已取消" if lang == "zh" else "Downloaded content invalid, aborted")
        if not new_web or b"{{LANG}}" not in new_web:
            return False, ("web模板异常,已取消" if lang == "zh" else "Web template invalid, aborted")

        # 备份(排除fonts:体积大且极少变更) Backup (excluding fonts: large and rarely changes)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(base_dir, "backup", ts)
        os.makedirs(backup_dir, exist_ok=True)
        for rel in ("print_the_shot_server.py", "web/index.html"):
            src = os.path.join(base_dir, rel)
            if os.path.exists(src):
                dst = os.path.join(backup_dir, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
        runtime_plugin = os.path.join(base_dir, "plugin", "plugin.tcl")  # 与base_dir一致,避免测试误写真实路径
        if os.path.exists(runtime_plugin):
            os.makedirs(os.path.join(backup_dir, "plugin"), exist_ok=True)
            shutil.copy2(runtime_plugin, os.path.join(backup_dir, "plugin", "plugin.tcl"))

        # 写入新文件 Write new files
        with open(os.path.join(base_dir, "print_the_shot_server.py"), "wb") as f:
            f.write(new_server)
        with open(os.path.join(base_dir, "web", "index.html"), "wb") as f:
            f.write(new_web)
        if new_plugin and b"print_the_shot" in new_plugin:
            with open(runtime_plugin, "wb") as f:
                f.write(new_plugin)
        msg = (f"更新完成,备份在 backup/{ts}/,请重启服务器生效" if lang == "zh"
               else f"Update complete — backup at backup/{ts}/, restart the server to apply")
        print(f"🔄 {msg}")
        return True, msg
    except Exception as e:
        return False, (f"更新失败: {e}" if lang == "zh" else f"Update failed: {e}")


def plugin_runtime_path():
    """插件运行时路径:优先 CWD/plugin/(可写,支持GitHub更新);
    打包环境下首次运行从bundle复制过去。"""
    runtime_dir = os.path.join(os.getcwd(), "plugin")
    os.makedirs(runtime_dir, exist_ok=True)
    runtime_path = os.path.join(runtime_dir, "plugin.tcl")
    if not os.path.exists(runtime_path) and os.path.exists(PLUGIN_TCL):
        import shutil
        shutil.copy2(PLUGIN_TCL, runtime_path)
    return runtime_path

# ---------------------------------------------------------------------------
# 多语言 / i18n i18n / Localization
# ---------------------------------------------------------------------------
LANGUAGES = {
    "en": {
        "server_title": "PrintTheShot Beta Server v{VERSION}",
        "status_running": "Running",
        "start_time": "Start Time",
        "shots_received": "Shots Received",
        "active_users": "Active Users",
        "max_users": "Max Users",
        "print_enabled": "Printing Enabled",
        "bean_info_enabled": "Bean Info Enabled",
        "print_queue": "Print Queue",
        "enable_print": "Enable Printing",
        "disable_print": "Disable Printing",
        "enable_bean_info": "Enable Bean Info",
        "disable_bean_info": "Disable Bean Info",
        "refresh": "Refresh",
        "clear_queue": "Clear Queue",
        "queue_empty": "Queue is empty",
        "data_upload": "Data Upload",
        "drag_drop": "Drag and drop JSON file here or click to select",
        "recent_data": "Recently Received Data",
        "no_data": "No data available",
        "print": "Print",
        "print_job_sent": "Print job sent",
        "plugin_download": "Download DE1 Plugin",
        "plugin_instructions": "Plugin Installation",
        "plugin_step1": "1. Download plugin.tcl and copy to DE1 tablet:",
        "plugin_step2": "/de1plus/plugins/print_the_shot/plugin.tcl",
        "plugin_step3": "2. Restart DE1App, plugin auto-loads",
        "plugin_step4": "3. Set Server URL to this machine's IP:8000",
        "language": "Language",
        "queue_status": "Queue Status: {count} jobs",
        "chart_pressure": "Pressure (Bar)",
        "chart_flow": "Flow Rate (g/s)",
        "chart_water_flow": "Water Flow",
        "chart_coffee_flow": "Coffee Flow",
        "chart_temperature": "Temp (°C)",
        "chart_time": "Time (s)",
        "chart_date_time": "Date&Time",
        "chart_profile": "Profile",
        "chart_extraction": "Extraction",
        "chart_grinder_temp": "Grind&Temp",
        "chart_in_weight": "In",
        "chart_out_weight": "Out",
        "chart_shot_time": "Time",
        "chart_grind_setting": "Grind",
        "chart_initial_temp": "Temp",
        "chart_unknown_profile": "Unknown Profile",
        "chart_na": "N/A",
        "chart_bean_info": "Bean Info",
        "chart_profile_info": "Profile Info",
        "chart_tasting_note": "Tasting Note",
        "view_large": "Click to view large image",
        "machine_id": "",
        "print_control": "Print Control",
        "upload_success": "Upload successful",
        "print_disabled": "Printing disabled",
        "all_dates": "All dates",
        "latest_n": "Latest 9",
        "show_latest_9": "Showing the latest 9 shots. Use date filter for the full day.",
        "capped_note": "Too many shots — showing latest 100. Please filter by date.",
        "h_stats": "Statistics",
        "stat_total": "Total Shots",
        "stat_dates": "Days",
        "stat_machines": "Machines",
        "stat_avg_size": "Avg Data Size",
        "stat_prints": "Prints (session)",
        "stat_profiles": "Top Profiles",
        "stat_per_date": "By Date",
        "stat_older": "{n} earlier days",
        "stat_avg_day": "avg {n}/day",
        "stat_beans": "Bean Distribution",
        "prev_day": "Prev day",
        "next_day": "Next day",
        "plugin_local": "Download plugin (local · matches this version)",
        "plugin_github": "Download from GitHub (latest)",
        "plugin_txt": "Download TXT (for Bluetooth send)",
        "per_page": "per page",
        "total_n": "Total {n}",
        "plugin_note": "The local plugin matches this server version; the GitHub one may be newer.",
        "h_ai_settings": "AI Translation and Languages",
        "btn_save_key": "Save API Key",
        "btn_check_balance": "Check Balance",
        "ai_enabled_label": "Enable AI translation for bean info (timeout falls back to original text)",
        "h_languages": "Languages:",
        "btn_add_lang": "Add Language",
        "ai_key_hint": "enter API key",
        "ai_key_saved": "API key saved",
        "ai_toggled": "AI translation setting saved",
        "ai_lang_hint": "language name required",
        "btn_translate": "Translate this chart with AI",
        "ai_translating": "Translating UI strings via AI, ~10-30s...",
        "stat_ai_balance": "AI Balance",
        "translate_done": "Translated and re-rendered",
        "update_title": "Service Update",
        "btn_check_update": "Check for updates",
        "btn_update_service": "Update service from GitHub (auto backup)",
        "update_check": "Local {local} · Remote {remote}",
        "update_ok": "Up to date",
        "update_avail": "Update available",
        "update_note": "Auto-backup to backup/ before updating; restart the server after update; packaged builds can't self-update.",
    },
    "zh": {
        "server_title": "PrintTheShot Beta 服务器 v{VERSION}",
        "status_running": "运行中",
        "start_time": "启动时间",
        "shots_received": "接收数据",
        "active_users": "并发用户",
        "max_users": "最大用户",
        "print_enabled": "打印已启用",
        "bean_info_enabled": "豆子信息已启用",
        "print_queue": "打印队列",
        "enable_print": "启用打印",
        "disable_print": "禁用打印",
        "enable_bean_info": "启用豆子信息",
        "disable_bean_info": "禁用豆子信息",
        "refresh": "刷新",
        "clear_queue": "清空队列",
        "queue_empty": "队列为空",
        "data_upload": "数据上传",
        "drag_drop": "拖放JSON文件到这里或点击选择",
        "recent_data": "最近接收的数据",
        "no_data": "暂无数据",
        "print": "打印",
        "print_job_sent": "打印任务已发送",
        "plugin_download": "下载DE1插件",
        "plugin_instructions": "插件安装",
        "plugin_step1": "1. 下载 plugin.tcl 并复制到DE1平板:",
        "plugin_step2": "/de1plus/plugins/print_the_shot/plugin.tcl",
        "plugin_step3": "2. 重启DE1App,插件自动加载",
        "plugin_step4": "3. 插件服务器地址填本机IP:8000",
        "language": "语言",
        "queue_status": "打印队列: {count} 个任务",
        "chart_pressure": "压力 (巴)",
        "chart_flow": "流速 (克/秒)",
        "chart_water_flow": "水流流速",
        "chart_coffee_flow": "咖啡流速",
        "chart_temperature": "温度 (°C)",
        "chart_time": "时间 (秒)",
        "chart_date_time": "日期时间",
        "chart_profile": "冲煮方案",
        "chart_extraction": "萃取参数",
        "chart_grinder_temp": "研磨与温度",
        "chart_in_weight": "咖啡粉",
        "chart_out_weight": "咖啡液",
        "chart_shot_time": "时间",
        "chart_grind_setting": "研磨度",
        "chart_initial_temp": "温度",
        "chart_unknown_profile": "未知方案",
        "chart_na": "未记录",
        "chart_bean_info": "咖啡豆信息",
        "chart_profile_info": "冲煮方案信息",
        "chart_tasting_note": "品鉴感受",
        "view_large": "点击查看大图",
        "machine_id": "",
        "print_control": "打印控制",
        "upload_success": "上传成功",
        "print_disabled": "打印已禁用",
        "all_dates": "全部日期",
        "latest_n": "最新 9 条",
        "show_latest_9": "显示最新 9 条,按日期筛选可查看当日全部",
        "capped_note": "数据较多,仅显示最近 100 条,建议按日期筛选",
        "h_stats": "统计数据",
        "stat_total": "总接收数据",
        "stat_dates": "数据天数",
        "stat_machines": "机器数",
        "stat_avg_size": "平均数据大小",
        "stat_prints": "打印次数(本次运行)",
        "stat_profiles": "Top 冲煮方案",
        "stat_per_date": "按日期分布",
        "stat_older": "更早 {n} 天",
        "stat_avg_day": "日均 {n}",
        "stat_beans": "豆子分布",
        "prev_day": "前一天",
        "next_day": "后一天",
        "plugin_local": "下载插件(本地·匹配当前版本)",
        "plugin_github": "从 GitHub 下载(最新)",
        "plugin_txt": "下载 TXT 版(蓝牙发送)",
        "per_page": "每页",
        "total_n": "共 {n} 条",
        "plugin_note": "本地插件与当前服务器版本匹配;GitHub 上的可能更新。",
        "h_ai_settings": "AI 翻译与语言",
        "btn_save_key": "保存 API Key",
        "btn_check_balance": "查余额",
        "ai_enabled_label": "启用 AI 翻译豆子信息(超时自动回退原文)",
        "h_languages": "语言:",
        "btn_add_lang": "新增语言",
        "ai_key_hint": "请输入 API Key",
        "ai_key_saved": "API Key 已保存",
        "ai_toggled": "AI 翻译设置已保存",
        "ai_lang_hint": "请输入语言名称",
        "btn_translate": "AI 翻译本条曲线",
        "ai_translating": "AI 正在翻译界面文案,约需 10-30 秒...",
        "stat_ai_balance": "AI 余额",
        "translate_done": "已翻译并重新渲染",
        "update_title": "服务更新",
        "btn_check_update": "检查更新",
        "btn_update_service": "从 GitHub 更新服务(自动备份)",
        "update_check": "当前 {local} · 远程 {remote}",
        "update_ok": "已是最新",
        "update_avail": "有更新可用",
        "update_note": "更新前自动备份到 backup/ 目录;更新后请重启服务器;打包版不支持在线更新。",
    },
}

current_language = "en"   # 默认英文,可在网页右上角切换 / default English; switchable at the top-right of the web UI

# 豆子/方案中英名映射:展示层翻译,存储保持原始名称
# Bean/profile name mapping (zh<->en): translated at the display layer; storage keeps the original
PROFILE_TRANSLATIONS = {
    "温和香甜": "Gentle & Sweet", "甜甜萃": "Sweet", "自适应": "Adaptive",
    "长萃": "Allongé", "绽放": "Blooming", "涡轮": "Turbo", "经典浓缩": "Classic",
    "伦敦之王": "Londinium", "克雷米纳": "Cremina", "高提取": "High Extraction",
}
BEAN_TRANSLATIONS = {
    "哥伦比亚·乌伊拉 卡图拉/卡斯蒂略 · 日晒": "Colombia Huila Caturra/Castillo · Natural",
    "哥伦比亚 鲁比·奇罗索III · 厌氧水洗": "Colombia Rubí Chiroso III · Anaerobic Washed",
    "哥伦比亚 迪纳斯蒂亚瑰夏 · 厌氧水洗": "Colombia Dinastía Gesha · Anaerobic Washed",
    "肯尼亚·涅里 卡利鲁尼AA SL28/鲁伊鲁11 · 水洗": "Kenya Nyeri Kaliluni AA SL28/Ruiru 11 · Washed",
    "埃塞俄比亚·科科塞 · 日晒": "Ethiopia Kokose · Natural",
    "洪都拉斯 戈沙·拉萨尔瓦赫瑰夏 · 水洗": "Honduras Gosha La Salvaje Gesha · Washed",
}


def display_name(name, mapping):
    """按当前界面语言翻译名称;未知名称原样返回 / translate a name for the current UI language"""
    if not name:
        return name
    if current_language == "en":
        return mapping.get(name, name)
    rev = {v: k for k, v in mapping.items()}
    return rev.get(name, name)


def get_text(key):
    return LANGUAGES.get(current_language, LANGUAGES["en"]).get(key, key)


# ---------------------------------------------------------------------------
# AI 翻译设置(DeepSeek):设置持久化 + 自定义语言 + 翻译缓存
# AI translation settings (DeepSeek): persisted settings + custom languages + translation cache
# ---------------------------------------------------------------------------
SETTINGS_FILE = os.path.join(os.getcwd(), "settings.json")
TRANSLATION_CACHE_FILE = os.path.join(os.getcwd(), "translations.json")
DEEPSEEK_API = "https://api.deepseek.com/chat/completions"
DEEPSEEK_BALANCE = "https://api.deepseek.com/user/balance"
AI_TIMEOUT = 8          # 单条翻译超时 / single-translation timeout (s)
AI_TIMEOUT_BATCH = 30   # 批量UI文案翻译超时 / batch UI-strings timeout (s)

settings = {"deepseek_key": "", "ai_enabled": False, "languages": {}}
translation_cache = {}  # {"zh": {"原文": "译文"}}  / per-language cache


def load_settings():
    """启动时加载设置与自定义语言 / load settings and custom languages at startup"""
    global settings, translation_cache
    try:
        if os.path.exists(SETTINGS_FILE):
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                settings.update(json.load(f))
    except Exception as e:
        print(f"⚠️ settings.json 读取失败: {e}")
    try:
        if os.path.exists(TRANSLATION_CACHE_FILE):
            with open(TRANSLATION_CACHE_FILE, "r", encoding="utf-8") as f:
                translation_cache = json.load(f)
    except Exception:
        translation_cache = {}
    # 把自定义语言合并进 LANGUAGES,渲染/注入直接可用
    for code, info in settings.get("languages", {}).items():
        if info.get("strings"):
            LANGUAGES[code] = info["strings"]


def save_settings():
    """持久化设置(settings.json 含 API key,已在 .gitignore)"""
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, ensure_ascii=False, indent=1)
    except Exception as e:
        print(f"⚠️ 设置保存失败: {e}")


def save_translation_cache():
    try:
        with open(TRANSLATION_CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(translation_cache, f, ensure_ascii=False, indent=1)
    except Exception:
        pass


def ai_call(messages, timeout=AI_TIMEOUT):
    """调用 DeepSeek,返回响应文本;失败抛异常 / call DeepSeek, returns text"""
    import urllib.request
    key = settings.get("deepseek_key", "")
    if not key:
        raise RuntimeError("no api key")
    body = json.dumps({
        "model": "deepseek-chat",
        "messages": messages,
        "temperature": 0.3,
        "max_tokens": 2000,
    }).encode("utf-8")
    req = urllib.request.Request(DEEPSEEK_API, data=body, method="POST",
                                 headers={"Authorization": f"Bearer {key}",
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        resp = json.loads(r.read().decode("utf-8"))
    return resp["choices"][0]["message"]["content"].strip()


def clean_bean_text(text):
    """清理豆子信息文本:去掉连字符/间隔符等无效信息,保留数字连字符与单词内连字符
    Clean bean-info text: strip hyphens/separators, keep numeric & word-internal hyphens"""
    if not text:
        return text
    t = str(text)
    t = t.replace("·", ", ").replace("–", ", ").replace("—", ", ")
    t = re.sub(r"\s*-\s*", ", ", t)          # 两侧有空格的连字符 → 逗号
    t = re.sub(r"([一-鿿])\s*-\s*([一-鿿])", r"\1,\2", t)  # 中文间连字符 → 逗号
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"^[,，;；\s]+|[,，;；\s]+$", "", t)
    return t.strip()


def ai_translate(text, target_lang):
    """把豆子信息翻译成目标语言;缓存优先,静态表快速路径,失败返回原文
    Translate bean info into the target language: cache first, static-map fast path, original on failure"""
    if not text or not text.strip():
        return text
    # 静态表快速路径(演示豆子直接命中,不花token)
    if target_lang == "en":
        t = BEAN_TRANSLATIONS.get(text)
        if t:
            return t
    elif target_lang == "zh":
        t = {v: k for k, v in BEAN_TRANSLATIONS.items()}.get(text)
        if t:
            return t
    # 缓存
    cached = translation_cache.get(target_lang, {}).get(text)
    if cached:
        return cached
    if not settings.get("deepseek_key") or not settings.get("ai_enabled"):
        return text
    try:
        out = ai_call([
            {"role": "system",
             "content": f"You translate coffee bean info (origin, processing, flavor notes) into {target_lang}. "
                        f"Keep brand names, farm names and technical terms intact. Return ONLY the translation."},
            {"role": "user", "content": text},
        ], timeout=AI_TIMEOUT)
        out = clean_bean_text(out)  # 去掉连字符等无效信息 / strip hyphens & stray separators
        translation_cache.setdefault(target_lang, {})[text] = out
        save_translation_cache()
        return out
    except Exception as e:
        print(f"⚠️ AI 翻译失败(使用原文): {e}")
        return text  # 降级:原文,打印不受影响 / fallback: original text


def is_windows():
    return platform.system() == "Windows"


# ---------------------------------------------------------------------------
# 智能换行(移植自原版,逻辑一致) Smart text wrapping (ported from v1.6, same logic)
# ---------------------------------------------------------------------------
def smart_wrap_text(text, max_cn=7, max_en=15, max_lines=12):
    """按字符数智能换行:中文按字符,英文按单词"""
    if not text:
        return []
    text = str(text)

    # 检测文本类型 Detect text type
    chinese_count = sum(1 for c in text if "一" <= c <= "鿿")
    if len(text) == 0:
        return []
    is_chinese = chinese_count / len(text) > 0.3

    if is_chinese:
        width = max_cn
        lines = []
        current_line = ""
        for char in text:
            if char in "，。、；！？「」『』（）【】《》":
                current_line += char
            elif len(current_line) >= width:
                lines.append(current_line)
                current_line = char
            else:
                current_line += char
        if current_line:
            lines.append(current_line)
    else:
        import textwrap
        width = max_en
        lines = textwrap.wrap(
            text, width=width, break_long_words=False,
            break_on_hyphens=True, drop_whitespace=True, replace_whitespace=True,
        )
        # 处理极长单词 Handle extremely long words
        final_lines = []
        for line in lines:
            if len(line) > width * 1.5:
                split_points = [" ", "-", ",", ";", "."]
                for split_char in split_points:
                    if split_char in line:
                        parts = line.split(split_char)
                        if len(parts) > 1:
                            for i, part in enumerate(parts):
                                if i > 0:
                                    part = split_char + part
                                if len(part) > width:
                                    for j in range(0, len(part), width):
                                        final_lines.append(part[j:j + width])
                                else:
                                    final_lines.append(part)
                            break
                else:
                    for j in range(0, len(line), width):
                        final_lines.append(line[j:j + width])
            else:
                final_lines.append(line)
        lines = final_lines

    if len(lines) > max_lines:
        lines = lines[:max_lines]
        lines.append("...")
    return lines


# ---------------------------------------------------------------------------
# PIL 图表渲染(替代 matplotlib) PIL chart rendering (replaces matplotlib)
# ---------------------------------------------------------------------------
# 画布 1296x576(与 v1.6 输出一致,203dpi 下的 6.38x2.84 英寸) Canvas 1296x576 (same output as v1.6, 6.38x2.84 inches at 203dpi)
CHART_W, CHART_H = 1296, 576
# 几何布局(像素)。左侧预留带:温度标题(0-22)|温度刻度(→46)|温度轴(52)| Geometry (pixels)
# 压力标题(56-78)|压力刻度(→94)|压力轴(98)|绘图区(104-850) pressure title(56-78)|pressure ticks(->94)|pressure axis(98)|plot(104-850)
AX_TEMP_X = 62      # 温度轴(最左)
AX_PRES_X = 112     # 压力轴(绘图y轴)
AX_FLOW_X = 854     # 流速轴(最右)
PLOT_L = 112     # 绘图区左缘=压力轴,时间原点与压力y轴重合
PLOT_R = 850
PLOT_T = 58
PLOT_B = 440
COL1_X, COL1_MAXW = 905, 170     # 第一列文本(冲煮信息,贴近图表)
COL2_X, COL2_MAXW = 1085, 211    # 第二列文本(豆子/方案信息,贴近图表)
LINE_H = 27                       # 文本行距
LEGEND_Y = 474


def _font(px):
    return ImageFont.truetype(FONT_PATH, px)


def _text_w(draw, font, text):
    return draw.textlength(text, font=font)


def _vtext(img, draw, x, y_center, text, font, fill=0):
    """竖向文本(旋转90度,自下而上阅读,同matplotlib ylabel)。
    透明背景:仅字形像素落到图面上,不会擦除下方的轴/数字/任何内容。"""
    tmp = Image.new("L", (4, 4), 0)
    td = ImageDraw.Draw(tmp)
    bb = td.textbbox((0, 0), text, font=font)
    w, h = bb[2] - bb[0], bb[3] - bb[1]
    if w <= 0 or h <= 0:
        return
    # 字形=白色(作为mask),背景=0(透明) glyphs = white (used as mask), background = 0 (transparent)
    tmp = Image.new("L", (w + 4, h + 4), 0)
    td = ImageDraw.Draw(tmp)
    td.text((2 - bb[0], 2 - bb[1]), text, font=font, fill=255)
    tmp = tmp.rotate(90, expand=True)
    # 用tmp作mask:只在字形位置写入 fill(黑),其余保持原图 Use tmp as a mask: write fill (black) only where glyphs are, keep the rest intact
    img.paste(fill, (x, int(y_center - tmp.height / 2)), mask=tmp)


def _dash_line(draw, x1, y1, x2, y2, width=1, fill=0, pattern=(14, 8)):
    """任意方向虚线(沿线段方向参数化步进,支持斜线/曲线段)"""
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length <= 0:
        return
    ux, uy = dx / length, dy / length
    pos = 0.0
    i = 0
    while pos < length:
        seg = pattern[i % len(pattern)]
        end = min(pos + seg, length)
        if i % 2 == 0:  # 奇数段留空
            draw.line((x1 + ux * pos, y1 + uy * pos, x1 + ux * end, y1 + uy * end),
                      fill=fill, width=width)
        pos = end
        i += 1


def _draw_path_dashed(draw, pts, pattern, width=3):
    """沿折线路径连续应用虚线模式(相位跨线段延续,与matplotlib一致)。
    关键:不能在每条采样线段上重置相位,否则短段几乎都落在'画'区间,虚线看起来像实线。"""
    seg_idx = 0      # pattern 元素索引
    phase = 0.0      # 当前 pattern 元素已消耗的长度
    for k in range(len(pts) - 1):
        x0, y0 = pts[k]
        x1, y1 = pts[k + 1]
        dx, dy = x1 - x0, y1 - y0
        seg_len = (dx * dx + dy * dy) ** 0.5
        if seg_len <= 0:
            continue
        ux, uy = dx / seg_len, dy / seg_len
        pos = 0.0
        while pos < seg_len:
            seg = pattern[seg_idx % len(pattern)]
            take = min(seg - phase, seg_len - pos)
            if seg_idx % 2 == 0:  # 偶数元素=画
                draw.line((x0 + ux * pos, y0 + uy * pos,
                           x0 + ux * (pos + take), y0 + uy * (pos + take)),
                          fill=0, width=width)
            pos += take
            phase += take
            if phase >= seg - 1e-9:
                phase = 0.0
                seg_idx += 1


def _plot_curve_style(draw, xs, ys, x_scale, y_max, style):
    """画曲线,与原版matplotlib一致:线型沿路径方向绘制,相位连续。
    所有值钳制到[0, y_max](传感器毛刺会产生负值,必须避免画到图外)。
    solid:   折线
    dashed:  '--'  (17, 8)px @203dpi  (matplotlib 默认 6pt/3pt)
    dotted:  ':'   (2.5, 7)px
    dashdot: '-.'  (17, 8.5, 2.5, 8.5)px
    """
    def iy(v):
        v = min(max(v, 0.0), y_max)
        return PLOT_B - (v / y_max) * (PLOT_B - PLOT_T)

    def ix(t):
        return PLOT_L + t * x_scale

    pts = [(ix(t), iy(v)) for t, v in zip(xs, ys)]
    if style == "solid":
        for k in range(len(pts) - 1):
            draw.line((pts[k][0], pts[k][1], pts[k + 1][0], pts[k + 1][1]),
                      fill=0, width=3)
    else:
        pattern = {"dashed": (17, 8), "dotted": (2.5, 7), "dashdot": (17, 8.5, 2.5, 8.5)}[style]
        _draw_path_dashed(draw, pts, pattern, width=3)


def _nice_ticks(vmin, vmax, max_ticks=10):
    """选"漂亮"的刻度步长:从候选步长中挑一个让刻度数<=max_ticks"""
    span = vmax - vmin
    if span <= 0:
        return [vmin]
    step = 1
    for c in (1, 2, 5, 10, 15, 20, 30, 60):
        if span / c <= max_ticks:
            step = c
            break
    return [v for v in (vmin + i * step for i in range(int(span / step) + 2)) if v <= vmax + 1e-9]


def render_chart(data, output_path, machine_id="UNKNOWN", lang="zh"):
    """从DE1 JSON渲染小票图表(纯PIL,黑白)"""
    global current_language
    try:
        t = get_text if lang == current_language else lambda k: LANGUAGES[lang].get(k, k)
        old_lang = current_language
        if lang != current_language:
            current_language = lang

        # ---- 数据提取与对齐(空序列自动忽略,避免原版空by_weight崩溃) ---- ---- Data extraction & alignment (ignore empty series; avoids the v1.6 empty-by_weight crash) ----
        elapsed = [float(v) for v in data["elapsed"]]
        pressure = [float(v) for v in data["pressure"]["pressure"]]
        flow = [float(v) for v in data["flow"]["flow"]]
        # by_weight 为空时回退到 by_weight_raw(部分固件版本写入不同字段) Fall back to by_weight_raw when by_weight is empty (some firmware versions use a different field)
        by_weight = [float(v) for v in (data["flow"].get("by_weight") or data["flow"].get("by_weight_raw", []))]
        basket_temp = [float(v) for v in data["temperature"]["basket"]]

        series = {"pressure": pressure, "flow": flow, "basket_temp": basket_temp}
        if by_weight:
            series["by_weight"] = by_weight
        min_length = min(len(elapsed), *[len(v) for v in series.values()])
        if min_length < 2:
            print(f"❌ 数据过短,无法渲染: {min_length} samples")
            return False
        elapsed = elapsed[:min_length]
        pressure = pressure[:min_length]
        flow = flow[:min_length]
        if by_weight:
            by_weight = by_weight[:min_length]
        basket_temp = basket_temp[:min_length]

        # 剔除起点毛刺:若某曲线首两点跳变超过数据范围的一半(如温度93→83), Drop start glitch
        # 丢弃首采样点,避免x=0处出现竖直"速降"线;曲线自然从y轴附近出发 drop the first sample to avoid a vertical 'plunge' at x=0; curves then start naturally near the y-axis
        def _start_glitch(vals):
            rng = max(vals) - min(vals)
            return len(vals) > 1 and rng > 0 and abs(vals[0] - vals[1]) > 0.5 * rng

        if any(_start_glitch(v) for v in (pressure, flow, basket_temp, by_weight)):
            elapsed = elapsed[1:]
            pressure = pressure[1:]
            flow = flow[1:]
            if by_weight:
                by_weight = by_weight[1:]
            basket_temp = basket_temp[1:]

        max_time = elapsed[-1] if elapsed else 30
        x_scale = (PLOT_R - PLOT_L) / max(max_time, 0.1)

        # ---- 画布 ---- ---- Canvas ----
        img = Image.new("L", (CHART_W, CHART_H), 255)
        draw = ImageDraw.Draw(img)
        font_m = _font(22)   # 8pt @ 203dpi
        font_l = _font(28)   # 10pt
        font_tick = _font(16)
        font_legend = _font(17)

        # ---- 坐标轴 ---- ---- Axes ----
        for ax in (AX_TEMP_X, AX_PRES_X, AX_FLOW_X):
            draw.line((ax, PLOT_T, ax, PLOT_B), fill=0, width=2)
        # x轴(时间)从左端y轴起画,三个y轴原点都与x轴重合 Time axis starts from the leftmost y-axis; all three y-axis origins coincide with the x-axis
        draw.line((AX_PRES_X, PLOT_B, PLOT_R, PLOT_B), fill=0, width=2)  # x轴从压力y轴起画

        # ---- 网格与刻度:温度0-100(最左),压力0-10(左),流速0-10(右) ---- ---- Grid & ticks
        # 每轴的刻度右对齐到自己的轴(anchor="rm"/"lm" 以刻度线垂直居中), Each axis's tick labels right-align to its own axis
        # 各轴刻度带互不重叠:57 / 108 / 858 Tick bands never overlap: 57 / 108 / 858
        for axis, ymax, label_x, align in (
            (AX_TEMP_X, 100, 57, "rm"),   # "100"宽27px → 起点30,与标题(0-17)留13px
            (AX_PRES_X, 10, 108, "rm"),   # "10"宽18px → 起点90,与标题(66-83)留7px
            (AX_FLOW_X, 10, 858, "lm"),   # 数字贴轴:从858起(止876)
        ):
            ticks = _nice_ticks(0, ymax, 12)  # 温度10一档(含90)、压力1一档(含9),90与9对齐
            for v in ticks:
                iy = PLOT_B - (v / ymax) * (PLOT_B - PLOT_T)
                if axis != AX_TEMP_X:
                    _dash_line(draw, PLOT_L, iy, PLOT_R, iy, width=1, pattern=(12, 8))
                draw.line((axis - 4, iy, axis, iy), fill=0, width=1)
                draw.text((label_x, iy), str(int(v)), font=font_tick, fill=0, anchor=align)

        # ---- 时间轴刻度(下方) ---- ---- Time-axis ticks (below) ----
        x_ticks = _nice_ticks(0, max_time)
        for v in x_ticks:
            ix = PLOT_L + v * x_scale
            draw.line((ix, PLOT_B, ix, PLOT_B + 4), fill=0, width=1)
            _dash_line(draw, ix, PLOT_T, ix, PLOT_B, width=1, pattern=(10, 10))
            draw.text((ix, PLOT_B + 6), str(int(v)), font=font_tick, fill=0, anchor="mt")

        # ---- 四条曲线(实线/虚线/点线/点划线) ---- ---- Four curves (solid/dashed/dotted/dash-dot) ----
        _plot_curve_style(draw, elapsed, pressure, x_scale, 10, "solid")
        _plot_curve_style(draw, elapsed, flow, x_scale, 10, "dashed")
        if by_weight:
            _plot_curve_style(draw, elapsed, by_weight, x_scale, 10, "dotted")
        _plot_curve_style(draw, elapsed, basket_temp, x_scale, 100, "dashdot")

        # ---- 图例(4列,下方) ---- ---- Legend (4 columns, below) ----
        legend_items = [
            ("solid", t("chart_pressure")),
            ("dashed", t("chart_water_flow")),
            ("dotted", t("chart_coffee_flow")),
            ("dashdot", t("chart_temperature")),
        ]
        lx = PLOT_L
        for style, label in legend_items:
            if style == "solid":
                draw.line((lx, LEGEND_Y, lx + 30, LEGEND_Y), fill=0, width=3)
            elif style == "dashed":
                _dash_line(draw, lx, LEGEND_Y, lx + 30, LEGEND_Y, width=3, pattern=(10, 6))
            elif style == "dotted":
                for dx in (6, 15, 24):
                    draw.ellipse((lx + dx - 2, LEGEND_Y - 2, lx + dx + 2, LEGEND_Y + 2), fill=0)
            else:
                _dash_line(draw, lx, LEGEND_Y, lx + 30, LEGEND_Y, width=3, pattern=(8, 4, 2, 4))
            draw.text((lx + 36, LEGEND_Y - 9), label, font=font_legend, fill=0)
            lx += 36 + _text_w(draw, font_legend, label) + 34

        # ---- 机器ID(左下角,方框按文字实际尺寸+内边距,紧贴图例) ---- ---- Machine ID (bottom-left, box sized to text + padding, snug below legend) ----
        mid = t("machine_id") + machine_id
        if mid.strip():
            bb = draw.textbbox((0, 0), mid, font=font_tick)
            tw, th = bb[2] - bb[0], bb[3] - bb[1]
            pad = 4
            bx, by = 16, CHART_H - 68
            draw.rounded_rectangle((bx, by, bx + tw + pad * 2, by + th + pad * 2),
                                   radius=4, outline=0, width=1)
            draw.text((bx + pad - bb[0], by + pad - bb[1]), mid, font=font_tick, fill=0)

        # ---- 第一列文本(冲煮信息) ---- ---- Column 1 text (brew info) ----
        profile_title = data.get("profile", {}).get("title", t("chart_unknown_profile"))
        profile_lines = smart_wrap_text(profile_title, 7, 14, 14)

        meta = data.get("meta", {})
        in_weight = meta.get("in", "N/A")
        out_weight = meta.get("out", "N/A")
        shot_time = meta.get("time", "N/A")
        grinder_setting = meta.get("grinder", {}).get("setting", "N/A")

        # 日期时间(优先用timestamp,与原版一致) Date/time (timestamp first, same as v1.6)
        formatted_date = formatted_time = "N/A"
        ts = data.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromtimestamp(float(ts))
                formatted_date = dt.strftime("%Y-%m-%d")
                formatted_time = dt.strftime("%H:%M:%S")
            except Exception:
                pass
        if formatted_date == "N/A":
            date_str = data.get("date", "")
            if date_str:
                try:
                    dt = datetime.strptime(date_str, "%a %b %d %H:%M:%S %Y")
                    formatted_date = dt.strftime("%Y-%m-%d")
                    formatted_time = dt.strftime("%H:%M:%S")
                except Exception:
                    pass

        initial_temp = basket_temp[0] if basket_temp else 0.0

        col1 = []
        col1.append((t("chart_date_time"), True))
        col1.append(("──────", False))
        col1.append((formatted_date, False))
        col1.append((formatted_time, False))
        col1.append(("", False))
        col1.append((t("chart_profile"), True))
        col1.append(("──────", False))
        for line in (profile_lines or [profile_title[:7]]):
            col1.append((line, False))
        col1.append(("", False))
        col1.append((t("chart_extraction"), True))
        col1.append(("──────", False))
        col1.append((f"{t('chart_in_weight')}: {in_weight}g", False))
        col1.append((f"{t('chart_out_weight')}: {out_weight}g", False))
        col1.append((f"{t('chart_shot_time')}: {shot_time}s", False))
        col1.append(("", False))
        col1.append((t("chart_grinder_temp"), True))
        col1.append(("──────", False))
        col1.append((f"{t('chart_grind_setting')}: {grinder_setting}", False))
        col1.append((f"{t('chart_initial_temp')}: {initial_temp:.1f}°C", False))

        font_col1_t = _font(23)
        font_col1 = _font(21)
        y = 52
        for text, is_title in col1:
            if text == "──────":
                y -= LINE_H * 0.5
            elif text == "":
                y -= LINE_H * 0.3
            else:
                fnt = font_col1_t if is_title else font_col1
                draw.text((COL1_X, y), text, font=fnt, fill=0,
                          stroke_width=0 if is_title else 0)
            y += LINE_H

        # ---- 第二列文本(豆子信息或方案信息) ---- ---- Column 2 text (bean info or profile info) ----
        bean_data = meta.get("bean", {}) or {}
        has_bean_info = bool(bean_data and (bean_data.get("brand") or bean_data.get("type") or bean_data.get("notes")))

        col2 = []
        if has_bean_info:
            col2.append((t("chart_bean_info"), True))
        else:
            col2.append((t("chart_profile_info"), True))
        col2.append(("──────", False))

        if has_bean_info:
            brand = bean_data.get("brand", "")
            bean_type = bean_data.get("type", "")
            line1 = f"{brand} - {bean_type}" if (brand and bean_type) else (brand or bean_type)
            line2 = bean_data.get("notes", "")
            roast_info = []
            if bean_data.get("roast_level"):
                roast_info.append(bean_data["roast_level"])
            roast_date = bean_data.get("roast_date", "")
            if len(str(roast_date)) == 8 and str(roast_date).isdigit():
                roast_info.append(f"{roast_date[:4]}-{roast_date[4:6]}-{roast_date[6:8]}")
            line3 = " ".join(roast_info)
            for line in (line1, line2, line3):
                if line:
                    for wrapped in smart_wrap_text(line, 8, 16, 16):
                        col2.append((wrapped, False))
            shot_notes = meta.get("shot", {}).get("notes", "")
            if shot_notes:
                col2.append(("Tasting Note (from JSON):", True))
                col2.append(("──────", False))
                for wrapped in smart_wrap_text(shot_notes, 8, 16, 16):
                    col2.append((wrapped, False))
        else:
            notes = data.get("profile", {}).get("notes", "")
            if notes:
                for wrapped in smart_wrap_text(notes, 8, 16, 16):
                    col2.append((wrapped, False))
            else:
                col2.append((t("chart_na"), False))

        # 固定品尝笔记区域 Fixed tasting-note area
        col2.append(("", False))
        col2.append((t("chart_tasting_note"), True))
        col2.append(("──────", False))
        for _ in range(4):
            col2.append(("", False))

        y2 = 52
        for text, is_title in col2:
            if text == "──────":
                y2 -= LINE_H * 0.5
            elif text == "":
                y2 -= LINE_H * 0.3
            else:
                fnt = _font(20) if is_title else _font(18)
                draw.text((COL2_X, y2), text, font=fnt, fill=0,
                          stroke_width=0 if is_title else 0)
            y2 += LINE_H

        # ---- 轴标题(竖向,17px=与图例同大;在 img.save 前最后绘制,盖住轴与刻度) ---- ---- Axis titles (vertical, 17px = legend size; drawn last before img.save, on top of axes & ticks) ----
        # 温度/压力完全脱离数字(数字起点30/90);流速贴轴 Temp/pressure titles fully clear of numbers (start 30/90); flow hugs the axis
        font_axis = _font(17)

        def _draw_axis_titles():
            # 位置经变体对比定稿:温度16(近"100"左缘30,与"90"左缘39保持间距)、压力74(贴"9"左缘99附近) Final positions chosen via variant comparison
            _vtext(img, draw, 16, (PLOT_T + PLOT_B) / 2, t("chart_temperature"), font_axis)
            _vtext(img, draw, 74, (PLOT_T + PLOT_B) / 2, t("chart_pressure"), font_axis)
            _vtext(img, draw, 866, (PLOT_T + PLOT_B) / 2, t("chart_flow"), font_axis)

        _draw_axis_titles()

        # 所有内容绘制完成后,再补画一次轴标题: Draw the axis titles once more after everything else:
        # 防止右侧文本块等任何后续绘制覆盖流速标题 prevent any later drawing (e.g. the right text columns) from covering the flow title
        _draw_axis_titles()

        img.save(output_path, "PNG")
        current_language = old_lang
        print(f"✅ Chart generated: {output_path}")
        return True

    except Exception as e:
        import traceback
        print(f"❌ Chart generation failed: {e}")
        traceback.print_exc()
        return False


def generate_print_image(png_path):
    """生成打印用BMP(与v1.6相同:放大4倍->旋转->二值化)"""
    try:
        bmp_path = png_path.replace(".png", "_print.bmp")
        target_width = 576 * 4
        target_height = int(target_width * 180 / 80)
        img = Image.open(png_path)
        img = img.convert("L")
        img = img.resize((target_height, target_width), Image.LANCZOS)
        img = img.rotate(90, expand=True)
        img = img.point(lambda p: 255 if p > 200 else 0)
        img = img.convert("1")
        img.save(bmp_path, "BMP")
        print(f"🖨️ Print image generated: {bmp_path}")
        return bmp_path
    except Exception as e:
        print(f"❌ Print image generation failed: {e}")
        return None


# ---------------------------------------------------------------------------
# 打印(Windows: ctypes GDI;macOS/Linux: lpr/lp) Printing (Windows: ctypes GDI; macOS/Linux: lpr/lp)
# ---------------------------------------------------------------------------
def windows_print_bmp(bmp_path):
    """纯ctypes调用Windows打印API(无pywin32依赖)"""
    try:
        import ctypes
        from ctypes import wintypes
        PRINTER_ALL_ACCESS = 0x000F000C
        DM_ORIENTATION = 0x00000001  # unused,保留扩展位

        class DOC_INFO_1(ctypes.Structure):
            _fields_ = [
                ("pDocName", wintypes.LPWSTR),
                ("pOutputFile", wintypes.LPWSTR),
                ("pDatatype", wintypes.LPWSTR),
            ]

        winspool = ctypes.windll.winspool
        name = ctypes.create_unicode_buffer(512)
        bufsize = wintypes.DWORD(512)
        winspool.GetDefaultPrinterW(name, ctypes.byref(bufsize))

        hprinter = wintypes.HANDLE()
        if not winspool.OpenPrinterW(name, ctypes.byref(hprinter), None):
            return False

        di = DOC_INFO_1()
        di.pDocName = "PrintTheShot"
        di.pDatatype = "RAW"
        jobid = winspool.StartDocPrinterW(hprinter, 1, ctypes.byref(di))
        if jobid == 0:
            winspool.ClosePrinter(hprinter)
            return False

        with open(bmp_path, "rb") as f:
            bmp_data = f.read()
        # BMP按行扫描,无需DIB转换 BMP scanned row-wise, no DIB conversion needed
        winspool.StartPagePrinter(hprinter)
        written = wintypes.DWORD(0)
        chunk = 65536
        for i in range(0, len(bmp_data), chunk):
            part = bmp_data[i:i + chunk]
            buf = ctypes.create_string_buffer(part)
            if not winspool.WritePrinter(hprinter, buf, len(part), ctypes.byref(written)):
                break
        winspool.EndPagePrinter(hprinter)
        winspool.EndDocPrinter(hprinter)
        winspool.ClosePrinter(hprinter)
        return True
    except Exception as e:
        print(f"❌ Windows print error: {e}")
        return False


def print_image(image_path):
    """打印图片:Windows走GDI,其他平台走lpr/lp"""
    if not PRINT_ENABLED:
        print("🖨️ Printing disabled, skipping")
        return False
    if not os.path.exists(image_path):
        print(f"❌ 图像文件不存在: {image_path}")
        return False

    print("🖨️ Sending print job...")
    if is_windows():
        success = windows_print_bmp(image_path)
        if success:
            return True
        print("❌ Windows打印失败(请确认默认打印机可用)")
        return False

    for cmd in (
        ["lpr", image_path, "-o", "media=Custom.80x180mm", "-o", "fit-to-page",
         "-o", "margin-top=0", "-o", "margin-bottom=0"],
        ["lp", image_path, "-o", "media=Custom.80x180mm", "-o", "fit-to-page",
         "-o", "margin-top=0"],
    ):
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0:
                print("✅ Print job sent successfully")
                return True
        except Exception as e:
            print(f"⚠️ 打印命令失败: {e}")
    print(f"❌ Print failed (lpr/lp不可用): 请确认CUPS打印机已配置")
    return False


# ---------------------------------------------------------------------------
# HTTP 服务器 HTTP Server
# ---------------------------------------------------------------------------
class PrintTheShotHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def log_message(self, fmt, *args):
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {fmt % args}")

    # ---------- 工具 ---------- ---------- Helpers ----------
    def _send_json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, path, content_type, as_attachment=False, download_name=None):
        if not os.path.exists(path):
            self.send_error(404, "Not found")
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-type", content_type)
        if as_attachment:
            name = download_name or os.path.basename(path)
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- GET ----------
    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/":
            self.show_index()
        elif path == "/api/status":
            self.send_api_status()
        elif path == "/api/queue":
            self.send_queue_status()
        elif path == "/api/shots":
            self.send_shots_list()
        elif path == "/api/stats":
            self.send_stats()
        elif path == "/api/update/check":
            self.check_update()
        elif path == "/api/settings/ai":
            self.send_ai_settings()
        elif path == "/api/ai/balance":
            self.send_ai_balance()
        elif path == "/api/languages":
            self.send_languages()
        elif path == "/api/settings":
            self._send_json({
                "bean_info_enabled": BEAN_INFO_ENABLED,
                "print_enabled": PRINT_ENABLED,
                "max_users": MAX_USERS,
            })
        elif path.startswith("/images/"):
            name = os.path.basename(path)
            self._serve_file(os.path.join(IMAGE_DIR, name), "image/png")
        elif path == "/plugin/plugin.tcl":
            self._serve_file(plugin_runtime_path(), "application/x-tcl", as_attachment=True)
        elif path == "/plugin/plugin.tcl.txt":
            # TXT版:蓝牙发送时安卓端常拒绝无扩展名/.tcl文件,tcl.txt可正常传输 TXT version: Android often rejects extension-less/.tcl files over Bluetooth; tcl.txt transfers fine
            self._serve_file(plugin_runtime_path(), "text/plain", as_attachment=True,
                             download_name="tcl.txt")
        elif path.startswith("/download/json/"):
            name = os.path.basename(path)
            self._serve_file(os.path.join(DATA_DIR, name), "application/json", as_attachment=True)
        elif path == "/api/language":
            self._send_json({"language": current_language})
        else:
            super().do_GET()

    # ---------- POST ----------
    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/upload":
            self.handle_upload()
        elif path == "/api/print":
            self.handle_print_control()
        elif path == "/api/language":
            self.handle_language_change()
        elif path == "/api/settings/beaninfo":
            self.handle_beaninfo_setting()
        elif path == "/api/settings/print":
            self.handle_print_setting()
        elif path == "/api/plugin/update":
            self.handle_plugin_update()
        elif path == "/api/update":
            self.handle_update()
        elif path == "/api/settings/ai":
            self.save_ai_settings()
        elif path == "/api/languages":
            self.add_language()
        elif path == "/api/languages/delete":
            self.delete_language()
        elif path == "/api/translate/shot":
            self.handle_translate_shot()
        else:
            self.send_error(404, "Endpoint not found")

    # ---------- DELETE ----------
    def do_DELETE(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/api/queue":
            print_jobs.clear()
            self._send_json({"success": True, "message": "Queue cleared"})
        else:
            self.send_error(404, "Endpoint not found")

    # ---------- 页面 ---------- ---------- Pages ----------
    def show_index(self):
        global current_language
        try:
            with open(WEB_INDEX, "r", encoding="utf-8") as f:
                html = f.read()
            html = html.replace("{{VERSION}}", VERSION)
            lang_json = dict(LANGUAGES[current_language])
            # 附带当前语言码与可用语言列表(供切换器动态渲染)
            # attach current code + available languages for the switcher
            lang_json["__code"] = current_language
            lang_json["__version"] = VERSION
            lang_json["__languages"] = [{"code": "en", "name": "English"}, {"code": "zh", "name": "中文"}] + [
                {"code": c, "name": i.get("name", c)}
                for c, i in settings.get("languages", {}).items()]
            lang_json = json.dumps(lang_json, ensure_ascii=False)
            lang_json = lang_json.replace("'", "&#39;")  # 防单引号破坏JS字符串(can't 之类)
            html = html.replace("{{LANG}}", lang_json)
            body = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            self.send_error(500, f"Template error: {e}")

    # ---------- API ----------
    def send_api_status(self):
        self._send_json({
            "status": get_text("status_running"),
            "version": VERSION,
            "start_time": server_start_time.strftime("%Y-%m-%d %H:%M:%S"),
            "shots_received": len(received_shots),
            "active_users": len(threading.enumerate()) - 1,
            "max_users": MAX_USERS,
            "print_enabled": PRINT_ENABLED,
            "bean_info_enabled": BEAN_INFO_ENABLED,
            "language": current_language,
        })

    def send_queue_status(self):
        with shots_lock:
            jobs = [dict(j) for j in print_jobs]
        self._send_json({"count": len(jobs), "jobs": jobs})

    def send_shots_list(self):
        """GET /api/shots[?date=YYYY-MM-DD]
        默认:最近100条(UI取前9);指定日期:当日全部(上限500);用于日期切换"""
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        date_str = q.get("date", [""])[0].replace("-", "")
        with shots_lock:
            shots = list(reversed(received_shots))
        dates = sorted({s.get("timestamp", "")[:8] for s in shots}, reverse=True)
        dates_fmt = [f"{d[:4]}-{d[4:6]}-{d[6:8]}" for d in dates]
        if date_str:
            shots = [s for s in shots if s.get("timestamp", "")[:8] == date_str][:500]
        else:
            shots = shots[:100]
        # 展示层翻译:卡片标题按界面语言显示,存储保持原始名称
        for s in shots:
            s["bean"] = display_name(s.get("bean", ""), BEAN_TRANSLATIONS)
            s["profile"] = display_name(s.get("profile", ""), PROFILE_TRANSLATIONS)
        self._send_json({"shots": shots, "dates": dates_fmt})

    def send_stats(self):
        """GET /api/stats — 统计数据(总数据/天数/机器/Top方案/日期分布)"""
        import collections
        with shots_lock:
            shots = list(received_shots)
        total = len(shots)
        per_date = collections.Counter(s.get("timestamp", "")[:8] for s in shots)
        profiles = collections.Counter(s.get("profile", "unknown") for s in shots)
        beans = collections.Counter(s.get("bean", "未知") for s in shots)
        machines = len({s.get("machine_id") for s in shots})
        avg_size = int(sum(s.get("data_size", 0) for s in shots) / total) if total else 0
        # 日期分布只展示最近7天,更早的合并为一行,避免列过长 Date distribution shows only the latest 7 days; older ones merge into one row to avoid a long list
        per_date_list = [{"date": f"{d[:4]}-{d[4:6]}-{d[6:8]}", "count": c}
                         for d, c in sorted(per_date.items(), reverse=True)]
        recent, older = per_date_list[:7], per_date_list[7:]
        self._send_json({
            "total_shots": total,
            "total_dates": len(per_date),
            "machines": machines,
            "avg_data_size": avg_size,
            "prints_session": len(print_jobs),
            "per_date": recent,
            "older_days": len(older),
            "older_count": sum(x["count"] for x in older),
            "top_profiles": [{"name": display_name(n, PROFILE_TRANSLATIONS), "count": c}
                             for n, c in profiles.most_common(5)],
            "top_beans": [{"name": display_name(n, BEAN_TRANSLATIONS), "count": c}
                          for n, c in beans.most_common(6)],
        })

    def handle_language_change(self):
        global current_language
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            lang = data.get("language", "zh")
            if lang in LANGUAGES:
                current_language = lang
            self._send_json({"success": True, "language": current_language})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def handle_beaninfo_setting(self):
        global BEAN_INFO_ENABLED
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if "enabled" in data:
                BEAN_INFO_ENABLED = bool(data["enabled"])
            self._send_json({"success": True, "bean_info_enabled": BEAN_INFO_ENABLED})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def handle_print_setting(self):
        global PRINT_ENABLED
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if "enabled" in data:
                PRINT_ENABLED = bool(data["enabled"])
            self._send_json({"success": True, "print_enabled": PRINT_ENABLED})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def check_update(self):
        """GET /api/update/check — 对比本地与GitHub远端版本"""
        import urllib.request, re
        try:
            req = urllib.request.Request(RAW_SERVER_URL, headers={"User-Agent": "PrintTheShotBeta"})
            with urllib.request.urlopen(req, timeout=15) as r:
                content = r.read().decode("utf-8", "replace")
            m = re.search(r'VERSION\s*=\s*"([^"]+)"', content)
            remote = m.group(1) if m else "unknown"
            self._send_json({
                "local": VERSION,
                "remote": remote,
                "update_available": remote != "unknown" and _version_key(remote) > _version_key(VERSION),
            })
        except Exception as e:
            self._send_json({"error": str(e)}, 500)

    def handle_update(self):
        """POST /api/update — 从GitHub更新整个服务(自动备份);打包版不支持"""
        global current_language
        if getattr(sys, "frozen", False):
            msg = ("打包版本不支持在线更新,请下载新安装包" if current_language == "zh"
                   else "Packaged build can't self-update — download the new installer")
            self._send_json({"success": False, "message": msg})
            return
        ok, msg = perform_update(GITHUB_ZIP_URL, os.getcwd(), current_language)
        self._send_json({"success": ok, "message": msg}, 200 if ok else 500)

    # ---------- AI 翻译设置 / AI translation settings ----------
    def send_ai_settings(self):
        """GET /api/settings/ai — 当前AI设置(不返回key本身)"""
        self._send_json({
            "key_set": bool(settings.get("deepseek_key")),
            "ai_enabled": bool(settings.get("ai_enabled")),
        })

    def save_ai_settings(self):
        """POST /api/settings/ai {key?, enabled?} — 保存key/开关"""
        global settings
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            if "key" in data and data["key"] is not None:
                settings["deepseek_key"] = data["key"].strip()
            if "enabled" in data:
                settings["ai_enabled"] = bool(data["enabled"])
            save_settings()
            self._send_json({"success": True, "key_set": bool(settings["deepseek_key"]),
                             "ai_enabled": settings["ai_enabled"]})
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def send_ai_balance(self):
        """GET /api/ai/balance — DeepSeek 余额(兼作连接测试)"""
        import urllib.request
        key = settings.get("deepseek_key", "")
        if not key:
            self._send_json({"success": False, "message": "no api key"})
            return
        try:
            req = urllib.request.Request(DEEPSEEK_BALANCE, method="GET",
                                         headers={"Authorization": f"Bearer {key}"})
            with urllib.request.urlopen(req, timeout=AI_TIMEOUT) as r:
                resp = json.loads(r.read().decode("utf-8"))
            infos = resp.get("balance_infos", [])
            total = sum(float(i.get("total_balance", 0)) for i in infos)
            currency = infos[0].get("currency", "CNY") if infos else "CNY"
            self._send_json({"success": True, "balance": round(total, 2), "currency": currency})
        except Exception as e:
            self._send_json({"success": False, "message": f"balance check failed: {e}"})

    def send_languages(self):
        """GET /api/languages — 可用语言列表(内置 + 自定义)"""
        langs = [{"code": "en", "name": "English", "builtin": True},
                 {"code": "zh", "name": "中文", "builtin": True}]
        for code, info in settings.get("languages", {}).items():
            langs.append({"code": code, "name": info.get("name", code), "builtin": False})
        self._send_json({"languages": langs})

    def add_language(self):
        """POST /api/languages {name} — 用DeepSeek把UI文案翻译成新语言并启用(代码自动生成)
        Add a custom language by plain-text name: translate all UI strings via DeepSeek"""
        global settings
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            name = data.get("name", "").strip()
            if not name:
                self._send_json({"success": False, "message": "language name required"})
                return
            # 代码自动生成(内部使用,用户无需关心) / auto-generate internal code
            code = data.get("code", "").strip().lower()
            if not code or not re.match(r"^[a-z0-9]{2,8}$", code) or code in ("en", "zh"):
                n = 1
                while f"lang{n}" in settings.get("languages", {}) or f"lang{n}" in ("en", "zh"):
                    n += 1
                code = f"lang{n}"
            if not settings.get("deepseek_key"):
                self._send_json({"success": False, "message": "DeepSeek API key required first"})
                return
            # 批量翻译EN文案 → JSON(AI理解语言名称,任意写法均可)
            prompt = ("Translate the following JSON object of UI strings into " + name +
                      ". Keep {placeholders} intact. "
                      "Return ONLY valid JSON with the same keys.")
            out = ai_call([{"role": "system", "content": prompt},
                           {"role": "user", "content": json.dumps(LANGUAGES["en"], ensure_ascii=False)}],
                          timeout=AI_TIMEOUT_BATCH)
            import re as _re
            m = _re.search(r"\{.*\}", out, _re.S)
            strings = json.loads(m.group(0)) if m else json.loads(out)
            settings.setdefault("languages", {})[code] = {"name": name, "strings": strings}
            save_settings()
            LANGUAGES[code] = strings  # 立即生效
            self._send_json({"success": True, "message": f"language {name} ({code}) added",
                             "strings_count": len(strings)})
        except Exception as e:
            self._send_json({"success": False, "message": f"add language failed: {e}"}, 500)

    def handle_translate_shot(self):
        """POST /api/translate/shot {filename} — 用DeepSeek翻译该条豆子信息并重渲染图表
        Translate a shot's bean info via DeepSeek and re-render its chart"""
        global current_language
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            filename = data.get("filename", "")
            filepath = os.path.join(DATA_DIR, filename)
            if not os.path.exists(filepath):
                self._send_json({"success": False, "message": "shot not found"})
                return
            if not settings.get("deepseek_key") or not settings.get("ai_enabled"):
                self._send_json({"success": False, "message": "AI translation not enabled"})
                return
            with open(filepath, "r", encoding="utf-8") as f:
                shot_data = json.load(f)
            bean_meta = shot_data.get("meta", {}).get("bean", {}) or {}
            if bean_meta:
                bean_meta = dict(bean_meta)
                bean_meta["type"] = clean_bean_text(ai_translate(str(bean_meta.get("type", "")), current_language))
                bean_meta["notes"] = clean_bean_text(ai_translate(str(bean_meta.get("notes", "")), current_language))
                shot_data.setdefault("meta", {})["bean"] = bean_meta
            image_path = os.path.join(IMAGE_DIR, filename.replace(".json", ".png"))
            ok = render_chart(shot_data, image_path, data.get("machine_id", "UNKNOWN"), current_language)
            new_bean = bean_meta.get("type", "未知") if bean_meta else "未知"
            with shots_lock:
                for s in received_shots:
                    if s.get("filename") == filename:
                        s["bean"] = new_bean
            persist_index()
            msg = "translated & re-rendered" if ok else "render failed"
            self._send_json({"success": ok, "message": msg})
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def delete_language(self):
        """DELETE /api/languages/delete {code} — 移除自定义语言"""
        global settings
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            code = data.get("code", "")
            langs = settings.get("languages", {})
            if code in langs:
                del langs[code]
                LANGUAGES.pop(code, None)
                save_settings()
            self._send_json({"success": True})
        except Exception as e:
            self._send_json({"success": False, "message": str(e)}, 500)

    def handle_plugin_update(self):
        """从GitHub拉取最新plugin.tcl,先本地备份再覆盖"""
        global current_language
        import urllib.request
        try:
            req = urllib.request.Request(PLUGIN_GITHUB_URL, headers={"User-Agent": "PrintTheShotBeta"})
            with urllib.request.urlopen(req, timeout=20) as r:
                new_data = r.read()
            # 基本校验:必须含插件标识,否则视为下载异常 Basic validation: must contain the plugin marker, else treat as a bad download
            if len(new_data) < 500 or b"print_the_shot" not in new_data:
                self._send_json({"success": False, "message": "下载内容异常,已取消"})
                return
            runtime_path = plugin_runtime_path()
            # 备份当前插件 Back up the current plugin
            backup = runtime_path + f".bak_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if os.path.exists(runtime_path):
                import shutil
                shutil.copy2(runtime_path, backup)
            # 写入新插件 Write new plugin
            with open(runtime_path, "wb") as f:
                f.write(new_data)
            msg = f"插件已更新(旧版备份: {os.path.basename(backup)})"
            if current_language == "en":
                msg = f"Plugin updated (backup: {os.path.basename(backup)})"
            print(f"🔄 {msg}")
            self._send_json({"success": True, "message": msg})
        except Exception as e:
            msg = f"更新失败: {e}" if current_language == "zh" else f"Update failed: {e}"
            self._send_json({"success": False, "message": msg}, 500)

    def handle_print_control(self):
        """手动打印:POST /api/print {filename: xxx.png}"""
        global current_language
        try:
            length = int(self.headers.get("Content-Length", 0))
            data = json.loads(self.rfile.read(length).decode("utf-8"))
            filename = data.get("filename", "")
            image_path = os.path.join(IMAGE_DIR, filename)
            if not os.path.exists(image_path):
                self._send_json({"success": False, "message": "image not found"})
                return
            threading.Thread(target=self._do_print, args=(image_path,), daemon=True).start()
            self._send_json({"success": True, "message": get_text("print_job_sent")})
        except Exception as e:
            self._send_json({"success": False, "error": str(e)}, 500)

    def _do_print(self, image_path):
        ok = print_image(image_path)
        job = {"filename": os.path.basename(image_path),
               "time": datetime.now().strftime("%H:%M:%S"), "ok": ok}
        with shots_lock:
            print_jobs.append(job)
            if len(print_jobs) > 20:
                del print_jobs[:-20]

    # ---------- 上传 ---------- ---------- Upload ----------
    def handle_upload(self):
        global current_language
        try:
            content_type = self.headers.get("Content-Type", "")
            length = int(self.headers.get("Content-Length", 0))
            post_data = self.rfile.read(length)

            if "multipart/form-data" in content_type:
                shot_data = self._extract_multipart_json(post_data, content_type)
            elif "application/json" in content_type:
                shot_data = json.loads(post_data.decode("utf-8"))
            else:
                shot_data = json.loads(post_data.decode("utf-8"))

            parsed = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(parsed.query)
            machine_id = q.get("machine_id", ["UNKNOWN"])[0]

            shot_id = time.time_ns() // 1000  # 微秒级唯一ID,避免同秒上传撞车 microsecond-unique ID to avoid same-second filename collisions
            filename = f"shot_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{shot_id}.json"
            filepath = os.path.join(DATA_DIR, filename)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(shot_data, f, ensure_ascii=False, indent=2)

            # 先应答,再后台渲染+打印 Respond first, then render + print in the background
            self._send_json({
                "status": "success", "id": shot_id,
                "message": f"Shot data received and saved as {filename}",
                "auto_printed": PRINT_ENABLED,
            })

            threading.Thread(target=self._process_shot,
                             args=(filepath, filename, shot_id, machine_id, len(post_data)),
                             daemon=True).start()

        except Exception as e:
            self.send_error(400, f"Upload error: {e}")

    def _extract_multipart_json(self, post_data, content_type):
        """极简multipart解析:取出第一个文件字段的JSON内容"""
        import re
        boundary = re.search(r"boundary=([^;]+)", content_type).group(1).strip('"')
        parts = post_data.split(("--" + boundary).encode())
        for part in parts:
            if b"filename=" in part[:400]:
                header_end = part.find(b"\r\n\r\n")
                if header_end > 0:
                    body = part[header_end + 4:]
                    body = body.replace(b"\r\n--", b"").rstrip()
                    return json.loads(body.decode("utf-8"))
        raise ValueError("No file part found in multipart data")

    def _process_shot(self, filepath, filename, shot_id, machine_id, data_size):
        global current_language
        with open(filepath, "r", encoding="utf-8") as f:
            shot_data = json.load(f)

        # AI翻译:豆子信息按当前界面语言翻译(超时降级用原文,打印不受影响)
        # AI translate: bean info into the current UI language (timeout falls back to original)
        lang = current_language
        if settings.get("ai_enabled") and settings.get("deepseek_key") and lang in LANGUAGES:
            bean_meta = shot_data.get("meta", {}).get("bean", {})
            if bean_meta:
                if lang != "zh" or not re.search("[一-鿿]", str(bean_meta.get("type", ""))):
                    translated_type = ai_translate(str(bean_meta.get("type", "")), lang)
                    translated_notes = ai_translate(str(bean_meta.get("notes", "")), lang)
                    bean_meta = dict(bean_meta)
                    bean_meta["type"] = translated_type
                    bean_meta["notes"] = translated_notes
                    shot_data.setdefault("meta", {})["bean"] = bean_meta

        image_filename = filename.replace(".json", ".png")
        image_path = os.path.join(IMAGE_DIR, image_filename)
        ok = render_chart(shot_data, image_path, machine_id, current_language)

        shot_info = {
            "id": shot_id,
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "filename": filename,
            "data_size": data_size,
            "clock": shot_data.get("clock", "unknown"),
            "profile": shot_data.get("profile", {}).get("title", "unknown"),
            "machine_id": machine_id,
            "image_exists": ok,
            "bean": shot_data.get("meta", {}).get("bean", {}).get("type", "未知"),
        }
        with shots_lock:
            received_shots.append(shot_info)
            if len(received_shots) > 5000:
                del received_shots[:-1000]
        persist_index()  # 锁外调用,避免与内部锁死锁;持久化历史,重启不丢 called outside the lock to avoid a deadlock with the inner lock; persists history across restarts

        if PRINT_ENABLED and ok:
            print("🖨️ 开始后台打印...")
            self._do_print(image_path)


# ---------------------------------------------------------------------------
# 入口 Entry
# ---------------------------------------------------------------------------
def ensure_directories():
    for directory in (DATA_DIR, IMAGE_DIR):
        os.makedirs(directory, exist_ok=True)


def persist_index():
    """把历史列表写入 shots_data/index.json(重启后恢复用)"""
    try:
        with shots_lock:
            with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
                json.dump(received_shots, f, ensure_ascii=False)
    except Exception as e:
        print(f"⚠️ 持久化失败 / Persist failed: {e}")


def load_history():
    """启动时恢复历史:index.json 优先,再扫描目录兜底(崩溃恢复)"""
    global received_shots
    restored = []
    index_path = os.path.join(DATA_DIR, "index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, "r", encoding="utf-8") as f:
                restored = json.load(f)
        except Exception as e:
            print(f"⚠️ index.json 读取失败,将重建: {e}")
            restored = []

    # 目录扫描:补上索引里没有的文件(机器ID无法从文件恢复,标 UNKNOWN) Directory scan: fill in files missing from the index (machine ID can't be recovered from files, marked UNKNOWN)
    known = {s.get("filename") for s in restored}
    try:
        for fn in sorted(os.listdir(DATA_DIR)):
            if not fn.endswith(".json") or fn == "index.json" or fn in known:
                continue
            fp = os.path.join(DATA_DIR, fn)
            profile, data_size, clock, data = "unknown", 0, "unknown", None
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    data = json.load(f)
                profile = data.get("profile", {}).get("title", "unknown")
                data_size = os.path.getsize(fp)
                clock = data.get("clock", "unknown")
            except Exception:
                pass
            ts = fn.replace("shot_", "").split("_")
            timestamp = ts[0] + "_" + ts[1] if len(ts) >= 2 else ""
            bean = data.get("meta", {}).get("bean", {}).get("type", "未知") if data else "未知"
            restored.append({
                "id": 0, "timestamp": timestamp, "filename": fn,
                "data_size": data_size, "clock": clock,
                "profile": profile, "machine_id": "UNKNOWN",
                "image_exists": os.path.exists(
                    os.path.join(IMAGE_DIR, fn.replace(".json", ".png"))),
                "bean": bean,
            })
    except FileNotFoundError:
        pass

    # 补齐旧索引条目缺失的 bean 字段(解析JSON文件,一次性) Backfill the bean field for old index entries (parse JSON files, one-time)
    for s in restored:
        if "bean" not in s:
            try:
                with open(os.path.join(DATA_DIR, s.get("filename", "")), "r", encoding="utf-8") as f:
                    d = json.load(f)
                s["bean"] = d.get("meta", {}).get("bean", {}).get("type", "未知")
            except Exception:
                s["bean"] = "未知"

    restored.sort(key=lambda s: s.get("timestamp", ""), reverse=True)
    with shots_lock:
        received_shots = restored[:5000]
    persist_index()


def print_server_info(port):
    import socket
    hostname = socket.gethostname()
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
    except Exception:
        local_ip = "localhost"
    print("")
    print("🍳 " + "=" * 60)
    print(f"🍳           PrintTheShot Beta v{VERSION}")
    print("🍳 " + "=" * 60)
    print(f"🍳  管理界面 / Web UI:   http://localhost:{port}")
    print(f"🍳  局域网访问 / LAN:    http://{local_ip}:{port}")
    print(f"🍳  上传端点 / Upload:   http://{local_ip}:{port}/upload")
    print(f"🍳  数据目录 / Data:     {os.path.abspath(DATA_DIR)}")
    print(f"🍳  打印功能 / Print:    {'启用' if PRINT_ENABLED else '禁用'}")
    print(f"🍳  启动时间 / Started:  {server_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("🍳  Ctrl+C 停止 / Stop")
    print("🍳 " + "=" * 60)


def main():
    global PRINT_ENABLED
    parser = argparse.ArgumentParser(description="PrintTheShot Beta server")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--render", nargs="+", metavar=("JSON", "[PNG]"),
                        help="仅渲染图表到PNG(测试用)")
    parser.add_argument("--no-print", action="store_true", help="启动时禁用自动打印")
    args = parser.parse_args()

    if args.render:
        src = args.render[0]
        dst = args.render[1] if len(args.render) > 1 else src.replace(".json", ".png")
        with open(src, "r", encoding="utf-8") as f:
            data = json.load(f)
        ok = render_chart(data, dst, machine_id="TEST")
        if ok:
            bmp = generate_print_image(dst)
            print(f"🎉 渲染成功 / Render OK: {dst}" + (f"\n   打印BMP: {bmp}" if bmp else ""))
        sys.exit(0 if ok else 1)

    if args.no_print:
        PRINT_ENABLED = False

    ensure_directories()
    load_settings()   # 加载AI设置与自定义语言 / load AI settings & custom languages
    load_history()  # 恢复历史数据(重启不丢)
    print_server_info(args.port)

    class ReuseTCPServer(socketserver.ThreadingTCPServer):
        allow_reuse_address = True
        daemon_threads = True

    with ReuseTCPServer(("", args.port), PrintTheShotHandler) as httpd:
        print(f"✅ 服务器启动成功 / Server started on port {args.port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n🛑 服务器停止 / Server stopped")


if __name__ == "__main__":
    main()
