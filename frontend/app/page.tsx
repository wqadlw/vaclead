"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Table } from "@douyinfe/semi-ui";
import { ArrowRight } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { LEAD_STATUS } from "@/lib/constants";

interface DashboardData {
  total_enterprises: number;
  total_signals: number;
  signals_7d: number;
  my_leads_total?: number;
  my_leads_by_status: Record<string, number>;
  pending_followups: {
    lead_id: number;
    enterprise_name: string;
    next_action: string | null;
    next_time: string | null;
  }[];
  recent_signals: {
    signal_id: number;
    title: string;
    enterprise_id: number;
    enterprise_name: string;
    pub_date: string | null;
    base_score: number;
  }[];
}

/* overview 版式：标题行 → KPI 分隔条（无图标）→ 主列（待跟进）+ 384px 边栏（最新信号） */
export default function DashboardPage() {
  const router = useRouter();
  const [data, setData] = useState<DashboardData | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api
      .get<DashboardData>("/api/dashboard")
      .then(setData)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, []);

  const myLeads = data?.my_leads_by_status ?? {};
  const myTotal = Object.values(myLeads).reduce((a, b) => a + b, 0);
  const myLeadsCaption =
    Object.entries(myLeads)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${LEAD_STATUS[k]?.label ?? k} ${v}`)
      .join(" · ") || "尚未领取线索";

  const kpis = [
    { label: "企业库", value: data?.total_enterprises ?? 0, caption: "POI 采集与导入累积" },
    { label: "意图信号", value: data?.total_signals ?? 0, caption: "招标公告 × 设备词表命中" },
    { label: "近 7 天信号", value: data?.signals_7d ?? 0, caption: "优先跟进新信号客户" },
    { label: "我的线索", value: myTotal, caption: myLeadsCaption },
  ];

  return (
    <AppShell>
      {/* 标题行：名词 h1 + 灰色数据行 + 唯一主操作 */}
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-[22px] font-semibold leading-7 text-[var(--ink)]">工作台</h1>
          <p className="mt-1 text-[13.5px] text-[var(--muted)]">
            近 7 天新增 {data?.signals_7d ?? 0} 条设备采购信号 —— 优先跟进有新信号的高分客户
          </p>
        </div>
        <Button theme="solid" onClick={() => router.push("/enterprises")} className="!h-9">
          筛选客户
        </Button>
      </div>

      {/* KPI 条：一个容器 1px 分隔，无图标无阴影 */}
      <div className="mt-5 grid grid-cols-2 overflow-hidden rounded-lg border border-[var(--border)] md:grid-cols-4">
        {kpis.map((k, i) => (
          <div
            key={k.label}
            className={`bg-white px-5 py-4 ${i > 0 ? "border-l border-[var(--rule)]" : ""} ${
              i >= 2 ? "max-md:border-t max-md:border-[var(--rule)]" : ""
            } ${i === 2 ? "max-md:border-l-0" : ""}`}
          >
            <div className="text-[13px] text-[var(--muted)]">{k.label}</div>
            <div className="mt-1 text-[28px] font-normal leading-9 tabular-nums text-[var(--ink)]">{k.value}</div>
            <div className="mt-0.5 truncate text-[12px] text-[#8a8d8f]">{k.caption}</div>
          </div>
        ))}
      </div>

      {/* 主列 + 边栏 */}
      <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-[1fr_384px]">
        <div>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[16px] font-medium text-[var(--ink)]">待跟进事项</h2>
            <button
              onClick={() => router.push("/leads")}
              className="flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-[13px] text-[var(--accent)] hover:underline"
            >
              全部线索 <ArrowRight size={12} />
            </button>
          </div>
          <Table<DashboardData["pending_followups"][number]>
            rowKey="lead_id"
            size="middle"
            loading={loading}
            dataSource={data?.pending_followups ?? []}
            pagination={false}
            empty="暂无待跟进 —— 从企业筛选页把高分客户转为线索"
            columns={[
              {
                title: "企业",
                dataIndex: "enterprise_name",
                render: (v: string) => <span className="font-medium text-[var(--ink)]">{v}</span>,
              },
              {
                title: "下一步",
                dataIndex: "next_action",
                render: (v: string) => v || <span className="text-[#9ca0a3]">待规划</span>,
              },
              {
                title: "约定时间",
                dataIndex: "next_time",
                width: 150,
                render: (v: string) => (v ? String(v).slice(0, 16).replace("T", " ") : "—"),
              },
            ]}
          />
        </div>

        {/* 边栏：最新信号（列表 1px 规则，45px 行距） */}
        <aside>
          <div className="mb-2 flex items-baseline justify-between">
            <h2 className="text-[16px] font-medium text-[var(--ink)]">最新信号</h2>
            <button
              onClick={() => router.push("/enterprises")}
              className="flex cursor-pointer items-center gap-1 border-0 bg-transparent p-0 text-[13px] text-[var(--accent)] hover:underline"
            >
              按信号筛选 <ArrowRight size={12} />
            </button>
          </div>
          <div className="rounded-lg border border-[var(--border)] bg-white">
            {(data?.recent_signals ?? []).length === 0 ? (
              <div className="p-4 text-[13px] text-[var(--muted)]">
                暂无信号 —— 在「拓客任务」导入招标公告后，这里按时间倒序展示最近命中的采购公告
              </div>
            ) : (
              data!.recent_signals.map((s, i) => (
                <div
                  key={s.signal_id}
                  className={`px-4 py-3 ${i > 0 ? "border-t border-[var(--rule)]" : ""}`}
                >
                  <div className="truncate text-[13px] font-medium text-[var(--ink)]" title={s.title}>
                    {s.title}
                  </div>
                  <div className="mt-0.5 flex items-center justify-between text-[12px] text-[var(--muted)]">
                    <span className="truncate">{s.enterprise_name}</span>
                    <span className="tabular-nums">{s.pub_date || "—"}</span>
                  </div>
                </div>
              ))
            )}
          </div>
        </aside>
      </div>
    </AppShell>
  );
}
