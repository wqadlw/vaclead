"use client";

/**
 * 留资表单管理（SPEC-J J5）：建表单 → 拿落地页链接 → 看提交。
 * SideSheet 查看提交明细；启停用一键切换。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, SideSheet, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { Plus } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface FormRow {
  id: number;
  name: string;
  token: string;
  active: number;
  submissions: number;
  created_at: string;
}
interface SubmissionRow {
  id: number;
  company: string;
  contact_name: string | null;
  phone: string | null;
  email: string | null;
  need: string | null;
  enterprise_id: number | null;
  lead_id: number | null;
  created_at: string;
}

export default function FormsPage() {
  const [items, setItems] = useState<FormRow[]>([]);
  const [name, setName] = useState("");
  const [subs, setSubs] = useState<FormRow | null>(null);
  const [subItems, setSubItems] = useState<SubmissionRow[]>([]);

  const load = useCallback(() => {
    api.get<{ items: FormRow[] }>("/api/lead-forms").then((d) => setItems(d.items)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  const create = async () => {
    try {
      const d = await api.post<{ token: string }>("/api/lead-forms", { name });
      setName("");
      load();
      Toast.success(`已创建，落地页链接：/form?t=${d.token.slice(0, 6)}…（见列表）`);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "创建失败");
    }
  };
  const toggle = async (row: FormRow) => {
    await api.patch(`/api/lead-forms/${row.id}`, { active: !row.active }).catch(() => undefined);
    load();
  };
  const openSubs = async (row: FormRow) => {
    setSubs(row);
    const d = await api.get<{ items: SubmissionRow[] }>(`/api/lead-forms/${row.id}/submissions`);
    setSubItems(d.items);
  };

  return (
    <AppShell>
      <div className="mb-4 flex items-center gap-2">
        <Input placeholder="表单名，如 官网留资" value={name} onChange={setName} className="!w-64" />
        <Button theme="solid" icon={<Plus size={14} />} disabled={!name.trim()} onClick={create}>创建表单</Button>
        <span className="text-[12.5px] text-[var(--muted)]">
          把落地页链接放进官网/公众号；提交自动归一建档、撞单复用已有线索。
        </span>
      </div>
      <div className="rounded-xl border border-[var(--border)] bg-white">
        <Table
          size="small"
          dataSource={items}
          rowKey="id"
          pagination={false}
          columns={[
            { title: "表单", dataIndex: "name", render: (v: string) => <span className="font-medium text-[var(--ink)]">{v}</span> },
            { title: "落地页", dataIndex: "token", render: (t: string) => (
              <a href={`/form?t=${t}`} target="_blank" rel="noreferrer" className="text-[12.5px] text-[var(--accent)]">
                /form?t={t.slice(0, 8)}…
              </a>
            ) },
            { title: "状态", dataIndex: "active", width: 90, render: (v: number) => (
              <Tag color={v ? "green" : "grey"} type="light">{v ? "收集中" : "已停用"}</Tag>
            ) },
            { title: "提交数", dataIndex: "submissions", width: 90 },
            { title: "", dataIndex: "id", width: 180, render: (_: number, r: FormRow) => (
              <span className="space-x-2">
                <Button size="small" theme="borderless" onClick={() => openSubs(r)}>提交记录</Button>
                <Button size="small" theme="borderless" onClick={() => toggle(r)}>{r.active ? "停用" : "启用"}</Button>
              </span>
            ) },
          ]}
          empty="还没有表单 —— 创建一个开始从官网捕获线索"
        />
      </div>

      <SideSheet title={`提交记录 · ${subs?.name ?? ""}`} visible={!!subs} onCancel={() => setSubs(null)} width={520}>
        <div className="space-y-3">
          {subItems.map((s) => (
            <div key={s.id} className="rounded-lg border border-[var(--border)] px-4 py-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-[var(--ink)]">{s.company}</span>
                <span className="text-[12px] text-[var(--muted)]">{(s.created_at || "").replace("T", " ").slice(0, 16)}</span>
              </div>
              <div className="mt-1 text-[13px] text-slate-600">
                {s.contact_name ?? "—"}
                {s.phone && <span className="ml-3 tabular-nums text-[var(--accent)]">{s.phone}</span>}
                {s.email && <span className="ml-3">{s.email}</span>}
              </div>
              {s.need && <div className="mt-1 text-[12.5px] text-[var(--muted)]">需求：{s.need}</div>}
              <div className="mt-1 text-[12px] text-[var(--muted)]">
                已归档：企业 #{s.enterprise_id} · 线索 #{s.lead_id}
              </div>
            </div>
          ))}
          {subItems.length === 0 && <div className="py-8 text-center text-[13px] text-[var(--muted)]">还没有提交</div>}
        </div>
      </SideSheet>
    </AppShell>
  );
}
