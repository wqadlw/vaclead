"use client";

/**
 * 客户画像（SPEC-SEG2 §4.1）：customer_segment 实体化主表页。
 * 契约（与后端对齐；后端并行上线中——所有请求失败静默降级，不白屏）：
 * - GET  /api/segments            → { items: [...] } 或裸数组（全员可看；每群实时统计）
 * - POST /api/segments            → 新建（admin；code 唯一）
 * - PUT  /api/segments/{id}       → 更新（admin；部分字段可省）
 * - 无 DELETE：下线一律用「停用」（enabled=0 即退出推荐/统计/筛选选项）
 * 页面全员可见，编辑/新建/启停仅 admin（role 判定照话术库面板：localStorage vaclead_role）。
 * 行内「看名单」跳 /enterprises?segment=<code>（企业列表按该群 industry_keys 过滤）。
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Input, Modal, Select, Switch, Table, Tag, TagInput, Toast } from "@douyinfe/semi-ui";
import { RefreshCw } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { INDUSTRY_OPTIONS } from "@/lib/constants";

export interface SegmentRow {
  id: number;
  name: string;
  code: string;
  keywords?: string[] | null;
  industry_keys?: string[] | null;
  business_lines?: string[] | null;
  priority?: string | null;
  enabled?: number | boolean;
  notes?: string | null;
  enterprise_count?: number | null;
  dialable_count?: number | null;
  sort?: number | null;
}

/** 列表端点容错：兼容 { items: [...] } 与裸数组（照话术库面板 listOf 同款），异常一律空态 */
function listOf<T>(d: unknown): T[] {
  if (Array.isArray(d)) return d as T[];
  if (d && typeof d === "object" && Array.isArray((d as { items?: unknown }).items)) {
    return (d as { items: T[] }).items;
  }
  return [];
}

/** 优先级：P0 红（主攻）/ P1 蓝（常规）/ P2 灰（观察） */
const PRIORITY_OPTIONS = [
  { value: "P0", label: "P0 · 主攻" },
  { value: "P1", label: "P1 · 常规" },
  { value: "P2", label: "P2 · 观察" },
];
const PRIORITY_TAG: Record<string, "red" | "blue" | "grey"> = { P0: "red", P1: "blue", P2: "grey" };

/** 业务线基准词表（SPEC-SEG2 编辑字段）——种子里的「泵」「系统集成」等动态并入下拉 */
const BUSINESS_LINE_BASE = ["整机", "系统", "维修", "配件", "泵油", "整线"];

/** industry_keys 中文名映射（INDUSTRY_OPTIONS 之外的键原样显示） */
const INDUSTRY_KEY_NAME: Record<string, string> = Object.fromEntries(
  INDUSTRY_OPTIONS.map((i) => [i.key, i.name]),
);

interface SegmentForm {
  name: string;
  code: string;
  keywords: string[];
  industry_keys: string[];
  business_lines: string[];
  priority: string;
  notes: string;
  enabled: boolean;
}

const EMPTY_FORM: SegmentForm = {
  name: "",
  code: "",
  keywords: [],
  industry_keys: [],
  business_lines: [],
  priority: "P0",
  notes: "",
  enabled: true,
};

/** JSON 数组字符串防御：后端旧响应可能给 '["a"]' 字符串而非数组 */
function strList(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((x): x is string => typeof x === "string" && x.length > 0);
  if (typeof v === "string" && v.trim()) {
    try {
      const parsed = JSON.parse(v);
      return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
    } catch {
      return [];
    }
  }
  return [];
}

