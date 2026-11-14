"use client";

/**
 * 「话术库」管理面板（SPEC-SALE S1）：script_card CRUD。
 * 契约（与后端对齐）：
 * - GET  /api/script-cards            → { items: [...] } 或裸数组（enabled 按 sort 排序，全员）
 * - POST /api/script-cards            → {id}（admin）
 * - PUT  /api/script-cards/{id}       → ok（admin，部分字段可省）
 * - DELETE /api/script-cards/{id}     → ok（admin）
 * 后端并行上线中：GET 失败整卡「暂不可用」态，不白屏；admin 判定由 settings 页
 * 照群机器人卡同款（localStorage vaclead_role）完成，面板自身不重复判。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, Modal, Select, Switch, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { MonitorPlay } from "lucide-react";
import { api } from "@/lib/api";

export interface ScriptCardRow {
  id: number;
  title: string;
  scene: string;
  role_hint: string | null;
  body_md: string;
  sort?: number | null;
  enabled: number | boolean;
  updated_at?: string;
}

/** scene 受控五值（SPEC-SALE S1 表设计） */
export const SCENE_OPTIONS = [
  { value: "first_contact", label: "首联（第一次打电话）" },
  { value: "competitor_replace", label: "竞对替换（挖角话术）" },
  { value: "repeat_purchase", label: "老户复购（耗材回访）" },
  { value: "proposal_window", label: "拟建窗口（项目介入）" },
  { value: "generic", label: "通用（不限场景）" },
];
const SCENE_CN: Record<string, string> = Object.fromEntries(
  SCENE_OPTIONS.map((s) => [s.value, s.label.split("（")[0]]),
);
const SCENE_TAG: Record<string, "blue" | "red" | "green" | "cyan" | "grey"> = {
  first_contact: "blue",
  competitor_replace: "red",
  repeat_purchase: "green",
  proposal_window: "cyan",
  generic: "grey",
};

/** role_hint 对齐 Enterprise.customer_role，空 = 通用（不限角色） */
export const ROLE_SELECT_OPTIONS = [
  { value: "", label: "通用（不限角色）" },
  { value: "oem", label: "应用设备制造商（OEM）" },
  { value: "end_user", label: "终端用户" },
  { value: "reseller", label: "分销商" },
  { value: "integrator", label: "系统工程商" },
];
const ROLE_CN: Record<string, string> = Object.fromEntries(
  ROLE_SELECT_OPTIONS.filter((r) => r.value).map((r) => [r.value, r.label.split("（")[0]]),
);

/** 列表端点容错：兼容 { items: [...] } 与裸数组，异常一律空态（照 settings 页 listOf 同款） */
function listOf<T>(d: unknown): T[] {
  if (Array.isArray(d)) return d as T[];
  if (d && typeof d === "object" && Array.isArray((d as { items?: unknown }).items)) {
    return (d as { items: T[] }).items;
  }
  return [];
}

interface CardForm {
  title: string;
  scene: string;
  role_hint: string;
  body_md: string;
}

const EMPTY_FORM: CardForm = { title: "", scene: "first_contact", role_hint: "", body_md: "" };

