// 广西 detail v3：关弹窗 → 采购意向公开入口 → 列表 → 点公告 → 全链捕获（用后即删）
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const xhrs = [];
page.on("request", (req) => {
  if (req.resourceType() === "xhr" || req.resourceType() === "fetch") {
    xhrs.push({ m: req.method(), u: req.url().slice(0, 260), post: (req.postData() || "").slice(0, 400) });
  }
});
page.on("response", async (res) => {
  const u = res.url();
  if (res.request().resourceType() === "xhr" && /(detail|Detail|article|Article|content|Body)/i.test(u)) {
    let body = "";
    try { body = (await res.text()).slice(0, 400); } catch { body = "<x>"; }
    xhrs.push({ resp: u.slice(0, 260), body });
  }
});

try {
  await page.goto("https://zfcg.gxzf.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(8000);
  // 关弹窗
  await page.locator('button:has-text("我知道了")').click({ timeout: 5000 }).catch(() => {});
  await page.waitForTimeout(1000);
  xhrs.length = 0;
  // 进"采购意向公开"入口
  await page.locator("text=采购意向公开").first().click({ timeout: 10000 });
  await page.waitForTimeout(6000);
  console.log("列表页 URL:", page.url().slice(0, 150));
  // 点第一条公告（意向列表第一行）
  const item = page.locator("a, .list-item, tr").filter({ hasText: /意向/ }).first();
  await item.click({ timeout: 10000 });
  await page.waitForTimeout(7000);
  console.log("详情页 URL:", page.url().slice(0, 160));
} catch (e) {
  console.log("ERR:", String(e).slice(0, 140));
}
console.log("==== XHR ====");
for (const x of xhrs.slice(0, 14)) {
  console.log(x.m || "RESP", x.u || x.resp);
  if (x.post) console.log("  POST:", x.post);
  if (x.body) console.log("  BODY:", x.body.replace(/\s+/g, " ").slice(0, 350));
}
await browser.close();
console.log("DONE");
