"""全量采集链式驱动（脱离 shell 的持久进程）。

流程：等 ggzy 全量任务(9356cd5ee9ab)结束 → 顺序跑 ccgp 三批（34 强词）
→ 输出最终统计。仅访问本机 127.0.0.1:8000 的固定接口（curl 子进程），
进度直接读 SQLite checkpoint。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

DB = Path(r"E:\AAAA\A-xiaosou\vaclead\data\vaclead.db")
META = Path(r"E:\AAAA\A-xiaosou\vaclead\data\_meta.json")
GGZY_TASK = "9356cd5ee9ab"


def curl_json(args: list[str]) -> dict:
    out = subprocess.run(
        ["curl", "-s", "--noproxy", "*", "-m", "60"] + args,
        capture_output=True, text=True, encoding="utf-8", timeout=90,
    )
    if not out.stdout.strip():
        raise RuntimeError(f"空响应: {out.stderr[:200]}")
    return json.loads(out.stdout)


def login() -> str:
    d = curl_json([
        "-X", "POST", "http://127.0.0.1:8000/api/auth/login",
        "-H", "Content-Type: application/json",
        "-d", '{"username":"admin","password":"admin123"}',
    ])
    return d["token"]


def task_status(task_id: str) -> tuple[str, str]:
    con = sqlite3.connect(DB)
    try:
        row = con.execute(
            "SELECT status, state_json FROM task_checkpoint WHERE task_id=?", (task_id,)
        ).fetchone()
        return (row[0], row[1]) if row else ("missing", "{}")
    finally:
        con.close()


def wait_task(task_id: str, label: str, max_min: int = 240) -> str:
    last = ""
    t0 = time.time()
    while time.time() - t0 < max_min * 60:
        st, sj = task_status(task_id)
        line = f"  {label} {st} {sj[:130]}"
        if line != last:
            print(line, flush=True)
            last = line
        if st in ("done", "failed", "cancelled"):
            return st
        time.sleep(30)
    return "timeout"


def main() -> None:
    print("== 等待 ggzy 全量任务 ==", flush=True)
    st = wait_task(GGZY_TASK, "ggzy")
    print(f"ggzy 结束：{st}", flush=True)
    if st not in ("done",):
        print("ggzy 未成功，仍继续 ccgp 批次", flush=True)

    token = login()
    meta = json.loads(META.read_text(encoding="utf-8"))
    terms = meta["strong_terms"]
    for bi in range(0, len(terms), 12):
        batch = terms[bi : bi + 12]
        body = json.dumps(
            {"keywords": batch, "days": 1095, "max_pages": 3}, ensure_ascii=False
        )
        body_file = DB.parent / "_ccgp_batch.json"
        body_file.write_text(body, encoding="utf-8")
        print(f"\n== ccgp 批次 {bi // 12 + 1}（{len(batch)} 词）==", flush=True)
        try:
            r = curl_json([
                "-X", "POST", "http://127.0.0.1:8000/api/tasks/ccgp-scan",
                "-H", f"Authorization: Bearer {token}",
                "-H", "Content-Type: application/json",
                "--data-binary", f"@{body_file.as_posix()}",
            ])
            print(f"  task={r.get('task_id')}", flush=True)
            st = wait_task(r["task_id"], f"ccgp_b{bi // 12 + 1}", max_min=120)
            print(f"ccgp 批次 {bi // 12 + 1} 结束：{st}", flush=True)
        except Exception as exc:  # noqa: BLE001 — 单批失败继续下一批
            print(f"ccgp 批次 {bi // 12 + 1} 失败：{exc}", flush=True)

    con = sqlite3.connect(DB)
    ents = con.execute("SELECT COUNT(*) FROM enterprise").fetchone()[0]
    sigs = con.execute("SELECT COUNT(*) FROM signal").fetchone()[0]
    by_source = con.execute(
        "SELECT source, COUNT(*) FROM enterprise GROUP BY source ORDER BY 2 DESC"
    ).fetchall()
    con.close()
    print(f"\n=== 全量采集完成：企业 {ents}，信号 {sigs} ===", flush=True)
    print(f"来源分布：{by_source}", flush=True)


if __name__ == "__main__":
    main()
