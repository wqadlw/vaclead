"use client";

/**
 * 信息采集中心（SPEC-COLLECT，2026-11-15 用户在情报晨报页问"专门出一个信息
 * 采集的功能"）：情报中心组的采集门户——源状态/快捷采集/采集历史三段。
 * 采集入口此前藏在"工具→拓客任务"，情报视角没有"去采更多"的动线；任务只有
 * 单查无历史列表（采过什么/采到多少/成功失败无处查）。
 * 数据：GET /api/sources（十二源状态，SPEC-MULTI）/ GET /api/tasks/recent
 * （SPEC-COLLECT C1 人话摘要）。主动作跳拓客任务页既有表单（不重复造）。
 */
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Tag, Toast } from "@douyinfe/semi-ui";
import { RefreshCw, Satellite } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";

interface SourceRow {
  key: string;
  label: string;
  category: string;
  enterprise_count: number;
  last_harvest: string | null;
  enabled: boolean;
}

interface RecentTask {
  task_id: string;
  kind: string;
  status: string;
  progress: number;
  updated_at: string;
  summary: string;
}

const CATEGORY_TAG: Record<string, "blue" | "cyan" | "green" | "orange" | "grey"> = {
  tender: "blue", proposal: "cyan", poi: "green", enrich: "orange", import: "grey",
};

const KIND_CN: Record<string, string> = {
  poi: "POI 采集", ggzy: "公共资源·中标", ccgp: "政采网公告", sdgp: "山东政采",
  tender: "招标导入", enrich: "工商补数", dup_scan: "全库查重",
  outreach: "序列触达", brand_extract: "品牌抽取", backfill: "回填",
};

const STATUS_TAG: Record<string, { color: "green" | "blue" | "red" | "grey"; label: string }> = {
  done: { color: "green", label: "完成" },
  running: { color: "blue", label: "进行中" },
  error: { color: "red", label: "失败" },
  failed: { color: "red", label: "失败" },
  cancelled: { color: "grey", label: "已取消" },
  queued: { color: "grey", label: "排队" },
};

function listOf<T>(d: unknown): T[] {
  if (Array.isArray(d)) return d as T[];
  if (d && typeof d === "object" && Array.isArray((d as { items?: unknown }).items)) {
    return (d as { items: T[] }).items;
  }
  return [];
}

