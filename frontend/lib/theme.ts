import type { ThemeConfig } from "antd";

/** vaclead 品牌主题 — 靛蓝紫主色 / 柔和圆角 / 低噪阴影。 */
export const antdTheme: ThemeConfig = {
  token: {
    colorPrimary: "#4f46e5",
    colorInfo: "#4f46e5",
    colorSuccess: "#10b981",
    colorWarning: "#f59e0b",
    colorError: "#ef4444",
    borderRadius: 10,
    fontSize: 14,
    fontFamily:
      '-apple-system, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Helvetica Neue", sans-serif',
    colorBgLayout: "#f4f5fa",
    colorTextHeading: "#0f172a",
    colorText: "#334155",
  },
  components: {
    Table: {
      headerBg: "#f8f9fd",
      headerColor: "#64748b",
      headerSplitColor: "transparent",
      rowHoverBg: "#f5f5ff",
    },
    Menu: {
      darkItemBg: "transparent",
      darkSubMenuItemBg: "transparent",
      darkItemSelectedBg: "linear-gradient(90deg,#4f46e5,#7c3aed)",
      darkItemHoverBg: "rgba(255,255,255,.06)",
    },
    Button: { controlHeight: 36, fontWeight: 500 },
    Card: { paddingLG: 20 },
  },
};

/** 页面标题映射（供页头显示）。 */
export const PAGE_TITLES: Record<string, string> = {
  "/": "工作台",
  "/enterprises": "企业筛选",
  "/leads": "线索看板",
  "/tasks": "拓客任务",
  "/settings": "设置",
};
