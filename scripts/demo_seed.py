"""demo 副本种子生成器：真实库 → 剥离凭证 → data-demo/demo.db.master。

与 scripts/demo_server.py（全假数据）互补：本脚本产出"真实库副本"，用于
vaclead.raozewei.cn 公开 demo（用户 2026-11-08 选定方案）。

做法：
  1. sqlite3 backup API 复制真实库（规避 WAL/SHM 一致性问题，不锁真库）
  2. 清空凭证与操作者隐私表（每条都是字面量 SQL，无动态拼接）：
     - settings_kv        高德/知了标讯/企查 key、emailengine 等全部运行配置
     - email_account      SMTP 发件凭证
     - webhook / webhook_delivery   私有回调地址与投递日志
     - user_api_key       机器通道密钥
     - llm_calls / audit_log        LLM 调用内容与操作者审计日志
  3. users 保留全部账号（避免线索 owner_id 悬空；密码为 bcrypt，登录页
     仅公示 admin/admin123 与 sales/sales123）

产物 demo.db.master 只读保留，服务器每日重置脚本用它覆盖运行库。
真库 data/vaclead.db 全程不被修改。
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = PROJECT / "data" / "vaclead.db"
DEFAULT_OUT = PROJECT / "data-demo" / "demo.db.master"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=DEFAULT_SRC, help="真实库路径")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="输出 master 副本路径")
    args = ap.parse_args()

    if not args.src.is_file():
        raise SystemExit(f"✗ 源库不存在：{args.src}")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    if args.out.exists():
        args.out.unlink()

    # 1. backup API 复制（在线安全，不依赖 wal checkpoint）
    src = sqlite3.connect(f"file:{args.src}?mode=ro", uri=True)
    dst = sqlite3.connect(args.out)
    with dst:
        src.backup(dst)
    src.close()

    # 2. 剥离凭证与隐私日志（每条语句均为字面量，便于安全审查）
    cur = dst.cursor()
    try:
        cur.execute("DELETE FROM settings_kv")
        print("  已清空 settings_kv")
    except sqlite3.OperationalError as e:
        print(f"  跳过 settings_kv（{e}）")
    try:
        cur.execute("DELETE FROM email_account")
        print("  已清空 email_account")
    except sqlite3.OperationalError as e:
        print(f"  跳过 email_account（{e}）")
    try:
        cur.execute("DELETE FROM webhook")
        print("  已清空 webhook")
    except sqlite3.OperationalError as e:
        print(f"  跳过 webhook（{e}）")
    try:
        cur.execute("DELETE FROM webhook_delivery")
        print("  已清空 webhook_delivery")
    except sqlite3.OperationalError as e:
        print(f"  跳过 webhook_delivery（{e}）")
    try:
        cur.execute("DELETE FROM user_api_key")
        print("  已清空 user_api_key")
    except sqlite3.OperationalError as e:
        print(f"  跳过 user_api_key（{e}）")
    try:
        cur.execute("DELETE FROM llm_calls")
        print("  已清空 llm_calls")
    except sqlite3.OperationalError as e:
        print(f"  跳过 llm_calls（{e}）")
    try:
        cur.execute("DELETE FROM audit_log")
        print("  已清空 audit_log")
    except sqlite3.OperationalError as e:
        print(f"  跳过 audit_log（{e}）")
    dst.commit()

    # 3. 体检报告：确认无残留凭证、账号清单、体量
    n_setting = cur.execute("SELECT COUNT(*) FROM settings_kv").fetchone()[0]
    n_user = cur.execute("SELECT COUNT(*) FROM user").fetchone()[0]
    users = cur.execute("SELECT username, role FROM user").fetchall()
    ents = cur.execute("SELECT COUNT(*) FROM enterprise").fetchone()[0]
    leads = cur.execute("SELECT COUNT(*) FROM lead").fetchone()[0]
    contacts = cur.execute("SELECT COUNT(*) FROM contact").fetchone()[0]
    dst.execute("VACUUM")
    dst.close()

    size_mb = args.out.stat().st_size / 1024 / 1024
    print(f"\n✓ 副本就绪：{args.out}（{size_mb:.1f} MB）")
    print(f"  settings_kv 残留 {n_setting} 条（应为 0）")
    print(f"  账号 {n_user} 个：{users}")
    print(f"  企业 {ents} / 线索 {leads} / 联系人 {contacts}")


if __name__ == "__main__":
    main()
