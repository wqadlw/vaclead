// README 批量截图（SPEC-BB/README 更新配套）：登录后逐页截取到 docs/screenshots/readme/
// 运行前提：后端 :8000 已启动（serve_8000.bat）。用后即留（脚本可复跑更新 README 图）。
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const BASE = process.env.SHOT_BASE || "http://127.0.0.1:8000";
const OUT = "E:/AAAA/A-xiaosou/vaclead/docs/screenshots/readme";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1520, height: 900 } });

const shot = async (name, full = false) => {
  await page.waitForTimeout(600);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: full });
  console.log("shot", name);
};

const waitText = async (text, ms = 12000) => {
  try {
    await page.waitForSelector(`text=${text}`, { timeout: ms });
  } catch {}
};

// 1. 登录页
await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(800);
await shot("login");

// 登录
await page.fill('input[type="text"], input:not([type="password"])', "admin");
await page.fill('input[type="password"]', "admin123");
await page.click('button[type="submit"], button:has-text("登录")');
await page.waitForURL(`${BASE}/`, { timeout: 15000 }).catch(() => {});
await page.waitForTimeout(1800);

// 2. 工作台（今日推荐/流失预警/节能账入口）
await shot("dashboard", true);

// 3. 标讯中心（图标页签）
await page.goto(`${BASE}/signals`, { waitUntil: "domcontentloaded" });
await waitText("标讯中心");
await page.waitForTimeout(1500);
await shot("signals", true);

// 4. 客户地图（Leaflet 瓦片加载）
await page.goto(`${BASE}/map`, { waitUntil: "domcontentloaded" });
await waitText("客户地图");
await page.waitForTimeout(4500);
await shot("map");

// 5. 企业筛选（OEM quick pick）
await page.goto(`${BASE}/enterprises`, { waitUntil: "domcontentloaded" });
await waitText("企业筛选");
await page.waitForTimeout(1500);
await shot("enterprises", true);

// 6. 企业情报页（选一家有联系人的：API 拿最新带电话企业）
const token = await page.evaluate(() => localStorage.getItem("vaclead_token"));
const res = await page.request.get(`${BASE}/api/enterprises?has_contact=1&page_size=1`, {
  headers: { Authorization: `Bearer ${token}` },
});
const list = await res.json();
const eid = list.items?.[0]?.id;
if (eid) {
  await page.goto(`${BASE}/enterprises/view?id=${eid}`, { waitUntil: "domcontentloaded" });
  await waitText("关键联系人");
  await page.waitForTimeout(1500);
  await shot("intel", true);
  // 7. 装机设备 tab
  await page.click('text=装机设备').catch(() => {});
  await page.waitForTimeout(800);
  await shot("intel-units");
  // 8. 提案打印视图（带 token 回退）
  await page.goto(
    `${BASE}/api/proposal/print?eid=${eid}&power_kw=37&hours_per_day=16&load_factor=0.8&price_per_kwh=0.7&saving_pct=25&token=${token}`,
    { waitUntil: "domcontentloaded" },
  ).catch(() => {});
  await page.waitForTimeout(800);
  await shot("proposal");
  await page.goto(`${BASE}/enterprises/view?id=${eid}`).catch(() => {});
}

// 9. 线索看板
await page.goto(`${BASE}/leads`, { waitUntil: "domcontentloaded" });
await waitText("商机管线");
await page.waitForTimeout(1200);
await shot("leads", true);

// 10. 报价单
await page.goto(`${BASE}/quotes`, { waitUntil: "domcontentloaded" });
await waitText("报价");
await page.waitForTimeout(1000);
await shot("quotes");

// 11. 合同管理
await page.goto(`${BASE}/contracts`, { waitUntil: "domcontentloaded" });
await waitText("合同");
await page.waitForTimeout(1000);
await shot("contracts");

// 12. 邮件触达
await page.goto(`${BASE}/outreach`, { waitUntil: "domcontentloaded" });
await waitText("序列");
await page.waitForTimeout(1000);
await shot("outreach", true);

// 13. 报表中心
await page.goto(`${BASE}/reports`, { waitUntil: "domcontentloaded" });
await waitText("报表");
await page.waitForTimeout(1500);
await shot("reports", true);

// 14. 设置治理
await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
await waitText("设置");
await page.waitForTimeout(1000);
await shot("settings", true);

// 15. 企业对比（取 3 家高 ICP 企业）
try {
  const r3 = await page.request.get(`${BASE}/api/enterprises?score_min=60&page_size=3`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  const l3 = await r3.json();
  const ids = (l3.items ?? []).map((x) => x.id).slice(0, 3).join(",");
  if (ids) {
    await page.goto(`${BASE}/compare?ids=${ids}`, { waitUntil: "domcontentloaded" });
    await page.waitForTimeout(1200);
    await shot("compare");
  }
} catch {}

// 16. 拓客任务
await page.goto(`${BASE}/tasks`, { waitUntil: "domcontentloaded" });
await waitText("任务");
await page.waitForTimeout(1200);
await shot("tasks");

await browser.close();
console.log("DONE");
