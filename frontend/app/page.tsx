"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, Table, Tag } from "antd";
import {
  AlertOutlined,
  ArrowRightOutlined,
  ThunderboltOutlined,
  SolutionOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { LEAD_STATUS } from "@/lib/constants";

interface DashboardData {
  total_enterprises: number;
  total_signals: number;
  signals_7d: number;
  my_leads_by_status: Record<string, number>;
  pending_followups: {
    lead_id: number;
    enterprise_name: string;
    next_action: string | null;
    next_time: string | null;
  }[];
}

const CHIP_STYLES: Record<string, string> = {
  indigo: "bg-indigo-50 text-indigo-600",
  violet: "bg-violet-50 text-violet-600",
  rose: "bg-rose-50 text-rose-500",
  emerald: "bg-emerald-50 text-emerald-600",
};

function KpiCard(props: { icon: React.ReactNode; chip: string; label: string; value: number; sub?: string }) {
  return (
    <Card styles={{ body: { padding: "18px 20px" } }}>
      <div className="flex items-center gap-4">
        <div className={`w-11 h-11 rounded-xl flex items-center justify-center text-[19px] shrink-0 ${CHIP_STYLES[props.chip]}`}>
          {props.icon}
        </div>
        <div className="min-w-0">
          <div className="text-[12px] text-slate-400">{props.label}</div>
          <div className="text-[26px] leading-8 font-semibold text-slate-800 tabular-nums">{props.value}</div>
          {props.sub && <div className="text-[11.5px] text-slate-400 mt-0.5 truncate">{props.sub}</div>}
        </div>
      </div>
    </Card>
  );
}

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
  const myLeadsSub =
    Object.entries(myLeads)
      .filter(([, v]) => v > 0)
      .map(([k, v]) => `${LEAD_STATUS[k]?.label ?? k} ${v}`)
      .join(" · ") || "尚未领取线索";

  return (
    <AppShell>
      {/* 欢迎条 */}
      <div className="rounded-2xl bg-gradient-to-r from-[#1e1b4b] via-[#312e81] to-[#5b21b6] text-white px-7 py-6 flex items-center justify-between relative overflow-hidden">
        <div
          className="absolute -right-16 -top-24 w-72 h-72 rounded-full opacity-20 pointer-events-none"
          style={{ background: "radial-gradient(circle,#a78bfa,transparent 70%)" }}
        />
        <div className="relative">
          <div className="text-[17px] font-semibold">今天的采购信号已就绪</div>
          <div className="text-indigo-200/80 text-[13px] mt-1.5">
            近 7 天新增 <span className="text-white font-semibold">{data?.signals_7d ?? 0}</span> 条设备采购信号 —— 到企业筛选页看看谁在买设备
          </div>
        </div>
        <button
          onClick={() => router.push("/enterprises")}
          className="relative shrink-0 flex items-center gap-1.5 rounded-lg bg-white/10 hover:bg-white/20 border border-white/20 px-4 py-2 text-[13px] font-medium transition-colors cursor-pointer"
        >
          开始筛选 <ArrowRightOutlined className="text-[11px]" />
        </button>
      </div>

      {/* KPI */}
      <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mt-5">
        <KpiCard icon={<TeamOutlined />} chip="indigo" label="企业库总数" value={data?.total_enterprises ?? 0} sub="POI + 导入持续累积" />
        <KpiCard icon={<ThunderboltOutlined />} chip="violet" label="意图信号累计" value={data?.total_signals ?? 0} sub="招投标 × 设备词表命中" />
        <KpiCard icon={<AlertOutlined />} chip="rose" label="近 7 天信号" value={data?.signals_7d ?? 0} sub="优先跟进新信号客户" />
        <KpiCard icon={<SolutionOutlined />} chip="emerald" label="我的线索" value={myTotal} sub={myLeadsSub} />
      </div>

      {/* 待跟进 */}
      <Card
        title={<span className="text-[14.5px]">待跟进事项</span>}
        extra={
          <a onClick={() => router.push("/leads")} className="cursor-pointer text-[13px]">
            全部线索 <ArrowRightOutlined className="text-[10px]" />
          </a>
        }
        className="mt-5"
      >
        <Table<DashboardData["pending_followups"][number]>
          rowKey="lead_id"
          size="middle"
          loading={loading}
          dataSource={data?.pending_followups ?? []}
          pagination={{ pageSize: 8, hideOnSinglePage: true }}
          locale={{ emptyText: "暂无待跟进 —— 从企业筛选页把高分客户转化为线索吧" }}
          columns={[
            {
              title: "企业",
              dataIndex: "enterprise_name",
              render: (v) => <span className="font-medium text-slate-700">{v}</span>,
            },
            {
              title: "下一步",
              dataIndex: "next_action",
              render: (v) => (v ? <Tag color="geekblue">{v}</Tag> : <span className="text-slate-300">待规划</span>),
            },
            {
              title: "约定时间",
              dataIndex: "next_time",
              width: 160,
              render: (v) => (v ? <Tag color="orange">{String(v).slice(0, 16).replace("T", " ")}</Tag> : "-"),
            },
          ]}
        />
      </Card>
    </AppShell>
  );
}
