// 广西 detail 最后一公里 v2：用 searchHome 真实标题定位 DOM 元素点击（用后即删）
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
  if (res.request().resourceType() === "xhr" && /(detail|Detail|article|Article|notice|content)/i.test(u)) {
    let body = "";
    try { body = (await res.text()).slice(0, 400); } catch { body = "<x>"; }
    xhrs.push({ resp: u.slice(0, 260), body });
  }
});

let targetTitle = "";
try {
  // 先 API 拿真实意向标题
  const lr = await page.request.post("https://zfcg.gxzf.gov.cn/portal/searchHome", {
    headers: { "Content-Type": "application/json", "Referer": "https://zfcg.gxzf.gov.cn/" },
    data: { code: "61-266648", subCodes: ["ZcyAnnouncement10016"], isProvince: true,
            districtCode: "459900", pageSize: 3, needTotal: true, needValidCount: false, needNewCnt: true },
  });
  const ld = await lr.json();
  const recs = ((((ld.get ? ld.get("result") : (ld.result || {})) || {}).data || {}).children) || [];
  targetTitle = recs[0]?.title?.slice(0, 25) || "";
  console.log("目标标题:", targetTitle);
} catch (e) {
  console.log("API ERR:", String(e).slice(0, 100));
}

try {
  await page.goto("https://zfcg.gxzf.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(9000);
  xhrs.length = 0;
  if (targetTitle) {
    // 用标题前 15 字定位 DOM 元素
    const el = page.locator(`text=${targetTitle.slice(0, 15)}`).first();
    await el.click({ timeout: 12000 });
    await page.waitForTimeout(7000);
    console.log("点击后 URL:", page.url().slice(0, 160));
  } else {
    console.log("无目标标题——退化为点第一个带意向的元素");
    await page.locator("text=意向").first().click({ timeout: 8000 }).catch(() => {});
    await page.waitForTimeout(6000);
    console.log("点击后 URL:", page.url().slice(0, 160));
  }
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
