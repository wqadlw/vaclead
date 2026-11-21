// 广西全站搜索→采购公告详情捕获（用后即删）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const xhrs = [];
page.on("request", (req) => {
  if (req.resourceType() === "xhr" || req.resourceType() === "fetch") {
    xhrs.push({ m: req.method(), u: req.url().slice(0, 260), post: (req.postData() || "").slice(0, 300) });
  }
});
page.on("response", async (res) => {
  const u = res.url();
  if (res.request().resourceType() === "xhr" && /(detail|Detail|article|content|Body)/i.test(u)) {
    let body = "";
    try { body = (await res.text()).slice(0, 400); } catch { body = "<x>"; }
    xhrs.push({ resp: u.slice(0, 260), body });
  }
});

try {
  await page.goto("https://zfcg.gxzf.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(8000);
  await page.locator('button:has-text("我知道了")').click({ timeout: 4000 }).catch(() => {});
  xhrs.length = 0;
  // 搜索框填"真空镀膜"点全站搜索
  await page.locator('input[placeholder*="关键字"], input[type="text"]').first().fill("真空镀膜", { timeout: 8000 });
  await page.locator('button:has-text("全站搜索"), a:has-text("全站搜索")').first().click({ timeout: 8000 });
  await page.waitForTimeout(6000);
  console.log("搜索结果页:", page.url().slice(0, 150));
  // 点第一条结果
  const item = page.locator("a").filter({ hasText: /真空/ }).first();
  await item.click({ timeout: 10000 });
  await page.waitForTimeout(7000);
  console.log("详情页:", page.url().slice(0, 160));
} catch (e) {
  console.log("ERR:", String(e).slice(0, 140));
}
console.log("==== XHR ====");
for (const x of xhrs.slice(0, 12)) {
  console.log(x.m || "RESP", x.u || x.resp);
  if (x.post) console.log("  POST:", x.post);
  if (x.body) console.log("  BODY:", x.body.replace(/\s+/g, " ").slice(0, 350));
}
await browser.close();
console.log("DONE");
