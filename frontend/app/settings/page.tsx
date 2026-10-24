"use client";

import { useCallback, useEffect, useState } from "react";
import { Button, Card, Descriptions, Form, Input, Table, Tabs, Tag, Toast } from "@douyinfe/semi-ui";
import { Bot, KeyRound } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import type { PageData } from "@/lib/api";
import { INDUSTRY_OPTIONS } from "@/lib/constants";

interface SettingsData {
  amap_key_masked: string | null;
  zhiliaoke_key_masked: string | null;
  agent_backends: Record<string, string>;
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

const DEVICE_TERMS_PREVIEW = [
  "真空泵", "真空机组", "罗茨泵", "分子泵", "旋片泵", "螺杆真空泵", "爪式真空泵",
  "涡旋真空泵", "扩散泵", "真空镀膜机", "磁控溅射", "蒸发镀膜", "PVD设备", "CVD设备",
  "冻干机", "真空冷冻干燥机", "真空干燥箱", "真空烧结炉", "真空热处理炉", "真空腔体",
  "真空检漏", "真空包装机", "真空获得设备", "真空阀门",
];

export default function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData | null>(null);
  const [runs, setRuns] = useState<PageData<AgentRunRow> | null>(null);
  const [runPage, setRunPage] = useState(1);
  const [saving, setSaving] = useState(false);
  const [amapKey, setAmapKey] = useState("");
  const [zhilKey, setZhilKey] = useState("");

  const loadSettings = useCallback(() => {
    api.get<SettingsData>("/api/settings").then(setSettings).catch(() => undefined);
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

  return (
    <AppShell>
      <div>
        <Tabs
          type="line"
          className="rounded-xl border border-[var(--border)] bg-white !px-2"
          tabList={[
            { tab: "数据源 Key", itemKey: "keys" },
            { tab: "词表预览", itemKey: "vocab" },
            { tab: "Agent 日志", itemKey: "agents" },
            { tab: "后端切换", itemKey: "backends" },
          ]}
        >
          {/* Keys */}
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

          {/* 词表 */}
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

          {/* Agent 日志 */}
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

          {/* 后端切换 */}
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
        </Tabs>
      </div>
    </AppShell>
  );
}
