# SPEC-CUX2：联系人列表分页正确化 + 批量标记

> 第二十次功能轮。审查发现：contacts_worklist 固定 `limit(2000)` 采样后
> Python 过滤——**全库 1.4 万+ 可拨联系人有 1.2 万永远翻不到**（offset
> 形参没进查询），分页名存实亡。头部级列表必须数据完整。

## 1. 分页正确化（Python 过滤下沉 SQL）

`GET /api/contacts` 重构为纯 SQL 链路（语义逐条保留）：

- 过滤 SQL 化：
  - has_phone=1 → `phones_json NOT IN ('','[]')`
  - call_status：untouched → `call_result IN _UNTOUCHED`（且 has_phone）；
    touched → has_phone AND call_result NOT IN _UNTOUCHED；其他值 →
    `call_result == 值`
  - decision_role / province → 精确等值
  - q → `(Contact.name LIKE %q% OR Enterprise.name LIKE %q%)`
- 排序 SQL 化（语义与现 Python 版一致）：
  - 默认智能推荐：可拨子查询半连接 desc → e.intent_score desc →
    untouched 优先（CASE call_result）→ id desc
  - order=newest → Contact.id desc；order=intent → e.intent_score desc；
    order=recent_called → `called_at IS NOT NULL` desc + called_at desc +
    id desc（NULL 尾）
- 真分页：`COUNT(*)` 总数 + `offset/limit` 进查询；**2000 上限删除**
- KPI 统计四卡保持现有独立轻查询（全局口径已正确）
- `shared_ent_count` 保留（Python 后处理一层，仅对当前页 50 行标注）
- 共享标记语义不变；`_dialable/_untouched` 内部字段由 SQL 结果回填给前端

## 2. 批量标记（企业级效率）

`POST /api/contacts/batch-call-result`（body {ids: number[], result: string}，
登录）：

- result 校验 ∈ CallResultTag 七态白名单
- `UPDATE contact SET call_result=?, called_at=utcnow WHERE id IN ids`
  （仅更新传入 ids；逐条返回 changed 数）
- 前端：列表头多选 checkbox（全选当页）+ 批量栏（选中 N 人 →
  标记为[七态下拉]→ 确认）→ 成功 Toast + 行内状态即时刷新
- 移动端：多选 checkbox 在横滑表格内可用（触屏目标达标）

## 3. 测试（test_spec_cux2.py）

- 分页正确性：种子 250 条（超旧 2000 采样上限）→ page=2 offset 翻到
  250 内全部可达；total 与 SQL COUNT 一致
- 过滤 SQL 化回归：call_status 三态/has_phone/q 双列 LIKE（既有用例
  平移断言）
- 排序 SQL 化回归：默认智能推荐序与 Python 旧版一致（同种子断言）
- 批量标记：多 ids 更新+called_at 写入+非法 result 422
- 全量绿 + tsc 零错误

## 4. 非目标

- 不做 FTS（q 的 LIKE 在 2 万行规模够用）；不做游标分页（offset 够）
- 批量跟进（文本类）不做——标记是高频，跟进保持逐条在驾驶舱写
