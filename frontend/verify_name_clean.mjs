// 验证名字清洗（渲染产物执行，一次性）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage();
await page.goto("http://127.0.0.1:8000/login", { waitUntil: "domcontentloaded" });
await page.evaluate(async () => {
  const r = await fetch("/api/auth/login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ username: "admin", password: "admin123" }) });
  const d = await r.json();
  localStorage.setItem("vaclead_token", d.token);
  localStorage.setItem("vaclead_role", "admin");
  localStorage.setItem("vaclead_user", "管理员");
});
await page.goto("http://127.0.0.1:8000/enterprises/view?id=13", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(1500);
await page.locator('button:has-text("搬运书签")').first().click();
await page.waitForTimeout(500);
const href = await page.locator('div[role="dialog"] a[href^="javascript:"]').first().getAttribute("href");
const js = decodeURIComponent(href.replace("javascript:", ""));
await browser.close();

const NL = String.fromCharCode(10);
global.document = {
  body: { innerText: ["合肥华润三九医药有限公司", "法定代表人 王三风", "注册资本 50000万人民币", "成立日期", "2002-01-28", "企业电话 0755-83312345"].join(NL) },
  title: "合肥华润三九医药有限公司 - 工商信息查询 – 风鸟企业查询平台",
  location: { href: "" },
};
global.alert = (m) => global.__calls.push("alert:" + m);
global.__calls = [];
try { new Function(js)(); console.log("EXEC OK"); } catch (e) { console.log("EXEC ERROR:", e.message); }
const nav = global.document.location.href;
console.log("NAV name:", decodeURIComponent((nav.split("name=")[1] || "?").split("&gongshang")[0]));