export default function CollectPage() {
  const router = useRouter();
  const [sources, setSources] = useState<SourceRow[] | null>(null);
  const [recent, setRecent] = useState<RecentTask[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [brandBusy, setBrandBusy] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    api
      .get<unknown>("/api/sources")
      .then((d) => setSources(listOf<SourceRow>(d)))
      .catch(() => setSources(null));
    api
      .get<{ tasks?: RecentTask[] }>("/api/tasks/recent")
      .then((d) => setRecent(Array.isArray(d?.tasks) ? d.tasks : []))
      .catch(() => setRecent(null))
      .finally(() => setLoading(false));
  }, []);
  useEffect(() => load(), [load]);

  /** 品牌抽取：POST 即跑（admin 语义，后端 403 由 api 层转人话） */
  const runBrandExtract = async () => {
    setBrandBusy(true);
    try {
      const r = await api.post<{ task_id: string }>("/api/tasks/brand-extract", {});
      Toast.success(`品牌抽取已启动（${r.task_id}）——完成后在下方历史查看结果`);
      load();
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "启动失败");
    } finally {
      setBrandBusy(false);
    }
  };

  const enabledSources = (sources ?? []).filter((s) => s.enabled);
  const totalEnt = enabledSources.reduce((a, s) => a + (s.enterprise_count || 0), 0);

  return (
    <AppShell>
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-[20px] font-semibold text-[var(--ink)]">信息采集</h1>
          <p className="mt-0.5 text-[12.5px] text-[var(--muted)]">
            十二个信息源的采集门户——状态一眼见，主动作直达拓客任务，历史随时翻
          </p>
        </div>
        <Button size="small" icon={<RefreshCw size={13} />} loading={loading} onClick={load}>
          刷新
        </Button>
      </div>

      {/* ===== 总览 ===== */}
      <div className="mt-4 grid grid-cols-2 gap-px overflow-hidden rounded-lg bg-[var(--border)] md:grid-cols-4">
        <div className="bg-white px-4 py-3">
          <div className="text-[12px] text-[var(--muted)]">启用中的信息源</div>
          <div className="mt-0.5 text-[26px] font-semibold leading-8 tabular-nums text-[var(--accent)]">
            {sources ? `${enabledSources.length}/${sources.length}` : "—"}
          </div>
        </div>
        <div className="bg-white px-4 py-3">
          <div className="text-[12px] text-[var(--muted)]">累计企业（启用源合计）</div>
          <div className="mt-0.5 text-[26px] font-semibold leading-8 tabular-nums text-[var(--ink)]">
            {sources ? totalEnt.toLocaleString("zh-CN") : "—"}
          </div>
        </div>
        <div className="bg-white px-4 py-3">
          <div className="text-[12px] text-[var(--muted)]">进行中任务</div>
          <div className="mt-0.5 text-[26px] font-semibold leading-8 tabular-nums text-[var(--ink)]">
            {recent ? recent.filter((t) => t.status === "running").length : "—"}
          </div>
        </div>
        <div className="bg-white px-4 py-3">
          <div className="text-[12px] text-[var(--muted)]">定时巡采</div>
          <div className="mt-1 text-[12px] leading-5 text-[var(--muted)]">
            白天每 2 小时标讯 / 每日 03:30 增量 / 四省+山东 05 点档——自动化运行中
          </div>
        </div>
      </div>

      {/* ===== 源状态 ===== */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-[15px] font-medium text-[var(--ink)]">信息源状态</h2>
          <span className="text-[11.5px] text-[var(--muted)]">启停管理在设置页 → 数据源</span>
        </div>
        {sources === null ? (
          <div className="py-8 text-center text-[13px] text-[var(--muted)]">
            暂不可用 —— 源状态服务未就绪，稍后点「刷新」重试
          </div>
        ) : (
          <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-2 xl:grid-cols-3">
            {sources.map((s) => (
              <div
                key={s.key}
                className={`rounded-lg border px-3 py-2.5 ${
                  s.enabled ? "border-[var(--border)] bg-white" : "border-[var(--rule)] bg-[var(--raised)] opacity-60"
                }`}
              >
                <div className="flex items-center gap-1.5">
                  <Satellite size={13} className={s.enabled ? "text-[var(--accent)]" : "text-[var(--muted)]"} />
                  <span className="truncate text-[13.5px] font-medium text-[var(--ink)]">{s.label}</span>
                  <Tag color={CATEGORY_TAG[s.category] ?? "grey"} type="light" size="small" className="ml-auto shrink-0">
                    {s.category}
                  </Tag>
                </div>
                <div className="mt-1 flex items-center justify-between text-[11.5px] tabular-nums text-[var(--muted)]">
                  <span>
                    {s.enterprise_count.toLocaleString("zh-CN")} 家
                    {s.last_harvest ? ` · 最近入库 ${s.last_harvest}` : ""}
                  </span>
                  {s.enabled ? (
                    <Tag color="green" type="light" size="small">启用</Tag>
                  ) : (
                    <Tag color="grey" type="light" size="small">停用</Tag>
                  )}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* ===== 快捷采集 ===== */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4">
        <h2 className="text-[15px] font-medium text-[var(--ink)]">快捷采集</h2>
        <p className="mt-0.5 text-[12px] text-[var(--muted)]">
          主动作到拓客任务页发起（带实时进度/取消/结果预览）；品牌抽取一键直跑
        </p>
        <div className="mt-3 grid grid-cols-1 gap-2 md:grid-cols-3">
          {[
            { t: "全国公共资源 · 中标数据", d: "中标方与采购人双向建档，成交价自动生成证据句", to: "/tasks" },
            { t: "中国政府采购网 · 公告", d: "公告自带采购人，供应商同步抽取；限速礼貌", to: "/tasks" },
            { t: "山东政采分网 · 本省直采", d: "采购公告+结果公告双栏目，采购人山东确定性建档", to: "/tasks" },
          ].map((x) => (
            <button
              key={x.t}
              onClick={() => router.push(x.to)}
              className="rounded-lg border border-[var(--border)] bg-white px-4 py-3 text-left transition-colors hover:border-[var(--accent)]"
            >
              <div className="text-[13.5px] font-medium text-[var(--ink)]">{x.t}</div>
              <div className="mt-1 text-[12px] leading-4 text-[var(--muted)]">{x.d}</div>
            </button>
          ))}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-2 border-t border-[var(--rule)] pt-2.5 text-[12.5px]">
          <span className="text-[var(--muted)]">次级动作：</span>
          <Button size="small" theme="outline" onClick={() => router.push("/tasks")}>
            POI 采集 / 工商补数 / 粘贴导入
          </Button>
          <Button size="small" theme="outline" loading={brandBusy} onClick={() => void runBrandExtract()}>
            品牌抽取（全库扫描）
          </Button>
          <Button size="small" theme="borderless" onClick={() => router.push("/quality")}>
            采集水位监控 →
          </Button>
        </div>
      </div>

      {/* ===== 采集历史 ===== */}
      <div className="mt-4 rounded-lg border border-[var(--border)] bg-white">
        <div className="flex items-center justify-between border-b border-[var(--rule)] px-4 py-2.5">
          <h2 className="text-[14px] font-medium text-[var(--ink)]">采集历史</h2>
          <span className="text-[11.5px] text-[var(--muted)]">最近 20 次 · 实时进度在拓客任务页</span>
        </div>
        {recent === null ? (
          <div className="py-8 text-center text-[13px] text-[var(--muted)]">
            暂不可用 —— 历史服务未就绪，稍后点「刷新」重试
          </div>
        ) : recent.length === 0 ? (
          <div className="py-10 text-center text-[13px] text-[var(--muted)]">
            还没有采集记录——从上面「快捷采集」发起第一次
          </div>
        ) : (
          <div className="divide-y divide-[var(--rule)]">
            {recent.map((t) => {
              const st = STATUS_TAG[t.status] ?? { color: "grey" as const, label: t.status };
              return (
                <div key={t.task_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5">
                  <div className="min-w-0 basis-44">
                    <div className="truncate text-[13px] font-medium text-[var(--ink)]">
                      {KIND_CN[t.kind] ?? t.kind}
                    </div>
                    <div className="text-[11px] tabular-nums text-[var(--muted)]">
                      {(t.updated_at || "").replace("T", " ")}
                    </div>
                  </div>
                  <Tag color={st.color} type="light" size="small">
                    {st.label}
                    {t.status === "running" ? ` ${t.progress}%` : ""}
                  </Tag>
                  <div className="min-w-0 flex-1 truncate text-[12.5px] text-[var(--ink)]" title={t.summary}>
                    {t.summary}
                  </div>
                  <button
                    className="shrink-0 text-[12px] text-[var(--accent)] hover:underline"
                    onClick={() => {
                      if (t.status === "running") router.push("/tasks");
                      else Toast.info(`任务 ${t.task_id} · ${t.summary}`);
                    }}
                  >
                    {t.status === "running" ? "看进度" : "详情"}
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </AppShell>
  );
}
