/**
 * 客户地图（SPEC-AJ AJ2 → 区域作战工具）：Leaflet + 高德栅格瓦片。
 * POI 坐标（GCJ-02）与高德瓦片同源——零纠偏。
 * ssr:false 动态导入（Leaflet 依赖 window，静态导出必须 client-only）。
 * framed-sheet：白底浅框、青蓝 Linear 主色 #5e6ad2，无渐变无阴影。
 *
 * 区域作战视图：出门前看"这片有哪些可拨客户"，看到目标即打电话。
 * - popup=作战卡：企业名→情报页链接、意图分、近期动态徽标、号码可复制可拨打。
 * - 点样式按意图分层：≥80 深紫大点、≥60 半透中点、有电话、无电话灰点。
 * - 动态值一律 escapeHtml 后进 innerHTML（企业名/行业/地区/号码，防 XSS）。
 * - SPEC-CR CR2：视野驱动加载——moveend 防抖 500ms 按当前视野 bbox 拉点；
 *   zoom≥11 带 bbox（后端 points 模式），更低 zoom 全国聚合（clusters 模式）。
 */
"use client";

import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { Select, Tag, Toast } from "@douyinfe/semi-ui";
import { Phone } from "lucide-react";
import type { Map as LeafletMap, CircleMarker } from "leaflet";
import AppShell from "@/components/AppShell";
import MapLegend from "@/components/MapLegend";
import { api } from "@/lib/api";

interface GeoPoint {
  id: number;
  name: string;
  lat: number;
  lng: number;
  city: string | null;
  district: string | null;
  industry_name: string | null;
  intent_score: number | null;
  has_phone: boolean;
  /** 首个可拨号码（后端增强字段，未部署时 undefined 按无号码降级） */
  phone?: string | null;
  /** 近 30 天有信号（后端增强字段） */
  recent_30d?: boolean;
}

interface GeoCluster {
  lat: number;
  lng: number;
  count: number;
  phone: number;
  /** 近期有动态的企业数（后端增强字段） */
  recent?: number;
}

const CITIES = [
  { name: "苏州", center: [31.3, 120.58] as [number, number] },
  { name: "无锡", center: [31.49, 120.31] as [number, number] },
  { name: "东莞", center: [23.02, 113.75] as [number, number] },
  { name: "佛山", center: [23.02, 113.12] as [number, number] },
  { name: "深圳", center: [22.54, 114.06] as [number, number] },
  { name: "宁波", center: [29.87, 121.54] as [number, number] },
  { name: "常州", center: [31.81, 119.97] as [number, number] },
  { name: "南通", center: [31.98, 120.89] as [number, number] },
  { name: "西安", center: [34.34, 108.94] as [number, number] },
  { name: "合肥", center: [31.82, 117.23] as [number, number] },
];

// research_edu（科研院所）后端默认排除，不再作为筛选维度（AK3 契约）
const INDUSTRIES = [
  { key: "", label: "全部行业" },
  { key: "vacuum", label: "真空设备" },
  { key: "coating", label: "真空镀膜" },
  { key: "semiconductor", label: "半导体" },
  { key: "environmental", label: "环保水务" },
  { key: "machining", label: "机械加工" },
  { key: "hvac_building", label: "暖通机电" },
  { key: "lab_instrument", label: "检验检测" },
  { key: "medical_health", label: "医疗卫生" },
  { key: "freeze_dried", label: "冻干食品" },
  { key: "pharma_biotech", label: "制药生物" },
  { key: "chemical_new_material", label: "化工新材料" },
  { key: "packaging_printing", label: "包装印刷" },
];

/* ---------- popup 内联图标：popup 是 innerHTML 字符串，按 lucide 线型内联 SVG ---------- */

const ICON_PHONE =
  '<svg xmlns="http://www.w3.org/2000/svg" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none"><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z"/></svg>';
const ICON_COPY =
  '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none"><rect width="14" height="14" x="8" y="8" rx="2" ry="2"/><path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/></svg>';
