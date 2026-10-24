# P2 LLM 评审提示词资产（对标 OpenOutreach qualify_lead.j2 / write_brief.j2）

> 本文件是 signal_parser / icp_judge LLM 后端的提示词契约。
> 结构学 OpenOutreach："rules 先行（一次生成/人工维护）+ 逐条 verdict（结构化输出）"。
> 我们的差异：规则来自 config/vacuum.yaml（人工维护，不需要 LLM 生成 brief）；
> reason 用中文；防误读判则按真空设备场景重写。

## 1. ICP 评审（icp_judge LLM 后端）

### 输入字段（verdict_fields 等价物）

```
企业名: {name}
地区: {province}{city}{district}
注册资本: {registered_capital_wan} 万（"未知"表示未披露，不等于小公司）
经营范围原文: {business_scope}
行业标签: {industry_name}   ← 由关键词匹配挂上，可能与经营范围矛盾，矛盾时以经营范围原文为准
```

### 系统提示词（rules 先行段，每 verdict 都渲染）

```
## 本名单的判定规则（决定一切）

{{icp_rules_text}}
（由 vacuum.yaml 的下游行业/权重/竞对规则渲染成中文 bullet，见 §3）

## 判定准则

1. 规则决定一切：与规则冲突的任何印象都按规则处理。
2. 买方不是卖方（buyer not seller）：真空设备的使用方才算客户。
   设备制造商、经销商、同行、代工厂都不算——哪怕经营范围看起来高度相关。
3. 行业标签不是企业名或职务：不要把标签词读成公司主业，
   一切以经营范围原文为准。
4. 未披露注册资本 ≠ 小公司：按其余证据判断。
5. 近似表述同判：经营范围用语与规则用词不完全一致但含义相同时，同样处理。
```

### 输出 Schema（Pydantic 强制，学 "schema enforcement over prose guidance"）

```python
class QualificationDecision(BaseModel):
    qualified: bool          # 是否值得进入候选名单
    reason: str              # 一句中文 ≤40 字
    # reason 约束：说具体事实（这家企业造什么+用得上什么设备）；
    # 禁用"这家公司/非常匹配/强烈推荐"开头；禁止复述我们的产品介绍。
    confidence: Literal["high", "medium", "low"]   # 受控枚举，非法值拒绝
```

### 调用参数

- 不设 temperature（部分模型拒绝非默认值）
- timeout 60s；SDK 层 max_retries=8 吃 429/限流
- 输出走 pydantic 结构化解析：解析失败自动重试 ≤2 次，仍失败记 llm_calls(ok=0) 并回退规则后端

## 2. 信号解析（signal_parser LLM 后端）

### 输出 Schema

```python
class ParsedSignal(BaseModel):
    enterprise_name: str
    matched_terms: list[str]          # 命中的 vacuum.yaml 设备词
    hit_part: Literal["title", "body"]
    evidence_claim: str               # 一句中文 ≤50 字：公告要求买什么、数量/预算
    base_score: float                 # 按 signal_weights 规则
    project_stage: Literal["inquiry", "tender", "award", "unknown"]
```

### 防误读判则（中文版）

```
1. 标题与正文冲突时以正文为准。
2. "拟采购/预算/招标/询价"都算采购意图；"中标公示"说明采购已完成（stage=award）。
3. enterprise_name 抽不到时填空串，禁止编造公司名。
4. matched_terms 只能从给定设备词表中选择，禁止发明新词。
```

## 3. rules 渲染器（icp_rules_text 生成，规则后端与 LLM 后端共用）

从 vacuum.yaml 渲染为中文 bullet（伪代码，P2 实现于 vertical.py）：

```
- 一线客户（+40 分）：半导体/集成电路、真空镀膜加工 —— 经营范围含 {{keywords}} 之一
- 二线客户（+30 分）：光伏、锂电池、冻干食品/医药
- 三线（+10 分）：金属热处理等泛制造
- 竞对排除：名称含"真空"且含"设备"/经营范围含设备制造/真空泵生产 → 疑似同行
- 加分项：注册资本≥1000万（+15）；经营范围含生产/制造/加工（+10）；专精特新/高企（+15）
```

## 4. llm_calls 埋点约定（表已建）

每次调用记录 (task_id, agent_name, model, prompt_tokens, completion_tokens,
cost_usd, latency_ms, ok, error)；解析失败与回退事件也要记（ok=0, error=原因）。
聚合：按天/按任务/按 agent GROUP BY；摘要不回写任务表，查询时聚合。
