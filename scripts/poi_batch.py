"""首批 POI 批量采集：10 城顺序执行（SQLite 单写者——并发任务会撞锁）。

逐城 POST /api/tasks/poi → 轮询 task_checkpoint 至 done/error → 下一城。
日志写 data/poi_batch.log。运行前提：演示服务 :8000 已启动。
用法：python scripts/poi_batch.py [industry_key ...]（缺省=vacuum/coating/semiconductor）
"""
import json
import sqlite3
import sys
import time
import urllib.request

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = "http://127.0.0.1:8000"
CITIES = ["无锡", "苏州", "东莞", "佛山", "深圳", "宁波", "常州", "南通", "西安", "合肥"]
DEFAULT_INDUSTRY_KEYS = ["vacuum", "coating", "semiconductor"]
INDUSTRY_KEYS = sys.argv[1:] or DEFAULT_INDUSTRY_KEYS
DB = r"E:\AAAA\A-xiaosou\vaclead\data\vaclead.db"


def api(path: str, payload: dict | None = None, token: str | None = None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, headers=headers)
    return json.load(opener.open(req, timeout=30))


def log(msg: str) -> None:
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(r"E:\AAAA\A-xiaosou\vaclead\data\poi_batch.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> None:
    tok = api("/api/auth/login", {"username": "admin", "password": "admin123"})["token"]
    con = sqlite3.connect(DB)
    total_inserted = 0
    for city in CITIES:
        try:
            r = api("/api/tasks/poi", {"city": city, "industry_keys": INDUSTRY_KEYS}, tok)
        except Exception as exc:  # noqa: BLE001
            log(f"{city} 启动失败: {exc}")
            continue
        tid = r["task_id"]
        log(f"{city} 任务 {tid} 启动")
        deadline = time.time() + 25 * 60
        while time.time() < deadline:
            row = con.execute(
                "SELECT status, progress, state_json FROM task_checkpoint WHERE task_id=?",
                (tid,),
            ).fetchone()
            if row and row[0] in ("done", "error", "cancelled"):
                st = json.loads(row[2] or "{}") if row[2] else {}
                log(f"{city} {row[0]} {row[1]}% inserted={st.get('inserted')} merged={st.get('merged')} err={st.get('error', '')[:80]}")
                total_inserted += st.get("inserted") or 0
                break
            time.sleep(15)
        else:
            log(f"{city} 超时未完成")
        time.sleep(3)
    n_poi = con.execute("SELECT count(*) FROM enterprise WHERE source='poi'").fetchone()[0]
    tel = con.execute(
        "SELECT count(*) FROM contact WHERE source='amap' AND phones_json != '[]'"
    ).fetchone()[0]
    log(f"批完成：累计 inserted={total_inserted}，POI 企业 {n_poi}，POI 电话 {tel} 条")


if __name__ == "__main__":
    main()
