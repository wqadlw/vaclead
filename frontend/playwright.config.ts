import { defineConfig } from "@playwright/test";

/**
 * E2E 配置（SPEC-H）：走单进程真实形态（后端托管静态导出）。
 * 独立临时库 VACLEAD_DB——绝不碰真库 data/vaclead.db。
 * E2E_PORT 可改端口错峰（双 Agent 并发跑门禁互不抢 :8010，COLLABORATION §5）。
 */
const PORT = Number(process.env.E2E_PORT ?? 8010);
const BASE = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: 0,
  workers: 1, // 单worker：共享一个后端实例，避免数据竞争
  reporter: [["list"]],
  use: {
    baseURL: BASE,
    headless: true,
    locale: "zh-CN",
    trace: "off",
  },
  webServer: {
    command: "npm run e2e:server",
    url: `${BASE}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
