"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Button, Card, InputNumber, Progress, Select, Toast, Modal } from "@douyinfe/semi-ui";
import { Building2, ClipboardPaste, CloudUpload, FileSpreadsheet, Globe2, Rocket, ScrollText } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { INDUSTRY_OPTIONS, POI_CITIES } from "@/lib/constants";

type Channel = "ggzy" | "ccgp";

const DONE_LABELS: Record<string, string> = {
  entities: "主体",
  new_entities: "新主体",
  bids: "中标记录",
  signals: "新信号",
  buyers: "采购人",
  announcements: "公告",
  suppliers: "供应商",
};

/** SPEC-MULTI S3：任务结果契约（GET /api/tasks/{id}/results，旧任务缺键返回空数组） */
interface TaskResultRow {
  id: number;
  name: string;
  province?: string | null;
  industry_name?: string | null;
}
interface TaskFailureRow {
  enterprise_id?: number | null;
  name: string;
  stage?: string | null;
  error?: string | null;
}
interface TaskResultsResp {
  task_id?: string;
  kind?: string;
  status?: string;
  new_enterprises?: TaskResultRow[];
  failures?: TaskFailureRow[];
  totals?: Record<string, unknown>;
}

/** SPEC-IMP：上传导入向导的目标字段选项（比粘贴路径多 established_at / legal_rep） */
const UPLOAD_FIELDS: { value: string; label: string }[] = [
  { value: "name", label: "企业名称（必填）" },
  { value: "province", label: "省" },
  { value: "city", label: "城市" },
  { value: "district", label: "区县" },
  { value: "address", label: "地址" },
  { value: "business_scope", label: "经营范围" },
  { value: "established_at", label: "成立日期" },
  { value: "legal_rep", label: "法定代表人" },
  { value: "registered_capital_wan", label: "注册资本(万)" },
  { value: "industry_key", label: "行业键" },
  { value: "skip", label: "忽略此列" },
];

/** SPEC-GSH：工商补数向导的目标字段选项（固定工商字段集——匹配键 + 五要素 + skip） */
const GONGSHANG_FIELDS: { value: string; label: string }[] = [
  { value: "name", label: "企业名称（匹配键）" },
  { value: "uscc", label: "信用代码（匹配键/回填）" },
  { value: "legal_rep", label: "法人" },
  { value: "established_at", label: "成立日期" },
  { value: "registered_capital_wan", label: "注册资本(万)" },
  { value: "business_scope", label: "经营范围" },
  { value: "skip", label: "忽略此列" },
];

/** SPEC-GSH：filled 按字段计数的中文名（预演/结果摘要展示用） */
const GONGSHANG_FIELD_LABELS: Record<string, string> = {
  legal_rep: "法人",
  established_at: "成立日期",
  registered_capital_wan: "注册资本",
  business_scope: "经营范围",
  uscc: "信用代码",
};

interface ImportPreviewResp {
  headers_raw?: string[];
  headers_synonymized?: string[];
  mapping_suggestion?: { col: number; field: string }[];
  preview_rows?: Record<string, string>[];
  total_rows?: number;
  dup_preview?: { existing: number; new: number };
}

interface ImportSkippedDetail {
  row: number;
  reason: string;
}

interface ImportResultResp {
  inserted?: number;
  merged?: number;
  skipped?: number;
  skipped_details?: ImportSkippedDetail[];
}

/** SPEC-GSH：工商补数回填响应（dry_run 预演与正式回填共用同一结构） */
interface GongshangBackfillResp {
  dry_run?: boolean;
  matched?: number;
  filled?: Record<string, number>;
  kept_existing?: number;
  skipped?: number;
  skipped_details?: ImportSkippedDetail[];
  headers_raw?: string[];
  mapping_suggestion?: { col: number; field: string }[];
  preview_rows?: Record<string, string>[];
  total_rows?: number;
}

function formatDone(summary: Record<string, unknown> | undefined): string {
  if (!summary || typeof summary !== "object") return "任务完成";
  const parts = Object.entries(summary)
    .filter(([, v]) => typeof v === "number" && v > 0)
    .map(([k, v]) => `${DONE_LABELS[k] ?? k} ${v}`);
  return parts.length ? `任务完成：${parts.join("，")}` : "任务完成（无新增数据）";
}

