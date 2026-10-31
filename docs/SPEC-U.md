# SPEC-U · 企业信息深化（用户拍板："企业信息是非常重要的"）

> 立项：2026-11-01 W38 · Agent B 认领。数据漏损实锤：ggzy 采集器自始抽取法定代表人
> （normalize_entity_record 返回 legal_rep），但 Enterprise 无此列——法人数据全程被丢弃。
> 本轮=企业工商信息的"补全 + 呈现 + 完整度"三件套，全部复用已验证模式（est_date 同款）。

## U1 法人代表入库（数据漏损修复）

- Enterprise 加列 `legal_rep TEXT`（幂等 ALTER + OPERATIONS §8 登记）
- `resolve_enterprise` 签名扩展 `legal_rep: str | None = None`（同 est_date 模式：
  新建落库、已有企业空值补填不覆盖）
- ggzy 增量调用处传 `rec["legal_rep"]`；CSV 导入表头同义词加「法定代表人」
- 真库回填：ggzy 主体池重扫（幂等补填，同 W36 模式）

## U2 情报页"工商信息卡"

- `GET /api/enterprises/{id}` detail 返回补 legal_rep/established_at（查现状缺啥补啥）
- 情报页档案页签顶部重排为**工商信息卡**：统一信用代码 / 法定代表人 / 成立日期 /
  注册资本 / 行业 / 地址 / 经营范围（全文折叠展示）——企查查第一屏字段集
- 数据缺失字段显示"—"不发明

## U3 企业资料完整度

- API 计算（非库列）：五要素各 1 分——法人 / 成立日期 / 注册资本 / 经营范围 / 联系方式
  （has_contact），detail 与列表行均返回 `completeness`（0-5）
- 企业列表：行上加完整度小徽标（圆点 ×5）；Quick picks 加「资料齐全」筛选
  （completeness=5，SQLite 路径 Python 计数过滤即可，量级允许）
- 情报页档案卡头部显示完整度（如 4/5，缺哪项一目了然）

## U4 导出与呈现补全

- 企业导出 CSV 增列：法定代表人 / 成立日期 / 资料完整度
- 情报页联系人区 legal_rep 角色与 contact 表法人行并存时的去重展示（法人以
  Enterprise.legal_rep 为准展示在工商卡，contact 行保留证据溯源）

## 测试

- test_spec_u.py：resolve legal_rep 新建落库/已有补填不覆盖；CSV 法定代表人列导入；
  completeness 计算（五要素组合）；标讯/导出含新列
- 全量门禁 + E2E（E2E_PORT=8017）

## 边界

- 不接付费工商源（china-check 需 token 死路、aliyun_qcc 付费——Provider 协议已就位待 Key）
- 不做股东/参保人数（超出官方公开数据边界）
