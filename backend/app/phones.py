"""联系电话抽取与规范化（SPEC-AH AH2）。

公告文本里的号码形态固定：手机 1[3-9] 开头 11 位、座机 0XX-XXXXXXXX（可带
分机）。参考 python-phonenumbers 的规范化思想但零依赖——域内形态有限，
正则 + 去重保序即可；提取结果存 contact.phones_json（["010-68290510", ...]）。

GLOB 数字判断（title 槽位含数字）是历史语义债：数字可能是"8038"这样的项目
编号——phones_json 是拨得通的真号码列。
"""
from __future__ import annotations

import json
import re

_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}[-—]?\d{7,8}(?:[-—]\d{3,4})?(?!\d)")


def _clean(m: str) -> str:
    """统一分隔符为半角连字符（告示里常见全角破折号）。"""
    return m.replace("—", "-")


def extract_phones(text: str | None) -> list[str]:
    """从自由文本抽取电话号码，去重保序（手机优先于座机出现序混合）。"""
    if not text:
        return []
    seen: list[str] = []
    for m in _MOBILE.finditer(text):
        p = _clean(m.group(0))
        if p not in seen:
            seen.append(p)
    for m in _LANDLINE.finditer(text):
        p = _clean(m.group(0))
        if p not in seen:
            seen.append(p)
    return seen


def load_phones(phones_json: str | None) -> list[str]:
    try:
        data = json.loads(phones_json or "[]")
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def dump_phones(phones: list[str]) -> str:
    return json.dumps(phones, ensure_ascii=False)
