"use client";

/**
 * 产品目录（SPEC-J J3）：报价单的行项目来源。
 * 列表 + 新建/编辑弹窗 + 软删（历史报价引用不受影响）。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, InputNumber, Modal, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { Plus } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface ProductRow {
  id: number;
  name: string;
  model: string | null;
  spec: string | null;
  price: number;
  price_unit: string;
}

const EMPTY = { name: "", model: "", spec: "", price: 0 as number | null, price_unit: "台" };

export default function ProductsPage() {
  const [items, setItems] = useState<ProductRow[]>([]);
  const [q, setQ] = useState("");
  const [modal, setModal] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [form, setForm] = useState(EMPTY);

  const load = useCallback(() => {
    const p = q.trim() ? `?q=${encodeURIComponent(q.trim())}` : "";
    api.get<{ items: ProductRow[] }>(`/api/products${p}`).then((d) => setItems(d.items)).catch(() => undefined);
  }, [q]);
  useEffect(load, [load]);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY);
    setModal(true);
  };
  const openEdit = (row: ProductRow) => {
    setEditing(row.id);
    setForm({ name: row.name, model: row.model ?? "", spec: row.spec ?? "", price: row.price, price_unit: row.price_unit });
    setModal(true);
  };
  const save = async () => {
    if (!form.name.trim()) return;
    const body = {
      name: form.name.trim(), model: form.model || null, spec: form.spec || null,
      price: Number(form.price ?? 0), price_unit: form.price_unit || "台",
    };
    try {
      if (editing) await api.patch(`/api/products/${editing}`, body);
      else await api.post("/api/products", body);
      Toast.success("已保存");
      setModal(false);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    }
  };
  const remove = async (id: number) => {
    await api.delete(`/api/products/${id}`).catch(() => undefined);
    load();
  };

  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Input placeholder="搜产品名" value={q} onChange={setQ} showClear className="!w-56" />
        </div>
        <Button theme="solid" icon={<Plus size={14} />} onClick={openCreate}>新建产品</Button>
      </div>
      <div className="rounded-xl border border-[var(--border)] bg-white">
        <Table
          size="small"
          dataSource={items}
          rowKey="id"
          pagination={false}
          columns={[
            { title: "产品名", dataIndex: "name", render: (v: string, r) => (
              <span className="font-medium text-[var(--ink)]">{v}{r.model && <span className="ml-1.5 text-[12px] text-[var(--muted)]">{r.model}</span>}</span>
            ) },
            { title: "参数", dataIndex: "spec", render: (v: string | null) => <span className="text-[12.5px] text-[var(--muted)]">{v ?? "—"}</span> },
            { title: "单价", dataIndex: "price", width: 150, render: (v: number, r) => (
              <span className="tabular-nums">{v.toLocaleString()} 万/{r.price_unit}</span>
            ) },
            { title: "在售", dataIndex: "id", width: 70, render: () => <Tag color="green" type="light">在售</Tag> },
            { title: "", dataIndex: "id", width: 130, render: (id: number, r) => (
              <span className="space-x-2">
                <Button size="small" theme="borderless" onClick={() => openEdit(r)}>编辑</Button>
                <Button size="small" type="danger" theme="borderless" onClick={() => remove(id)}>下架</Button>
              </span>
            ) },
          ]}
          empty="还没有产品 —— 新建后可在报价单里引用"
        />
      </div>
      <Modal
        title={editing ? "编辑产品" : "新建产品"}
        visible={modal}
        onOk={save}
        onCancel={() => setModal(false)}
        okText="保存"
        width={460}
      >
        <div className="space-y-3 py-2">
          <Input placeholder="产品名，如 分子泵 FF-160" value={form.name} onChange={(v) => setForm({ ...form, name: v })} />
          <Input placeholder="型号（选填）" value={form.model} onChange={(v) => setForm({ ...form, model: v })} />
          <Input placeholder="参数摘要（选填）" value={form.spec} onChange={(v) => setForm({ ...form, spec: v })} />
          <div className="flex items-center gap-2">
            <InputNumber value={form.price ?? undefined} onChange={(v) => setForm({ ...form, price: v as number })} min={0} className="!w-40" />
            <span className="text-[13px] text-slate-500">万元 /</span>
            <Input value={form.price_unit} onChange={(v) => setForm({ ...form, price_unit: v })} className="!w-24" />
          </div>
        </div>
      </Modal>
    </AppShell>
  );
}