const ICON_ACTIVITY =
  '<svg xmlns="http://www.w3.org/2000/svg" width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="flex:none"><path d="M22 12h-2.48a2 2 0 0 0-1.93 1.46l-2.35 8.36a.25.25 0 0 1-.48 0L9.24 2.18a.25.25 0 0 0-.48 0l-2.35 8.36A2 2 0 0 1 4.49 12H2"/></svg>';

/** XSS 防线：所有动态值进 innerHTML 前必须过此函数（企业名/行业/地区/号码）。 */
function escapeHtml(v: string): string {
  return v
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

/** 点样式分层（数据事实修正）：近期有动态（amber）> 高意图（深紫）>
 *  有电话（青蓝）> 无电话（灰）。intent_score 列仅 680 家>0（POI 无
 *  公示信号全为 0）——不能作地图主分层键。 */
function pointStyle(p: GeoPoint): {
  radius: number;
  color: string;
  weight: number;
  fillColor: string;
  fillOpacity: number;
  opacity?: number;
} {
  const score = p.intent_score ?? 0;
  if (p.recent_30d === true) {
    return { radius: 6.5, color: "#c2801b", weight: 2, fillColor: "#c2801b", fillOpacity: 0.85 };
  }
  if (score >= 80) {
    return { radius: 7, color: "#5e6ad2", weight: 2, fillColor: "#5e6ad2", fillOpacity: 0.9 };
  }
  if (score >= 60) {
    return {
      radius: 5.5, color: "#5e6ad2", weight: 1.5, fillColor: "#5e6ad2",
      fillOpacity: 0.75, opacity: 0.75,
    };
  }
  if (p.has_phone) {
    return { radius: 5, color: "#5e6ad2", weight: 1, fillColor: "#5e6ad2", fillOpacity: 0.85 };
  }
  return { radius: 3.5, color: "#9aa5a9", weight: 1, fillColor: "#e8ecee", fillOpacity: 0.6 };
}

/** 结构化作战卡：名称链接情报页 → 行业地区 → 意图/动态 → 电话区块。 */
function buildPopupHtml(p: GeoPoint): string {
  const name = escapeHtml(p.name ?? "");
  const meta = escapeHtml(
    [p.industry_name ?? "未分类", p.district ?? p.city ?? ""].filter(Boolean).join(" · "),
  );
  const link = `/enterprises/view?id=${encodeURIComponent(String(p.id))}`;

  const score = p.intent_score;
  const intentHtml =
    score == null
      ? ""
      : score >= 80
        ? `<span style="display:inline-flex;align-items:center;gap:3px;padding:1px 7px;border-radius:999px;background:var(--accent-weak);border:1px solid var(--accent);color:var(--accent);font-size:11.5px;font-weight:600;line-height:18px">意图 ${score}</span>`
        : `<span style="color:var(--muted);font-size:11.5px;line-height:18px">意图 ${score}</span>`;
  const recentHtml =
    p.recent_30d === true
      ? `<span style="display:inline-flex;align-items:center;gap:3px;padding:1px 7px;border-radius:999px;background:#fdf4e3;border:1px solid #ecd9ad;color:#8a5a10;font-size:11.5px;font-weight:600;line-height:18px">${ICON_ACTIVITY}近期有动态</span>`
      : "";

  const phone = (p.phone ?? "").trim();
  const tel = phone.replace(/[^\d+]/g, "");
  const phoneBlock = phone
    ? `<div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px">
        <div style="display:flex;align-items:center;gap:6px;color:var(--ink);font-weight:500">
          ${ICON_PHONE}
          <input readonly data-map-phone value="${escapeHtml(phone)}"
            style="flex:1;min-width:0;border:none;background:transparent;font:inherit;font-weight:500;color:inherit;padding:1px 2px;cursor:text;outline:none"
            title="点击全选号码" />
        </div>
        <div style="margin-top:6px;display:flex;gap:6px;align-items:center">
          <button type="button" data-copy-phone="${escapeHtml(phone)}"
            style="display:inline-flex;align-items:center;gap:4px;border:1px solid var(--border);background:var(--surface);border-radius:6px;padding:2px 9px;font-size:12px;color:var(--ink);cursor:pointer">${ICON_COPY}复制</button>
          <a href="tel:${escapeHtml(tel)}"
            style="display:inline-flex;align-items:center;gap:4px;border:1px solid var(--accent-weak);background:var(--accent-weak);border-radius:6px;padding:2px 9px;font-size:12px;font-weight:600;color:var(--accent);text-decoration:none">${ICON_PHONE}拨打</a>
        </div>
      </div>`
    : `<div style="margin-top:8px;border-top:1px solid var(--border);padding-top:8px;color:#9aa5a9;font-size:12px">— 无公开电话（可去风鸟补）</div>`;

  return `<div style="font-size:13px;min-width:210px;line-height:1.5">
      <a href="${link}" target="_blank" rel="noreferrer"
        style="font-size:13.5px;font-weight:600;color:var(--ink);text-decoration:none"
        title="进入企业情报页">${name}</a>
      <div style="color:var(--muted);margin-top:1px">${meta}</div>
      <div style="margin-top:6px;display:flex;gap:6px;align-items:center;flex-wrap:wrap">${intentHtml}${recentHtml}</div>
      ${phoneBlock}
    </div>`;
}

/** popup 内事件委托（Leaflet 对 popup wrapper 做了 disableClickPropagation，必须挂 wrapper 上）。 */
function attachPopupHandlers(e: { popup: { getElement: () => HTMLElement | null } }): void {
  const root = e.popup.getElement();
  const wrap = root?.querySelector<HTMLElement>(".leaflet-popup-content-wrapper") ?? root;
  if (!wrap || wrap.dataset.vacleadDelegated === "1") return;
  wrap.dataset.vacleadDelegated = "1";
  wrap.addEventListener("click", (ev) => {
    const target = ev.target as HTMLElement | null;
    if (!target) return;
    // 点号码框：全选，方便 Ctrl+C
    const input = target.closest<HTMLInputElement>("input[data-map-phone]");
    if (input) {
      input.focus();
      input.select();
      return;
    }
    // 复制按钮
    const btn = target.closest<HTMLElement>("[data-copy-phone]");
    if (!btn) return;
    const value = btn.dataset.copyPhone ?? "";
    const inp = wrap.querySelector<HTMLInputElement>("input[data-map-phone]");
    if (navigator.clipboard?.writeText) {
      navigator.clipboard
        .writeText(value)
        .then(() => Toast.success({ content: "号码已复制", duration: 2 }))
        .catch(() => {
          inp?.focus();
          inp?.select();
          Toast.warning({ content: "复制失败——号码已全选，Ctrl+C 即可", duration: 3 });
        });
    } else {
      inp?.focus();
      inp?.select();
      Toast.warning({ content: "当前浏览器不支持一键复制——号码已全选，Ctrl+C 即可", duration: 3 });
    }
  });
}

export default function MapPage() {
  const divRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const markersRef = useRef<CircleMarker[]>([]);
  // SPEC-CR CR2：请求序号守卫 + moveend 防抖计时器 + 最新加载闭包（供地图事件回调调用）
  const loadSeqRef = useRef(0);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const loadRef = useRef<() => Promise<void>>(async () => {});
  // 初始 setView 已定位首个城市；重复定位会触发零位移 moveend 造成冗余请求
  const positionedCityRef = useRef(CITIES[0].name);
  const [city, setCity] = useState("苏州");
  const [industry, setIndustry] = useState("");
  const [withPhone, setWithPhone] = useState(false);
  const [recentOnly, setRecentOnly] = useState(false);
  const [mode, setMode] = useState<"points" | "clusters">("points");
  const [stats, setStats] = useState({ total: 0, phone: 0 });
  const [ready, setReady] = useState(false);

  // 初始化地图（一次性）
  useEffect(() => {
    let disposed = false;
    (async () => {
      const L = (await import("leaflet")).default;
      if (disposed || !divRef.current || mapRef.current) return;
      const map = L.map(divRef.current, { zoomControl: true, attributionControl: true })
        .setView(CITIES[0].center, 11);
      L.tileLayer("https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}", {
        subdomains: ["1", "2", "3", "4"],
        attribution: "© 高德地图 AutoNavi",
        maxZoom: 18,
      }).addTo(map);
      mapRef.current = map;
      // SPEC-CR CR2：视野驱动加载——moveend（拖拽/缩放/平移结束）防抖 500ms 后按当前视野重载
      map.on("moveend", () => {
        if (debounceRef.current) clearTimeout(debounceRef.current);
        debounceRef.current = setTimeout(() => void loadRef.current(), 500);
      });
      setReady(true);
    })();
    return () => {
      disposed = true;
      if (debounceRef.current) clearTimeout(debounceRef.current);
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  const loadPoints = useMemo(
    () => async () => {
      const map = mapRef.current;
      if (!map) return;
      // 取消排队中的防抖请求：立即加载与延迟加载都读当前视野，等价且省一次冗余请求
      if (debounceRef.current) {
        clearTimeout(debounceRef.current);
        debounceRef.current = null;
      }
      // 请求序号守卫：拖拽连发 moveend 时，慢的旧响应不得覆盖新结果——只让最新请求落账
      const seq = ++loadSeqRef.current;
      const params = new URLSearchParams({ limit: "2000" });
      if (withPhone) params.set("with_phone", "1");
      if (industry) params.set("industry_key", industry);
      if (recentOnly) params.set("recent", "1");
      // SPEC-CR CR2：zoom≥11 城市级视野带 bbox（后端返回 points 模式）；
      // 更低 zoom 不带 bbox 全国聚合（后端 clusters 模式，threshold=1500 自动切档）
      if (map.getZoom() >= 11) {
        const b = map.getBounds();
        params.set("south", b.getSouth().toFixed(6));
        params.set("west", b.getWest().toFixed(6));
        params.set("north", b.getNorth().toFixed(6));
        params.set("east", b.getEast().toFixed(6));
      }
      try {
        const d = await api.get<{ mode: string; items: (GeoPoint | GeoCluster)[]; total: number }>(
          `/api/enterprises/geo?${params.toString()}`,
        );
        const L = (await import("leaflet")).default;
        if (seq !== loadSeqRef.current) return; // 旧响应不落账
        // 成功后才清旧点——失败静默保留旧点（网络抖动不清空地图）
        for (const m of markersRef.current) m.remove();
        markersRef.current = [];
        let phoneCount = 0;
        if (d.mode === "clusters") {
          setMode("clusters");
          // AK2：全国级聚合——圆面积∝数量，青蓝占比=电话覆盖率
          for (const cl of d.items as GeoCluster[]) {
            phoneCount += cl.phone;
            const ratio = cl.phone / Math.max(cl.count, 1);
            const marker = L.circleMarker([cl.lat, cl.lng], {
              radius: 6 + Math.min(18, Math.sqrt(cl.count) * 1.6),
              color: "var(--accent)",
              weight: 1.5,
              fillColor: ratio > 0.4 ? "#5e6ad2" : "#b9bfe8",
              fillOpacity: 0.45,
            }).addTo(map);
            const recentTxt = typeof cl.recent === "number" ? ` · 近期 ${cl.recent}` : "";
            marker.bindTooltip(`${cl.count} 家 · 电话 ${cl.phone}${recentTxt}`, { permanent: false });
            marker.on("click", () => map.setView([cl.lat, cl.lng], 12));
            markersRef.current.push(marker);
          }
        } else {
          setMode("points");
          for (const p of d.items as GeoPoint[]) {
            if (p.has_phone) phoneCount += 1;
            const marker = L.circleMarker([p.lat, p.lng], pointStyle(p)).addTo(map);
            marker.bindPopup(buildPopupHtml(p), { maxWidth: 280 });
            marker.on("popupopen", attachPopupHandlers);
            markersRef.current.push(marker);
          }
        }
        setStats({ total: d.total, phone: phoneCount });
      } catch {
        // 静默失败：保留旧点，视野操作不被网络抖动打断（重试=再拖一下地图）
      }
    },
    [withPhone, industry, recentOnly],
  );

  // loadPoints 引用跟进：moveend 防抖回调始终调用最新筛选闭包
  useEffect(() => {
    loadRef.current = loadPoints;
  }, [loadPoints]);

  // 切城市：只平移视野——moveend 会触发防抖重载（SPEC-CR CR2 视野驱动）
  useEffect(() => {
    if (!ready || positionedCityRef.current === city) return;
    positionedCityRef.current = city;
    const c = CITIES.find((x) => x.name === city);
    if (c) mapRef.current?.setView(c.center, 11);
  }, [city, ready]);

  // 首次初始化 + 筛选变化：立即按当前视野重载（bbox 读自地图 getBounds）
  useEffect(() => {
    if (!ready) return;
    void loadPoints();
  }, [ready, loadPoints]);

  return (
    <AppShell>
      <div className="flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[22px] font-semibold leading-7 text-[var(--ink)]">客户地图</h1>
          <p className="mt-1 text-[13.5px] text-[var(--muted)]">
            区域作战视图 · 看到目标即可拨打 · 近期动态=近 30 天有公示
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2.5">
          <span className="text-[12.5px] text-[var(--muted)]">
            当前视野 <span className="font-medium text-[var(--ink)]">{stats.total}</span> 家
            ·{" "}
            <span className="inline-flex items-center gap-0.5 align-baseline text-[var(--accent)]">
              <Phone size={12} strokeWidth={2.25} className="self-center" aria-hidden />
              有电话 <span className="font-medium">{stats.phone}</span>
            </span>
            {stats.total >= 2000 && (
              <span className="ml-1 text-[#c2801b]">（视野上限 2000，放大可看更小范围全量）</span>
            )}
          </span>
        </div>
      </div>

      {/* 筛选行：城市 chips 一行横排 + 行业 + 意图/电话过滤 */}
      <div className="mt-3 flex flex-wrap items-center gap-1.5">
        {CITIES.map((c) => (
          <button
            key={c.name}
            type="button"
            onClick={() => setCity(c.name)}
            className={`rounded-md border px-2 py-[3px] text-[12px] leading-4 transition-colors ${
              city === c.name
                ? "border-[var(--accent)] bg-[var(--accent-weak)] font-medium text-[var(--accent)]"
                : "border-[var(--border)] text-[var(--muted)] hover:border-[var(--control)] hover:text-[var(--ink)]"
            }`}
          >
            {c.name}
          </button>
        ))}
        <span aria-hidden className="mx-1 h-4 w-px bg-[var(--border)]" />
        <Select
          value={industry}
          onChange={(v) => setIndustry(String(v))}
          style={{ width: 130 }}
          size="small"
          optionList={INDUSTRIES.map((i) => ({ value: i.key, label: i.label }))}
        />
        <Tag
          color={recentOnly ? "blue" : "grey"}
          type="light"
          className="!cursor-pointer"
          onClick={() => setRecentOnly((v) => !v)}
        >
          近期有动态
        </Tag>
        <Tag
          color={withPhone ? "blue" : "grey"}
          type="light"
          className="!cursor-pointer"
          onClick={() => setWithPhone((v) => !v)}
        >
          只看有电话
        </Tag>
      </div>

      <div className="relative mt-4 h-[calc(100vh-232px)] min-h-[480px] w-full overflow-hidden rounded-lg border border-[var(--border)]">
        <div ref={divRef} className="h-full w-full" />
        <MapLegend mode={mode} />
      </div>
    </AppShell>
  );
}
