"use client";

/**
 * 企业对比页（SPEC-Z Z4）：2-4 家横向对比——属性为行、企业为列。
 * 纯前端聚合既有 detail 端点（零后端改动）；URL ?ids=1,2,3 可分享。
 * 选入：企业列表/情报页「加入对比」（localStorage vaclead_compare）。
 * 数值行最优值 accent 高亮。
 */
import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Button, Skeleton, Tag } from "@douyinfe/semi-ui";
import { X } from "lucide-react";
import AppShell from "@/components/AppShell";
import { api, type Enterprise } from "@/lib/api";
import { COMPARE_MAX, readCompare, removeFromCompare } from "@/lib/compare";

interface CompareCol {
  ent: Enterprise;
  stat: { bid_count: number; bid_amount_total_wan: number; last_win_date: string | null } | null;
  tags: string[];
}

/** 数值行定义：getter + 最优方向（max=大者优 / min=小者优 / null=不高亮） */
type RowDef = {
  label: string;
  get: (c: CompareCol) => string | number | null | undefined;
  best?: "max" | "min";
  fmt?: (v: string | number | null | undefined) => string;
};

const _rowDefs: RowDef[] = [
  { label: "ICP 分", get: (c) => c.ent.icp_score, best: "max" },
  { label: "意图分", get: (c) => c.ent.intent_score, best: "max" },
  { label: "资料完整度", get: (c) => c.ent.completeness ?? 0, best: "max", fmt: (v) => `${v}/4` },
  { label: "注册资本(万)", get: (c) => c.ent.registered_capital_wan, best: "max", fmt: (v) => (v == null ? "—" : Number(v).toLocaleString()) },
  { label: "成立日期", get: (c) => c.ent.established_at, fmt: (v) => v == null || v === "" ? "—" : String(v) },
  { label: "法定代表人", get: (c) => c.ent.legal_rep, fmt: (v) => (v == null || v === "" ? "—" : String(v)) },
  { label: "中标次数", get: (c) => c.stat?.bid_count ?? null, best: "max" },
  { label: "累计中标(万)", get: (c) => c.stat?.bid_amount_total_wan ?? null, best: "max", fmt: (v) => (v == null ? "—" : Number(v).toLocaleString()) },
  { label: "最近中标", get: (c) => c.stat?.last_win_date, fmt: (v) => (v == null || v === "" ? "—" : String(v)) },
  { label: "地区", get: (c) => [c.ent.province, c.ent.city].filter(Boolean).join(" ") || null },
  { label: "来源", get: (c) => c.ent.source },
];

