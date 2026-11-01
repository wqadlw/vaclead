/** 企业对比选入清单（SPEC-Z Z4）：localStorage 暂存，≤4 家，URL 分享用 ids。 */
import type { ReactNode } from "react";

const COMPARE_KEY = "vaclead_compare";
export const COMPARE_MAX = 4;
export const COMPARE_PAGE_HINT: ReactNode = null;

export function readCompare(): number[] {
  try {
    const raw = JSON.parse(localStorage.getItem(COMPARE_KEY) || "[]") as number[];
    return Array.isArray(raw) ? raw.slice(0, COMPARE_MAX) : [];
  } catch {
    return [];
  }
}

export function addToCompare(id: number): { ok: boolean; reason?: string } {
  const cur = readCompare();
  if (cur.includes(id)) return { ok: false, reason: "已在对比清单" };
  if (cur.length >= COMPARE_MAX) return { ok: false, reason: `最多对比 ${COMPARE_MAX} 家` };
  localStorage.setItem(COMPARE_KEY, JSON.stringify([...cur, id]));
  return { ok: true };
}

export function removeFromCompare(id: number): number[] {
  const next = readCompare().filter((x) => x !== id);
  localStorage.setItem(COMPARE_KEY, JSON.stringify(next));
  return next;
}
