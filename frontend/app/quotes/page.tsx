"use client";

/**
 * 报价单（SPEC-J J3）：intent→won 之间的转化单据。
 * 列表 → 详情侧栏（行项/状态流转/打印）；新建时从产品目录选行。
 * 静态导出：详情用查询参数（/quotes/view?id=）承载，本页仅列表。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, Modal, Select, SideSheet, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { Plus, Printer } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface QuoteItem {
  id?: number;
  product_id: number | null;
  name: string;
  spec?: string | null;
  qty: number;
  price: number;
  price_unit: string;
  subtotal?: number;
}
interface QuoteRow {
  id: number;
  lead_id: number;
  status: string;
  valid_until: string | null;
  total_wan: number;
  enterprise_name: string | null;
  remark: string | null;
  items: QuoteItem[];
}
interface ProductRow { id: number; name: string; model: string | null; price: number; price_unit: string }
interface LeadRow { id: number; enterprise_name?: string; name?: string }

const STATUS_TAG: Record<string, { label: string; color: "grey" | "blue" | "green" | "red" }> = {
  draft: { label: "草稿", color: "grey" },
  sent: { label: "已发送", color: "blue" },
  won: { label: "赢单", color: "green" },
  lost: { label: "输单", color: "red" },
};
const NEXT_STATUS: Record<string, string[]> = {
  draft: ["sent", "won", "lost"],
  sent: ["won", "lost", "draft"],
  won: [],
  lost: ["draft"],
};

export default function QuotesPage() {
  const [items, setItems] = useState<QuoteRow[]>([]);
  const [status, setStatus] = useState<string>("");
  const [detail, setDetail] = useState<QuoteRow | null>(null);
  const [creating, setCreating] = useState(false);
  const [products, setProducts] = useState<ProductRow[]>([]);
  const [leads, setLeads] = useState<LeadRow[]>([]);
  const [newLeadId, setNewLeadId] = useState<number | null>(null);
  const [newItems, setNewItems] = useState<QuoteItem[]>([]);

  const load = useCallback(() => {
    const p = status ? `?status=${status}` : "";
    api.get<{ items: QuoteRow[] }>(`/api/quotes${p}`).then((d) => setItems(d.items)).catch(() => undefined);
  }, [status]);
  useEffect(load, [load]);

  const openCreate = () => {
    api.get<{ items: ProductRow[] }>("/api/products").then((d) => setProducts(d.items)).catch(() => undefined);
    api.get<{ items: LeadRow[] }>("/api/leads?page_size=50").then((d) => setLeads(d.items)).catch(() => undefined);
    setNewLeadId(null);
    setNewItems([]);
    setCreating(true);
  };
  const addItem = () => {
    setNewItems([...newItems, { product_id: null, name: "", qty: 1, price: 0, price_unit: "台" }]);
  };
  const pickProduct = (idx: number, pid: number) => {
    const p = products.find((x) => x.id === pid);
    const next = [...newItems];
    next[idx] = {
      product_id: pid, name: p ? p.name : "", spec: null,
      qty: next[idx]?.qty || 1, price: p ? p.price : 0, price_unit: p ? p.price_unit : "台",
    };
    setNewItems(next);
  };
  const total = newItems.reduce((a, b) => a + (b.qty || 0) * (b.price || 0), 0);
  const saveQuote = async () => {
    if (!newLeadId || newItems.length === 0 || newItems.some((i) => !i.name.trim())) {
      Toast.warning("选线索、每行选产品");
      return;
    }
    try {
      await api.post("/api/quotes", { lead_id: newLeadId, items: newItems });
      Toast.success("报价单已创建");
      setCreating(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "创建失败");
    }
  };
  const transition = async (qid: number, st: string) => {
    await api.patch(`/api/quotes/${qid}/status`, { status: st }).catch((e) => Toast.error(e instanceof Error ? e.message : "失败"));
    const d = await api.get<QuoteRow>(`/api/quotes/${qid}`);
    setDetail(d);
    load();
  };

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <Select value={status} onChange={(v) => setStatus(String(v))} placeholder="全部状态" showClear className="!w-36">
          <Select.Option value="draft">草稿</Select.Option>
          <Select.Option value="sent">已发送</Select.Option>
          <Select.Option value="won">赢单</Select.Option>
          <Select.Option value="lost">输单</Select.Option>
        </Select>
        <Button theme="solid" icon={<Plus size={14} />} onClick={openCreate}>新建报价单</Button>
      </div>
      <div className="rounded-xl border border-[var(--border)] bg-white">
        <Table
          size="small"
          dataSource={items}
          rowKey="id"
          pagination={false}
          onRow={(r) => ({
            onClick: () => api.get<QuoteRow>(`/api/quotes/${r.id}`).then(setDetail).catch(() => undefined),
            style: { cursor: "pointer" },
          })}
          columns={[
            { title: "#", dataIndex: "id", width: 70 },
            { title: "客户", dataIndex: "enterprise_name", render: (v: string | null) => <span className="font-medium text-[var(--ink)]">{v ?? "—"}</span> },
            { title: "金额", dataIndex: "total_wan", width: 130, render: (v: number) => <span className="tabular-nums">{v.toLocaleString()} 万</span> },
            { title: "状态", dataIndex: "status", width: 100, render: (v: string) => {
              const s = STATUS_TAG[v] ?? { label: v, color: "grey" as const };
              return <Tag color={s.color} type="light">{s.label}</Tag>;
            } },
            { title: "有效期至", dataIndex: "valid_until", width: 120, render: (v: string | null) => v ?? "—" },
            { title: "行项", dataIndex: "items", width: 80, render: (v: QuoteItem[]) => (v?.length ?? 0) },
          ]}
          empty="还没有报价单 —— 从线索列表或本页新建"
        />
      </div>

      <SideSheet title={`报价单 #${detail?.id ?? ""}`} visible={!!detail} onCancel={() => setDetail(null)} width={560}>
        {detail && (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <span className="text-[15px] font-medium text-[var(--ink)]">{detail.enterprise_name}</span>
              <Tag color={(STATUS_TAG[detail.status] ?? { color: "grey" }).color} type="light">
                {(STATUS_TAG[detail.status] ?? { label: detail.status }).label}
              </Tag>
            </div>
            <div className="text-[20px] font-semibold text-[var(--ink)] tabular-nums">
              合计 {detail.total_wan.toLocaleString()} 万元
              {detail.valid_until && <span className="ml-2 text-[12px] text-[var(--muted)]">有效期至 {detail.valid_until}</span>}
            </div>
            <Table
              size="small"
              dataSource={detail.items}
              rowKey="id"
              pagination={false}
              columns={[
                { title: "项目", dataIndex: "name" },
                { title: "数量", dataIndex: "qty", width: 70, render: (v: number, r) => `${v} ${r.price_unit}` },
                { title: "单价(万)", dataIndex: "price", width: 90, render: (v: number) => v.toLocaleString() },
                { title: "小计", dataIndex: "subtotal", width: 90, render: (v: number) => <span className="tabular-nums">{v.toLocaleString()}</span> },
              ]}
            />
            {detail.remark && <div className="text-[13px] text-[var(--muted)]">备注：{detail.remark}</div>}
            <div className="flex flex-wrap gap-2 pt-2">
              {(NEXT_STATUS[detail.status] ?? []).map((st) => (
                <Button key={st} onClick={() => transition(detail.id, st)}>
                  → {(STATUS_TAG[st] ?? { label: st }).label}
                </Button>
              ))}
              <Button icon={<Printer size={14} />} onClick={() => window.open(`/quotes/print?id=${detail.id}`, "_blank")}>
                打印
              </Button>
            </div>
          </div>
        )}
      </SideSheet>

      <Modal
        title="新建报价单"
        visible={creating}
        onOk={saveQuote}
        onCancel={() => setCreating(false)}
        okText="创建"
        width={620}
      >
        <div className="space-y-3 py-2">
          <Select
            placeholder="选线索（仅跟进中/意向）"
            value={newLeadId ?? undefined}
            onChange={(v) => setNewLeadId(Number(v))}
            filter
            className="!w-full"
          >
            {leads.map((l) => (
              <Select.Option key={l.id} value={l.id}>
                #{l.id} {l.enterprise_name ?? ""}
              </Select.Option>
            ))}
          </Select>
          {newItems.map((it, idx) => (
            <div key={idx} className="flex items-center gap-2">
              <Select placeholder="选产品" value={it.product_id ?? undefined} onChange={(v) => pickProduct(idx, Number(v))} className="!w-56" filter>
                {products.map((p) => (
                  <Select.Option key={p.id} value={p.id}>{p.name}</Select.Option>
                ))}
              </Select>
              <Input
                className="!w-16"
                type="number"
                value={String(it.qty)}
                onChange={(v) => {
                  const next = [...newItems];
                  next[idx] = { ...it, qty: Number(v) || 1 };
                  setNewItems(next);
                }}
              />
              <span className="text-[12px] text-[var(--muted)]">× {it.price.toLocaleString()} 万</span>
            </div>
          ))}
          <div className="flex items-center justify-between">
            <Button size="small" onClick={addItem}>+ 加一行</Button>
            <span className="text-[14px] font-medium tabular-nums text-[var(--ink)]">合计 {total.toLocaleString()} 万</span>
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
