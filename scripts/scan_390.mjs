// 390px 全页扫描 v2：页级溢出 + 页内可交互元素触屏目标尺寸（一次性）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const BASE = "http://127.0.0.1:8000";
const PAGES = [
  ["home", "/"],
  ["signals", "/signals"],
  ["enterprises", "/enterprises"],
  ["proposals", "/proposals"],
  ["contacts", "/contacts"],
  ["leads", "/leads"],
  ["map", "/map"],
  ["reports", "/reports"],
  ["quality", "/quality"],
  ["notifications", "/notifications"],
  ["settings", "/settings"],
  ["detail", "/enterprises/view?id=13"],
];

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
await page.evaluate(async () => {
  const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "admin123" }) });
  const d = await r.json();
  localStorage.setItem("vaclead_token", d.token);
  localStorage.setItem("vaclead_role", "admin");
  localStorage.setItem("vaclead_user", "管理员");
});

for (const [name, path] of PAGES) {
  await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  const r = await page.evaluate(() => {
    const sw = document.body.scrollWidth;
    // 触屏目标底线：32px CSS 意图 × 0.875 根字号 = 28px 实测（宽）/ 24px（高）
    // 豁免：Leaflet/高德法律署名链（合规要素，非动作目标）
    const small = [];
    for (const el of document.querySelectorAll("button, a")) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 || rect.height === 0) continue;
      if (el.closest(".leaflet-control-attribution, .amap-footer, [data-legal-attribution], [data-inline-prose]")) continue;
      if (rect.width < 28 || rect.height < 24) {
        const t = (el.textContent || "").trim().slice(0, 16) || el.getAttribute("aria-label") || el.getAttribute("title") || "";
        if (t) small.push(`${el.tagName}:${rect.width}x${rect.height}:${t}`);
      }
    }
    return { sw, small: small.slice(0, 4), smallCount: small.length };
  });
  const flag = r.sw > 400 ? " ⚠️" : " ✓";
  console.log(`${name}: sw=${r.sw}${flag} 小目标=${r.smallCount} ${JSON.stringify(r.small)}`);
}
await browser.close();
console.log("SCAN DONE");