export default function ScriptCardsPanel() {
  // rows === null 表示 /api/script-cards 请求失败 → 整卡「暂不可用」（照 SourcesPanel 模式）
  const [rows, setRows] = useState<ScriptCardRow[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState<ScriptCardRow | null>(null);
  const [form, setForm] = useState<CardForm>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    // 管理面板拉全量（含停用卡可重新启用）——后端 include_disabled 仅 admin 放行
    api
      .get<unknown>("/api/script-cards?include_disabled=1")
      .then((d) => setRows(listOf<ScriptCardRow>(d)))
      .catch(() => setRows(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  const openNew = () => {
    setEditing(null);
    setForm(EMPTY_FORM);
    setSheetOpen(true);
  };

  const openEdit = (r: ScriptCardRow) => {
    setEditing(r);
    setForm({
      title: r.title,
      scene: SCENE_CN[r.scene] ? r.scene : "generic",
      role_hint: r.role_hint ?? "",
      body_md: r.body_md ?? "",
    });
    setSheetOpen(true);
  };

  const save = async () => {
    if (!form.title.trim()) {
      Toast.warning("请填写话术标题");
      return;
    }
    if (!form.body_md.trim()) {
      Toast.warning("请填写话术正文");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        title: form.title.trim(),
        scene: form.scene,
        role_hint: form.role_hint || null,
        body_md: form.body_md,
      };
      if (editing) {
        await api.put(`/api/script-cards/${editing.id}`, payload);
        Toast.success("话术已更新");
      } else {
        await api.post("/api/script-cards", payload);
        Toast.success("话术已创建");
      }
      setSheetOpen(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (r: ScriptCardRow, next: boolean) => {
    setActingId(r.id);
    try {
      await api.put(`/api/script-cards/${r.id}`, { enabled: next });
      setRows((prev) =>
        prev ? prev.map((x) => (x.id === r.id ? { ...x, enabled: next ? 1 : 0 } : x)) : prev,
      );
      Toast.success(next ? `已启用「${r.title}」` : `已停用「${r.title}」（驾驶舱不再推荐）`);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActingId(null);
    }
  };

  const remove = (r: ScriptCardRow) => {
    Modal.confirm({
      title: `删除话术「${r.title}」？`,
      content: "删除后通话驾驶舱与推荐场景都不再使用该话术，不可恢复。",
      okText: "删除",
      okType: "danger",
      onOk: async () => {
        try {
          await api.delete(`/api/script-cards/${r.id}`);
          Toast.success("已删除");
          load();
        } catch (e) {
          Toast.error(e instanceof Error ? e.message : "删除失败");
        }
      },
    });
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <p className="max-w-[640px] text-[13px] leading-6 text-[var(--muted)]">
          销售话术卡（SALES_PLAYBOOK 系统化）：按场景/角色维护标准话术，通话驾驶舱按线索场景自动推荐，
          销售点开照念、一键复制。停用即下架，排序决定同场景推荐顺序。
        </p>
        <div className="flex gap-2">
          <Button size="small" theme="borderless" type="tertiary" loading={loading} onClick={load}>
            刷新
          </Button>
          {/* SPEC-SALE S3：老板大屏入口（不进侧栏导航，设置页/命令面板进入） */}
          <Button
            size="small"
            theme="outline"
            icon={<MonitorPlay size={13} />}
            onClick={() => window.open("/screen", "_blank")}
          >
            数据大屏
          </Button>
          <Button theme="solid" size="small" onClick={openNew}>
            + 新建话术
          </Button>
        </div>
      </div>
      {rows === null ? (
        <div className="rounded-lg border border-[var(--border)] bg-white py-10 text-center text-[13px] text-[var(--muted)]">
          暂不可用 —— 话术库服务未就绪（后端并行上线中）或无权限，稍后点「刷新」重试
        </div>
      ) : (
        <Table<ScriptCardRow>
          rowKey="id"
          size="small"
          loading={loading}
          dataSource={rows}
          empty="暂无话术 —— 点击右上角「+ 新建话术」录入第一张卡，或等后端种子任务自动生成 4 张昊志话术"
          pagination={{ pageSize: 10 }}
          columns={[
            {
              title: "标题",
              dataIndex: "title",
              width: 230,
              render: (v: string, r: ScriptCardRow) => (
                <div className="max-w-[220px]">
                  <div className="truncate text-[13px] font-medium text-[var(--ink)]" title={v}>
                    {v}
                  </div>
                  <div className="truncate text-[11px] text-[var(--muted)]" title={r.body_md}>
                    {(r.body_md || "").slice(0, 40) || "—"}
                  </div>
                </div>
              ),
            },
            {
              title: "场景",
              dataIndex: "scene",
              width: 100,
              render: (v: string) => (
                <Tag color={SCENE_TAG[v] ?? "grey"} type="light" size="small">
                  {SCENE_CN[v] ?? v}
                </Tag>
              ),
            },
            {
              title: "适用角色",
              dataIndex: "role_hint",
              width: 140,
              render: (v: string | null) => (
                <span className="text-[12.5px] text-[var(--muted)]">
                  {v ? (ROLE_CN[v] ?? v) : "通用（不限）"}
                </span>
              ),
            },
            {
              title: "排序",
              dataIndex: "sort",
              width: 70,
              align: "right" as const,
              render: (v: number | null) => (
                <span className="tabular-nums text-[12.5px] text-[var(--muted)]">
                  {typeof v === "number" ? v : "—"}
                </span>
              ),
            },
            {
              title: "启用",
              dataIndex: "enabled",
              width: 72,
              render: (_: unknown, r: ScriptCardRow) => (
                <Switch
                  size="small"
                  checked={!!r.enabled}
                  loading={actingId === r.id}
                  onChange={(checked: boolean) => void toggleEnabled(r, checked)}
                />
              ),
            },
            {
              title: "更新时间",
              dataIndex: "updated_at",
              width: 150,
              render: (v: string | undefined) => (
                <span className="text-[12px] tabular-nums text-[var(--muted)]">
                  {(v || "").replace("T", " ").slice(0, 16) || "—"}
                </span>
              ),
            },
            {
              title: "",
              width: 120,
              render: (_: unknown, r: ScriptCardRow) => (
                <div className="flex gap-1.5">
                  <Button size="small" theme="borderless" onClick={() => openEdit(r)}>
                    编辑
                  </Button>
                  <Button size="small" theme="borderless" type="danger" onClick={() => remove(r)}>
                    删
                  </Button>
                </div>
              ),
            },
          ]}
        />
      )}

      {/* 新建 / 编辑 Modal：title 输入 + scene 下拉 + role_hint 下拉（可空=通用）+ body_md 多行 */}
      <Modal
        title={editing ? `编辑话术 — ${editing.title}` : "新建话术"}
        visible={sheetOpen}
        onCancel={() => setSheetOpen(false)}
        onOk={() => void save()}
        okText="保存"
        cancelText="取消"
        width={560}
        maskClosable={false}
      >
        <div className="space-y-3.5 py-1">
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">标题 *</div>
            <Input
              value={form.title}
              placeholder="如：首联·自报家门（官方授权三件套开场）"
              showClear
              onChange={(v) => setForm((f) => ({ ...f, title: v }))}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <div className="mb-1 text-[12.5px] text-[var(--muted)]">场景 *</div>
              <Select
                style={{ width: "100%" }}
                value={form.scene}
                optionList={SCENE_OPTIONS}
                onChange={(v) => setForm((f) => ({ ...f, scene: String(v) || "generic" }))}
              />
            </div>
            <div>
              <div className="mb-1 text-[12.5px] text-[var(--muted)]">适用角色（可空 = 通用）</div>
              <Select
                style={{ width: "100%" }}
                value={form.role_hint}
                optionList={ROLE_SELECT_OPTIONS}
                onChange={(v) => setForm((f) => ({ ...f, role_hint: String(v ?? "") }))}
              />
            </div>
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              话术正文 *（销售照念口径，支持换行）
            </div>
            <textarea
              value={form.body_md}
              rows={8}
              placeholder={"您好，我是昊志机电的小 X——\n我们服务过 XX 行业 200+ 台设备…"}
              className="w-full rounded-lg border border-[var(--border)] bg-white px-3 py-2 text-[12.5px] leading-5 outline-none focus:border-[var(--accent)]"
              onChange={(e) => setForm((f) => ({ ...f, body_md: e.target.value }))}
            />
          </div>
        </div>
      </Modal>
    </div>
  );
}
