"use client";

import { useRef, useState } from "react";
import { Button, Card, Progress, Select, Toast } from "@douyinfe/semi-ui";
import { CloudUpload, FileSpreadsheet, Rocket, ScrollText } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { INDUSTRY_OPTIONS, POI_CITIES } from "@/lib/constants";

export default function TasksPage() {
  const [city, setCity] = useState<string>("苏州");
  const [industries, setIndustries] = useState<string[]>(["semiconductor"]);
  const [starting, setStarting] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const esRef = useRef<EventSource | null>(null);

  const subscribeTask = (taskId: string) => {
    esRef.current?.close();
    const base = "http://127.0.0.1:8000";
    const es = new EventSource(`${base}/api/tasks/${taskId}/events`);
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data);
        if (payload.type === "progress") {
          setProgress(payload.progress ?? 0);
          if (payload.log) setLogs((prev) => [...prev.slice(-200), String(payload.log)]);
        } else if (payload.type === "done") {
          setProgress(100);
          setLogs((prev) => [...prev, "✅ 任务完成"]);
          es.close();
          setStarting(false);
        } else if (payload.type === "error") {
          setLogs((prev) => [...prev, `❌ ${payload.message}`]);
          es.close();
          setStarting(false);
        }
      } catch {
        /* 非 JSON 帧忽略 */
      }
    };
    es.onerror = () => {
      es.close();
      setStarting(false);
    };
  };

  const startPoi = async () => {
    if (industries.length === 0) {
      Toast.warning("请至少选择一个下游行业");
      return;
    }
    setStarting(true);
    setProgress(0);
    setLogs(["任务提交中…"]);
    try {
      const res = await api.post<{ task_id: string }>("/api/tasks/poi", { city, industry_keys: industries });
      Toast.success("任务已启动");
      subscribeTask(res.task_id);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "启动失败");
      setStarting(false);
    }
  };

  const uploadCsv = async (file: File, kind: "enterprises" | "tenders") => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api.post<{ inserted?: number; merged?: number; signals_created?: number }>(
        `/api/import/${kind}`,
        fd,
      );
      Toast.success(
        kind === "enterprises"
          ? `导入完成：新增 ${res.inserted ?? 0} 家，合并 ${res.merged ?? 0} 家`
          : `解析完成：生成信号 ${res.signals_created ?? 0} 条`,
      );
      setLogs((prev) => [...prev, `📄 ${file.name} 导入成功`]);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "导入失败");
    }
  };

  const Dropzone = ({ kind, title, hint }: { kind: "enterprises" | "tenders"; title: string; hint: string }) => (
    <label
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const f = e.dataTransfer.files?.[0];
        if (f) void uploadCsv(f, kind);
      }}
      className="block border-[1.5px] border-dashed border-slate-200 hover:border-indigo-300 hover:bg-indigo-50/40 rounded-xl px-4 py-5 text-center cursor-pointer transition-colors"
    >
      <input
        type="file"
        accept=".csv"
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void uploadCsv(f, kind);
          e.currentTarget.value = "";
        }}
      />
      <FileSpreadsheet className="mx-auto text-[var(--accent)]" size={22} />
      <div className="text-[13.5px] font-medium text-slate-700 mt-2">{title}</div>
      <div className="text-[11.5px] text-slate-400 mt-1 leading-5">{hint}</div>
    </label>
  );

  return (
    <AppShell>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* 左列 */}
        <div className="space-y-4">
          <Card
            title={
              <span className="flex items-center gap-2 text-[14.5px] font-medium text-[var(--ink)]">
                <Rocket size={16} className="text-[var(--accent)]" /> 高德 POI 地图拓客
              </span>
            }
          >
            <div className="flex gap-3 items-center">
              <Select
                style={{ width: 140 }}
                optionList={POI_CITIES.map((c) => ({ value: c, label: c }))}
                value={city}
                onChange={(v) => setCity(v as string)}
              />
              <Select
                style={{ flex: 1 }}
                placeholder="选择下游行业"
                multiple
                optionList={INDUSTRY_OPTIONS.map((i) => ({ value: i.key, label: i.name }))}
                value={industries}
                onChange={(v) => setIndustries(v as string[])}
              />
              <Button theme="solid" loading={starting} onClick={startPoi}>
                开始采集
              </Button>
            </div>
            <p className="text-slate-400 text-[12px] leading-5 mt-3.5">
              按行业关键词扫描所选城市的工厂 POI，自动入库、归一去重、ICP 打分。需在「设置」配置高德 Key。
            </p>
          </Card>

          <Card
            title={
              <span className="flex items-center gap-2 text-[14.5px] font-medium text-[var(--ink)]">
                <CloudUpload size={16} className="text-[var(--accent)]" /> CSV 数据导入
              </span>
            }
          >
            <div className="grid grid-cols-1 gap-3">
              <Dropzone
                kind="enterprises"
                title="导入企业名单 CSV"
                hint="列：name, province, city, district, address, business_scope, registered_capital_wan, industry_key"
              />
              <Dropzone
                kind="tenders"
                title="导入招标公告 CSV"
                hint="列：title, content, url, pub_date, enterprise_name —— 命中设备词自动生成意图信号"
              />
            </div>
          </Card>
        </div>

        {/* 右列：进度 */}
        <Card
          title={
            <span className="flex items-center gap-2 text-[14.5px] font-semibold text-slate-700">
              <ScrollText size={16} className="text-[var(--accent)]" /> 实时进度
            </span>
          }
            className="self-start"
          >
          {progress === null ? (
            <div className="text-[13px] text-slate-400 py-10 text-center">
              尚无运行中的任务 —— 启动 POI 采集或导入 CSV 后这里会实时滚动日志
            </div>
          ) : (
            <>
              <Progress percent={progress} showInput aria-label="任务进度" stroke="#4f46e5" />
              <pre className="bg-[#0e1230] text-emerald-300 text-[11.5px] leading-5 p-3.5 rounded-xl mt-3.5 max-h-[420px] overflow-auto whitespace-pre-wrap font-mono">
                {logs.join("\n") || "等待日志…"}
              </pre>
            </>
          )}
        </Card>
      </div>
    </AppShell>
  );
}
