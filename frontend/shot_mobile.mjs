// 390px 三页截图目检（一次性）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const BASE = "http://127.0.0.1:8000";
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

for (const [name, path] of [["m-home", "/"], ["m-signals", "/signals"], ["m-contacts", "/contacts"], ["m-map", "/map"]]) {
  await page.goto(`${BASE}${path}`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1500);
  await page.screenshot({ path: `E:/AAAA/A-xiaosou/vaclead/scripts/${name}.png`, fullPage: false });
  console.log("shot", name);
}
await browser.close();
console.log("DONE");
