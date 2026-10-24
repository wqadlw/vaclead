"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Card, Table, Tag } from "@douyinfe/semi-ui";
import { motion } from "framer-motion";
import { ArrowRight, BellRing, Radar, Users, Zap } from "lucide-react";
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

const KPIS = [
  { key: "total_enterprises", label: "企业库总数", sub: "POI + 导入持续累积", icon: Users, chip: "bg-indigo-50 text-indigo-600" },
  { key: "total_signals", label: "意图信号累计", sub: "招投标 × 设备词表命中", icon: Zap, chip: "bg-violet-50 text-violet-600" },
  { key: "signals_7d", label: "近 7 天信号", sub: "优先跟进新信号客户", icon: BellRing, chip: "bg-rose-50 text-rose-500" },
] as const;

const gridStagger = {
  hidden: {},
  show: { transition: { staggerChildren: 0.08, delayChildren: 0.1 } },
};
const rise = {
  hidden: { opacity: 0, y: 18 },
  show: { opacity: 1, y: 0, transition: { duration: 0.5, ease: [0.22, 1, 0.36, 1] } },
};

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
      <motion.div variants={gridStagger} initial="hidden" animate="show">
        {/* 欢迎横幅 */}
        <motion.div
          variants={rise}
          className="rounded-2xl bg-gradient-to-r from-[#1e1b4b] via-[#312e81] to-[#5b21b6] text-white px-7 py-6 flex items-center justify-between relative overflow-hidden"
        >
          <div
            className="absolute -right-16 -top-24 w-72 h-72 rounded-full opacity-25 pointer-events-none"
            style={{ background: "radial-gradient(circle,#a78bfa,transparent 70%)" }}
          />
          <div className="relative">
            <div className="text-[17px] font-semibold">今天的采购信号已就绪</div>
            <div className="text-indigo-200/80 text-[13px] mt-1.5">
              近 7 天新增 <span className="text-white font-semibold">{data?.signals_7d ?? 0}</span>{" "}
              条设备采购信号 —— 到企业筛选页看看谁在买设备
            </div>
          </div>
          <motion.button
            whileHover={{ scale: 1.04 }}
            whileTap={{ scale: 0.97 }}
            onClick={() => router.push("/enterprises")}
            className="relative shrink-0 flex items-center gap-1.5 rounded-[10px] bg-white/10 hover:bg-white/20 border border-white/20 px-4 py-2 text-[13px] font-medium transition-colors cursor-pointer"
          >
            开始筛选 <ArrowRight size={14} />
          </motion.button>
        </motion.div>

        {/* KPI 卡片 */}
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-4 mt-5">
          {KPIS.map((k) => (
            <motion.div key={k.key} variants={rise}>
              <Card
                shadows="hover"
                loading={loading}
                className="!rounded-2xl !border-0 !shadow-[0_1px_2px_rgba(15,23,42,.04),0_10px_28px_-14px_rgba(15,23,42,.14)]"
                bodyStyle={{ padding: "18px 20px" }}
              >
                <div className="flex items-center gap-4">
                  <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${k.chip}`}>
                    <k.icon size={20} strokeWidth={2.1} />
                  </div>
                  <div className="min-w-0">
                    <div className="text-[12px] text-slate-400">{k.label}</div>
                    <div className="text-[26px] leading-8 font-semibold text-slate-800 tabular-nums">
                      {data?.[k.key] ?? 0}
                    </div>
                    <div className="text-[11px] text-slate-400 mt-0.5 truncate">{k.sub}</div>
                  </div>
                </div>
              </Card>
            </motion.div>
          ))}
          <motion.div variants={rise}>
            <Card
              shadows="hover"
              loading={loading}
              className="!rounded-2xl !border-0 !shadow-[0_1px_2px_rgba(15,23,42,.04),0_10px_28px_-14px_rgba(15,23,42,.14)]"
              bodyStyle={{ padding: "18px 20px" }}
            >
              <div className="flex items-center gap-4">
                <div className="w-11 h-11 rounded-xl flex items-center justify-center shrink-0 bg-emerald-50 text-emerald-600">
                  <Radar size={20} strokeWidth={2.1} />
                </div>
                <div className="min-w-0">
                  <div className="text-[12px] text-slate-400">我的线索</div>
                  <div className="text-[26px] leading-8 font-semibold text-slate-800 tabular-nums">{myTotal}</div>
                  <div className="text-[11px] text-slate-400 mt-0.5 truncate">{myLeadsSub}</div>
                </div>
              </div>
            </Card>
          </motion.div>
        </div>

        {/* 待跟进 */}
        <motion.div variants={rise}>
          <Card
            title={<span className="text-[14.5px] font-semibold text-slate-700">待跟进事项</span>}
            headerExtraContent={
              <span
                onClick={() => router.push("/leads")}
                className="cursor-pointer text-[13px] text-indigo-600 hover:text-indigo-500 flex items-center gap-1"
              >
                全部线索 <ArrowRight size={12} />
              </span>
            }
            className="mt-5 !rounded-2xl !border-0 !shadow-[0_1px_2px_rgba(15,23,42,.04),0_10px_28px_-14px_rgba(15,23,42,.14)]"
          >
            <Table<DashboardData["pending_followups"][number]>
              rowKey="lead_id"
              size="small"
              loading={loading}
              dataSource={data?.pending_followups ?? []}
              pagination={{ pageSize: 8 }}
              empty="暂无待跟进 —— 从企业筛选页把高分客户转化为线索吧"
              columns={[
                {
                  title: "企业",
                  dataIndex: "enterprise_name",
                  render: (v) => <span className="font-medium text-slate-700">{v}</span>,
                },
                {
                  title: "下一步",
                  dataIndex: "next_action",
                  render: (v) =>
                    v ? <Tag color="blue">{v}</Tag> : <span className="text-slate-300">待规划</span>,
                },
                {
                  title: "约定时间",
                  dataIndex: "next_time",
                  width: 170,
                  render: (v) => (v ? <Tag color="orange">{String(v).slice(0, 16).replace("T", " ")}</Tag> : "-"),
                },
              ]}
            />
          </Card>
        </motion.div>
      </motion.div>
    </AppShell>
  );
}
