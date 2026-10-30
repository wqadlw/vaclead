"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Button,
  Descriptions,
  Input,
  InputNumber,
  Modal,
  Select,
  SideSheet,
  Switch,
  Table,
  Tabs,
  TabPane,
  Tag,
  Toast,
} from "@douyinfe/semi-ui";
import { Bot, KeyRound } from "lucide-react";
import AppShell from "@/components/AppShell";
import AutomationPanel from "@/components/AutomationPanel";
import { ApiKeysPanel, AuditPanel, SubRulesPanel, TargetPanel } from "@/components/GovernancePanels";
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

/* ================= SPEC-N 组织与分配：成员管理 / 团队 / 线索分配 ================= */

interface OrgUser {
  id: number;
  username: string;
  display_name: string | null;
  email: string | null;
  role: string;
  team_id: number | null;
  active: number | boolean;
  created_at?: string;
}

interface OrgTeam {
  id: number;
  name: string;
  leader_id: number | null;
  /** 后端直接返回的冗余字段（若有） */
  leader_name?: string | null;
  member_count?: number;
  created_at?: string;
}

interface AssignConditions {
  industry_key?: string | null;
  province?: string | null;
  source?: string | null;
}

interface AssignRule {
  id: number;
  name: string;
  conditions: AssignConditions | string | null;
  target_team_id: number | null;
  /** 后端冗余返回的目标团队名（若有） */
  team_name?: string | null;
  target_user_ids: number[] | string | null;
  strategy: string;
  enabled: number | boolean;
  priority: number | null;
  run_count: number;
  last_run_at?: string | null;
  created_at?: string;
}

const SOURCE_OPTIONS = [
  { value: "ggzy", label: "官方中标数据" },
  { value: "ccgp", label: "政采网公告" },
  { value: "tender", label: "招标导入" },
  { value: "poi", label: "POI 采集" },
  { value: "csv", label: "CSV 导入" },
  { value: "manual", label: "手工" },
];
const SOURCE_LABEL: Record<string, string> = Object.fromEntries(
  SOURCE_OPTIONS.map((s) => [s.value, s.label]),
);
const INDUSTRY_LABEL: Record<string, string> = Object.fromEntries(
  INDUSTRY_OPTIONS.map((i) => [i.key, i.name]),
);

/** 列表端点容错：兼容 { items: [...] } 与裸数组，异常一律空态 */
function listOf<T>(d: unknown): T[] {
  if (Array.isArray(d)) return d as T[];
  if (d && typeof d === "object" && Array.isArray((d as { items?: unknown }).items)) {
    return (d as { items: T[] }).items;
  }
  return [];
}

function asRecord(v: unknown): Record<string, unknown> | null {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : null;
}

function parseConditions(c: unknown): AssignConditions {
  const r = asRecord(c);
  if (r) return r as AssignConditions;
  if (typeof c === "string") {
    try {
      const p: unknown = JSON.parse(c);
      const pr = asRecord(p);
      if (pr) return pr as AssignConditions;
    } catch {
      /* 容错：坏 JSON 视为无条件 */
    }
  }
  return {};
}

function parseUserIds(v: unknown): number[] {
  let arr: unknown = v;
  if (typeof v === "string") {
    try {
      arr = JSON.parse(v);
    } catch {
      arr = null;
    }
  }
  return Array.isArray(arr)
    ? arr.map(Number).filter((n) => Number.isFinite(n))
    : [];
}

const userLabel = (u: OrgUser) => u.display_name || u.username;

/* ---------------- N1 成员管理 ---------------- */

