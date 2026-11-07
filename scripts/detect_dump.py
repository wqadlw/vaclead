"""词表校准 diff 工具（SPEC-CH CH5）：对全库跑 detect_industry，输出 name->key 映射。

用法：python detect_dump.py [alt_config_path] > out.json
  不带参数 = 当前生效配置；带 alt_config = 用指定配置（VACLEAD_CONFIG）。
"""
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault("VACLEAD_DISABLE_JOBS", "1")
if len(sys.argv) > 1:
    os.environ["VACLEAD_CONFIG"] = sys.argv[1]

sys.path.insert(0, r"E:/AAAA/A-xiaosou/vaclead/backend")
import importlib

import app.vertical as v

importlib.reload(v)

from app.db import SessionLocal
from app.models import Enterprise

out = {}
with SessionLocal() as s:
    for (name,) in s.query(Enterprise.name).filter(Enterprise.deleted_at.is_(None)).all():
        det = v.detect_industry(name)
        out[name] = det["key"] if det else ""

import json

print(json.dumps(out, ensure_ascii=False))
