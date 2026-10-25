"use client";

/**
 * 自动化规则面板（吸收 twenty workflow 精髓：信号事件→条件→动作）。
 * 挂设置页；规则 CRUD + 运行次数/最近结果可视化。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, InputNumber, Modal, Select, SideSheet, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { api } from "@/lib/api";
import { INDUSTRY_OPTIONS } from "@/lib/constants";

interface RuleRow {
  id: number;
  name: string;
  enabled: number;
  cond_signal_type: string | null;
  cond_industry_key: string | null;
  cond_source: string | null;
  cond_min_score: number | null;
  cond_has_contact: number | null;
  cond_min_intent: number | null;
  action_type: string;
  actions: string[];
  else_actions: string[];
  run_count: number;
  last_run_at: string | null;
  last_result: string | null;
}

const ACTION_LABEL: Record<string, string> = {
  notify: "发站内通知",
  create_lead: "自动建线索",
  tag_competitor: "打同行标",
  bump_probability: "赢率+10",
  set_grade: "按分数定级 S/A/B",
  create_todo: "自动建待办(3天)",
};

const EMPTY_FORM = {
  name: "",
  enabled: 1,
  cond_signal_type: "",
  cond_industry_key: "",
  cond_source: "",
  cond_min_score: "",
  cond_has_contact: "",
  cond_min_intent: "",
  action_type: "notify",
  actions: ["notify"] as string[],
  else_actions: [] as string[],
};

export default function AutomationPanel() {
  const [rules, setRules] = useState<RuleRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [runsOpen, setRunsOpen] = useState(false);
  const [runs, setRuns] = useState<
    { id: number; signal_id: number; enterprise_name: string; action_type: string; result: string; ok: number; created_at: string }[]
  >([]);
  const [editing, setEditing] = useState<RuleRow | null>(null);
  const [form, setForm] = useState(EMPTY_FORM);

  const load = useCallback(() => {
    setLoading(true);
    api
      .get<{ items: RuleRow[] }>("/api/automation-rules")
      .then((d) => setRules(d.items))
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  const save = async () => {
    if (!form.name.trim()) {
      Toast.warning("请填写规则名");
      return;
    }
    const payload = {
      name: form.name,
      enabled: form.enabled,
      cond_signal_type: form.cond_signal_type || null,
      cond_industry_key: form.cond_industry_key || null,
      cond_source: form.cond_source || null,
      cond_min_score: form.cond_min_score === "" ? null : Number(form.cond_min_score),
      cond_has_contact: form.cond_has_contact === "" ? null : Number(form.cond_has_contact),
      cond_min_intent: form.cond_min_intent === "" ? null : Number(form.cond_min_intent),
      action_type: form.actions[0] ?? form.action_type,
      actions: form.actions,
      else_actions: form.else_actions,
    };
    try {
      if (editing) await api.patch(`/api/automation-rules/${editing.id}`, payload);
      else await api.post("/api/automation-rules", payload);
      Toast.success(editing ? "规则已更新" : "规则已创建");
      setModalOpen(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    }
  };

  const viewRuns = async (rid: number) => {
    setRunsOpen(true);
    try {
      const d = await api.get<{ items: typeof runs }>(`/api/automation-rules/${rid}/runs`);
      setRuns(d.items);
    } catch {
      setRuns([]);
    }
  };

  const remove = async (id: number) => {
    try {
      await api.delete(`/api/automation-rules/${id}`);
      Toast.success("已删除");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  // dry-run：预览命中（不执行写操作）
  const [dryResult, setDryResult] = useState<{
    would_hit: number;
    else_branch_would: number;
    actions: string[];
    hits: { signal_id: number; enterprise_name: string; signal_title: string }[];
  } | null>(null);
  const [dryLoading, setDryLoading] = useState(false);

  const runDry = async () => {
    if (!editing) return;
    setDryLoading(true);
    try {
      const d = await api.post<typeof dryResult>(
        `/api/automation-rules/${editing.id}/dry-run?limit=10`,
      );
      setDryResult(d);
    } catch {
      setDryResult(null);
    } finally {
      setDryLoading(false);
    }
  };

  const openNew = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setModalOpen(true);
  };

  const openEdit = (r: RuleRow) => {
    setEditing(r);
    setDryResult(null);
    setForm({
      name: r.name,
      enabled: r.enabled,
      cond_signal_type: r.cond_signal_type ?? "",
      cond_industry_key: r.cond_industry_key ?? "",
      cond_source: r.cond_source ?? "",
      cond_min_score: r.cond_min_score == null ? "" : String(r.cond_min_score),
      cond_has_contact: r.cond_has_contact == null ? "" : String(r.cond_has_contact),
      cond_min_intent: r.cond_min_intent == null ? "" : String(r.cond_min_intent),
      action_type: r.action_type,
      actions: r.actions?.length ? r.actions : [r.action_type],
      else_actions: r.else_actions ?? [],
    });
    setModalOpen(true);
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <p className="max-w-[640px] text-[13px] leading-6 text-[var(--muted)]">
          自动化规则：采集产生新信号时自动执行动作（条件全部满足才触发）。执行次数与最近结果见列表；
          每日 04:07 自动扫描，采集任务完成后也会即时触发。
        </p>
        <div className="flex gap-2">
          <Button size="small" theme="borderless" type="tertiary" loading={loading} onClick={load}>
            刷新
          </Button>
          <Button
            theme="solid"
            size="small"
            onClick={() => {
              setEditing(null);
              setForm(EMPTY_FORM);
              setModalOpen(true);
            }}
          >
            + 新建规则
          </Button>
        </div>
      </div>
      <Table<RuleRow>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rules}
        empty="暂无规则 —— 让系统自动干活：中标自动通知、高分自动建线索…"
        pagination={{ pageSize: 8 }}
        columns={[
          {
            title: "规则名",
            dataIndex: "name",
            width: 170,
            render: (v: string) => (
              <span className="text-[13px] font-medium text-[var(--ink)]">{v}</span>
            ),
          },
          {
            title: "条件",
            width: 220,
            render: (_: unknown, r: RuleRow) => {
              const parts: string[] = [];
              if (r.cond_signal_type) parts.push(`类型=${r.cond_signal_type}`);
              if (r.cond_industry_key) parts.push(`行业=${r.cond_industry_key}`);
              if (r.cond_source) parts.push(`来源=${r.cond_source}`);
              if (r.cond_min_score != null) parts.push(`分数≥${r.cond_min_score}`);
              return (
                <span className="text-[12px] text-[var(--muted)]">
                  {parts.join(" 且 ") || "全部信号"}
                </span>
              );
            },
          },
          {
            title: "动作链",
            dataIndex: "actions",
            width: 170,
            render: (_: unknown, r: RuleRow) => {
              const chain = r.actions?.length ? r.actions : [r.action_type];
              return (
                <div className="flex flex-wrap items-center gap-1">
                  {chain.map((a, i) => (
                    <span key={i} className="flex items-center gap-1">
                      {i > 0 && <span className="text-[10px] text-[var(--control)]">→</span>}
                      <Tag color="cyan" type="light" size="small">{ACTION_LABEL[a] ?? a}</Tag>
                    </span>
                  ))}
                  {r.else_actions?.length > 0 && (
                    <Tag color="grey" type="light" size="small">否则: {r.else_actions.length}</Tag>
                  )}
                </div>
              );
            },
          },
          {
            title: "执行",
            dataIndex: "run_count",
            width: 64,
            align: "right" as const,
            render: (v: number) => (
              <span className="tabular-nums text-[12.5px] text-[var(--ink)]">{v}</span>
            ),
          },
          {
            title: "最近结果",
            dataIndex: "last_result",
            width: 130,
            render: (v: string | null) => (
              <span className="truncate text-[11.5px] text-[var(--muted)]">{v || "—"}</span>
            ),
          },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 56,
            render: (v: number) => (
              <Tag color={v ? "green" : "grey"} type="light" size="small">
                {v ? "开" : "关"}
              </Tag>
            ),
          },
          {
            title: "",
            width: 120,
            render: (_: unknown, r: RuleRow) => (
              <div className="flex gap-1.5">
                <Button size="small" theme="borderless" onClick={() => void viewRuns(r.id)}>
                  记录
                </Button>
                <Button size="small" theme="borderless" onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Button size="small" theme="borderless" type="danger" onClick={() => void remove(r.id)}>
                  删
                </Button>
              </div>
            ),
          },
        ]}
      />

      <SideSheet
        title="规则运行记录（最近 50 条）"
        visible={runsOpen}
        onCancel={() => setRunsOpen(false)}
        width={520}
      >
        {runs.length === 0 ? (
          <div className="text-[13px] text-[var(--muted)]">暂无运行记录</div>
        ) : (
          <div className="space-y-2">
            {runs.map((r) => (
              <div key={r.id} className="rounded-lg border border-[var(--border)] px-3 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--ink)]">
                    {r.enterprise_name}
                  </span>
                  <Tag color={r.ok ? "green" : "red"} type="light" size="small">
                    {r.ok ? r.result : "失败"}
                  </Tag>
                </div>
                <div className="mt-0.5 text-[11.5px] text-[var(--muted)]">
                  {String(r.created_at).slice(0, 16).replace("T", " ")} · 信号#{r.signal_id} · {r.action_type}
                </div>
              </div>
            ))}
          </div>
        )}
      </SideSheet>

      <Modal
        title={editing ? `编辑规则 — ${editing.name}` : "新建自动化规则"}
        visible={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={() => void save()}
        okText="保存"
        width={480}
      >
        <div className="space-y-3 pt-1">
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">规则名</div>
            <Input
              value={form.name}
              onChange={(v) => setForm((f) => ({ ...f, name: v }))}
              placeholder="如：科研教育中标自动建线索"
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              动作链（按序号顺序执行，点击切换）
            </div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(ACTION_LABEL).map(([key, label]) => {
                const idx = form.actions.indexOf(key);
                const on = idx >= 0;
                return (
                  <button
                    key={key}
                    onClick={() =>
                      setForm((f) => {
                        const next = on
                          ? f.actions.filter((a) => a !== key)
                          : [...f.actions, key];
                        return { ...f, actions: next.length ? next : f.actions };
                      })
                    }
                    className={`cursor-pointer rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                      on
                        ? "border-[var(--accent)] bg-[color:var(--accent)]/10 text-[var(--accent)]"
                        : "border-[var(--border)] bg-white text-[var(--muted)] hover:border-[var(--accent)]"
                    }`}
                  >
                    {on ? `${idx + 1}. ` : ""}
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              否则执行（条件不满足时的备选链，可选）
            </div>
            <div className="flex flex-wrap gap-1.5">
              {Object.entries(ACTION_LABEL).map(([key, label]) => {
                const on = form.else_actions.includes(key);
                return (
                  <button
                    key={key}
                    onClick={() =>
                      setForm((f) => ({
                        ...f,
                        else_actions: on
                          ? f.else_actions.filter((a) => a !== key)
                          : [...f.else_actions, key],
                      }))
                    }
                    className={`cursor-pointer rounded-full border px-2.5 py-1 text-[12px] transition-colors ${
                      on
                        ? "border-[var(--control)] bg-[var(--raised)] text-[var(--ink)]"
                        : "border-[var(--border)] bg-white text-[var(--muted)] hover:border-[var(--control)]"
                    }`}
                  >
                    {label}
                  </button>
                );
              })}
            </div>
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              条件（全部满足才触发，留空=不限）
            </div>
            <div className="space-y-2">
              <Select
                style={{ width: "100%" }}
                placeholder="信号类型"
                showClear
                value={form.cond_signal_type || undefined}
                optionList={[
                  { value: "tender_win", label: "中标" },
                  { value: "tender_buy", label: "公开采购" },
                  { value: "tender", label: "政采公告" },
                ]}
                onChange={(v) => setForm((f) => ({ ...f, cond_signal_type: (v as string) || "" }))}
              />
              <Select
                style={{ width: "100%" }}
                placeholder="行业"
                showClear
                value={form.cond_industry_key || undefined}
                optionList={INDUSTRY_OPTIONS.map((i) => ({ value: i.key, label: i.name }))}
                onChange={(v) => setForm((f) => ({ ...f, cond_industry_key: (v as string) || "" }))}
              />
              <Select
                style={{ width: "100%" }}
                placeholder="来源"
                showClear
                value={form.cond_source || undefined}
                optionList={[
                  { value: "ggzy", label: "官方中标数据" },
                  { value: "ccgp", label: "政采网" },
                  { value: "csv", label: "CSV 导入" },
                ]}
                onChange={(v) => setForm((f) => ({ ...f, cond_source: (v as string) || "" }))}
              />
              <InputNumber
                style={{ width: "100%" }}
                placeholder="信号分数 ≥（如 40）"
                min={0}
                max={100}
                value={form.cond_min_score === "" ? undefined : Number(form.cond_min_score)}
                onChange={(v) => setForm((f) => ({ ...f, cond_min_score: v == null ? "" : String(v) }))}
              />
              <Select
                style={{ width: "100%" }}
                placeholder="联系人电话"
                showClear
                value={form.cond_has_contact || undefined}
                optionList={[
                  { value: "1", label: "有联系人电话" },
                  { value: "0", label: "无联系人电话" },
                ]}
                onChange={(v) => setForm((f) => ({ ...f, cond_has_contact: (v as string) || "" }))}
              />
              <InputNumber
                style={{ width: "100%" }}
                placeholder="意图分 ≥（如 30）"
                min={0}
                max={100}
                value={form.cond_min_intent === "" ? undefined : Number(form.cond_min_intent)}
                onChange={(v) => setForm((f) => ({ ...f, cond_min_intent: v == null ? "" : String(v) }))}
              />
            </div>
          </div>
          {editing && (
            <div className="rounded-lg border border-dashed border-[var(--border)] p-3">
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[12.5px] font-medium text-[var(--ink)]">试运行预览</span>
                <Button size="small" theme="borderless" loading={dryLoading} onClick={() => void runDry()}>
                  扫描最近信号
                </Button>
              </div>
              {dryResult ? (
                <div className="text-[12px] leading-5 text-[var(--muted)]">
                  会命中 <span className="font-medium text-[var(--accent)]">{dryResult.would_hit}</span> 条
                  （否则分支 {dryResult.else_branch_would} 条），将执行：
                  {dryResult.actions.map((a) => ACTION_LABEL[a] ?? a).join(" → ")}
                  {dryResult.hits.length > 0 && (
                    <div className="mt-1.5 space-y-0.5">
                      {dryResult.hits.slice(0, 4).map((h) => (
                        <div key={h.signal_id} className="truncate">
                          · {h.enterprise_name}（{h.signal_title}）
                        </div>
                      ))}
                      {dryResult.hits.length > 4 && <div>…共 {dryResult.hits.length} 条</div>}
                    </div>
                  )}
                </div>
              ) : (
                <div className="text-[11.5px] text-[var(--muted)]">
                  不执行任何写操作，预览这条规则会打到哪些企业
                </div>
              )}
            </div>
          )}
          <div className="flex items-center gap-2">
            <span className="text-[12.5px] text-[var(--muted)]">启用</span>
            <input
              type="checkbox"
              checked={form.enabled === 1}
              onChange={(e) => setForm((f) => ({ ...f, enabled: e.target.checked ? 1 : 0 }))}
              className="accent-[var(--accent)]"
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
