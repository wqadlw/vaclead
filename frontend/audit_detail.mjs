// 详情页移动端审计：头部按钮组换行/KPI 网格/页签滚动（一次性）
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
await page.goto("http://127.0.0.1:8000/enterprises/view?id=13", { waitUntil: "domcontentloaded" });
await page.waitForTimeout(2000);

const result = await page.evaluate(() => {
  const sw = document.body.scrollWidth;
  // 头部按钮组：数量与布局
  const btns = [...document.querySelectorAll("button, a")].filter(
    (e) => ["加入对比", "关注", "转为线索", "生成节能账"].some((t) => (e.textContent || "").includes(t)),
  ).length;
  // 页签是否横向溢出
  const tabs = document.querySelector('[role="tablist"]');
  const tabsOverflow = tabs ? tabs.scrollWidth > tabs.clientWidth : false;
  // 页面级溢出元素
  let worst = "";
  let worstW = 0;
  for (const el of document.querySelectorAll("*")) {
    const w = el.scrollWidth;
    if (w > worstW && w > 400) { worstW = w; worst = `${el.tagName}.${String(el.className).slice(0, 50)}`; }
  }
  return { sw, btns, tabsOverflow, worst, worstW };
});
console.log(JSON.stringify(result, null, 1));
await page.screenshot({ path: "E:/AAAA/A-xiaosou/vaclead/scripts/m-detail-top.png" });
await browser.close();
console.log("DONE");
