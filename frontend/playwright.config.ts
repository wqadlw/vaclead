import { defineConfig } from "@playwright/test";

/**
 * E2E 配置（SPEC-H）：走单进程真实形态（后端 :8010 托管静态导出）。
 * 独立临时库 VACLEAD_DB——绝不碰真库 data/vaclead.db。
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  retries: 0,
  workers: 1, // 单worker：共享一个后端实例，避免数据竞争
  reporter: [["list"]],
  use: {
    baseURL: "http://127.0.0.1:8010",
    headless: true,
    locale: "zh-CN",
    trace: "off",
  },
  webServer: {
    command: "npm run e2e:server",
    url: "http://127.0.0.1:8010/api/health",
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