export default function TasksPage() {
  // POI 采集
  const [city, setCity] = useState<string>("苏州");
  const [industries, setIndustries] = useState<string[]>(["semiconductor"]);
  const [useQuadtree, setUseQuadtree] = useState(false);
  // 官方数据采集
  const [channel, setChannel] = useState<Channel>("ggzy");
  const [terms, setTerms] = useState<string[]>(["真空"]);
  const [ggzyOptions, setGgzyOptions] = useState<{ value: string; label: string }[]>([]);
  const [ccgpOptions, setCcgpOptions] = useState<{ value: string; label: string }[]>([]);
  const [scanDays, setScanDays] = useState<number>(365);
  const [maxPages, setMaxPages] = useState<number>(2);
  // 粘贴导入两步向导（twenty MatchColumnsStep 模式）：粘贴→映射确认→导入
  const [pasteOpen, setPasteOpen] = useState(false);
  const [pasteText, setPasteText] = useState("");
  const [pasteBusy, setPasteBusy] = useState(false);
  const [pasteStep, setPasteStep] = useState<1 | 2>(1);
  const [pasteHeaders, setPasteHeaders] = useState<string[]>([]);
  const [pasteMapping, setPasteMapping] = useState<{ col: number; field: string }[]>([]);
  // 上传导入两步向导（SPEC-IMP）：选文件→preview→映射确认→导入
  const [upOpen, setUpOpen] = useState(false);
  const [upFile, setUpFile] = useState<File | null>(null);
  const [upBusy, setUpBusy] = useState(false);
  const [upConfirming, setUpConfirming] = useState(false);
  const [upHeaders, setUpHeaders] = useState<string[]>([]);
  const [upMapping, setUpMapping] = useState<{ col: number; field: string }[]>([]);
  const [upPreviewRows, setUpPreviewRows] = useState<Record<string, string>[]>([]);
  const [upTotalRows, setUpTotalRows] = useState(0);
  const [upDup, setUpDup] = useState<{ existing: number; new: number } | null>(null);
  const [upResult, setUpResult] = useState<{
    inserted: number; merged: number; skipped: number; skipped_details: ImportSkippedDetail[];
  } | null>(null);
  // 工商补数两步向导（SPEC-GSH）：拖表→dry_run 预演→映射确认→正式回填（只填空不覆盖）
  const [gsOpen, setGsOpen] = useState(false);
  const [gsFile, setGsFile] = useState<File | null>(null);
  const [gsBusy, setGsBusy] = useState(false);
  const [gsConfirming, setGsConfirming] = useState(false);
  const [gsHeaders, setGsHeaders] = useState<string[]>([]);
  const [gsMapping, setGsMapping] = useState<{ col: number; field: string }[]>([]);
  const [gsPreviewRows, setGsPreviewRows] = useState<Record<string, string>[]>([]);
  const [gsTotalRows, setGsTotalRows] = useState(0);
  const [gsPreview, setGsPreview] = useState<{
    matched: number; filled: Record<string, number>; kept_existing: number; skipped: number;
  } | null>(null);
  const [gsResult, setGsResult] = useState<{
    matched: number; filled: Record<string, number>; kept_existing: number;
    skipped: number; skipped_details: ImportSkippedDetail[];
  } | null>(null);
  // 运行态
  const [starting, setStarting] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const [taskId, setTaskId] = useState<string | null>(null);
  // SPEC-MULTI S3：完成后拉取的结果预览（新采企业 + 失败明细）；null = 无数据/请求失败（不渲染）
  const [taskResults, setTaskResults] = useState<{
    total: number;
    rows: TaskResultRow[];
    failures: TaskFailureRow[];
  } | null>(null);
  const esRef = useRef<EventSource | null>(null);
  const router = useRouter();

  useEffect(() => {
    api
      .get<{ strong_terms: string[]; ggzy_terms: string[] }>("/api/meta/vertical")
      .then((m) => {
        setGgzyOptions(m.ggzy_terms.map((t) => ({ value: t, label: t })));
        setCcgpOptions(m.strong_terms.map((t) => ({ value: t, label: t })));
      })
      .catch(() => setGgzyOptions([{ value: "真空", label: "真空" }]));
  }, []);

  // SPEC-CUX5：刷新/离开页面后找回运行中任务（读 sessionStorage + 状态端点，
  // running 则重新订阅 SSE 恢复进度显示——修复"发起后刷新即失联"断点）
  useEffect(() => {
    const saved = sessionStorage.getItem("runningTaskId");
    if (!saved) return;
    api
      .get<{ status: string }>(`/api/tasks/${saved}`)
      .then((d) => {
        if (d.status === "running") {
          subscribeTask(saved);
          setLogs((prev) => [...prev, "已恢复对运行中任务的跟踪"]);
        } else {
          sessionStorage.removeItem("runningTaskId");
        }
      })
      .catch(() => sessionStorage.removeItem("runningTaskId"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const termOptions = channel === "ggzy" ? ggzyOptions : ccgpOptions;

  const subscribeTask = (tid: string) => {
    esRef.current?.close();
    const base = window.location.port === "3000" ? "http://127.0.0.1:8000" : "";
    const es = new EventSource(`${base}/api/tasks/${tid}/events`);
    esRef.current = es;
    setTaskId(tid);
    // SPEC-CUX5：运行中任务持久化——刷新/离开页面后可找回（配合状态端点）
    sessionStorage.setItem("runningTaskId", tid);
    es.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data);
        if (payload.type === "snapshot") {
          if (typeof payload.progress === "number") setProgress(payload.progress);
          if (payload.log) setLogs((prev) => [...prev, String(payload.log)]);
        } else if (payload.type === "progress") {
          if (typeof payload.progress === "number") setProgress(payload.progress);
          if (payload.log) setLogs((prev) => [...prev.slice(-200), String(payload.log)]);
        } else if (payload.type === "done") {
          setProgress(100);
          const summary =
            payload.cancelled || payload.summary === undefined
              ? payload.inserted !== undefined
                ? `任务完成：新增 ${payload.inserted} 家，合并 ${payload.merged ?? 0} 家`
                : "任务完成"
              : formatDone(payload.summary as Record<string, unknown>);
          setLogs((prev) => [...prev, summary]);
          // SPEC-MULTI S3：先存刚完成的 task_id 再清运行态，用于拉取结果预览
          const finishedTaskId = tid;
          sessionStorage.removeItem("runningTaskId");
          es.close();
          setStarting(false);
          setTaskId(null);
          if (!payload.cancelled && finishedTaskId) {
            api
              .get<TaskResultsResp>(`/api/tasks/${finishedTaskId}/results`)
              .then((d) => {
                const rows = Array.isArray(d?.new_enterprises) ? d.new_enterprises : [];
                const fails = Array.isArray(d?.failures) ? d.failures : [];
                if (rows.length === 0 && fails.length === 0) {
                  setTaskResults(null);
                  return;
                }
                // 后端 cap 50：totals.new_entities 有真总数且 ≥ 列表长度时优先用之
                const totalsNew = d?.totals?.new_entities;
                const total =
                  typeof totalsNew === "number" && totalsNew >= rows.length ? totalsNew : rows.length;
                setTaskResults({ total, rows, failures: fails });
              })
              .catch(() => setTaskResults(null)); // 404/网络失败静默——结果卡不渲染
          }
        } else if (payload.type === "cancelled") {
          setProgress(100);
          setLogs((prev) => [...prev, `⏹ 任务已取消：${formatDone(payload.summary as Record<string, unknown>)}`]);
          sessionStorage.removeItem("runningTaskId");
          es.close();
          setStarting(false);
          setTaskId(null);
        } else if (payload.type === "error") {
          setLogs((prev) => [...prev, `失败：${payload.message ?? payload.log ?? "任务失败"}`]);
          sessionStorage.removeItem("runningTaskId");
          es.close();
          setStarting(false);
          setTaskId(null);
        }
        /* heartbeat / log 类型无需特殊处理 */
      } catch {
        /* 非 JSON 帧忽略 */
      }
    };
    es.onerror = () => {
      es.close();
      setStarting(false);
    };
  };

  const cancelTask = async () => {
    if (!taskId) return;
    try {
      await api.post(`/api/tasks/${taskId}/cancel`);
      Toast.info("取消请求已发送");
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "取消失败");
    }
  };

  const startPoi = async () => {
    if (industries.length === 0) {
      Toast.warning("请至少选择一个下游行业");
      return;
    }
    setStarting(true);
    setProgress(0);
    setLogs(["任务提交中…"]);
    setTaskResults(null); // SPEC-MULTI S3：新任务开始，清掉上一轮结果预览
    try {
      const res = await api.post<{ task_id: string }>("/api/tasks/poi", {
        city,
        industry_keys: industries,
        districts: useQuadtree ? ["全部区县"] : undefined,
      });
      Toast.success("任务已启动");
      subscribeTask(res.task_id);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "启动失败");
      setStarting(false);
    }
  };

  const startOfficial = async () => {
    if (terms.length === 0) {
      Toast.warning("请至少选择一个设备关键词");
      return;
    }
    setStarting(true);
    setProgress(0);
    setLogs([`${channel === "ggzy" ? "官方中标数据" : "政采网公告"}采集任务提交中…`]);
    setTaskResults(null); // SPEC-MULTI S3：新任务开始，清掉上一轮结果预览
    try {
      const res =
        channel === "ggzy"
          ? await api.post<{ task_id: string; keywords: string[] }>("/api/tasks/ggzy-harvest", {
              keywords: terms,
              max_pages: maxPages,
              pull_bid_details: true,
            })
          : await api.post<{ task_id: string; keywords: string[] }>("/api/tasks/ccgp-scan", {
              keywords: terms,
              days: scanDays,
              max_pages: maxPages,
            });
      Toast.success(`任务已启动（${res.keywords?.length ?? terms.length} 个关键词）`);
      subscribeTask(res.task_id);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "启动失败");
      setStarting(false);
    }
  };

  const switchChannel = (c: Channel) => {
    setChannel(c);
    setTerms(c === "ggzy" ? ["真空"] : ["真空设备"]);
  };

  const TARGET_FIELDS = [
    { value: "name", label: "企业名称（必填）" },
    { value: "province", label: "省" },
    { value: "city", label: "城市" },
    { value: "district", label: "区县" },
    { value: "address", label: "地址" },
    { value: "business_scope", label: "经营范围" },
    { value: "registered_capital_wan", label: "注册资本(万)" },
    { value: "industry_key", label: "行业键" },
    { value: "skip", label: "忽略此列" },
  ];
  const HEADER_SYNONYMS: Record<string, string> = {
    name: "name", 企业名称: "name", 企业: "name", 名称: "name", 公司: "name", 公司名称: "name", 客户名称: "name",
    province: "province", 省: "province", 省份: "province",
    city: "city", 城市: "city", 市: "city",
    district: "district", 区县: "district", 区: "district",
    address: "address", 地址: "address",
    business_scope: "business_scope", 经营范围: "business_scope",
    registered_capital_wan: "registered_capital_wan", 注册资本: "registered_capital_wan",
    "注册资本(万元)": "registered_capital_wan", "注册资本(万)": "registered_capital_wan",
    industry_key: "industry_key", 行业: "industry_key",
  };

  /** 第二步：本地解析表头并自动猜测映射（twenty MatchColumns 自动匹配逻辑） */
  const gotoMatchStep = () => {
    const lines = pasteText.split("\n").filter((l) => l.trim());
    if (lines.length < 2) {
      Toast.warning("至少需要表头 + 1 行数据");
      return;
    }
    const delim = (lines[0].match(/\t/g)?.length ?? 0) >= (lines[0].match(/,/g)?.length ?? 0) ? "\t" : ",";
    const headers = lines[0].split(delim).map((h) => h.trim());
    setPasteHeaders(headers);
    setPasteMapping(
      headers.map((h, i) => ({
        col: i,
        field: HEADER_SYNONYMS[h.toLowerCase()] ?? HEADER_SYNONYMS[h] ?? (i === 0 ? "name" : "skip"),
      })),
    );
    setPasteStep(2);
  };

  const submitPaste = async () => {
    if (!pasteText.trim()) {
      Toast.warning("请先粘贴表格内容");
      return;
    }
    setPasteBusy(true);
    try {
      const res = await api.post<{
        inserted: number; merged: number; skipped: number; detected_delimiter: string;
      }>(
        pasteStep === 2
          ? "/api/import/paste"
          : "/api/import/paste",
        pasteStep === 2
          ? { text: pasteText, mode: "confirm", headers: pasteHeaders, mapping: pasteMapping.map((m) => m.field) }
          : { text: pasteText },
      );
      Toast.success(
        `粘贴导入完成：新增 ${res.inserted}，合并 ${res.merged}，跳过 ${res.skipped}`,
      );
      setPasteOpen(false);
      setPasteText("");
      setPasteStep(1);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "导入失败");
    } finally {
      setPasteBusy(false);
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
      setLogs((prev) => [...prev, `导入成功：${file.name}`]);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "导入失败");
    }
  };

  /** SPEC-IMP 第一步：企业 CSV 先走 preview（去重预演 + 映射建议），再弹向导确认 */
  const startEnterpriseUpload = async (file: File) => {
    setUpFile(file);
    setUpOpen(true);
    setUpResult(null);
    setUpBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api.post<ImportPreviewResp>("/api/import/enterprises/preview", fd);
      const headers = res.headers_raw ?? [];
      setUpHeaders(headers);
      setUpMapping(
        headers.map((_, i) => ({
          col: i,
          field:
            res.mapping_suggestion?.find((m) => m.col === i)?.field ??
            (i === 0 ? "name" : "skip"),
        })),
      );
      setUpPreviewRows(res.preview_rows ?? []);
      setUpTotalRows(res.total_rows ?? 0);
      setUpDup(res.dup_preview ?? null);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "解析文件失败");
      closeUpWizard();
    } finally {
      setUpBusy(false);
    }
  };

  /** SPEC-IMP 第二步：确认导入（file + 显式 mapping JSON，下标=列序） */
  const confirmEnterpriseImport = async () => {
    if (!upFile) return;
    setUpConfirming(true);
    try {
      const fd = new FormData();
      fd.append("file", upFile);
      const ordered = [...upMapping].sort((a, b) => a.col - b.col).map((m) => m.field);
      fd.append("mapping", JSON.stringify(ordered));
      const res = await api.post<ImportResultResp>("/api/import/enterprises", fd);
      const inserted = res.inserted ?? 0;
      const merged = res.merged ?? 0;
      const skipped = res.skipped ?? 0;
      const details = res.skipped_details ?? [];
      Toast.success(`导入完成：新增 ${inserted} 家，合并 ${merged} 家，跳过 ${skipped} 家`);
      setLogs((prev) => [...prev, `导入成功：${upFile.name}（新增 ${inserted}，合并 ${merged}，跳过 ${skipped}）`]);
      if (details.length > 0) {
        // 有跳过明细：弹窗停留展示（行号+原因），点「完成」再关
        setUpResult({ inserted, merged, skipped, skipped_details: details });
      } else {
        finishEnterpriseImport();
      }
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "导入失败");
    } finally {
      setUpConfirming(false);
    }
  };

  const closeUpWizard = () => {
    setUpOpen(false);
    setUpFile(null);
    setUpHeaders([]);
    setUpMapping([]);
    setUpPreviewRows([]);
    setUpTotalRows(0);
    setUpDup(null);
    setUpResult(null);
  };

  const finishEnterpriseImport = () => {
    closeUpWizard();
    router.refresh();
  };

  // ---- SPEC-GSH：工商补数（只填空值）向导 ----

  /** 第一步：拖表即 dry_run 预演（后端零写入，只返回 matched/filled/skipped 计数） */
  const startGongshangUpload = async (file: File) => {
    setGsFile(file);
    setGsOpen(true);
    setGsResult(null);
    setGsPreview(null);
    setGsBusy(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const res = await api.post<GongshangBackfillResp>(
        "/api/import/gongshang-backfill?dry_run=1",
        fd,
      );
      const headers = res.headers_raw ?? [];
      setGsHeaders(headers);
      setGsMapping(
        headers.map((_, i) => ({
          col: i,
          field:
            res.mapping_suggestion?.find((m) => m.col === i)?.field ??
            (i === 0 ? "name" : "skip"),
        })),
      );
      setGsPreviewRows(res.preview_rows ?? []);
      setGsTotalRows(res.total_rows ?? 0);
      setGsPreview({
        matched: res.matched ?? 0,
        filled: res.filled ?? {},
        kept_existing: res.kept_existing ?? 0,
        skipped: res.skipped ?? 0,
      });
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "解析文件失败");
      closeGsWizard();
    } finally {
      setGsBusy(false);
    }
  };

  /** 第二步：确认映射 → 正式回填（无 dry_run；后端只填空不覆盖） */
  const confirmGongshangBackfill = async () => {
    if (!gsFile) return;
    setGsConfirming(true);
    try {
      const fd = new FormData();
      fd.append("file", gsFile);
      const ordered = [...gsMapping].sort((a, b) => a.col - b.col).map((m) => m.field);
      fd.append("mapping", JSON.stringify(ordered));
      const res = await api.post<GongshangBackfillResp>("/api/import/gongshang-backfill", fd);
      const result = {
        matched: res.matched ?? 0,
        filled: res.filled ?? {},
        kept_existing: res.kept_existing ?? 0,
        skipped: res.skipped ?? 0,
        skipped_details: res.skipped_details ?? [],
      };
      const filledTotal = Object.values(result.filled).reduce((a, b) => a + b, 0);
      Toast.success(
        `回填完成：匹配 ${result.matched} 家，补 ${filledTotal} 格，保留已有 ${result.kept_existing} 格，未匹配 ${result.skipped} 行`,
      );
      setLogs((prev) => [
        ...prev,
        `工商补数成功：${gsFile.name}（匹配 ${result.matched}，补 ${filledTotal} 格，保留 ${result.kept_existing} 格，未匹配 ${result.skipped}）`,
      ]);
      setGsResult(result);
    } catch (e) {
      Toast.error(e instanceof Error ? e.message : "回填失败");
    } finally {
      setGsConfirming(false);
    }
  };

  const closeGsWizard = () => {
    setGsOpen(false);
    setGsFile(null);
    setGsHeaders([]);
    setGsMapping([]);
    setGsPreviewRows([]);
    setGsTotalRows(0);
    setGsPreview(null);
    setGsResult(null);
  };

  const finishGsImport = () => {
    closeGsWizard();
    router.refresh();
  };

  /** Dropzone 分流：企业 CSV / 工商补数走两步向导，招标 CSV 保持直传 */
  const pickDropzoneFile = (file: File, kind: "enterprises" | "tenders" | "gongshang") => {
    if (kind === "enterprises") {
      void startEnterpriseUpload(file);
    } else if (kind === "gongshang") {
      void startGongshangUpload(file);
    } else {
      void uploadCsv(file, kind);
    }
  };

  const Dropzone = ({
    kind,
    title,
    hint,
  }: {
    kind: "enterprises" | "tenders" | "gongshang";
    title: string;
    hint: string;
  }) => (
    <label
      onDragOver={(e) => e.preventDefault()}
      onDrop={(e) => {
        e.preventDefault();
        const f = e.dataTransfer.files?.[0];
        if (f) pickDropzoneFile(f, kind);
      }}
      className="block border-[1.5px] border-dashed border-slate-200 hover:border-[color:var(--accent)] hover:bg-[color:var(--accent)]/5 rounded-xl px-4 py-5 text-center cursor-pointer transition-colors"
    >
      <input
        type="file"
        accept={kind === "tenders" ? ".csv" : ".csv,.xlsx"}
        className="hidden"
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) pickDropzoneFile(f, kind);
          e.currentTarget.value = "";
        }}
      />
      <FileSpreadsheet className="mx-auto text-[var(--accent)]" size={22} />
      <div className="text-[13.5px] font-medium text-slate-700 mt-2">{title}</div>
      <div className="text-[11.5px] text-slate-400 mt-1 leading-5">{hint}</div>
    </label>
  );

  // 上传向导预览表的列 = 前 5 行字段按出现顺序去重（映射后字段横向展示）
  const upPreviewCols: string[] = [];
  for (const row of upPreviewRows.slice(0, 5)) {
    for (const k of Object.keys(row)) {
      if (!upPreviewCols.includes(k)) upPreviewCols.push(k);
    }
  }

  // SPEC-GSH 工商补数向导预览表的列（同上传向导的去重规则）
  const gsPreviewCols: string[] = [];
  for (const row of gsPreviewRows.slice(0, 5)) {
    for (const k of Object.keys(row)) {
      if (!gsPreviewCols.includes(k)) gsPreviewCols.push(k);
    }
  }

  return (
    <AppShell>
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        {/* 左列 */}
        <div className="space-y-4">
          <Card
            title={
              <span className="flex items-center gap-2 text-[14.5px] font-medium text-[var(--ink)]">
                <Globe2 size={16} className="text-[var(--accent)]" /> 官方数据采集（免 Key）
              </span>
            }
          >
            <div className="flex gap-3 items-center flex-wrap">
              <Select
                style={{ width: 190 }}
                value={channel}
                onChange={(v) => switchChannel(v as Channel)}
                optionList={[
                  { value: "ggzy", label: "全国公共资源交易·中标数据" },
                  { value: "ccgp", label: "中国政府采购网·公告" },
                ]}
              />
              <Select
                style={{ flex: 1, minWidth: 220 }}
                placeholder="设备关键词（可多选）"
                multiple
                filter
                maxTagCount={3}
                optionList={termOptions}
                value={terms}
                onChange={(v) => setTerms(v as string[])}
              />
            </div>
            <div className="flex gap-3 items-center mt-3">
              {channel === "ccgp" && (
                <label className="flex items-center gap-2 text-[12.5px] text-[var(--muted)]">
                  近
                  <InputNumber
                    value={scanDays}
                    min={7}
                    max={1095}
                    style={{ width: 90 }}
                    onChange={(v) => setScanDays(Number(v) || 365)}
                  />
                  天
                </label>
              )}
              <label className="flex items-center gap-2 text-[12.5px] text-[var(--muted)]">
                每词最多
                <InputNumber
                  value={maxPages}
                  min={1}
                  max={10}
                  style={{ width: 80 }}
                  onChange={(v) => setMaxPages(Number(v) || 2)}
                />
                页
              </label>
              <div className="flex-1" />
              {starting && taskId ? (
                <Button type="danger" theme="outline" onClick={cancelTask}>
                  取消
                </Button>
              ) : (
                <Button theme="solid" loading={starting} onClick={startOfficial}>
                  开始采集
                </Button>
              )}
            </div>
            <p className="text-slate-400 text-[12px] leading-5 mt-3.5">
              {channel === "ggzy"
                ? "直连国家信息中心数据服务（data.ggzy.gov.cn）：中标方与采购人双向建档，成交价与项目名自动生成证据句。限速 1.5s/请求。"
                : "直连中国政府采购网（ccgp.gov.cn）：公告自带采购人，自动归到买家主体；中标公告同时抽取供应商。限速 3-6s/请求。"}
              采购人是真买家——优先「转线索」。
            </p>
          </Card>

          <Card
            title={
              <span className="flex items-center gap-2 text-[14.5px] font-medium text-[var(--ink)]">
                <Rocket size={16} className="text-[var(--accent)]" /> 高德 POI 地图拓客
              </span>
            }
          >
            <div className="flex gap-3 items-center flex-wrap">
              <Select
                style={{ width: 140 }}
                optionList={POI_CITIES.map((c) => ({ value: c, label: c }))}
                value={city}
                onChange={(v) => setCity(v as string)}
              />
              <Select
                style={{ flex: 1, minWidth: 200 }}
                placeholder="选择下游行业"
                multiple
                optionList={INDUSTRY_OPTIONS.map((i) => ({ value: i.key, label: i.name }))}
                value={industries}
                onChange={(v) => setIndustries(v as string[])}
              />
              <label className="flex items-center gap-1.5 text-[12.5px] text-[var(--muted)] cursor-pointer select-none shrink-0">
                <input
                  type="checkbox"
                  checked={useQuadtree}
                  onChange={(e) => setUseQuadtree(e.target.checked)}
                  className="accent-[var(--accent)]"
                />
                四叉树细分
              </label>
              {starting && taskId ? (
                <Button type="danger" theme="outline" onClick={cancelTask}>
                  取消
                </Button>
              ) : (
                <Button theme="solid" loading={starting} onClick={startPoi}>
                  开始采集
                </Button>
              )}
            </div>
            <p className="text-slate-400 text-[12px] leading-5 mt-3.5">
              按行业关键词扫描所选城市的工厂 POI，自动入库、归一去重、ICP 打分；勾选四叉树后，宽词在大城市按区县边界递归细分不漏数据。需在「设置」配置高德 Key。
            </p>
          </Card>

          <Card
            title={
              <span className="flex items-center gap-2 text-[14.5px] font-medium text-[var(--ink)]">
                <ClipboardPaste size={16} className="text-[var(--accent)]" /> 粘贴导入（Excel 直接 Ctrl+V）
              </span>
            }
          >
            <p className="text-slate-400 text-[12px] leading-5">
              从 Excel 复制区域后粘贴到此——自动识别 Tab/逗号分隔与中文表头（企业名称/城市/地址/注册资本/经营范围），无需另存 CSV 文件。
            </p>
            <Button
              theme="outline"
              className="!mt-3"
              onClick={() => setPasteOpen(true)}
            >
              打开粘贴窗口
            </Button>
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
                title="导入企业名单（Excel / CSV）"
                hint="支持 Excel(.xlsx) / CSV；表头随意——中文列名自动识别（如 公司名称/省份/注册资本），上传后可逐列确认映射；识别不了的列在向导里手动指一下即可"
              />
              <Dropzone
                kind="tenders"
                title="导入招标公告 CSV"
                hint="列：title, content, url, pub_date, enterprise_name —— 命中设备词自动生成意图信号"
              />
              {/* SPEC-GSH：工商补数（只填空不覆盖）——风鸟对照表批量回填 */}
              <Dropzone
                kind="gongshang"
                title="工商补数（只填空值）"
                hint="拖入风鸟导出的对照表（企业名称/信用代码 + 法人/成立日期/注册资本/经营范围）——按名称或信用代码匹配库内企业，只补空格永不覆盖已有值；先预演后回填"
              />
            </div>
          </Card>

          {/* SPEC-CK：风鸟配额流水线——人工查询+官方导出，零爬虫合规 */}
                  <Card
          title={
            <span className="flex items-center gap-2 text-[14.5px] font-medium text-[var(--ink)]">
              <Building2 size={16} className="text-[var(--accent)]" /> 工商补数（风鸟）
            </span>
          }
        >
            <div className="space-y-2 text-[12.5px] leading-5 text-[var(--muted)]">
              <p>
                企业工商资料（法人/注册资本/成立日期/电话）是最薄的一层——
                用风鸟免费账号按下面流程补齐：
              </p>
              <ol className="ml-4 list-decimal space-y-0.5">
                <li>下载缺数清单（按意图分排序，最值得查的在前，每行带直搜链接）</li>
                <li>企业列表 → 点开公司 → 档案页签底部「从风鸟补工商资料」</li>
                <li>在风鸟公司页整块复制工商信息 → 粘贴 → 解析入库（仅补空不覆盖）</li>
              </ol>
              <div className="flex flex-wrap items-center gap-2 pt-1">
                <Button size="small" icon={<FileSpreadsheet size={13} />} onClick={() => window.open("/api/tasks/gongshang-lookup-export", "_blank")}>
                  下载缺数清单
                </Button>
                <span className="text-[11.5px]">也可直接在风鸟把常用客户加进「我的监控」（免费），工商变更实时推送</span>
              </div>
            </div>
          </Card>
        </div>

        {/* 右列：实时进度 + SPEC-MULTI S3 任务结果预览 */}
        <div className="space-y-4 self-start">
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
              尚无运行中的任务 —— 启动官方数据采集、POI 采集或导入 CSV 后这里会实时滚动日志
            </div>
          ) : (
            <>
              <Progress percent={progress} showInput aria-label="任务进度" stroke="var(--accent)" />
              <pre className="bg-[#10202a] text-emerald-200 text-[11.5px] leading-5 p-3.5 rounded-xl mt-3.5 max-h-[420px] overflow-auto whitespace-pre-wrap font-mono">
                {logs.join("\n") || "等待日志…"}
              </pre>
            </>
          )}
        </Card>

          {/* SPEC-MULTI S3：本次新采企业 + 失败明细（results 请求 404/失败则不渲染） */}
          {taskResults && (taskResults.rows.length > 0 || taskResults.failures.length > 0) && (
            <Card
              title={
                <span className="flex items-center gap-2 text-[14.5px] font-semibold text-slate-700">
                  <Building2 size={16} className="text-[var(--accent)]" /> 本次新采企业
                </span>
              }
            >
              <div className="text-[11.5px] text-[var(--muted)]">共 {taskResults.total} 家新入库</div>
              <div className="mt-2 max-h-[320px] space-y-1 overflow-auto">
                {taskResults.rows.slice(0, 50).map((r) => (
                  <button
                    key={r.id}
                    onClick={() => router.push(`/enterprises/view?id=${r.id}`)}
                    className="flex w-full cursor-pointer items-center gap-2 rounded-lg border border-[var(--border)] bg-white px-3 py-1.5 text-left transition-colors hover:border-[var(--accent)]"
                    title={`${r.name} · 打开企业详情`}
                  >
                    <span className="min-w-0 flex-1 truncate text-[13px] font-medium text-[var(--ink)]">{r.name}</span>
                    <span className="shrink-0 text-[12px] text-[var(--muted)]">{r.province || "—"}</span>
                    <span className="shrink-0 text-[12px] text-[var(--muted)]">{r.industry_name || "行业未知"}</span>
                  </button>
                ))}
                {taskResults.rows.length === 0 && (
                  <div className="py-3 text-center text-[12.5px] text-[var(--muted)]">本次无新入库企业</div>
                )}
              </div>
              {taskResults.failures.length > 0 && (
                <div className="mt-3">
                  <div className="mb-1.5 text-[12.5px] font-medium text-[var(--negative)]">
                    失败明细（{taskResults.failures.length}）
                  </div>
                  <div className="max-h-[200px] space-y-1 overflow-auto">
                    {taskResults.failures.map((f, i) => (
                      <div
                        key={`${f.enterprise_id ?? "x"}-${i}`}
                        className="rounded-lg border border-[var(--border)] bg-white px-3 py-1.5 text-[12px] leading-5"
                      >
                        <span className="font-medium text-[var(--ink)]">{f.name}</span>
                        {f.stage ? <span className="mx-1.5 text-[var(--muted)]">· {f.stage}</span> : null}
                        <span className="text-[var(--negative)]">{f.error || ""}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </Card>
          )}
        </div>
      </div>
      {/* 粘贴导入弹窗（twenty spreadsheet-import 模式） */}
      <Modal
        title="粘贴导入企业"
        visible={pasteOpen}
        onCancel={() => setPasteOpen(false)}
        footer={null}
        width={560}
      >
        <div className="space-y-3 pt-1">
          {pasteStep === 1 ? (
            <>
              <p className="text-[12.5px] leading-5 text-[var(--muted)]">
                第 1 步：从 Excel 复制表格（含表头行）粘贴到下方，下一步确认列映射。
              </p>
              <textarea
                value={pasteText}
                onChange={(e) => setPasteText(e.target.value)}
                rows={10}
                placeholder={"企业名称\t城市\t注册资本\n苏州XX真空设备有限公司\t苏州市\t500"}
                className="w-full rounded-lg border border-[var(--border)] bg-white p-3 text-[12.5px] leading-5 outline-none focus:border-[var(--accent)]"
              />
              <Button theme="solid" block onClick={() => gotoMatchStep()}>
                下一步：确认列映射
              </Button>
            </>
          ) : (
            <>
              <p className="text-[12.5px] leading-5 text-[var(--muted)]">
                第 2 步：确认列映射（已按表头自动匹配，可调整；「忽略此列」的列不导入）。
              </p>
              <div className="max-h-[300px] space-y-2 overflow-auto">
                {pasteHeaders.map((h, i) => {
                  const field = pasteMapping.find((m) => m.col === i)?.field ?? "skip";
                  return (
                    <div key={i} className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2">
                      <span className="w-28 shrink-0 truncate text-[12.5px] font-medium text-[var(--ink)]" title={h}>
                        {h || `（第 ${i + 1} 列）`}
                      </span>
                      <span className="text-[var(--control)]">→</span>
                      <Select
                        size="small"
                        style={{ flex: 1 }}
                        value={field}
                        optionList={TARGET_FIELDS.map((f) => ({ value: f.value, label: f.label }))}
                        onChange={(v) =>
                          setPasteMapping((prev) => {
                            const next = prev.filter((m) => m.col !== i);
                            next.push({ col: i, field: v as string });
                            return next;
                          })
                        }
                      />
                    </div>
                  );
                })}
              </div>
              <div className="flex gap-2">
                <Button theme="light" block onClick={() => setPasteStep(1)}>
                  上一步
                </Button>
                <Button theme="solid" block loading={pasteBusy} onClick={() => void submitPaste()}>
                  确认并导入
                </Button>
              </div>
            </>
          )}
        </div>
      </Modal>
      {/* 上传导入弹窗（SPEC-IMP：对齐粘贴向导的两步模式） */}
      <Modal
        title="上传导入企业"
        visible={upOpen}
        onCancel={() => (upResult ? finishEnterpriseImport() : closeUpWizard())}
        footer={null}
        width={640}
      >
        <div className="space-y-3 pt-1">
          {/* 顶部：文件名 + 行数 + 去重预演徽章 */}
          <div className="flex items-center flex-wrap gap-2">
            <span
              className="max-w-[260px] truncate text-[13px] font-medium text-[var(--ink)]"
              title={upFile?.name ?? ""}
            >
              {upFile?.name ?? "（未选择文件）"}
            </span>
            <span className="text-[12px] text-[var(--muted)]">共 {upTotalRows} 行</span>
            {upDup && (
              <span className="ml-auto shrink-0 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11.5px] text-amber-700">
                库内已有 {upDup.existing} 家，将新增 {upDup.new} 家
              </span>
            )}
          </div>

          {upBusy ? (
            <div className="py-10 text-center text-[12.5px] text-[var(--muted)]">正在解析文件…</div>
          ) : upResult ? (
            <>
              <p className="text-[12.5px] leading-5 text-[var(--muted)]">
                导入完成：新增 {upResult.inserted} 家，合并 {upResult.merged} 家，跳过 {upResult.skipped} 家。
              </p>
              <div>
                <p className="mb-1.5 text-[12.5px] font-medium text-[var(--ink)]">
                  跳过明细（{upResult.skipped_details.length} 条）
                </p>
                <div className="max-h-[200px] overflow-auto rounded-lg border border-[var(--border)]">
                  {upResult.skipped_details.map((d, i) => (
                    <div
                      key={i}
                      className={`flex items-start gap-2 px-3 py-1.5 text-[12px] leading-5 ${
                        i > 0 ? "border-t border-[var(--border)]" : ""
                      }`}
                    >
                      <span className="shrink-0 text-[var(--muted)]">第 {d.row} 行</span>
                      <span className="text-[var(--ink)]">{d.reason}</span>
                    </div>
                  ))}
                </div>
              </div>
              <Button theme="solid" block onClick={finishEnterpriseImport}>
                完成
              </Button>
            </>
          ) : (
            <>
              <p className="text-[12.5px] leading-5 text-[var(--muted)]">
                第 2 步：确认列映射（已按表头自动匹配，可调整；「忽略此列」的列不导入）。
              </p>
              <div className="max-h-[260px] space-y-2 overflow-auto">
                {upHeaders.map((h, i) => {
                  const field = upMapping.find((m) => m.col === i)?.field ?? "skip";
                  return (
                    <div key={i} className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2">
                      <span className="w-28 shrink-0 truncate text-[12.5px] font-medium text-[var(--ink)]" title={h}>
                        {h || `（第 ${i + 1} 列）`}
                      </span>
                      <span className="text-[var(--control)]">→</span>
                      <Select
                        size="small"
                        style={{ flex: 1 }}
                        value={field}
                        optionList={UPLOAD_FIELDS}
                        onChange={(v) =>
                          setUpMapping((prev) => {
                            const next = prev.filter((m) => m.col !== i);
                            next.push({ col: i, field: v as string });
                            return next;
                          })
                        }
                      />
                    </div>
                  );
                })}
              </div>
              {upPreviewRows.length > 0 && (
                <div>
                  <p className="mb-1.5 text-[12px] text-[var(--muted)]">
                    数据预览（按建议映射，前 {Math.min(upPreviewRows.length, 5)} 行，横向滚动查看）
                  </p>
                  <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
                    <table className="min-w-full text-[11.5px]">
                      <thead>
                        <tr className="bg-slate-50">
                          {upPreviewCols.map((c) => (
                            <th
                              key={c}
                              className="whitespace-nowrap border-b border-[var(--border)] px-2.5 py-1.5 text-left font-medium text-[var(--muted)]"
                            >
                              {c}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {upPreviewRows.slice(0, 5).map((row, ri) => (
                          <tr key={ri}>
                            {upPreviewCols.map((c) => (
                              <td
                                key={c}
                                className="whitespace-nowrap border-b border-[var(--border)] px-2.5 py-1.5 text-[var(--ink)]"
                              >
                                {row[c] ?? ""}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              <div className="flex gap-2">
                <Button theme="light" block onClick={closeUpWizard}>
                  取消
                </Button>
                <Button
                  theme="solid"
                  block
                  loading={upConfirming}
                  onClick={() => void confirmEnterpriseImport()}
                >
                  确认导入 {upTotalRows} 行
                </Button>
              </div>
            </>
          )}
        </div>
      </Modal>
      {/* 工商补数弹窗（SPEC-GSH：拖表 dry_run 预演 → 映射确认 → 正式回填只填空） */}
      <Modal
        title="工商补数（只填空值）"
        visible={gsOpen}
        onCancel={() => (gsResult ? finishGsImport() : closeGsWizard())}
        footer={null}
        width={640}
      >
        <div className="space-y-3 pt-1">
          {/* 顶部：文件名 + 行数 + 只填空红线提示 */}
          <div className="flex items-center flex-wrap gap-2">
            <span
              className="max-w-[260px] truncate text-[13px] font-medium text-[var(--ink)]"
              title={gsFile?.name ?? ""}
            >
              {gsFile?.name ?? "（未选择文件）"}
            </span>
            <span className="text-[12px] text-[var(--muted)]">共 {gsTotalRows} 行</span>
            <span className="ml-auto shrink-0 rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11.5px] text-emerald-700">
              只填空值 · 永不覆盖已有数据
            </span>
          </div>

          {gsBusy ? (
            <div className="py-10 text-center text-[12.5px] text-[var(--muted)]">
              正在解析文件并预演回填…
            </div>
          ) : gsResult ? (
            <>
              {/* 正式回填结果（与预演计数同构） */}
              <p className="text-[12.5px] leading-5 text-[var(--muted)]">
                回填完成：匹配 {gsResult.matched} 家，未匹配 {gsResult.skipped} 行。
              </p>
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(gsResult.filled)
                  .filter(([, n]) => n > 0)
                  .map(([k, n]) => (
                    <span
                      key={k}
                      className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11.5px] text-emerald-700"
                    >
                      {GONGSHANG_FIELD_LABELS[k] ?? k} 补 {n} 格
                    </span>
                  ))}
                <span className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11.5px] text-slate-600">
                  保留已有 {gsResult.kept_existing} 格
                </span>
              </div>
              {gsResult.skipped_details.length > 0 && (
                <div>
                  <p className="mb-1.5 text-[12.5px] font-medium text-[var(--ink)]">
                    未匹配明细（{gsResult.skipped_details.length} 条）
                  </p>
                  <div className="max-h-[200px] overflow-auto rounded-lg border border-[var(--border)]">
                    {gsResult.skipped_details.map((d, i) => (
                      <div
                        key={i}
                        className={`flex items-start gap-2 px-3 py-1.5 text-[12px] leading-5 ${
                          i > 0 ? "border-t border-[var(--border)]" : ""
                        }`}
                      >
                        <span className="shrink-0 text-[var(--muted)]">第 {d.row} 行</span>
                        <span className="text-[var(--ink)]">{d.reason}</span>
                      </div>
                    ))}
                  </div>
                </div>
              )}
              <Button theme="solid" block onClick={finishGsImport}>
                完成
              </Button>
            </>
          ) : (
            <>
              {/* dry_run 预演计数徽章（零写入，正式回填前先看会发生什么） */}
              {gsPreview && (
                <div className="flex flex-wrap gap-1.5">
                  <span className="rounded-md border border-[color:var(--accent)]/30 bg-[color:var(--accent)]/5 px-2 py-0.5 text-[11.5px] text-[var(--accent)]">
                    预演：匹配 {gsPreview.matched} 家
                  </span>
                  {Object.entries(gsPreview.filled)
                    .filter(([, n]) => n > 0)
                    .map(([k, n]) => (
                      <span
                        key={k}
                        className="rounded-md border border-emerald-200 bg-emerald-50 px-2 py-0.5 text-[11.5px] text-emerald-700"
                      >
                        {GONGSHANG_FIELD_LABELS[k] ?? k} 可补 {n} 格
                      </span>
                    ))}
                  <span className="rounded-md border border-slate-200 bg-slate-50 px-2 py-0.5 text-[11.5px] text-slate-600">
                    已有值保留 {gsPreview.kept_existing} 格
                  </span>
                  <span className="rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 text-[11.5px] text-amber-700">
                    未匹配 {gsPreview.skipped} 行
                  </span>
                </div>
              )}
              <p className="text-[12.5px] leading-5 text-[var(--muted)]">
                确认列映射（已按表头自动识别企业名称/信用代码匹配列，可调整；「忽略此列」不参与）。确认后正式回填——仅补空格，已有值一律保留。
              </p>
              <div className="max-h-[260px] space-y-2 overflow-auto">
                {gsHeaders.map((h, i) => {
                  const field = gsMapping.find((m) => m.col === i)?.field ?? "skip";
                  return (
                    <div key={i} className="flex items-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2">
                      <span className="w-28 shrink-0 truncate text-[12.5px] font-medium text-[var(--ink)]" title={h}>
                        {h || `（第 ${i + 1} 列）`}
                      </span>
                      <span className="text-[var(--control)]">→</span>
                      <Select
                        size="small"
                        style={{ flex: 1 }}
                        value={field}
                        optionList={GONGSHANG_FIELDS}
                        onChange={(v) =>
                          setGsMapping((prev) => {
                            const next = prev.filter((m) => m.col !== i);
                            next.push({ col: i, field: v as string });
                            return next;
                          })
                        }
                      />
                    </div>
                  );
                })}
              </div>
              {gsPreviewRows.length > 0 && (
                <div>
                  <p className="mb-1.5 text-[12px] text-[var(--muted)]">
                    数据预览（前 {Math.min(gsPreviewRows.length, 5)} 行，横向滚动查看）
                  </p>
                  <div className="overflow-x-auto rounded-lg border border-[var(--border)]">
                    <table className="min-w-full text-[11.5px]">
                      <thead>
                        <tr className="bg-slate-50">
                          {gsPreviewCols.map((c) => (
                            <th
                              key={c}
                              className="whitespace-nowrap border-b border-[var(--border)] px-2.5 py-1.5 text-left font-medium text-[var(--muted)]"
                            >
                              {GONGSHANG_FIELD_LABELS[c] ?? (c === "name" ? "企业名称" : c)}
                            </th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {gsPreviewRows.slice(0, 5).map((row, ri) => (
                          <tr key={ri}>
                            {gsPreviewCols.map((c) => (
                              <td
                                key={c}
                                className="whitespace-nowrap border-b border-[var(--border)] px-2.5 py-1.5 text-[var(--ink)]"
                              >
                                {row[c] ?? ""}
                              </td>
                            ))}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
              <div className="flex gap-2">
                <Button theme="light" block onClick={closeGsWizard}>
                  取消
                </Button>
                <Button
                  theme="solid"
                  block
                  loading={gsConfirming}
                  onClick={() => void confirmGongshangBackfill()}
                >
                  确认回填 {gsTotalRows} 行
                </Button>
              </div>
            </>
          )}
        </div>
      </Modal>
    </AppShell>
  );
}
