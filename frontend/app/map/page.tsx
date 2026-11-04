/**
 * 客户地图（SPEC-AJ AJ2）：Leaflet + 高德栅格瓦片。
 * POI 坐标（GCJ-02）与高德瓦片同源——零纠偏。
 * ssr:false 动态导入（Leaflet 依赖 window，静态导出必须 client-only）。
 * framed-sheet：白底浅框、青蓝 #1b6c80 主色，无渐变无阴影。
 */
"use client";

import "leaflet/dist/leaflet.css";
import { useEffect, useMemo, useRef, useState } from "react";
import { Select, Tag, Toast } from "@douyinfe/semi-ui";
import type { Map as LeafletMap, CircleMarker } from "leaflet";
import AppShell from "@/components/AppShell";
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
}

const CITIES = [
  { name: "苏州", center: [31.30, 120.58] as [number, number] },
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
  { key: "research_edu", label: "科研院所" },
];

export default function MapPage() {
  const divRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const markersRef = useRef<CircleMarker[]>([]);
  const [city, setCity] = useState("苏州");
  const [industry, setIndustry] = useState("");
  const [withPhone, setWithPhone] = useState(false);
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
      setReady(true);
    })();
    return () => {
      disposed = true;
      mapRef.current?.remove();
      mapRef.current = null;
    };
  }, []);

  const loadPoints = useMemo(
    () => async () => {
      if (!mapRef.current) return;
      const map = mapRef.current;
      // 清旧点
      for (const m of markersRef.current) m.remove();
      markersRef.current = [];
      try {
        const d = await api.get<{ items: GeoPoint[]; total: number }>(
          `/api/enterprises/geo?limit=2000${withPhone ? "&with_phone=1" : ""}${
            industry ? `&industry_key=${industry}` : ""
          }`,
        );
        const L = (await import("leaflet")).default;
        let phoneCount = 0;
        for (const p of d.items) {
          if (p.has_phone) phoneCount += 1;
          const marker = L.circleMarker([p.lat, p.lng], {
            radius: p.has_phone ? 5 : 3.5,
            color: p.has_phone ? "#1b6c80" : "#9aa5a9",
            weight: 1,
            fillColor: p.has_phone ? "#1b6c80" : "#e8ecee",
            fillOpacity: p.has_phone ? 0.85 : 0.6,
          }).addTo(map);
          marker.bindPopup(
            `<div style="font-size:13px;min-width:180px">
               <b>${p.name}</b><br/>
               <span style="color:#6b7175">${p.industry_name ?? "未分类"} · ${p.district ?? p.city ?? ""}</span><br/>
               ${p.has_phone ? '<span style="color:#1b6c80">☎ 有公开电话</span>' : '<span style="color:#9aa5a9">无公开电话</span>'}<br/>
               <a href="/enterprises/view?id=${p.id}" target="_blank" rel="noreferrer">查看详情 →</a>
             </div>`,
          );
          markersRef.current.push(marker);
        }
        setStats({ total: d.total, phone: phoneCount });
      } catch (e) {
        Toast.error(e instanceof Error ? e.message : "加载失败");
      }
    },
    [withPhone, industry],
  );

  // 切城市：平移 + 重载点
  useEffect(() => {
    if (!ready) return;
    const c = CITIES.find((x) => x.name === city);
    if (c) mapRef.current?.setView(c.center, 11);
    void loadPoints();
  }, [city, ready, loadPoints]);

  // 筛选变化：重载
  useEffect(() => {
    if (!ready) return;
    void loadPoints();
  }, [withPhone, industry, ready, loadPoints]);

  return (
    <AppShell>
      <div className="flex items-end justify-between">
        <div>
          <h1 className="text-[22px] font-semibold leading-7 text-[var(--ink)]">客户地图</h1>
          <p className="mt-1 text-[13.5px] text-[var(--muted)]">
            POI 自主公开电话打点 · 青蓝=打得通 · 点击查看联系卡
          </p>
        </div>
        <div className="flex items-center gap-2.5">
          <span className="text-[12.5px] text-[var(--muted)]">
            本视图 <span className="font-medium text-[var(--ink)]">{stats.total}</span> 家
            · 有电话 <span className="font-medium text-[var(--accent)]">{stats.phone}</span>
            {stats.total > 2000 && (
              <span className="ml-1 text-[#c2801b]">（视图上限 2000，缩放到单城看全量）</span>
            )}
          </span>
          <Select
            value={city}
            onChange={(v) => setCity(String(v))}
            style={{ width: 110 }}
            size="small"
            optionList={CITIES.map((c) => ({ value: c.name, label: c.name }))}
          />
          <Select
            value={industry}
            onChange={(v) => setIndustry(String(v))}
            style={{ width: 130 }}
            size="small"
            optionList={INDUSTRIES.map((i) => ({ value: i.key, label: i.label }))}
          />
          <Tag
            color={withPhone ? "blue" : "grey"}
            type="light"
            className="!cursor-pointer"
            onClick={() => setWithPhone((v) => !v)}
          >
            ☎ 只看有电话
          </Tag>
        </div>
      </div>
      <div
        ref={divRef}
        className="mt-4 h-[calc(100vh-190px)] min-h-[480px] w-full overflow-hidden rounded-lg border border-[var(--border)]"
      />
    </AppShell>
  );
}
