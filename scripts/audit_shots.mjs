// 前端细节审查截图（临时脚本）：登录后逐页全页截图到 scripts/audit/
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const BASE = "http://127.0.0.1:8000";
const OUT = "E:/AAAA/A-xiaosou/vaclead/scripts/audit";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });

// 登录
await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.evaluate(async () => {
  const r = await fetch("/api/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: "admin", password: "admin123" }),
  });
  const d = await r.json();
  localStorage.setItem("vaclead_token", d.token);
  localStorage.setItem("vaclead_role", "admin");
  localStorage.setItem("vaclead_user", "管理员");
});

const pages = [
  ["home", "/"],
  ["signals", "/signals"],
  ["enterprises", "/enterprises"],
  ["competitors", "/competitors"],
  ["proposals", "/proposals"],
  ["contacts", "/contacts"],
  ["leads", "/leads"],
  ["notifications", "/notifications"],
  ["quality", "/quality"],
  ["reports", "/reports"],
  ["settings", "/settings"],
];

for (const [name, route] of pages) {
  await page.goto(`${BASE}${route}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  await page.screenshot({ path: path.join(OUT, `${name}.png`), fullPage: true });
  console.log("shot", name);
}

// 详情页（取一个有信号的企业）
const detailId = await page.evaluate(async () => {
  const token = localStorage.getItem("vaclead_token");
  const r = await fetch("/api/signals?page_size=1", {
    headers: { Authorization: `Bearer ${token}` },
  });
  const d = await r.json();
  return d.items?.[0]?.ent_id ?? 1;
});
await page.goto(`${BASE}/enterprises/view?id=${detailId}`, { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await page.screenshot({ path: path.join(OUT, "detail.png"), fullPage: true });
console.log("shot detail");

await browser.close();
console.log("ALL DONE");
