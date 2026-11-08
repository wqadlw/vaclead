"""SPEC-CM 补丁：拓客任务卡简化（粘贴框迁至情报页），一次性。"""
import io

p = "frontend/app/tasks/page.tsx"
src = io.open(p, encoding="utf-8").read()

# 1) 卡片主体：去掉 textarea/回灌按钮/结果行，换指引
start = src.find("            <div className=\"space-y-2 text-[12.5px] leading-5 text-[var(--muted)]\">")
end = src.find("          </Card>", start)
assert start != -1 and end != -1, "card body not found"
new_body = '''            <div className="space-y-2 text-[12.5px] leading-5 text-[var(--muted)]">
              <p>
                企业工商资料（法人/注册资本/成立日期/电话）是最薄的一层——
                用风鸟免费账号按下面流程补齐：
              </p>
              <ol className="ml-4 list-decimal space-y-0.5">
                <li>下载缺数清单（按意图分排序，最值得查的在前，每行带直搜链接）</li>
                <li>企业列表 → 点开公司 → 档案页签底部「从风鸟补工商资料」</li>
                <li>在风鸟公司页整块复制工商信息 → 粘贴 → 解析入库（仅补空不覆盖）</li>
              </ol>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Button size="small" icon={<FileSpreadsheet size={13} />} onClick={() => window.open("/api/tasks/gongshang-lookup-export", "_blank")}>
                  下载缺数清单
                </Button>
                <span className="text-[11.5px]">也可直接在风鸟把常用客户加进「我的监控」（免费），工商变更实时推送</span>
              </div>
            </div>
'''
src = src[:start] + new_body + src[end:]

# 2) 删状态与 handler（gongshang 三 state + importGongshang 函数）
i0 = src.find("      // SPEC-CK 风鸟回灌")
i1 = src.find('  const [city, setCity]', i0)
assert i0 != -1 and i1 != -1, "state block not found"
src = src[:i0] + src[i1:]

io.open(p, "w", encoding="utf-8", newline="\n").write(src)
print("tasks card simplified")
