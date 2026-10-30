# SPEC-L · 工业领域垂直扩展（用户拍板："改成工业领域，不要局限于真空行业"）

> 立项：2026-10-31 W35 · 平台从单一真空设备垂直升级为工业领域全行业设备拓客
> 架构前提：词表/行业类目/评分全部由 config/*.yaml 配置驱动（vertical.py 唯一入口），
> 采集/评分/管线/触达代码垂直无关——本轮**零核心代码重构**，只动配置+品牌+文档。

## 设计原则

1. **真空不丢**：现有 34 强词/12 宽词全部保留（存量 346 家企业与既有规则不受影响）
2. **配置驱动**：新文件 `config/industrial.yaml` 成为默认配置（VACLEAD_CONFIG 可覆盖，
   vacuum.yaml 归档保留保证可回滚）
3. **采集成本分级**：ggzy 企业名检索每词=一次请求成本——宽词分两级：
   - `core`（每日增量，≤20 词）：跨行业高价值品类宽词
   - `extended`（全量扫描/手动任务用）：长尾品类宽词
   - jobs.py 每日增量继续用 ggzy_name_terms()（返回 core）——**零 jobs.py 改动**
4. **类目互斥**：downstream_industries 一个企业名尽量只命中一个类目（keywords 互斥设计，
   检测顺序即优先级）
5. **评分配置兼容**：icp_segments/weights 语义不变（tier1/2/3 分值不改），新增类目按
   tier 归位自动参与评分

## 工作项

### L1 词表体系（核心工作量）
- `config/industrial.yaml`：
  - vertical: 工业设备
  - device_terms.strong：34（真空全部保留）→ ~100 词，按行业组组织
    （真空/半导体/机床/塑料/包装食品/化工/工业炉热处理/焊接切割/压缩风机/环保/检测仪器/自动化/医疗设备…）
  - device_terms.weak：克制（真空/镀膜/冻干/烧结 + 少量通用弱词——泛词会污染打分）
  - downstream_industries：7 类 → 15-20 类（key/中文名/tier/GB4754 行业码/keywords）
  - ggzy 宽词：core ≤20 + extended 长尾
  - profile_templates：保留 6 个 + 新行业模板 4-6 个
  - hiring_keywords：8 → ~15（通用设备岗 + 行业工艺岗）
- `vertical.py`：默认路径切 industrial.yaml + ggzy_extended_terms() 访问器 + docstring
- 类目与词表来源：调研 Agent 产出（头部平台分类 + GB4754 骨架 + 政采高频品类）+ 人工校准互斥性

### L2 品牌与文案去真空化
- frontend/lib/theme.ts BRAND：tagline → "INDUSTRIAL SALES INTELLIGENCE"、
  subtitle → "工业设备智能拓客平台"（同事 J4 在同文件改 PAGE_TITLES——不同区域零冲突）
- login 页文案、README 标题与定位句、SPEC.md 头部、WORKLOG 不改（历史日志保留原文）

### L3 测试与门禁
- 词表合约测试更新：强词规模断言（≥80）、真空词全保留断言、类目互斥抽检
  （任一 keywords 集合与其它类目交集为空或受控）、新类目 detect_industry/评分回归
- 全量 pytest + build + E2E

### L4 沉淀
- OPERATIONS §采集策略：宽词分级说明（core 每日增量 / extended 全量轮换）
- refs/STUDY.md：行业分类体系调研结论
- WORKLOG W35 + 记忆 + CHANGELOG v1.0.4 + push

## 边界与不做

- 存量 346 家企业数据不清洗（它们本来就是"工业买家"，只是词表更宽了）
- 不做行业子站（ccgp 分省站）扩展——DATA_ACQUISITION §4 P2 另立项
- Agent A 的 J4 文件零接触（models/main/jobs/outreach 均不碰）
