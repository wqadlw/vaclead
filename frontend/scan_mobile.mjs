// 390px 移动端溢出扫描（7 页 scrollWidth + 最大溢出元素，一次性）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const BASE = "http://127.0.0.1:8000";
const PAGES = [
  ["home", "/"],
  ["contacts", "/contacts"],
  ["map", "/map"],
  ["enterprises", "/enterprises"],
  ["signals", "/signals"],
  ["proposals", "/proposals"],
  ["reports", "/reports"],
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
  const result = await page.evaluate(() => {
    const sw = document.body.scrollWidth;
    let worstTag = "", worstW = 0;
    for (const el of document.querySelectorAll("*")) {
      const w = el.scrollWidth;
      if (w > worstW && w > 400) { worstW = w; worstTag = `${el.tagName}.${String(el.className).slice(0, 60)}`; }
    }
    return { sw, worstTag, worstW };
  });
  const flag = result.sw > 400 ? " ⚠️溢出" : " ✓";
  console.log(`${name}: scrollWidth=${result.sw}${flag} 最大元素=${result.worstW} ${result.worstTag.slice(0, 70)}`);
}
await browser.close();
console.log("SCAN DONE");
