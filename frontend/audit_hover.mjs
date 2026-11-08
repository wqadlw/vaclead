// 移动端 hover 依赖审计：找出所有 group-hover/opacity-0 依赖的控件（一次性）
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

const PAGES = [
  ["home", "/"],
  ["competitors", "/competitors"],
  ["contacts", "/contacts"],
  ["proposals", "/proposals"],
  ["enterprises-view", "/enterprises/view?id=13"],
  ["leads", "/leads"],
];

for (const [name, path] of PAGES) {
  await page.goto(`http://127.0.0.1:8000${path}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  const result = await page.evaluate(() => {
    // 找 opacity 0/透明且靠 hover 显现的元素
    const hidden = [];
    for (const el of document.querySelectorAll("*")) {
      const cls = String(el.className || "");
      if (cls.includes("group-hover:opacity-100") || cls.includes("hover:opacity-100")) {
        const cs = getComputedStyle(el);
        if (parseFloat(cs.opacity) < 0.3 && el.offsetParent !== null) {
          hidden.push(`${el.tagName}[opacity=${cs.opacity}] ${(el.textContent || "").trim().slice(0, 30)}`);
        }
      }
    }
    // 无横向溢出检查
    return { hidden: hidden.slice(0, 6), sw: document.body.scrollWidth };
  });
  console.log(`${name}: sw=${result.sw} hover依赖=${result.hidden.length}`);
  for (const h of result.hidden) console.log("   ", h);
}
await browser.close();
console.log("AUDIT DONE");
