"use client";

/**
 * 治理面板（SPEC-J）：审计日志 / API 密钥 / 订阅规则 / 月度目标。
 * 挂设置页；每块自含状态，framed-sheet 扁平风格（无阴影无渐变）。
 */
import { useCallback, useEffect, useState } from "react";
import { Button, Input, InputNumber, Select, Table, Tag, Toast } from "@douyinfe/semi-ui";
import { api } from "@/lib/api";

const ACTION_TAG: Record<string, "green" | "blue" | "red"> = {
  insert: "green",
  update: "blue",
  delete: "red",
};
const ENTITY_CN: Record<string, string> = {
  enterprise: "企业",
  lead: "线索",
  contact: "联系人",
};

/* ---------------- J1 审计日志（admin） ---------------- */
export function AuditPanel() {
  const [items, setItems] = useState<
    { id: number; user: string; action: string; entity: string; entity_id: number; changes: Record<string, [string, string]>; created_at: string }[]
  >([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [entity, setEntity] = useState<string>("");

  const load = useCallback(() => {
    const p = new URLSearchParams({ page: String(page), page_size: "15" });
    if (entity) p.set("entity", entity);
    api
      .get<{ items: typeof items; total: number }>(`/api/audit?${p.toString()}`)
      .then((d) => {
        setItems(d.items);
        setTotal(d.total);
      })
      .catch(() => undefined);
  }, [page, entity]);

  useEffect(load, [load]);

  return (
    <div className="p-6">
      <div className="mb-4 flex items-center gap-2">
        <span className="text-[13px] text-slate-500">关键实体写操作留痕（insert/update/delete），系统来源标注为「系统」。</span>
        <Select value={entity} onChange={(v) => { setEntity(String(v)); setPage(1); }} placeholder="全部实体" showClear className="!w-32">
          <Select.Option value="enterprise">企业</Select.Option>
          <Select.Option value="lead">线索</Select.Option>
          <Select.Option value="contact">联系人</Select.Option>
        </Select>
      </div>
      <Table
        size="small"
        pagination={{ total, currentPage: page, pageSize: 15, onPageChange: setPage }}
        dataSource={items}
        rowKey="id"
        columns={[
          { title: "时间", dataIndex: "created_at", width: 170, render: (v: string) => (v || "").replace("T", " ").slice(0, 19) },
          { title: "操作者", dataIndex: "user", width: 90 },
          { title: "动作", dataIndex: "action", width: 80, render: (v: string) => <Tag color={ACTION_TAG[v] ?? "grey"} type="light">{v}</Tag> },
          { title: "对象", dataIndex: "entity", width: 130, render: (v: string, r) => `${ENTITY_CN[v] ?? v}#${r.entity_id}` },
          {
            title: "变更", dataIndex: "changes",
            render: (c: Record<string, [string, string]>) => (
              <div className="space-y-0.5 text-[12px]">
                {Object.entries(c ?? {}).map(([k, [b, a]]) => (
                  <div key={k}>
                    <span className="text-slate-500">{k}：</span>
                    <span className="text-slate-400 line-through">{(b || "空").slice(0, 40)}</span>
                    <span className="text-[var(--accent)]"> → {(a || "空").slice(0, 60)}</span>
                  </div>
                ))}
                {!Object.keys(c ?? {}).length && <span className="text-slate-400">新建</span>}
              </div>
            ),
          },
        ]}
        empty="暂无审计记录"
      />
    </div>
  );
}

/* ---------------- J1 API 密钥 ---------------- */
interface KeyRow { id: number; name: string; scopes: string; prefix: string; last_used_at: string | null; created_at: string }

export function ApiKeysPanel() {
  const [items, setItems] = useState<KeyRow[]>([]);
  const [name, setName] = useState("");
  const [scopes, setScopes] = useState("read");
  const [fresh, setFresh] = useState<string | null>(null);

  const load = useCallback(() => {
    api.get<{ items: KeyRow[] }>("/api/api-keys").then((d) => setItems(d.items)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  const create = async () => {
    try {
      const d = await api.post<{ key: string }>("/api/api-keys", { name, scopes });
      setFresh(d.key);
      setName("");
      load();
      Toast.success("已创建，明文仅显示一次");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "创建失败");
    }
  };
  const revoke = async (id: number) => {
    await api.delete(`/api/api-keys/${id}`).catch(() => undefined);
    load();
  };

  return (
    <div className="p-6 max-w-[640px]">
      <div className="mb-4 text-[13px] text-slate-500 leading-6">
        机器对机器访问通道（请求头 <code className="text-[var(--accent)]">X-Api-Key</code>）。
        read 仅放行查询；rw 全放行。明文只在创建时显示一次。
      </div>
      <div className="mb-4 flex items-center gap-2">
        <Input placeholder="密钥名称，如 报表系统" value={name} onChange={setName} className="!w-56" />
        <Select value={scopes} onChange={(v) => setScopes(String(v))} className="!w-28">
          <Select.Option value="read">只读</Select.Option>
          <Select.Option value="rw">读写</Select.Option>
        </Select>
        <Button theme="solid" disabled={!name.trim()} onClick={create}>创建</Button>
      </div>
      {fresh && (
        <div className="mb-4 rounded-lg border border-[var(--border)] bg-[var(--bg-soft,#f7f9fa)] p-3 text-[13px]">
          <div className="mb-1 text-slate-500">新密钥（仅此一次）：</div>
          <code className="break-all text-[var(--accent)]">{fresh}</code>
        </div>
      )}
      <Table
        size="small"
        dataSource={items}
        rowKey="id"
        pagination={false}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "前缀", dataIndex: "prefix", width: 140, render: (v: string) => <code className="text-slate-500">{v}…</code> },
          { title: "权限", dataIndex: "scopes", width: 80, render: (v: string) => <Tag color={v === "rw" ? "orange" : "blue"} type="light">{v === "rw" ? "读写" : "只读"}</Tag> },
          { title: "最近使用", dataIndex: "last_used_at", width: 170, render: (v: string | null) => (v ? v.replace("T", " ").slice(0, 19) : "从未") },
          { title: "", dataIndex: "id", width: 80, render: (id: number) => <Button size="small" type="danger" theme="borderless" onClick={() => revoke(id)}>吊销</Button> },
        ]}
        empty="还没有 API 密钥"
      />
    </div>
  );
}

/* ---------------- J7 订阅规则 ---------------- */
interface SubRuleRow { id: number; kind: string; value: string; created_at: string }

export function SubRulesPanel() {
  const [items, setItems] = useState<SubRuleRow[]>([]);
  const [kind, setKind] = useState("keyword");
  const [value, setValue] = useState("");

  const load = useCallback(() => {
    api.get<{ items: SubRuleRow[] }>("/api/subscription-rules").then((d) => setItems(d.items)).catch(() => undefined);
  }, []);
  useEffect(load, [load]);

  const add = async () => {
    try {
      await api.post("/api/subscription-rules", { kind, value });
      setValue("");
      load();
      Toast.success("已订阅，每日新信号命中后将通知你");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "创建失败");
    }
  };

  return (
    <div className="p-6 max-w-[640px]">
      <div className="mb-4 text-[13px] text-slate-500 leading-6">
        按关键词/地区订阅公共采购信号（每日增量后自动匹配并通知，走静默聚合）；「筛选订阅」在
        标讯中心按当前筛选一键创建。关键词匹配公告标题与企业名；地区匹配省/市。
      </div>
      <div className="mb-4 flex items-center gap-2">
        <Select value={kind} onChange={(v) => setKind(String(v))} className="!w-28">
          <Select.Option value="keyword">关键词</Select.Option>
          <Select.Option value="region">地区</Select.Option>
        </Select>
        <Input
          placeholder={kind === "keyword" ? "如 分子泵 / 镀膜机" : "如 江苏省 / 北京市"}
          value={value}
          onChange={setValue}
          className="!w-56"
        />
        <Button theme="solid" disabled={value.trim().length < 2} onClick={add}>订阅</Button>
      </div>
      <Table
        size="small"
        dataSource={items}
        rowKey="id"
        pagination={false}
        columns={[
          { title: "类型", dataIndex: "kind", width: 90, render: (v: string) => <Tag color={v === "keyword" ? "blue" : v === "filter" ? "orange" : "cyan"} type="light">{v === "keyword" ? "关键词" : v === "filter" ? "筛选订阅" : "地区"}</Tag> },
          { title: "内容", dataIndex: "value", render: (v: string, r: SubRuleRow) => {
              if (r.kind !== "filter") return v;
              try {
                const c = JSON.parse(v) as Record<string, string | number>;
                const parts: string[] = [];
                if (c.type) parts.push(`类型=${c.type}`);
                if (c.industry_key) parts.push(`行业=${c.industry_key}`);
                if (c.province) parts.push(`地区=${c.province}`);
                if (c.q) parts.push(`关键词=${c.q}`);
                if (c.days) parts.push(`近${c.days}天`);
                return <span className="text-[12.5px]">{parts.join("，") || "全部标讯"}</span>;
              } catch {
                return v;
              }
            } },
          { title: "创建时间", dataIndex: "created_at", width: 170, render: (v: string) => (v || "").replace("T", " ").slice(0, 19) },
          {
            title: "", dataIndex: "id", width: 80,
            render: (id: number) => (
              <Button size="small" type="danger" theme="borderless" onClick={async () => { await api.delete(`/api/subscription-rules/${id}`).catch(() => undefined); load(); }}>
                删除
              </Button>
            ),
          },
        ]}
        empty="暂无订阅规则"
      />
    </div>
  );
}

/* ---------------- J6 月度目标 ---------------- */
export function TargetPanel() {
  const now = new Date();
  const month = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  const [amount, setAmount] = useState<number | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    api.get<{ amount_wan: number | null }>(`/api/my-target?month=${month}`)
      .then((d) => setAmount(d.amount_wan))
      .catch(() => undefined);
  }, [month]);

  const save = async () => {
    setLoading(true);
    try {
      await api.put("/api/my-target", { month, amount_wan: amount ?? 0 });
      Toast.success("目标已保存，工作台将显示达成率");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "保存失败");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="p-6 max-w-[560px]">
      <div className="mb-4 text-[13px] text-slate-500 leading-6">
        设定 {month} 个人成交目标（万元）。工作台「本月加权预测」卡会用
        Σ(商机金额×赢率) 对照目标计算达成率。
      </div>
      <div className="flex items-center gap-2">
        <span className="text-[13px] text-slate-600">{month} 目标</span>
        <InputNumber value={amount ?? undefined} onChange={(v) => setAmount(v as number)} min={0} className="!w-40" />
        <span className="text-[13px] text-slate-500">万元</span>
        <Button theme="solid" loading={loading} onClick={save}>保存</Button>
      </div>
    </div>
  );
}
