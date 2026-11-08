// 定位 CSS 文本节点的 DOM 来源（一次性）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 390, height: 844 } });
await page.goto("http://127.0.0.1:8000/login", { waitUntil: "domcontentloaded" });
await page.evaluate(async () => {
  const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "admin123" }) });
  const d = await r.json();
  localStorage.setItem("vaclead_token", d.token);
  localStorage.setItem("vaclead_role", "admin");
  localStorage.setItem("vaclead_user", "管理员");
});
await page.goto("http://127.0.0.1:8000/contacts", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);

const result = await page.evaluate(() => {
  const el = [...document.querySelectorAll("*")].find(
    (e) => e.childElementCount === 0 && (e.textContent || "").includes("th,td {"),
  );
  if (!el) return { found: false };
  const chain = [];
  let cur = el;
  for (let i = 0; i < 6 && cur; i++) {
    chain.push(`${cur.tagName}.${String(cur.className).slice(0, 50)} [text=${(cur.textContent || "").slice(0, 60)}]`);
    cur = cur.parentElement;
  }
  return { found: true, text: (el.textContent || "").slice(0, 160), chain };
});
console.log(JSON.stringify(result, null, 1));
await browser.close();
