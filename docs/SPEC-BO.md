# SPEC-BO · 实时性与拨号轮：小时级行动窗口 + tel: 触达（Agent B 认领）

> 立项：2026-11-07 W59 · PRODUCT_GAP §5.3 真缺口 #2——"推送实时性：
> 剑鱼微信实时推 vs 我们 03:30 日批；补小时级招标类增量+推送即可对齐
> （低成本高感知）"。第七次复查同源结论：外呼 80 分方案 = tel: 链接 +
> call_result 回写（底座 SPEC-AN 已备，缺最后一米）。
>
> **数据通道实测边界（诚实定案）**：招标中"行动窗口"数据全部出自
> ccgp（ggzy 只出中标=事后语义）；ccgp 限流实测≈密集 400+ 请求/日
> 触发拦截页。故小时级方案取**轻扫摊平**而非全量提频：
> 每 2 小时 × 12 细词 × 1 页 × days=2 ≈ 96 请求/日（增量），
> 摊在 8 个时点；resilience 域级积分制遇拦截页自动熔断降级，
> 日批 03:30 全量（3 页）不动。一周实测无拦截页 → 提频只改注册参数。

## BO1 run_hourly_tender_scan()（jobs.py）

- ccgp 强词前 12 × days=2 × max_pages=1，入库语义与日批逐字一致
  （buyer 抽取→resolve ccgp_buyer→apply_icp→url+title 去重→tender 信号）
- 提取 `_scan_ccgp_words(stats, days, max_pages)` 供日批/轻扫共用——
  **日批行为零变化**（test_spec_i 回归锁）
- 返回 stats {scanned_words, ccgp_signals}，水位照常由包装器落库

## BO2 cron 注册（main.py）

- `hourly_tender`：`_register_cron("hourly_tender", …, "7-21/2", 40)`
  ——工作时段每 2 小时（APScheduler 区间步进语法），:40 错峰
  （避开 :00/:30 的 owner_signal 与 02:00-05:30 cron 密集窗）
- `subscription_rules` 提频："*/2",6 → "*",6（每小时，与小时级到达对齐）

## BO3 tel: 点击拨号（触达最后一米）

- 情报页联系人号码 chips 旁：lucide Phone 小图标 `<a href="tel:">`——
  桌面唤起外呼软抖、手机唤起拨号盘；原号码文本点击复制交互不变
- 推荐卡号码徽标旁同款拨打钮（stopPropagation 防卡片跳转）
- 打完电话的结果回写已有：快标三态（SPEC-AW）+ 7 态标记（SPEC-AN）

## BO4 测试 tests/test_spec_bo.py

- monkeypatch search_ccgp_all 注入两条公告（一含买家/一无买家）→
  断言 tender 信号入库、无买家被跳过、captured kwargs days=2 max_pages=1
- run_hourly_tender_scan 返回 stats 且水位写 'hourly_tender'（包装器路径）

## 边界

- ggzy 不做小时批：中标=事后语义，日批足够；拟建（粤/鄂）保持日批
- 推送最坏延迟=到达(≤2h)+订阅扫播(≤1h)=3h，对齐"小时级"承诺；
  微信/企微实时推=WEWORK_INTEGRATION.md P3
- 拦截页若现：resilience 熔断+failed 水位可见，不硬冲（合规第一）

## 落地记录（2026-11-07 W59）

- `_scan_ccgp_words` 抽出为日批/轻扫共用 helper（入库语义逐字一致，
  test_spec_i 日批回归零变化）；`run_hourly_tender_scan` = days=2×1 页。
- 注册：`"7-21/2", 40`（APScheduler 区间步进）+ subscription_rules
  `"*/2",6`→`"*",6`（每小时 :06）。
- tel: 拨号两处：情报页联系人 chips 旁 + 推荐卡号码徽标旁（lucide
  Phone 11px，stopPropagation）；号码文本点击复制交互不变。
- 测试：test_spec_bo.py 2 条（入库+参数捕获+幂等+无买家跳过 /
  包装器水位 'hourly_tender'）。门禁：pytest 311 + build + E2E 7 全绿。
- 观察项：一周内查 ccgp 拦截页告警与 hourly_tender 水位——无拦截
  → 提频至每小时（改注册参数一行）；有拦截 → 维持或回调。
