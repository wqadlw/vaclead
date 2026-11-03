"use client";

/**
 * 合同管理（SPEC-AD）：赢单→签约→回款→续约。
 * 列表 + 详情侧栏（分期回款进度 + 状态流转）+ 新建（从产品目录选行自动分摊）。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, InputNumber, Modal, Select, SideSheet, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { Plus } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface Milestone { id?: number; seq: number; label: string; stage: string; ratio: number; amount_wan: number; due_date?: string; status?: string }
interface ContractRow {
  id: number; lead_id: number; name: string; amount_wan: number; status: string;
  enterprise_name: string | null; start_date: string | null; end_date: string | null;
  renewal_date: string | null; payment_terms: string | null; remark: string | null;
  milestones: Milestone[];
}
interface LeadOption { id: number; enterprise_name?: string }

const STATUS_TAG: Record<string, { label: string; color: "grey" | "blue" | "green" | "red" }> = {
  draft: { label: "草稿", color: "grey" },
  active: { label: "履约中", color: "blue" },
  completed: { label: "已完成", color: "green" },
  terminated: { label: "已终止", color: "red" },
};
const NEXT_STATUS: Record<string, string[]> = {
  draft: ["active", "terminated"], active: ["completed", "terminated"],
  completed: [], terminated: [],
};
const STAGE_LABEL: Record<string, string> = {
  prepayment: "预付款", delivery: "发货款", acceptance: "验收款", warranty: "质保金", payment: "付款",
};

export default function ContractsPage() {
  const [items, setItems] = useState<ContractRow[]>([]);
  const [status, setStatus] = useState<string>("");
  const [detail, setDetail] = useState<ContractRow | null>(null);
  const [creating, setCreating] = useState(false);
  const [leads, setLeads] = useState<LeadOption[]>([]);
  const [newLeadId, setNewLeadId] = useState<number | null>(null);
  const [newName, setNewName] = useState("");
  const [newEndDate, setNewEndDate] = useState("");
  const [newItems, setNewItems] = useState<Milestone[]>([]);

  const load = useCallback(() => {
    const p = status ? `?status=${status}` : "";
    api.get<{ items: ContractRow[] }>(`/api/contracts${p}`).then((d) => setItems(d.items)).catch(() => undefined);
  }, [status]);
  useEffect(load, [load]);

  const openCreate = () => {
    api.get<{ items: LeadOption[] }>("/api/leads?status=won&page_size=50").then((d) => setLeads(d.items)).catch(() => undefined);
    setNewLeadId(null); setNewName(""); setNewEndDate(""); setNewItems([]);
    setCreating(true);
  };
  const addItem = () => setNewItems([...newItems, { seq: newItems.length + 1, label: "", stage: "payment", ratio: 0, amount_wan: 0 }]);
  const updateItem = (idx: number, field: keyof Milestone, val: string | number) => {
    setNewItems((prev) => prev.map((m, i) => (i === idx ? { ...m, [field]: val } : m)));
  };
  const total = newItems.reduce((a, b) => a + (b.amount_wan || 0), 0);

  const save = async () => {
    if (!newLeadId || newItems.length === 0) { Toast.warning("选赢单线索并添加分期"); return; }
    try {
      await api.post("/api/contracts", { lead_id: newLeadId, name: newName || undefined, milestones: newItems, end_date: newEndDate || undefined });
      Toast.success("合同已创建");
      setCreating(false); load();
    } catch (e) { Toast.error(e instanceof Error ? e.message : "创建失败"); }
  };
  const transition = async (cid: number, st: string) => {
    try {
      await api.patch(`/api/contracts/${cid}/status`, { status: st });
      const d = await api.get<ContractRow>(`/api/contracts/${cid}`);
      setDetail(d); load();
    } catch (e) { Toast.error(e instanceof Error ? e.message : "失败"); }
  };

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <Select value={status || undefined} placeholder="全部状态" showClear onChange={(v) => setStatus(String(v ?? ""))} className="!w-36">
          <Select.Option value="draft">草稿</Select.Option>
          <Select.Option value="active">履约中</Select.Option>
          <Select.Option value="completed">已完成</Select.Option>
          <Select.Option value="terminated">已终止</Select.Option>
        </Select>
        <Button theme="solid" icon={<Plus size={14} />} onClick={openCreate}>新建合同</Button>
      </div>
      <div className="rounded-xl border border-[var(--border)] bg-white">
        <Table size="small" dataSource={items} rowKey="id" pagination={false}
          onRow={(r?: ContractRow) => ({
            onClick: () => r && api.get<ContractRow>(`/api/contracts/${r.id}`).then(setDetail).catch(() => undefined),
            style: { cursor: "pointer" },
          })}
          columns={[
            { title: "#", dataIndex: "id", width: 60 },
            { title: "客户", dataIndex: "enterprise_name", ellipsis: true, render: (v: string | null) => <span className="font-medium text-[var(--ink)]">{v ?? "—"}</span> },
            { title: "金额(万)", dataIndex: "amount_wan", width: 110, render: (v: number) => <span className="tabular-nums font-medium">{v.toLocaleString()}</span> },
            { title: "状态", dataIndex: "status", width: 100, render: (v: string) => { const s = STATUS_TAG[v] ?? { label: v, color: "grey" as const }; return <Tag color={s.color} type="light">{s.label}</Tag>; } },
            { title: "到期日", dataIndex: "end_date", width: 110, render: (v: string | null) => v ?? "—" },
            { title: "分期", dataIndex: "milestones", width: 70, render: (v: Milestone[]) => v?.length ?? 0 },
          ]}
          empty="暂无合同 — 从赢单线索创建"
        />
      </div>

      <SideSheet title={`合同详情`} visible={!!detail} onCancel={() => setDetail(null)} width={560}>
        {detail && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-[15px] font-medium text-[var(--ink)]">{detail.enterprise_name}</span>
              <Tag color={(STATUS_TAG[detail.status] ?? { color: "grey" }).color} type="light">{(STATUS_TAG[detail.status] ?? { label: detail.status }).label}</Tag>
            </div>
            <div className="text-[22px] font-semibold tabular-nums text-[var(--ink)]">
              {detail.amount_wan.toLocaleString()} 万元
            </div>
            <Table size="small" dataSource={detail.milestones} rowKey="seq" pagination={false}
              columns={[
                { title: "期次", dataIndex: "label" },
                { title: "金额(万)", dataIndex: "amount_wan", width: 90, render: (v: number) => <span className="tabular-nums">{v}</span> },
                { title: "状态", dataIndex: "status", width: 80, render: (v: string) => <Tag color={v === "paid" ? "green" : v === "overdue" ? "red" : "grey"} type="light" size="small">{v === "paid" ? "已回" : v === "overdue" ? "逾期" : "待回"}</Tag> },
              ]}
            />
            <div className="flex flex-wrap gap-2 pt-2">
              {(NEXT_STATUS[detail.status] ?? []).map((st) => (
                <Button key={st} size="small" onClick={() => transition(detail.id, st)}>
                  → {(STATUS_TAG[st] ?? { label: st }).label}
                </Button>
              ))}
            </div>
          </div>
        )}
      </SideSheet>

      <Modal title="新建合同" visible={creating} onOk={save} onCancel={() => setCreating(false)} okText="创建" width={640}>
        <div className="space-y-3 py-2">
          <Select placeholder="选赢单线索" value={newLeadId ?? undefined} onChange={(v) => setNewLeadId(Number(v))} filter className="!w-full">
            {leads.map((l) => <Select.Option key={l.id} value={l.id}>#{l.id} {l.enterprise_name ?? ""}</Select.Option>)}
          </Select>
          {newItems.map((_, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <span className="w-12 text-[12.5px] text-[var(--muted)]">第{idx + 1}期</span>
              <Input className="!w-28" placeholder="款项名" value={newItems[idx]?.label ?? ""} onChange={(v) => updateItem(idx, "label", v)} />
              <InputNumber className="!w-24" placeholder="万" min={0} value={newItems[idx]?.amount_wan ?? undefined} onChange={(v) => updateItem(idx, "amount_wan", typeof v === "number" ? v : 0)} />
            </div>
          ))}
          <div className="flex items-center justify-between">
            <Button size="small" onClick={addItem}>+ 加分期</Button>
            <span className="text-[15px] font-semibold tabular-nums text-[var(--ink)]">合计 {total.toLocaleString()} 万</span>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