function CompareBody() {
  const sp = useSearchParams();
  const ids = (sp.get("ids") ?? "")
    .split(",")
    .map((s) => Number(s))
    .filter((n) => Number.isInteger(n) && n > 0)
    .slice(0, COMPARE_MAX);
  const [cols, setCols] = useState<CompareCol[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    Promise.all(
      ids.map((id) =>
        Promise.all([
          api.get<Enterprise>(`/api/enterprises/${id}`),
          api
            .get<{ stat: CompareCol["stat"] }>(`/api/enterprises/${id}`)
            .then((d) => d.stat),
        ]).then(([ent, stat]) => ({
          ent,
          stat,
          tags: (() => {
            try {
              const a = JSON.parse((ent.tags as unknown as string) || "[]") as string[];
              return Array.isArray(a) ? a : [];
            } catch {
              return [];
            }
          })(),
        })),
      ),
    )
      .then(setCols)
      .catch(() => undefined)
      .finally(() => setLoading(false));
  }, [ids.join(",")]);
  useEffect(load, [load]);

  const remove = (id: number) => {
    removeFromCompare(id);
    setCols(cols.filter((c) => c.ent.id !== id));
  };

  if (ids.length < 2) {
    return (
      <div className="py-16 text-center text-[13.5px] text-[var(--muted)]">
        请从企业列表选择 2-4 家「加入对比」，或检查链接中的 ids 参数。
      </div>
    );
  }

  const bestOf = (def: RowDef): Set<number> => {
    if (!def.best) return new Set();
    const vals = cols.map((c) => def.get(c));
    const nums = vals.filter((v): v is number => typeof v === "number");
    if (nums.length < 2) return new Set();
    const target = def.best === "max" ? Math.max(...nums) : Math.min(...nums);
    if (target === 0) return new Set();
    return new Set(cols.filter((c) => def.get(c) === target).map((c) => c.ent.id));
  };

  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[13px]">
        <thead>
          <tr>
            <th className="sticky left-0 z-10 w-32 bg-white p-2 text-left text-[var(--muted)]">属性</th>
            {cols.map((c) => (
              <th key={c.ent.id} className="min-w-[180px] border-b border-[var(--border)] p-2 text-left align-top">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <a
                      href={`/enterprises/view?id=${c.ent.id}`}
                      className="block truncate text-[14px] font-medium text-[var(--accent)] hover:underline"
                    >
                      {c.ent.name}
                    </a>
                    <div className="mt-1 flex flex-wrap gap-1">
                      {(c.ent.industry_name || c.ent.industry_key) && (
                        <Tag color="cyan" type="light" size="small">
                          {c.ent.industry_name || c.ent.industry_key}
                        </Tag>
                      )}
                      {c.ent.is_competitor === 1 && (
                        <Tag color="amber" type="light" size="small">疑似同行</Tag>
                      )}
                    </div>
                    {c.tags.length > 0 && (
                      <div className="mt-1 flex flex-wrap gap-1">
                        {c.tags.slice(0, 3).map((t) => (
                          <Tag key={t} color="light-blue" type="light" size="small">{t}</Tag>
                        ))}
                      </div>
                    )}
                  </div>
                  <Button
                    size="small"
                    type="tertiary"
                    theme="borderless"
                    icon={<X size={13} />}
                    onClick={() => remove(c.ent.id)}
                  />
                </div>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {_rowDefs.map((def) => {
            const best = bestOf(def);
            return (
              <tr key={def.label} className="border-b border-[var(--rule)]">
                <td className="sticky left-0 z-10 bg-white p-2 text-[var(--muted)]">{def.label}</td>
                {cols.map((c) => {
                  const raw = def.get(c);
                  const isBest = best.has(c.ent.id);
                  return (
                    <td key={c.ent.id} className={`p-2 ${isBest ? "font-medium text-[var(--accent)]" : "text-[var(--ink)]"}`}>
                      {def.fmt ? def.fmt(raw) : raw == null || raw === "" ? "—" : String(raw)}
                    </td>
                  );
                })}
              </tr>
            );
          })}
          {/* 标签行（多值不高亮） */}
          <tr className="border-b border-[var(--rule)]">
            <td className="sticky left-0 z-10 bg-white p-2 text-[var(--muted)]">标签</td>
            {cols.map((c) => (
              <td key={c.ent.id} className="p-2">
                {c.tags.length > 0 ? (
                  <div className="flex flex-wrap gap-1">
                    {c.tags.map((t) => (
                      <Tag key={t} color="light-blue" type="light" size="small">{t}</Tag>
                    ))}
                  </div>
                ) : (
                  "—"
                )}
              </td>
            ))}
          </tr>
        </tbody>
      </table>
    </div>
  );
}

export default function ComparePage() {
  return (
    <AppShell>
      <div className="mb-4 flex items-center justify-between">
        <h1 className="text-[17px] font-semibold text-[var(--ink)]">企业对比</h1>
        <span className="text-[12px] text-[var(--muted)]">最多 4 家 · 最优值高亮 · 链接可分享</span>
      </div>
      <div className="rounded-xl border border-[var(--border)] bg-white p-5">
        <Suspense fallback={<Skeleton placeholder={<Skeleton.Paragraph rows={6} />} loading active />}>
          <CompareBody />
        </Suspense>
      </div>
    </AppShell>
  );
}