function MembersPanel() {
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [teams, setTeams] = useState<OrgTeam[]>([]);
  const [loading, setLoading] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState<OrgUser | null>(null);
  const [saving, setSaving] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const [form, setForm] = useState({
    username: "",
    display_name: "",
    email: "",
    role: "sales",
    team_id: undefined as number | undefined,
    password: "",
  });

  const teamName = useCallback(
    (tid: number | null | undefined) =>
      tid == null ? "—" : teams.find((t) => t.id === tid)?.name ?? `团队#${tid}`,
    [teams],
  );

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get<unknown>("/api/users").then((d) => setUsers(listOf<OrgUser>(d))),
      api.get<unknown>("/api/teams").then((d) => setTeams(listOf<OrgTeam>(d))),
    ])
      .catch(() => Toast.error("成员列表加载失败（该面板仅管理员可用）"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  const openNew = () => {
    setEditing(null);
    setForm({ username: "", display_name: "", email: "", role: "sales", team_id: undefined, password: "" });
    setSheetOpen(true);
  };

  const openEdit = (u: OrgUser) => {
    setEditing(u);
    setForm({
      username: u.username,
      display_name: u.display_name ?? "",
      email: u.email ?? "",
      role: u.role === "admin" ? "admin" : "sales",
      team_id: u.team_id ?? undefined,
      password: "",
    });
    setSheetOpen(true);
  };

  const save = async () => {
    if (!form.username.trim()) {
      Toast.warning("请填写用户名");
      return;
    }
    if (!editing && form.password.length < 6) {
      Toast.warning("初始密码至少 6 位");
      return;
    }
    if (editing && form.password && form.password.length < 6) {
      Toast.warning("重置密码至少 6 位");
      return;
    }
    setSaving(true);
    try {
      if (editing) {
        await api.put(`/api/users/${editing.id}`, {
          display_name: form.display_name || null,
          email: form.email || null,
          role: form.role,
          team_id: form.team_id ?? null,
          ...(form.password ? { password: form.password } : {}),
        });
        Toast.success(form.password ? "成员已更新，密码已重置" : "成员已更新");
      } else {
        await api.post("/api/users", {
          username: form.username.trim(),
          password: form.password,
          role: form.role,
          display_name: form.display_name || null,
          email: form.email || null,
          team_id: form.team_id ?? null,
        });
        Toast.success("成员已创建");
      }
      setSheetOpen(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const toggleActive = async (u: OrgUser, next: boolean) => {
    setActingId(u.id);
    try {
      await api.put(`/api/users/${u.id}`, { active: next });
      setUsers((prev) => prev.map((x) => (x.id === u.id ? { ...x, active: next } : x)));
      Toast.success(next ? `已启用 ${u.username}` : `已禁用 ${u.username}（登录将被拒绝）`);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActingId(null);
    }
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <p className="max-w-[640px] text-[13px] leading-6 text-[var(--muted)]">
          成员账号与角色管理：禁用即软删（登录 401、历史归属保留），不可禁用自己或最后一个在职管理员。
          团队用于线索协作可见范围与自动分配目标。
        </p>
        <div className="flex gap-2">
          <Button size="small" theme="borderless" type="tertiary" loading={loading} onClick={load}>
            刷新
          </Button>
          <Button theme="solid" size="small" onClick={openNew}>
            + 新建成员
          </Button>
        </div>
      </div>
      <Table<OrgUser>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={users}
        empty="暂无成员 —— 点击右上角「+ 新建成员」创建第一个账号"
        pagination={{ pageSize: 10 }}
        columns={[
          {
            title: "用户名",
            dataIndex: "username",
            width: 190,
            render: (_: unknown, r: OrgUser) => (
              <div>
                <div className="text-[13px] font-medium text-[var(--ink)]">{r.username}</div>
                {r.display_name && (
                  <div className="text-[11px] text-[var(--muted)]">{r.display_name}</div>
                )}
              </div>
            ),
          },
          {
            title: "角色",
            dataIndex: "role",
            width: 84,
            render: (v: string) => (
              <Tag color={v === "admin" ? "blue" : "grey"} type="light" size="small">
                {v === "admin" ? "管理员" : "销售"}
              </Tag>
            ),
          },
          {
            title: "团队",
            dataIndex: "team_id",
            width: 130,
            render: (_: unknown, r: OrgUser) => (
              <span className="text-[12.5px] text-[var(--muted)]">{teamName(r.team_id)}</span>
            ),
          },
          {
            title: "邮箱",
            dataIndex: "email",
            render: (v: string | null) => (
              <span className="truncate text-[12.5px] text-[var(--muted)]">{v || "—"}</span>
            ),
          },
          {
            title: "启用",
            dataIndex: "active",
            width: 72,
            render: (_: unknown, r: OrgUser) => (
              <Switch
                size="small"
                checked={!!r.active}
                loading={actingId === r.id}
                onChange={(checked: boolean) => void toggleActive(r, checked)}
              />
            ),
          },
          {
            title: "",
            width: 64,
            render: (_: unknown, r: OrgUser) => (
              <Button size="small" theme="borderless" onClick={() => openEdit(r)}>
                编辑
              </Button>
            ),
          },
        ]}
      />

      <SideSheet
        title={editing ? `编辑成员 — ${editing.username}` : "新建成员"}
        visible={sheetOpen}
        onCancel={() => setSheetOpen(false)}
        width={460}
      >
        <div className="space-y-3.5">
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">用户名（登录凭证，创建后不可修改）</div>
            <Input
              value={form.username}
              disabled={!!editing}
              placeholder="如 zhangsan"
              onChange={(v) => setForm((f) => ({ ...f, username: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">显示名</div>
            <Input
              value={form.display_name}
              placeholder="如 张三"
              onChange={(v) => setForm((f) => ({ ...f, display_name: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">邮箱（可选）</div>
            <Input
              mode="password"
              value={form.email}
              placeholder="name@company.com"
              onChange={(v) => setForm((f) => ({ ...f, email: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">角色</div>
            <Select
              style={{ width: "100%" }}
              value={form.role}
              optionList={[
                { value: "sales", label: "销售（自己 + 公海 + 团队线索）" },
                { value: "admin", label: "管理员（全库权限）" },
              ]}
              onChange={(v) => setForm((f) => ({ ...f, role: (v as string) || "sales" }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">所属团队（可选）</div>
            <Select
              style={{ width: "100%" }}
              placeholder="未分配团队"
              showClear
              value={form.team_id}
              optionList={teams.map((t) => ({ value: t.id, label: t.name }))}
              onChange={(v) => setForm((f) => ({ ...f, team_id: typeof v === "number" ? v : undefined }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">
              {editing ? "重置密码（留空 = 不修改）" : "初始密码（至少 6 位）"}
            </div>
            <Input
              mode="password"
              value={form.password}
              placeholder={editing ? "留空表示不修改" : "至少 6 位"}
              onChange={(v) => setForm((f) => ({ ...f, password: v }))}
            />
          </div>
          <div className="flex gap-2 pt-1">
            <Button theme="solid" loading={saving} onClick={() => void save()}>
              保存
            </Button>
            <Button theme="borderless" type="tertiary" onClick={() => setSheetOpen(false)}>
              取消
            </Button>
          </div>
        </div>
      </SideSheet>
    </div>
  );
}

/* ---------------- N2 团队 ---------------- */

function TeamsPanel() {
  const [teams, setTeams] = useState<OrgTeam[]>([]);
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState<OrgTeam | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState({ name: "", leader_id: undefined as number | undefined });

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get<unknown>("/api/teams").then((d) => setTeams(listOf<OrgTeam>(d))),
      api.get<unknown>("/api/users").then((d) => setUsers(listOf<OrgUser>(d))),
    ])
      .catch(() => Toast.error("团队列表加载失败（该面板仅管理员可用）"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  const memberCount = (t: OrgTeam) =>
    typeof t.member_count === "number" ? t.member_count : users.filter((u) => u.team_id === t.id).length;
  const leaderName = (t: OrgTeam) => {
    if (t.leader_name) return t.leader_name;
    if (t.leader_id == null) return "—";
    const u = users.find((x) => x.id === t.leader_id);
    return u ? `${userLabel(u)}（@${u.username}）` : `用户#${t.leader_id}`;
  };

  const openNew = () => {
    setEditing(null);
    setForm({ name: "", leader_id: undefined });
    setSheetOpen(true);
  };

  const openEdit = (t: OrgTeam) => {
    setEditing(t);
    setForm({ name: t.name, leader_id: t.leader_id ?? undefined });
    setSheetOpen(true);
  };

  const save = async () => {
    if (!form.name.trim()) {
      Toast.warning("请填写团队名称");
      return;
    }
    setSaving(true);
    try {
      const payload = { name: form.name.trim(), leader_id: form.leader_id ?? null };
      if (editing) await api.put(`/api/teams/${editing.id}`, payload);
      else await api.post("/api/teams", payload);
      Toast.success(editing ? "团队已更新" : "团队已创建");
      setSheetOpen(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <p className="max-w-[640px] text-[13px] leading-6 text-[var(--muted)]">
          团队是线索协作单位：团队负责人可查看团队成员的线索，自动分配规则可把新线索按团队轮转。
          成员在「成员管理」里归属团队。
        </p>
        <div className="flex gap-2">
          <Button size="small" theme="borderless" type="tertiary" loading={loading} onClick={load}>
            刷新
          </Button>
          <Button theme="solid" size="small" onClick={openNew}>
            + 新建团队
          </Button>
        </div>
      </div>
      <Table<OrgTeam>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={teams}
        empty="暂无团队 —— 点击右上角「+ 新建团队」创建第一个团队"
        pagination={{ pageSize: 10 }}
        columns={[
          {
            title: "团队名称",
            dataIndex: "name",
            width: 200,
            render: (v: string) => (
              <span className="text-[13px] font-medium text-[var(--ink)]">{v}</span>
            ),
          },
          {
            title: "负责人",
            dataIndex: "leader_id",
            width: 180,
            render: (_: unknown, r: OrgTeam) => (
              <span className="text-[12.5px] text-[var(--muted)]">{leaderName(r)}</span>
            ),
          },
          {
            title: "成员数",
            dataIndex: "id",
            width: 84,
            align: "right" as const,
            render: (_: unknown, r: OrgTeam) => (
              <span className="tabular-nums text-[12.5px] text-[var(--ink)]">{memberCount(r)}</span>
            ),
          },
          {
            title: "",
            width: 64,
            render: (_: unknown, r: OrgTeam) => (
              <Button size="small" theme="borderless" onClick={() => openEdit(r)}>
                编辑
              </Button>
            ),
          },
        ]}
      />

      <SideSheet
        title={editing ? `编辑团队 — ${editing.name}` : "新建团队"}
        visible={sheetOpen}
        onCancel={() => setSheetOpen(false)}
        width={420}
      >
        <div className="space-y-3.5">
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">团队名称</div>
            <Input
              value={form.name}
              placeholder="如 华东一组"
              onChange={(v) => setForm((f) => ({ ...f, name: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">负责人（可选，可查看团队成员线索）</div>
            <Select
              style={{ width: "100%" }}
              placeholder="未指定负责人"
              showClear
              filter
              value={form.leader_id}
              optionList={users.map((u) => ({ value: u.id, label: `${userLabel(u)}（@${u.username}）` }))}
              onChange={(v) => setForm((f) => ({ ...f, leader_id: typeof v === "number" ? v : undefined }))}
            />
          </div>
          <div className="flex gap-2 pt-1">
            <Button theme="solid" loading={saving} onClick={() => void save()}>
              保存
            </Button>
            <Button theme="borderless" type="tertiary" onClick={() => setSheetOpen(false)}>
              取消
            </Button>
          </div>
        </div>
      </SideSheet>
    </div>
  );
}

/* ---------------- N3 线索自动分配规则 ---------------- */

function AssignmentPanel() {
  const [rules, setRules] = useState<AssignRule[]>([]);
  const [teams, setTeams] = useState<OrgTeam[]>([]);
  const [users, setUsers] = useState<OrgUser[]>([]);
  const [loading, setLoading] = useState(false);
  const [sheetOpen, setSheetOpen] = useState(false);
  const [editing, setEditing] = useState<AssignRule | null>(null);
  const [saving, setSaving] = useState(false);
  const [actingId, setActingId] = useState<number | null>(null);
  const [form, setForm] = useState({
    name: "",
    industry_key: "",
    province: "",
    source: "",
    strategy: "round_robin",
    target_team_id: undefined as number | undefined,
    target_user_ids: [] as number[],
    priority: 10,
    enabled: true,
  });
  // 试运行（零写入预览）
  const [dryOpen, setDryOpen] = useState(false);
  const [dryForm, setDryForm] = useState({ industry_key: "", province: "", source: "" });
  const [dryRunning, setDryRunning] = useState(false);
  const [dryResult, setDryResult] = useState<{ rule: string | null; targets: string[] } | null>(null);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all([
      api.get<unknown>("/api/assignment-rules").then((d) => setRules(listOf<AssignRule>(d))),
      api.get<unknown>("/api/teams").then((d) => setTeams(listOf<OrgTeam>(d))),
      api.get<unknown>("/api/users").then((d) => setUsers(listOf<OrgUser>(d))),
    ])
      .catch(() => Toast.error("分配规则加载失败（该面板仅管理员可用）"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => load(), [load]);

  const teamName = (r: AssignRule) => {
    if (r.team_name) return r.team_name;
    if (r.target_team_id == null) return null;
    return teams.find((t) => t.id === r.target_team_id)?.name ?? `团队#${r.target_team_id}`;
  };
  const userName = (uid: number) => {
    const u = users.find((x) => x.id === uid);
    return u ? userLabel(u) : `用户#${uid}`;
  };

  const condSummary = (r: AssignRule) => {
    const c = parseConditions(r.conditions);
    const parts: string[] = [];
    if (c.industry_key) parts.push(`行业=${INDUSTRY_LABEL[c.industry_key] ?? c.industry_key}`);
    if (c.province) parts.push(`省份=${c.province}`);
    if (c.source) parts.push(`来源=${SOURCE_LABEL[c.source] ?? c.source}`);
    return parts.length ? parts.join(" · ") : "全部线索（无条件过滤）";
  };

  const openNew = () => {
    setEditing(null);
    setForm({
      name: "",
      industry_key: "",
      province: "",
      source: "",
      strategy: "round_robin",
      target_team_id: undefined,
      target_user_ids: [],
      priority: 10,
      enabled: true,
    });
    setSheetOpen(true);
  };

  const openEdit = (r: AssignRule) => {
    setEditing(r);
    const c = parseConditions(r.conditions);
    setForm({
      name: r.name,
      industry_key: c.industry_key ?? "",
      province: c.province ?? "",
      source: c.source ?? "",
      strategy: r.strategy === "fixed" ? "fixed" : "round_robin",
      target_team_id: r.target_team_id ?? undefined,
      target_user_ids: parseUserIds(r.target_user_ids),
      priority: typeof r.priority === "number" ? r.priority : 10,
      enabled: !!r.enabled,
    });
    setSheetOpen(true);
  };

  const save = async () => {
    if (!form.name.trim()) {
      Toast.warning("请填写规则名");
      return;
    }
    if (form.target_user_ids.length === 0 && form.target_team_id == null) {
      Toast.warning("请选择目标团队或至少一名目标成员");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        name: form.name.trim(),
        conditions: {
          industry_key: form.industry_key || null,
          province: form.province.trim() || null,
          source: form.source || null,
        },
        strategy: form.strategy,
        target_team_id: form.target_team_id ?? null,
        target_user_ids: form.target_user_ids,
        priority: typeof form.priority === "number" ? form.priority : 10,
        enabled: form.enabled,
      };
      if (editing) await api.put(`/api/assignment-rules/${editing.id}`, payload);
      else await api.post("/api/assignment-rules", payload);
      Toast.success(editing ? "规则已更新" : "规则已创建");
      setSheetOpen(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const toggleEnabled = async (r: AssignRule, next: boolean) => {
    setActingId(r.id);
    try {
      await api.put(`/api/assignment-rules/${r.id}`, { enabled: next });
      setRules((prev) => prev.map((x) => (x.id === r.id ? { ...x, enabled: next } : x)));
      Toast.success(next ? `已启用「${r.name}」` : `已停用「${r.name}」`);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "操作失败");
    } finally {
      setActingId(null);
    }
  };

  const remove = async (r: AssignRule) => {
    try {
      await api.delete(`/api/assignment-rules/${r.id}`);
      Toast.success("已删除");
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "删除失败");
    }
  };

  const runDry = async () => {
    setDryRunning(true);
    try {
      const d = await api.post<unknown>("/api/assignment-rules/dry-run", {
        industry_key: dryForm.industry_key || null,
        province: dryForm.province.trim() || null,
        source: dryForm.source || null,
      });
      setDryResult(parseDryRun(d, users));
    } catch (e) {
      setDryResult(null);
      Toast.error(e instanceof Error ? e.message : "试运行失败");
    } finally {
      setDryRunning(false);
    }
  };

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center justify-between">
        <p className="max-w-[640px] text-[13px] leading-6 text-[var(--muted)]">
          线索自动分配：任何入口新建的线索（导入/留资/自动化/手动）按优先级匹配第一条命中的规则，
          未命中则进入公海。固定策略始终给第一位成员，轮询策略按成员顺序持续轮转。
        </p>
        <div className="flex gap-2">
          <Button size="small" theme="borderless" type="tertiary" loading={loading} onClick={load}>
            刷新
          </Button>
          <Button
            size="small"
            theme="outline"
            onClick={() => {
              setDryResult(null);
              setDryForm({ industry_key: "", province: "", source: "" });
              setDryOpen(true);
            }}
          >
            试运行
          </Button>
          <Button theme="solid" size="small" onClick={openNew}>
            + 新建规则
          </Button>
        </div>
      </div>
      <Table<AssignRule>
        rowKey="id"
        size="small"
        loading={loading}
        dataSource={rules}
        empty="暂无分配规则 —— 新线索将全部进入公海，创建规则后按条件自动分配"
        pagination={{ pageSize: 8 }}
        columns={[
          {
            title: "规则名",
            dataIndex: "name",
            width: 160,
            render: (v: string, r: AssignRule) => (
              <div>
                <div className="text-[13px] font-medium text-[var(--ink)]">{v}</div>
                <div className="text-[11px] text-[var(--muted)]">
                  优先级 {typeof r.priority === "number" ? r.priority : "—"}
                </div>
              </div>
            ),
          },
          {
            title: "条件",
            dataIndex: "conditions",
            width: 220,
            render: (_: unknown, r: AssignRule) => (
              <span className="text-[12px] text-[var(--muted)]">{condSummary(r)}</span>
            ),
          },
          {
            title: "策略",
            dataIndex: "strategy",
            width: 82,
            render: (v: string) => (
              <Tag color={v === "fixed" ? "grey" : "blue"} type="light" size="small">
                {v === "fixed" ? "固定" : "轮询"}
              </Tag>
            ),
          },
          {
            title: "目标",
            dataIndex: "target_user_ids",
            width: 210,
            render: (_: unknown, r: AssignRule) => {
              const tn = teamName(r);
              const ids = parseUserIds(r.target_user_ids);
              const un = ids.map(userName).join("、");
              return (
                <div className="max-w-[200px]">
                  {tn && <div className="truncate text-[12px] text-[var(--ink)]" title={tn}>{tn}</div>}
                  {un ? (
                    <div className="truncate text-[11px] text-[var(--muted)]" title={un}>
                      {ids.length === 1 ? un : `${un}（${ids.length} 人轮转）`}
                    </div>
                  ) : (
                    !tn && <span className="text-[11.5px] text-[var(--muted)]">未指定</span>
                  )}
                </div>
              );
            },
          },
          {
            title: "启用",
            dataIndex: "enabled",
            width: 72,
            render: (_: unknown, r: AssignRule) => (
              <Switch
                size="small"
                checked={!!r.enabled}
                loading={actingId === r.id}
                onChange={(checked: boolean) => void toggleEnabled(r, checked)}
              />
            ),
          },
          {
            title: "执行",
            dataIndex: "run_count",
            width: 64,
            align: "right" as const,
            render: (v: number, r: AssignRule) => (
              <span
                className="tabular-nums text-[12.5px] text-[var(--ink)]"
                title={r.last_run_at ? `最近执行 ${String(r.last_run_at).slice(0, 19).replace("T", " ")}` : "尚未执行"}
              >
                {v}
              </span>
            ),
          },
          {
            title: "",
            width: 100,
            render: (_: unknown, r: AssignRule) => (
              <div className="flex gap-1.5">
                <Button size="small" theme="borderless" onClick={() => openEdit(r)}>
                  编辑
                </Button>
                <Button size="small" theme="borderless" type="danger" onClick={() => void remove(r)}>
                  删
                </Button>
              </div>
            ),
          },
        ]}
      />

      <SideSheet
        title={editing ? `编辑规则 — ${editing.name}` : "新建分配规则"}
        visible={sheetOpen}
        onCancel={() => setSheetOpen(false)}
        width={480}
      >
        <div className="space-y-3.5">
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">规则名</div>
            <Input
              value={form.name}
              placeholder="如：广东镀膜客户轮询给华东组"
              onChange={(v) => setForm((f) => ({ ...f, name: v }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">命中条件（全部满足才命中，留空 = 不限）</div>
            <div className="space-y-2">
              <Select
                style={{ width: "100%" }}
                placeholder="行业（可选）"
                showClear
                value={form.industry_key || undefined}
                optionList={INDUSTRY_OPTIONS.map((i) => ({ value: i.key, label: i.name }))}
                onChange={(v) => setForm((f) => ({ ...f, industry_key: (v as string) || "" }))}
              />
              <Input
                value={form.province}
                placeholder="省份（可选，如 广东省）"
                onChange={(v) => setForm((f) => ({ ...f, province: v }))}
              />
              <Select
                style={{ width: "100%" }}
                placeholder="来源（可选）"
                showClear
                value={form.source || undefined}
                optionList={SOURCE_OPTIONS}
                onChange={(v) => setForm((f) => ({ ...f, source: (v as string) || "" }))}
              />
            </div>
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">分配策略</div>
            <Select
              style={{ width: "100%" }}
              value={form.strategy}
              optionList={[
                { value: "round_robin", label: "轮询（按目标成员顺序持续轮转）" },
                { value: "fixed", label: "固定（始终给第一位目标成员）" },
              ]}
              onChange={(v) => setForm((f) => ({ ...f, strategy: (v as string) || "round_robin" }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">目标成员（固定取第一位，轮询按此顺序）</div>
            <Select
              style={{ width: "100%" }}
              placeholder="选择一名或多名成员"
              multiple
              filter
              value={form.target_user_ids}
              optionList={users.map((u) => ({ value: u.id, label: `${userLabel(u)}（@${u.username}）` }))}
              onChange={(v) =>
                setForm((f) => ({ ...f, target_user_ids: Array.isArray(v) ? (v as number[]) : [] }))
              }
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">归属团队（可选，作为规则的目标团队标记）</div>
            <Select
              style={{ width: "100%" }}
              placeholder="不挂团队"
              showClear
              value={form.target_team_id}
              optionList={teams.map((t) => ({ value: t.id, label: t.name }))}
              onChange={(v) => setForm((f) => ({ ...f, target_team_id: typeof v === "number" ? v : undefined }))}
            />
          </div>
          <div>
            <div className="mb-1 text-[12.5px] text-[var(--muted)]">优先级（数字越小越先匹配）</div>
            <InputNumber
              style={{ width: "100%" }}
              min={0}
              max={999}
              value={form.priority}
              onChange={(v) => setForm((f) => ({ ...f, priority: typeof v === "number" ? v : 10 }))}
            />
          </div>
          <div className="flex items-center gap-2">
            <span className="text-[12.5px] text-[var(--muted)]">启用</span>
            <Switch
              size="small"
              checked={form.enabled}
              onChange={(checked: boolean) => setForm((f) => ({ ...f, enabled: checked }))}
            />
          </div>
          <div className="flex gap-2 pt-1">
            <Button theme="solid" loading={saving} onClick={() => void save()}>
              保存
            </Button>
            <Button theme="borderless" type="tertiary" onClick={() => setSheetOpen(false)}>
              取消
            </Button>
          </div>
        </div>
      </SideSheet>

      <Modal
        title="分配试运行"
        visible={dryOpen}
        onCancel={() => setDryOpen(false)}
        footer={null}
        width={440}
      >
        <div className="space-y-3 pt-1">
          <p className="text-[12.5px] leading-5 text-[var(--muted)]">
            模拟一条新线索按当前启用规则匹配（按优先级取首条命中）。只做预览，零写入。
          </p>
          <Select
            style={{ width: "100%" }}
            placeholder="行业（可选）"
            showClear
            value={dryForm.industry_key || undefined}
            optionList={INDUSTRY_OPTIONS.map((i) => ({ value: i.key, label: i.name }))}
            onChange={(v) => setDryForm((f) => ({ ...f, industry_key: (v as string) || "" }))}
          />
          <Input
            value={dryForm.province}
            placeholder="省份（可选，如 广东省）"
            onChange={(v) => setDryForm((f) => ({ ...f, province: v }))}
          />
          <Select
            style={{ width: "100%" }}
            placeholder="来源（可选）"
            showClear
            value={dryForm.source || undefined}
            optionList={SOURCE_OPTIONS}
            onChange={(v) => setDryForm((f) => ({ ...f, source: (v as string) || "" }))}
          />
          <Button theme="solid" loading={dryRunning} onClick={() => void runDry()}>
            试运行
          </Button>
          {dryResult && (
            <div className="rounded-lg border border-[var(--border)] p-3 text-[12.5px] leading-6">
              <div className="flex items-center gap-2">
                <Tag color={dryResult.rule ? "green" : "grey"} type="light" size="small">
                  {dryResult.rule ? "命中" : "未命中"}
                </Tag>
                {dryResult.rule ? (
                  <span className="font-medium text-[var(--ink)]">{dryResult.rule}</span>
                ) : (
                  <span className="text-[var(--muted)]">无规则命中 → 线索将进入公海</span>
                )}
              </div>
              {dryResult.targets.length > 0 && (
                <div className="mt-1.5 text-[var(--ink)]">
                  目标：<span className="font-medium">{dryResult.targets.join("、")}</span>
                </div>
              )}
              <div className="mt-1 text-[11px] text-[var(--muted)]">预览，零写入 —— 不会创建或修改任何线索</div>
            </div>
          )}
        </div>
      </Modal>
    </div>
  );
}

/** dry-run 响应容错解析：既兼容 org.py 契约 {matched,rule_name,target_name,target_id}，
 *  也兼容 {rule|matched_rule} × {target_users|targets|target_user_ids} 等变体 */
function parseDryRun(
  d: unknown,
  users: OrgUser[],
): { rule: string | null; targets: string[] } {
  const o = asRecord(d);
  if (!o) return { rule: null, targets: [] };
  const hit = asRecord(o.matched_rule) ?? asRecord(o.rule) ?? asRecord(o.hit_rule);
  let rule: string | null = null;
  if (typeof o.rule_name === "string" && o.rule_name) rule = o.rule_name;
  else if (typeof o.hit_rule_name === "string" && o.hit_rule_name) rule = o.hit_rule_name;
  else if (hit && typeof hit.name === "string" && hit.name) rule = hit.name;
  else if (o.matched === true && typeof o.rule === "string") rule = o.rule;

  const targets: string[] = [];
  const pushName = (t: unknown) => {
    if (typeof t === "string" && t) targets.push(t);
    else {
      const r = asRecord(t);
      if (r) {
        const label = r.display_name ?? r.username ?? r.name;
        if (label != null && label !== "") targets.push(String(label));
      }
    }
  };
  // 契约形态：单个 target_name / target_id
  if (typeof o.target_name === "string" && o.target_name) targets.push(o.target_name);
  else if (o.target_id != null) {
    const u = users.find((x) => x.id === Number(o.target_id));
    targets.push(u ? userLabel(u) : `用户#${String(o.target_id)}`);
  } else if (hit && typeof hit.target_name === "string" && hit.target_name) {
    targets.push(hit.target_name);
  } else {
    // 变体形态：数组
    const rawTargets = o.target_users ?? o.targets ?? o.users ?? (hit ? hit.target_users : undefined);
    if (Array.isArray(rawTargets)) rawTargets.forEach(pushName);
    else if (typeof rawTargets === "string" && rawTargets) targets.push(rawTargets);
    if (targets.length === 0) {
      const ids = parseUserIds(o.target_user_ids ?? (hit ? hit.target_user_ids : undefined));
      ids.forEach((id) => {
        const u = users.find((x) => x.id === id);
        targets.push(u ? userLabel(u) : `用户#${id}`);
      });
    }
  }
  return { rule, targets };
}

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

  const [scanStarting, setScanStarting] = useState(false);
  const startDupScan = async () => {
    setScanStarting(true);
    try {
      await api.post("/api/tasks/dup-scan", {});
      Toast.success("查重扫描已启动，稍后自动刷新结果");
      // 扫描为后台任务（数百级企业秒级完成），延时拉取复核队列
      setTimeout(loadReview, 3000);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "启动失败");
    } finally {
      setScanStarting(false);
    }
  };

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
            { tab: "成员管理", itemKey: "members" },
            { tab: "团队", itemKey: "teams" },
            { tab: "线索分配", itemKey: "assignment" },
            { tab: "数据复核", itemKey: "review" },
            { tab: "自动化", itemKey: "automation" },
            { tab: "通知偏好", itemKey: "notify" },
            { tab: "订阅规则", itemKey: "subrules" },
            { tab: "月度目标", itemKey: "target" },
            { tab: "审计日志", itemKey: "audit" },
            { tab: "API 密钥", itemKey: "apikeys" },
            { tab: "词表预览", itemKey: "vocab" },
            { tab: "Agent 日志", itemKey: "agents" },
            { tab: "后端切换", itemKey: "backends" },
          ]}
        >
          {/* SPEC-J 治理面板 */}
          <TabPane itemKey="subrules">
            <SubRulesPanel />
          </TabPane>
          <TabPane itemKey="target">
            <TargetPanel />
          </TabPane>
          <TabPane itemKey="audit">
            <AuditPanel />
          </TabPane>
          <TabPane itemKey="apikeys">
            <ApiKeysPanel />
          </TabPane>
          {/* Keys */}
          <TabPane itemKey="keys">
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
          </TabPane>

          {/* 公海规则 */}
          <TabPane itemKey="pool">
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
          </TabPane>

          {/* 成员管理（SPEC-N N1） */}
          <TabPane itemKey="members">
            <MembersPanel />
          </TabPane>

          {/* 团队（SPEC-N N2） */}
          <TabPane itemKey="teams">
            <TeamsPanel />
          </TabPane>

          {/* 线索分配（SPEC-N N3） */}
          <TabPane itemKey="assignment">
            <AssignmentPanel />
          </TabPane>

          {/* 数据复核 */}
          <TabPane itemKey="review">
          <div className="p-6">
            <div className="flex items-center justify-between mb-4">
              <p className="text-[13px] leading-6 text-[var(--muted)] max-w-[640px]">
                归一引擎对 80-89 分的疑似重复企业不做静默合并，落在此处等待人工裁决：确认同主体选「合并为一档」（信号与别名全部迁移），确为两家选「不是同一家」。
              </p>
              <Button theme="borderless" type="tertiary" loading={scanStarting} onClick={startDupScan}>
                查重扫描
              </Button>
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
          </TabPane>

          {/* 自动化规则 */}
          <TabPane itemKey="automation">
            <AutomationPanel />
          </TabPane>

          {/* 通知偏好（Novu subscriber preferences 模式） */}
          <TabPane itemKey="notify">
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
          </TabPane>

          {/* 词表 */}
          <TabPane itemKey="vocab">
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
          </TabPane>

          {/* Agent 日志 */}
          <TabPane itemKey="agents">
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
          </TabPane>

          {/* 后端切换 */}
          <TabPane itemKey="backends">
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
          </TabPane>
        </Tabs>
      </div>
    </AppShell>
  );
}
