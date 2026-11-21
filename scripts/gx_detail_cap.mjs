// 广西意向公告详情 XHR 捕获（用后即删）：首页 → 点意向 → 全 XHR
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
  if (res.request().resourceType() === "xhr" && /(detail|Detail|article|Article|notice|Notice)/i.test(u)) {
    let body = "";
    try { body = (await res.text()).slice(0, 400); } catch { body = "<x>"; }
    xhrs.push({ resp: u.slice(0, 260), body });
  }
});

try {
  await page.goto("https://zfcg.gxzf.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(8000);
  xhrs.length = 0;
  // 找"意向公开"区块的公告链接点击
  const link = page.locator("a").filter({ hasText: /采购意向/ }).first();
  await link.click({ timeout: 10000 });
  await page.waitForTimeout(7000);
  console.log("详情 URL:", page.url().slice(0, 160));
} catch (e) {
  console.log("ERR:", String(e).slice(0, 140));
}
console.log("==== 详情 XHR ====");
for (const x of xhrs.slice(0, 12)) {
  console.log(x.m || "RESP", x.u || x.resp);
  if (x.post) console.log("  POST:", x.post);
  if (x.body) console.log("  BODY:", x.body.replace(/\s+/g, " ").slice(0, 350));
}
await browser.close();
console.log("DONE");
