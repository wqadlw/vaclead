"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Descriptions, Input, InputNumber, Table, Tabs, Tag, Toast } from "@douyinfe/semi-ui";
import { Bot, KeyRound } from "lucide-react";
import AppShell from "@/components/AppShell";
import AutomationPanel from "@/components/AutomationPanel";
import { api } from "@/lib/api";
import type { PageData } from "@/lib/api";
import { INDUSTRY_OPTIONS } from "@/lib/constants";

interface SettingsData {
  amap_key_masked: string | null;
  zhiliaoke_key_masked: string | null;
  agent_backends: Record<string, string>;
}

interface PoolSettingsData {
  name: string;
  recycle_days: number;
  receive_limit: number;
  remind_days: number;
}

interface AgentRunRow {
  id: number;
  agent_name: string;
  backend: string;
  decision_summary: string;
  duration_ms: number;
  task_id: string | null;
  created_at: string;
}

interface ReviewRow {
  id: number;
  score: number;
  reason: string;
  status: string;
  candidate: { id: number; name: string; source?: string } | null;
  target: { id: number; name: string; source?: string } | null;
}

const DEVICE_TERMS_PREVIEW = [
  "真空泵", "真空机组", "罗茨泵", "分子泵", "旋片泵", "螺杆真空泵", "爪式真空泵",
  "涡旋真空泵", "扩散泵", "真空镀膜机", "磁控溅射", "蒸发镀膜", "PVD设备", "CVD设备",
  "冻干机", "真空冷冻干燥机", "真空干燥箱", "真空烧结炉", "真空热处理炉", "真空腔体",
  "真空检漏", "真空包装机", "真空获得设备", "真空阀门",
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [pool, setPool] = useState<PoolSettingsData | null>(null);
  const [runs, setRuns] = useState<PageData<AgentRunRow> | null>(null);
  const [runPage, setRunPage] = useState(1);
  const [saving, setSaving] = useState(false);
  const [savingPool, setSavingPool] = useState(false);
  const [amapKey, setAmapKey] = useState("");
  const [zhilKey, setZhilKey] = useState("");
  const [recycleDays, setRecycleDays] = useState<number>(30);
  const [receiveLimit, setReceiveLimit] = useState<number>(0);
  const [remindDays, setRemindDays] = useState<number>(3);
  // 数据复核（SPEC-B §4）
  const [reviewItems, setReviewItems] = useState<ReviewRow[]>([]);
  const [reviewLoading, setReviewLoading] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);

  const loadReview = useCallback(() => {
    setReviewLoading(true);
    api
      .get<{ items: ReviewRow[] }>("/api/review-queue?status=all")
      .then((d) => setReviewItems(d.items))
      .catch(() => undefined)
      .finally(() => setReviewLoading(false));
  }, []);

  const actReview = async (rid: number, action: "merge" | "reject") => {
    setActingId(rid);
    try {
      await api.post(`/api/review-queue/${rid}/${action}`);
      Toast.success(action === "merge" ? "已合并为一档" : "已标记为不同主体");
      loadReview();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActingId(null);
    }
  };

  const loadSettings = useCallback(() => {
    api.get<SettingsData>("/api/settings").then(setSettings).catch(() => undefined);
    api.get<PoolSettingsData>("/api/pool-settings").then((p) => {
      setPool(p);
      setRecycleDays(p.recycle_days);
      setReceiveLimit(p.receive_limit);
      setRemindDays(p.remind_days);
    }).catch(() => undefined);
  }, []);

  const loadRuns = useCallback(
    (p: number) => {
      api
        .get<PageData<AgentRunRow>>(`/api/agent-runs?page=${p}&page_size=15`)
        .then(setRuns)
        .catch(() => undefined);
    },
    [],
  );

  useEffect(() => loadSettings(), [loadSettings]);
  useEffect(() => loadRuns(runPage), [runPage, loadRuns]);
  useEffect(() => loadReview(), [loadReview]);

  // 通知偏好（W25：Novu subscriber preferences 模式）
  const [notifyChannels, setNotifyChannels] = useState<
    { key: string; label: string; available: boolean }[]
  >([]);
  const [notifyPrefs, setNotifyPrefs] = useState<string[]>(["in_app"]);

  const loadNotifyPrefs = useCallback(() => {
    api
      .get<{ channels: { key: string; label: string; available: boolean }[]; preferences: string[] }>(
        "/api/notify-preferences",
      )
      .then((d) => {
        setNotifyChannels(d.channels);
        setNotifyPrefs(d.preferences);
      })
      .catch(() => undefined);
  }, []);

  const saveNotifyPrefs = (next: string[]) => {
    setNotifyPrefs(next);
    api.put("/api/notify-preferences", { channels: next }).catch(() => {
      Toast.error("保存失败");
      loadNotifyPrefs();
    });
  };

  useEffect(() => loadNotifyPrefs(), [loadNotifyPrefs]);

  const saveKeys = async () => {
    if (!amapKey && !zhilKey) {
      Toast.warning("请至少填写一个 Key");
      return;
    }
    setSaving(true);
    try {
      await api.put("/api/settings", {
        amap_key: amapKey || undefined,
        zhiliaoke_key: zhilKey || undefined,
      });
      Toast.success("已保存");
      setAmapKey("");
      setZhilKey("");
      loadSettings();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const savePool = async () => {
    setSavingPool(true);
    try {
      await api.put("/api/pool-settings", {
        recycle_days: recycleDays,
        receive_limit: receiveLimit,
        remind_days: remindDays,
      });
      Toast.success("公海规则已保存");
      loadSettings();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSavingPool(false);
    }
  };

  return (
    <AppShell>
      <div>
        <Tabs
          type="line"
          className="rounded-xl border border-[var(--border)] bg-white !px-2"
          tabList={[
            { tab: "数据源 Key", itemKey: "keys" },
            { tab: "公海规则", itemKey: "pool" },
            { tab: "数据复核", itemKey: "review" },
            { tab: "自动化", itemKey: "automation" },
            { tab: "通知偏好", itemKey: "notify" },
            { tab: "词表预览", itemKey: "vocab" },
            { tab: "Agent 日志", itemKey: "agents" },
            { tab: "后端切换", itemKey: "backends" },
          ]}
        >
          {/* Keys */}
          <div className="p-6 max-w-[560px]">
            <div className="text-[13px] text-slate-500 mb-5 leading-6">
              高德 Key 用于地图拓客（<a className="text-[var(--accent)]" href="https://lbs.amap.com" target="_blank" rel="noreferrer">lbs.amap.com</a> 申请），
              知了标讯 Key 用于招标自动采集。未配置时对应功能禁用、CSV 导入兜底。
            </div>
            <Descriptions
              size="small"
              className="!mb-5"
              data={[
                {
                  key: "高德 Key",
                  value: settings?.amap_key_masked ? <Tag color="green" type="light">{settings.amap_key_masked}</Tag> : <Tag color="grey" type="light">未配置</Tag>,
                },
                {
                  key: "知了标讯 Key",
                  value: settings?.zhiliaoke_key_masked ? <Tag color="green" type="light">{settings.zhiliaoke_key_masked}</Tag> : <Tag color="grey" type="light">未配置</Tag>,
                },
              ]}
            />
            <div className="space-y-3.5">
              <div>
                <div className="flex items-center gap-1.5 text-[13px] font-medium text-slate-600 mb-1.5">
                  <KeyRound size={13} /> 高德 Web 服务 Key（多 Key 用英文逗号分隔）
                </div>
                <Input mode="password" placeholder="留空表示不修改" value={amapKey} onChange={setAmapKey} />
              </div>
              <div>
                <div className="flex items-center gap-1.5 text-[13px] font-medium text-slate-600 mb-1.5">
                  <KeyRound size={13} /> 知了标讯 API Key
                </div>
                <Input mode="password" placeholder="留空表示不修改" value={zhilKey} onChange={setZhilKey} />
              </div>
              <Button theme="solid" loading={saving} onClick={saveKeys}>
                保存
              </Button>
            </div>
          </div>

          {/* 公海规则 */}
          <div className="p-6">
            <div className="max-w-[560px]">
              <p className="mb-5 text-[13px] leading-6 text-[var(--muted)]">
                公海规则由每晚 02:00 的回收任务执行：超过 N 天未跟进的线索自动退回公海；领取上限限制每人同时持有的线索数。
              </p>
              <div className="space-y-4">
                <div className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-white px-4 py-3">
                  <div>
                    <div className="text-[13.5px] font-medium text-[var(--ink)]">回收天数</div>
                    <div className="text-[12px] text-[var(--muted)]">跟进/领取超过该天数未更新的线索退回公海</div>
                  </div>
                  <InputNumber style={{ width: 90 }} min={1} max={365} value={recycleDays} onChange={(v) => setRecycleDays(typeof v === "number" ? v : 30)} />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-white px-4 py-3">
                  <div>
                    <div className="text-[13.5px] font-medium text-[var(--ink)]">领取上限</div>
                    <div className="text-[12px] text-[var(--muted)]">每人同时持有的跟进中+意向线索数上限，0 为不限</div>
                  </div>
                  <InputNumber style={{ width: 90 }} min={0} max={999} value={receiveLimit} onChange={(v) => setReceiveLimit(typeof v === "number" ? v : 0)} />
                </div>
                <div className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-white px-4 py-3">
                  <div>
                    <div className="text-[13.5px] font-medium text-[var(--ink)]">到期提醒</div>
                    <div className="text-[12px] text-[var(--muted)]">回收前 N 天在工作台提示（预留）</div>
                  </div>
                  <InputNumber style={{ width: 90 }} min={0} max={30} value={remindDays} onChange={(v) => setRemindDays(typeof v === "number" ? v : 3)} />
                </div>
                <Button theme="solid" loading={savingPool} onClick={savePool}>
                  保存公海规则
                </Button>
              </div>
            </div>
          </div>

          {/* 数据复核 */}
          <div className="p-6">
            <div className="flex items-center justify-between mb-4">
              <p className="text-[13px] leading-6 text-[var(--muted)] max-w-[640px]">
                归一引擎对 80-89 分的疑似重复企业不做静默合并，落在此处等待人工裁决：确认同主体选「合并为一档」（信号与别名全部迁移），确为两家选「不是同一家」。
              </p>
              <Button theme="borderless" type="tertiary" loading={reviewLoading} onClick={loadReview}>
                刷新
              </Button>
            </div>
            <Table<ReviewRow>
              rowKey="id"
              size="small"
              loading={reviewLoading}
              dataSource={reviewItems}
              empty="暂无待复核项 —— 采集/导入触发归一时，80-89 分的疑似重复会出现在这里"
              pagination={{ pageSize: 10 }}
              columns={[
                {
                  title: "候选企业",
                  dataIndex: "candidate",
                  width: 220,
                  render: (_: unknown, r: ReviewRow) =>
                    r.candidate ? (
                      <div>
                        <div className="text-[13px] font-medium text-[var(--ink)] truncate max-w-[210px]" title={r.candidate.name}>{r.candidate.name}</div>
                        <div className="text-[11px] text-[var(--muted)]">来源 {r.candidate.source || "—"}</div>
                      </div>
                    ) : (
                      <span className="text-[var(--muted)]">（已并入）</span>
                    ),
                },
                {
                  title: "归并目标",
                  dataIndex: "target",
                  width: 220,
                  render: (_: unknown, r: ReviewRow) =>
                    r.target ? (
                      <div>
                        <div className="text-[13px] font-medium text-[var(--ink)] truncate max-w-[210px]" title={r.target.name}>{r.target.name}</div>
                        <div className="text-[11px] text-[var(--muted)]">来源 {r.target.source || "—"}</div>
                      </div>
                    ) : (
                      <span className="text-[var(--muted)]">—</span>
                    ),
                },
                {
                  title: "相似度",
                  dataIndex: "score",
                  width: 76,
                  render: (v: number) => <Tag color="cyan" type="light">{v}</Tag>,
                },
                {
                  title: "状态",
                  dataIndex: "status",
                  width: 76,
                  render: (v: string) => (
                    <Tag color={v === "pending" ? "amber" : v === "merged" ? "green" : "grey"} type="light">
                      {v === "pending" ? "待复核" : v === "merged" ? "已合并" : "已驳回"}
                    </Tag>
                  ),
                },
                {
                  title: "",
                  width: 170,
                  render: (_: unknown, r: ReviewRow) =>
                    r.status === "pending" ? (
                      <div className="flex gap-2">
                        <Button size="small" theme="solid" loading={actingId === r.id} onClick={() => actReview(r.id, "merge")}>
                          合并
                        </Button>
                        <Button size="small" theme="outline" loading={actingId === r.id} onClick={() => actReview(r.id, "reject")}>
                          不是同一家
                        </Button>
                      </div>
                    ) : null,
                },
              ]}
            />
          </div>

          {/* 自动化规则 */}
          <AutomationPanel />

          {/* 通知偏好（Novu subscriber preferences 模式） */}
          <div className="p-6">
            <p className="mb-4 max-w-[560px] text-[13px] leading-6 text-[var(--muted)]">
              通知渠道偏好：自动化规则与订阅动态按此分发。站内为保底渠道（不可关）；
              邮件需配置 EmailEngine 地址后生效；企微为 P3 规划。
            </p>
            <div className="max-w-[420px] space-y-2">
              {(notifyChannels ?? []).map((c) => (
                <label
                  key={c.key}
                  className={`flex items-center justify-between rounded-lg border px-4 py-3 ${
                    c.key === "in_app" ? "border-[var(--border)] bg-[var(--raised)]" : "border-[var(--border)] bg-white"
                  }`}
                >
                  <span className="text-[13.5px] text-[var(--ink)]">
                    {c.label}
                    {c.key === "in_app" && <span className="ml-1.5 text-[11.5px] text-[var(--muted)]">（保底）</span>}
                    {!c.available && c.key !== "in_app" && (
                      <span className="ml-1.5 text-[11.5px] text-[var(--warning)]">未配置</span>
                    )}
                  </span>
                  <input
                    type="checkbox"
                    checked={notifyPrefs.includes(c.key)}
                    disabled={c.key === "in_app"}
                    onChange={(e) => {
                      const next = e.target.checked
                        ? [...notifyPrefs, c.key]
                        : notifyPrefs.filter((k) => k !== c.key);
                      if (!next.includes("in_app")) next.unshift("in_app");
                      saveNotifyPrefs(next);
                    }}
                    className="h-4 w-4 cursor-pointer accent-[var(--accent)]"
                  />
                </label>
              ))}
            </div>
          </div>

          {/* 词表 */}
          <div className="p-6">
            <div className="text-[13.5px] font-medium text-slate-700 mb-3">设备词表（{DEVICE_TERMS_PREVIEW.length}+，config/vacuum.yaml）</div>
            <div className="flex flex-wrap gap-1.5 mb-6">
              {DEVICE_TERMS_PREVIEW.map((t) => (
                <Tag key={t} color="indigo" type="light">{t}</Tag>
              ))}
            </div>
            <div className="text-[13.5px] font-medium text-slate-700 mb-3">下游行业（按采购优先级）</div>
            <div className="grid grid-cols-2 lg:grid-cols-3 gap-2.5 max-w-[760px]">
              {INDUSTRY_OPTIONS.map((i) => (
                <div key={i.key} className="rounded-xl border border-slate-100 bg-slate-50/70 px-3.5 py-2.5">
                  <div className="text-[13px] font-medium text-slate-700">{i.name}</div>
                  <div className="text-[11px] text-slate-400 mt-0.5">
                    {i.key} · tier {i.tier}
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Agent 日志 */}
          <div className="p-4">
            <Table<AgentRunRow>
              rowKey="id"
              size="small"
              dataSource={runs?.items ?? []}
              empty="暂无 Agent 运行记录 —— 启动采集任务后可在此查看每个 Agent 的决策日志"
              pagination={{
                currentPage: runPage,
                pageSize: 15,
                total: runs?.total ?? 0,
                onPageChange: (p: number) => setRunPage(p),
              }}
              columns={[
                { title: "Agent", dataIndex: "agent_name", width: 150, render: (v) => <span className="font-medium text-slate-700">{v}</span> },
                {
                  title: "后端",
                  dataIndex: "backend",
                  width: 90,
                  render: (v) => <Tag color={v === "rule" ? "blue" : "purple"} type="light">{v}</Tag>,
                },
                { title: "决策摘要", dataIndex: "decision_summary", ellipsis: true },
                { title: "耗时", dataIndex: "duration_ms", width: 90, render: (v) => <span className="tabular-nums text-slate-500">{v}ms</span> },
                {
                  title: "时间",
                  dataIndex: "created_at",
                  width: 160,
                  render: (v) => <span className="text-slate-400 text-[12px]">{String(v).slice(0, 19).replace("T", " ")}</span>,
                },
              ]}
            />
          </div>

          {/* 后端切换 */}
          <div className="p-6">
            <div className="flex items-center gap-2 text-[14px] font-medium text-slate-700 mb-4">
              <Bot size={16} className="text-[var(--accent)]" /> Agent 后端实现
            </div>
            <div className="max-w-[520px] space-y-2">
              {Object.entries(settings?.agent_backends ?? {}).map(([name, backend]) => (
                <div key={name} className="flex items-center justify-between rounded-xl border border-slate-100 bg-slate-50/70 px-4 py-2.5">
                  <span className="text-[13.5px] text-slate-700 font-mono">{name}</span>
                  <Tag color={backend === "rule" ? "blue" : "purple"} type="light">
                    {backend === "rule" ? "规则引擎" : "LLM"}
                  </Tag>
                </div>
              ))}
            </div>
            <p className="text-slate-400 text-[12px] leading-5 mt-4 max-w-[520px]">
              LLM 后端为 P2 预留位：配置 GLM/DeepSeek 等 OpenAI 兼容 Key 后，可将 signal_parser / icp_judge / query_translator 切换为 LLM 实现，编排图零改动。
            </p>
          </div>
        </Tabs>
      </div>
    </AppShell>
  );
}
