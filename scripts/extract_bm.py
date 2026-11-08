"""验证 makeBookmarkletSrc 生成的书签 JS 语法与逻辑（node 执行）。"""
import io
import re

src = io.open("frontend/app/enterprises/view/page.tsx", encoding="utf-8", newline="").read()
m = re.search(r"const js = `((?:[^`\\]|\\.)*)`;", src)
assert m, "template not found"
tpl = m.group(1)
tpl = tpl.replace("${origin}", "http://127.0.0.1:8000")

out = "// bookmarklet under test\n" + tpl
io.open("scripts/_bm_test.mjs", "w", encoding="utf-8", newline="\n").write(out)
print("extracted", len(tpl), "chars")
