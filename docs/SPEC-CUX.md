# SPEC-CUX：联系人中心深度强化（对标大厂 dialer）

> 第十九次功能轮（用户指令：contacts 更强大更好用，对标大厂）。
> 调研：Salesloft/Close 键盘驱动 dialer、Veloxy 信号优先排序、
> Outreach 单一工作区。三件：键盘流 / 排序切换 / 通话时间线。

## 1. 键盘流（Close 惯例，电话销售一天 50 通的提效核心）

- `J`/`↓` 下一行、`K`/`↑` 上一行（选中行视觉高亮 accent 左边条+底色）
- `Enter` 打开选中行驾驶舱；`Esc` 关闭（已有）
- 输入框/textarea 聚焦时全部跳过（不干扰打字）
- 列表头右侧加快捷键提示 icon（hover 显示 `J/K 导航 · Enter 打开`）
- 选中态与 visibleCount/筛选变化自动夹取（越界回 0）

## 2. 排序切换（服务端 order，信号优先可解释）

`GET /api/contacts` 加 `order: str = ""`——白名单：
- `""` 智能推荐（默认：可拨→意图→未拨→id，现状逐字节保留）
- `newest`：Contact.id desc（最近入库先打——新采集的新名片）
- `intent`：企业意图分 desc
- `recent_called`：called_at 非 NULL 先按 desc，NULL 尾（回访视角）
- order 非法回退默认；前端筛选条加排序 Select + URL 同步（order 参数）

## 3. 驾驶舱通话时间线（不打断的工作区：历史都在眼前）

`GET /api/contacts/{cid}/prep` 加 `timeline`：
- 拨打事件：called_at（有值才一条，label=当前 call_result 中文）
- 跟进记录：via Contact→Lead join Followup（created_at desc，全部）
- 合流按 created_at desc 排序，项 = {kind: "call"|"followup", at, label/content}
- 前端驾驶舱左栏"怎么聊"下方渲染时间线（最近 5 条 + 折叠）——
  替代现在只有 last_followup 一条的单行

## 4. 测试（test_spec_cux.py）

- order 三态排序断言（newest 按 id、intent 按企业分、recent_called NULL 尾）
- timeline 合流：造 called_at + 2 条 followup → 3 项按时间倒序
- 键盘流为纯前端（E2E：Enter 打开驾驶舱一条断言进 mobile/journey 既有文件）
- 全量绿 + tsc 零错误

## 5. 非目标

- 批量标记/批量跟进（v2）；自动拨号器（无呼叫中心依赖）；AI 排序打分
  （GLM Key 未到位；现有可触达性×信号已是确定性版）
