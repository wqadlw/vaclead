export const INDUSTRY_OPTIONS = [
  { key: "research_edu", name: "科研院所/高校医疗", tier: 1 },
  { key: "semiconductor", name: "半导体/集成电路", tier: 1 },
  { key: "coating", name: "真空镀膜加工", tier: 1 },
  { key: "photovoltaic", name: "光伏", tier: 2 },
  { key: "lithium", name: "锂电池", tier: 2 },
  { key: "freeze_dried", name: "冻干食品/医药", tier: 2 },
  { key: "heat_treatment", name: "金属热处理", tier: 3 },
] as const;

export const POI_CITIES = [
  "苏州", "无锡", "东莞", "佛山", "深圳", "宁波", "常州", "南通", "西安", "合肥",
] as const;

export const LEAD_STATUS: Record<string, { label: string; color: string }> = {
  new: { label: "新线索", color: "blue" },
  following: { label: "跟进中", color: "processing" },
  intent: { label: "意向", color: "gold" },
  won: { label: "成交", color: "success" },
  lost: { label: "放弃", color: "default" },
  pool: { label: "公海", color: "warning" },
};

export const LOST_REASONS: Record<string, string> = {
  no_need: "无需求",
  competitor: "同行",
  unreachable: "联系不上",
  budget: "预算不足",
  other: "其他",
};

/** SPEC-AE 生命周期阶段（won 之后的后销售旅程） */
export const LIFECYCLE: Record<string, { label: string; color: "grey" | "green" | "red" }> = {
  lead: { label: "培育期", color: "grey" },
  customer_success: {label: "客户成功", color: "green" },
  at_risk: { label: "流失预警", color: "red" },
  churned: { label: "已流失", color: "grey" },
};

export const GRADE_COLOR: Record<string, string> = {
  S: "red",
  A: "orange",
  B: "blue",
};

export const TIER_COLOR: Record<number, string> = {
  1: "geekblue",
  2: "cyan",
  3: "default",
};

export function gradeOf(score: number): "S" | "A" | "B" | "-" {
  if (score >= 80) return "S";
  if (score >= 60) return "A";
  if (score >= 40) return "B";
  return "-";
}