export default function ProfilePage() {
  const router = useRouter();
  // rows === null 表示 /api/segments 请求失败 → 整卡「暂不可用」态（照 SourcesPanel/话术库模式）
  const [rows, setRows] = useState<SegmentRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [isAdmin, setIsAdmin] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState<SegmentRow | null>(null);
  const [form, setForm] = useState<SegmentForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);

  useEffect(() => setIsAdmin(localStorage.getItem("vaclead_role") === "admin"), []);

  const load = useCallback(() => {
    setLoading(true);
    api
      .get<unknown>("/api/segments")
      .then((d) => {
        const items = listOf<SegmentRow>(d);
        // 展示顺序：enabled 在前，再按 priority P0>P1>P2（后端给了 sort 则尊重）
        const priRank: Record<string, number> = { P0: 0, P1: 1, P2: 2 };
        items.sort((a, b) => {
          if (!!a.enabled !== !!b.enabled) return a.enabled ? -1 : 1;
          const sa = typeof a.sort === "number" ? a.sort : 99;
          const sb = typeof b.sort === "number" ? b.sort : 99;
          if (sa !== sb) return sa - sb;
          return (priRank[a.priority ?? ""] ?? 9) - (priRank[b.priority ?? ""] ?? 9);
        });
        setRows(items);
      })
      .catch(() => setRows(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  const openNew = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setSheetOpen(true);
  };

  const openEdit = (r: SegmentRow) => {
    setEditing(r);
    setForm({
      name: r.name ?? "",
      code: r.code ?? "",
      keywords: strList(r.keywords),
      industry_keys: strList(r.industry_keys),
      business_lines: strList(r.business_lines),
      priority: PRIORITY_TAG[r.priority ?? ""] ? (r.priority as string) : "P2",
      notes: r.notes ?? "",
      enabled: !!r.enabled,
    });
    setSheetOpen(true);
  };

  const save = async () => {
    if (!form.name.trim()) {
      Toast.warning("请填写客户群名称（销售语言，如：镀膜加工厂）");
      return;
    }
    if (!editing && !form.code.trim()) {
      Toast.warning("请填写 code（唯一标识，如：coating_factory）");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        code: form.code.trim(),
        keywords: form.keywords,
        industry_keys: form.industry_keys,
        business_lines: form.business_lines,
        priority: form.priority,
        notes: form.notes.trim() || null,
        enabled: form.enabled ? 1 : 0,
      };
      if (editing) {
        await api.put(`/api/segments/${editing.id}`, payload);
        Toast.success(`客户群「${payload.name}」已更新`);
      } else {
        await api.post("/api/segments", payload);
        Toast.success(`客户群「${payload.name}」已创建`);
      }
      setSheetOpen(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (r: SegmentRow, next: boolean) => {
    setActingId(r.id);
    try {
      await api.put(`/api/segments/${r.id}`, { enabled: next ? 1 : 0 });
      setRows((prev) =>
        prev ? prev.map((x) => (x.id === r.id ? { ...x, enabled: next ? 1 : 0 } : x)) : prev,
      );
      Toast.success(next ? `已启用「${r.name}」` : `已停用「${r.name}」（退出推荐/统计/筛选）`);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActingId(null);
    }
  };

  /** 业务线选项 = 基准词表 ∪ 已有行出现过的值（种子含「泵」「系统集成」，防选项缺失） */
  const lineOptions = useMemo(() => {
    const known = new Set(BUSINESS_LINE_BASE);
    const extra: string[] = [];
    for (const r of rows ?? []) {
      for (const l of strList(r.business_lines)) {
        if (!known.has(l)) {
          known.add(l);
          extra.push(l);
        }
      }
    }
    return [...BUSINESS_LINE_BASE, ...extra].map((v) => ({ value: v, label: v }));
  }, [rows]);

  /** 行业键选项 = INDUSTRY_OPTIONS ∪ 已有行出现过的键（名单归属依据可超出固定类目） */
  const industryKeyOptions = useMemo(() => {
    const base: { value: string; label: string }[] = INDUSTRY_OPTIONS.map((i) => ({
      value: i.key,
      label: i.name,
    }));
    const known = new Set<string>(base.map((o) => o.value));
    const extra: { value: string; label: string }[] = [];
    for (const r of rows ?? []) {
      for (const k of strList(r.industry_keys)) {
        if (!known.has(k)) {
          known.add(k);
          extra.push({ value: k, label: k });
        }
      }
    }
    return [...base, ...extra];
  }, [rows]);

  const numOrDash = (v: number | null | undefined) =>
    typeof v === "number" ? v.toLocaleString("zh-CN") : "—";

  return (
    <AppShell>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[20px] font-semibold text-[var(--ink)]">客户画像</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--muted)]">
            客户群定义驱动推荐/名单/采集——改这里，全系统跟着变
          </p>
        </div>
        <div className="flex gap-2">
          {isAdmin && (
            <Button theme="solid" size="small" onClick={openNew}>
              + 新建客户群
            </Button>
          )}
          <Button size="small" icon={<RefreshCw size={13} />} loading={loading} onClick={load}>
            刷新
          </Button>
        </div>
      </div>

      {rows === null ? (
        <div className="mt-4 rounded-lg border border-[var(--border)] bg-white py-10 text-center text-[13px] text-[var(--muted)]">
          暂不可用 —— 客户画像服务未就绪（后端并行上线中），稍后点「刷新」重试
        </div>
      ) : (
        <div className="mt-4">
          <Table<SegmentRow>
            rowKey="id"
            size="small"
            loading={loading}
            dataSource={rows}
            empty="暂无客户群 —— 等后端种子任务自动建十群，或管理员点「+ 新建客户群」"
            pagination={{ pageSize: 10 }}
            columns={[
              {
                title: "客户群",
                dataIndex: "name",
                width: 200,
                render: (v: string, r: SegmentRow) => (
                  <div className="max-w-[190px]">
                    <div className="truncate text-[13px] font-medium text-[var(--ink)]" title={v}>
                      {v || "—"}
                    </div>
                    <div className="truncate font-mono text-[11px] text-[var(--muted)]" title={r.notes ?? ""}>
                      {r.code}
                      {r.notes ? ` · ${r.notes}` : ""}
                    </div>
                  </div>
                ),
              },
              {
                title: "关键词（POI 采集词）",
                dataIndex: "keywords",
                width: 240,
                render: (_: unknown, r: SegmentRow) => {
                  const kws = strList(r.keywords);
                  if (kws.length === 0) return <span className="text-[12px] text-[var(--muted)]">词表待补</span>;
                  const shown = kws.slice(0, 4);
                  return (
                    <div className="flex flex-wrap gap-1">
                      {shown.map((k) => (
                        <Tag key={k} color="cyan" type="light" size="small">
                          {k}
                        </Tag>
                      ))}
                      {kws.length > shown.length && (
                        <span title={kws.join(" / ")}>
                          <Tag color="grey" type="light" size="small">
                            +{kws.length - shown.length}
                          </Tag>
                        </span>
                      )}
                    </div>
                  );
                },
              },
              {
                title: "业务线",
                dataIndex: "business_lines",
                width: 160,
                render: (_: unknown, r: SegmentRow) => {
                  const lines = strList(r.business_lines);
                  if (lines.length === 0) return <span className="text-[12px] text-[var(--muted)]">—</span>;
                  return (
                    <div className="flex flex-wrap gap-1">
                      {lines.slice(0, 5).map((l) => (
                        <Tag key={l} color="white" type="light" size="small">
                          {l}
                        </Tag>
                      ))}
                    </div>
                  );
                },
              },
              {
                title: "优先级",
                dataIndex: "priority",
                width: 90,
                render: (v: string | null) => (
                  <Tag color={PRIORITY_TAG[v ?? ""] ?? "grey"} type="light" size="small">
                    {v || "P2"}
                  </Tag>
                ),
              },
              {
                title: "企业数",
                dataIndex: "enterprise_count",
                width: 90,
                align: "right" as const,
                render: (v: number | null | undefined) => (
                  <span className="tabular-nums text-[12.5px] text-[var(--ink)]" title="行业归属且非竞对/非买家的存活企业">
                    {numOrDash(v)}
                  </span>
                ),
              },
              {
                title: "可拨数",
                dataIndex: "dialable_count",
                width: 90,
                align: "right" as const,
                render: (v: number | null | undefined) => (
                  <span
                    className={`tabular-nums text-[12.5px] ${
                      typeof v === "number" && v > 0 ? "font-medium text-[var(--positive)]" : "text-[var(--muted)]"
                    }`}
                    title="该群企业去重可拨联系人"
                  >
                    {numOrDash(v)}
                  </span>
                ),
              },
              {
                title: "状态",
                dataIndex: "enabled",
                width: 72,
                render: (_: unknown, r: SegmentRow) =>
                  isAdmin ? (
                    <Switch
                      size="small"
                      checked={!!r.enabled}
                      loading={actingId === r.id}
                      onChange={(checked: boolean) => void toggleEnabled(r, checked)}
                    />
                  ) : (
                    <Tag color={r.enabled ? "green" : "grey"} type="light" size="small">
                      {r.enabled ? "启用" : "停用"}
                    </Tag>
                  ),
              },
              {
                title: "操作",
                width: 170,
                render: (_: unknown, r: SegmentRow) => (
                  <div className="flex gap-1.5">
                    {isAdmin && (
                      <Button size="small" theme="borderless" onClick={() => openEdit(r)}>
                        编辑
                      </Button>
                    )}
                    <Button
                      size="small"
                      theme="borderless"
                      onClick={() => router.push(`/enterprises?segment=${encodeURIComponent(r.code)}`)}
                    >
                      看名单
                    </Button>
                    {isAdmin && (
                      <Button
                        size="small"
                        theme="borderless"
                        type={r.enabled ? "danger" : "tertiary"}
                        loading={actingId === r.id}
                        onClick={() => void toggleEnabled(r, !r.enabled)}
                      >
                        {r.enabled ? "停用" : "启用"}
                      </Button>
                    )}
                  </div>
                ),
              },
            ]}
          />
          <p className="mt-2 text-[11.5px] text-[var(--muted)]">
            统计口径：企业行业归属 ∈ 该群 industry_keys 且非竞对/非政采买家/非科研医疗等客户排除条件；可拨 = 该群企业去重可拨联系人。「看名单」在企业列表按客户群过滤（?segment=code）。
          </p>
        </div>
      )}

      {/* 新建 / 编辑 Modal（admin）：name/code/keywords 多选/industry_keys 多选/业务线多选/优先级/notes/enabled */}
      <Modal
        title={editing ? `编辑客户群 — ${editing.name}` : "新建客户群"}
        visible={sheetOpen}
        onCancel={() => setSheetOpen(false)}
        onOk={() => void save()}
        okText="保存"
        cancelText="取消"
        width={600}
        maskClosable={false}
      >
        <div className="space-y-3.5 py-1">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1 text-[12.5px] text-[var(--muted)]">名称 *（销售语言）</div>
              <Input
                value={form.name}
                placeholder="如：镀膜加工厂"
                showClear
                onChange={(v) => setForm((f) => ({ ...f, name: v }))}
              />
            </div>
            <div>
              <div className="mb-1 text-[12.5px] text-[var(--muted)]">code *（唯一标识{editing ? "，不可改" : ""}）</div>
              {editing ? (
                <div className="rounded-lg border border-[var(--rule)] bg-[var(--raised)] px-3 py-1.5 font-mono text-[12.5px] text-[var(--muted)]">
                  {form.code}
                </div>
              ) : (
                <Input
                  value={form.code}
                  placeholder="如：coating_factory"
                  onChange={(v) => setForm((f) => ({ ...f, code: v.replace(/\s+/g, "_").toLowerCase() }))}
                />
              )}
            </div>
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              POI 采集词（回车添加，驱动「按客户群带词」采集）
            </div>
            <TagInput
              value={form.keywords}
              placeholder="输入后回车，如：真空镀膜 / 水镀 / 光学镀膜"
              addOnBlur
              onChange={(vals: string[]) => {
                const out: string[] = [];
                for (const v of vals) {
                  const t = (v ?? "").trim();
                  if (t && !out.includes(t)) out.push(t);
                }
                setForm((f) => ({ ...f, keywords: out }));
              }}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              行业归属（industry_keys——名单归属依据，决定哪些企业算这一群）
            </div>
            <Select
              style={{ width: "100%" }}
              placeholder="选择行业类目（可多选）"
              multiple
              filter
              maxTagCount={4}
              optionList={industryKeyOptions}
              value={form.industry_keys}
              onChange={(v) => setForm((f) => ({ ...f, industry_keys: (v as string[]) ?? [] }))}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1 text-[12.5px] text-[var(--muted)]">业务线（买昊志什么）</div>
              <Select
                style={{ width: "100%" }}
                placeholder="可多选"
                multiple
                maxTagCount={3}
                optionList={lineOptions}
                value={form.business_lines}
                onChange={(v) => setForm((f) => ({ ...f, business_lines: (v as string[]) ?? [] }))}
              />
            </div>
            <div>
              <div className="mb-1 text-[12.5px] text-[var(--muted)]">优先级（推荐加权 P0 &gt; P1 &gt; P2）</div>
              <Select
                style={{ width: "100%" }}
                value={form.priority}
                optionList={PRIORITY_OPTIONS}
                onChange={(v) => setForm((f) => ({ ...f, priority: String(v || "P2") }))}
              />
            </div>
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">典型客户（销售能懂的话）</div>
            <textarea
              value={form.notes}
              rows={3}
              placeholder="如：长三角光学镀膜代工厂，3-10 台镀膜机，月耗泵油 20L 起步"
              className="w-full rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-[12.5px] leading-5 outline-none focus:border-[var(--accent)]"
              onChange={(e) => setForm((f) => ({ ...f, notes: e.target.value }))}
            />
          </div>
          <div className="flex items-center gap-2">
            <Switch
              checked={form.enabled}
              onChange={(checked: boolean) => setForm((f) => ({ ...f, enabled: checked }))}
            />
            <span className="text-[12.5px] text-[var(--muted)]">
              启用（停用即退出推荐/统计/筛选选项，删除不提供）
            </span>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
