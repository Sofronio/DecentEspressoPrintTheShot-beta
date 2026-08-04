#!/usr/bin/env python3
"""
生成月度测试数据(Prodigal 豆子):每天 ~50 杯波动,约 1500 条。
用法: python scripts/generate_test_data.py [天数] [每天基准杯数]
注意:会清空 shots_data/ 与 shots_images/ 后重建。
"""
import json
import os
import random
import sys
import time
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 31
BASE_PER_DAY = int(sys.argv[2]) if len(sys.argv) > 2 else 50

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "shots_data")
IMAGE_DIR = os.path.join(ROOT, "shots_images")
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(IMAGE_DIR, exist_ok=True)

BEANS = [
    ("哥伦比亚·乌伊拉 卡图拉/卡斯蒂略 · 日晒",
     "热带水果、甜香料、奶油;乌龙茶、西柚、芒果,甜感平衡 (杯测85.75)", "20260718"),
    ("哥伦比亚 鲁比·奇罗索III · 厌氧水洗",
     "菠萝、桃子果酱、西柚、花香;明亮酸质,建议养豆3-4周", "20260720"),
    ("哥伦比亚 迪纳斯蒂亚瑰夏 · 厌氧水洗",
     "华丽花香、菠萝、草莓果酱、清脆柑橘", "20260722"),
    ("肯尼亚·涅里 卡利鲁尼AA SL28/鲁伊鲁11 · 水洗",
     "桃、杏、浆果;中温桃子突出,多汁酸甜,醇厚扎实", "20260725"),
    ("埃塞俄比亚·科科塞 · 日晒",
     "油桃、樱桃、热带水果;中度玫瑰花香、奶油感,干净顺滑", "20260728"),
    ("洪都拉斯 戈沙·拉萨尔瓦赫瑰夏 · 水洗",
     "COE冠军批次;精致花香、蜂蜜、柑橘,层次丰富", "20260730"),
]
# Decent 官方内置方案(DECENT Espresso 推荐的中文名)
PROFILES = ["温和香甜", "甜甜萃", "自适应", "长萃", "绽放",
            "涡轮", "经典浓缩", "伦敦之王", "克雷米纳", "高提取"]
MACHINES = ["DE1-01", "DE1-02", "DE1-03"]

from print_the_shot_server import render_chart

SAMPLE = json.load(open(os.path.join(ROOT, "sample_shots", "prodigal_el_rafugio.json")))
random.seed(42)


def render_one(fn_idx):
    """模块级worker(必须可pickle):渲染单张图表"""
    fn, idx = fn_idx
    with open(os.path.join(DATA_DIR, fn), "r", encoding="utf-8") as f:
        data = json.load(f)
    png = os.path.join(IMAGE_DIR, fn.replace(".json", ".png"))
    return render_chart(data, png, MACHINES[idx % 3], "zh")


def build_one(day: date, idx: int, day_idx: int, day_count: int, gid: int):
    # 时间按"天内序号"分配,早8点~晚7点均匀散布,避免跨天溢出
    minutes = 8 * 60 + int(day_idx * (11 * 60) / max(1, day_count)) + random.randint(0, 6)
    hh, mm = minutes // 60, minutes % 60
    ts = f"{day.strftime('%Y%m%d')}_{hh:02d}{mm:02d}"
    fn = f"shot_{ts}_{1787000000 + gid}.json"
    bean = BEANS[idx % len(BEANS)]
    data = dict(SAMPLE)
    data["profile"] = {"title": PROFILES[idx % len(PROFILES)], "notes": ""}
    data["meta"] = dict(SAMPLE.get("meta", {}))
    data["meta"]["bean"] = {"brand": "Prodigal", "type": bean[0], "notes": bean[1],
                            "roast_level": "浅烘", "roast_date": bean[2]}
    dt = time.mktime((day.year, day.month, day.day, hh, mm, 0, 0, 0, -1))
    data["timestamp"] = str(int(dt))
    data["date"] = time.strftime("%a %b %d %H:%M:%S %Y", time.localtime(dt))
    return ts, fn, data


def main():
    import glob
    for f in glob.glob(os.path.join(DATA_DIR, "shot_*.json")) + glob.glob(os.path.join(IMAGE_DIR, "*.png")):
        os.remove(f)
    if os.path.exists(os.path.join(DATA_DIR, "index.json")):
        os.remove(os.path.join(DATA_DIR, "index.json"))

    start = date(2026, 8, 4) - timedelta(days=DAYS - 1)
    tasks = []  # (ts, fn, data)
    gid = 0
    for day in (start + timedelta(days=d) for d in range(DAYS)):
        count = random.randint(35, 65) if BASE_PER_DAY == 50 else max(5, BASE_PER_DAY + random.randint(-15, 15))
        for i in range(count):
            ts, fn, data = build_one(day, i, i, count, gid)
            with open(os.path.join(DATA_DIR, fn), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            tasks.append((ts, fn, data))
            gid += 1

    # 多进程渲染图表(读取已落盘JSON,避免跨进程传大对象)
    import multiprocessing
    pool = multiprocessing.Pool(8)
    pool.map(render_one, [(t[1], i) for i, t in enumerate(tasks)])
    pool.close()
    pool.join()

    entries = [{"id": 1787000000 + i, "timestamp": ts, "filename": fn,
                "data_size": os.path.getsize(os.path.join(DATA_DIR, fn)),
                "clock": "0", "profile": d["profile"]["title"],
                "machine_id": MACHINES[i % 3], "image_exists": True}
               for i, (ts, fn, d) in enumerate(tasks)]
    with open(os.path.join(DATA_DIR, "index.json"), "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=1)
    print(f"OK: {len(entries)} 条, {DAYS} 天, {start} ~ 2026-08-04")


if __name__ == "__main__":
    main()
