// 山东政采详情页 getNoticeBody 真实请求捕获（用后即删）
// 流程：列表页拿一条公告 id → 进详情页 → 捕获 XHR 完整形态
import { createRequire } from "node:module";
const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
const xhrs = [];
page.on("request", (req) => {
  if (req.resourceType() === "xhr" || req.resourceType() === "fetch") {
    xhrs.push({
      m: req.method(),
      u: req.url().slice(0, 260),
      headers: Object.fromEntries(Object.entries(req.headers()).filter(([k]) => /token|auth|captcha|sign/i.test(k))),
      post: (req.postData() || "").slice(0, 300),
    });
  }
});
page.on("response", async (res) => {
  const u = res.url();
  if (res.request().resourceType() === "xhr" && /NoticeBody|detail|getDetail/i.test(u)) {
    let body = "";
    try { body = (await res.text()).slice(0, 600); } catch { body = "<x>"; }
    xhrs.push({ resp: u.slice(0, 260), body });
  }
});

try {
  // 1. 先从我们自己的 API 拿一条真实 notice id
  const login = await page.request.post("http://127.0.0.1:8000/api/auth/login", {
    data: { username: "admin", password: "admin123" },
  });
  const token = (await login.json()).token;
  // 直接用列表接口取一条 0301 公告 id
  const listResp = await page.request.post(
    "https://www.ccgp-shandong.gov.cn:8087/api/website/site/getListByCode",
    { data: { colCode: "0301", area: "370000", currentPage: 1, pageSize: 3, homePage: 0, mergeType: 0, title: "真空" } },
  );
  const ld = await listResp.json().catch(() => null);
  console.log("list resp 形态:", typeof ld, String(JSON.stringify(ld)).slice(0, 120));
  const rec = ((((ld || {}).data || {}).data || {}).records || [])[0];
  console.log("样本公告:", rec ? rec.title.slice(0, 40) : "无");
  const noticeId = rec ? rec.id : "";
  console.log("notice id:", String(noticeId).slice(0, 40));

  // 2. 进详情页（SPA 路由 /detail?id=xxx 形态——从列表页点入最真实）
  await page.goto("https://www.ccgp-shandong.gov.cn/", { waitUntil: "commit", timeout: 40000 });
  await page.waitForTimeout(4000);
  xhrs.length = 0;
  // 搜索到目标公告再点入：直接访问搜索页带 title
  await page.goto(
    `https://www.ccgp-shandong.gov.cn/search?title=${encodeURIComponent("真空")}&colCode=0301&area=370000`,
    { waitUntil: "commit", timeout: 40000 },
  );
  await page.waitForTimeout(6000);
  xhrs.length = 0;
  // 点第一条公告链接
  const link = page.locator("a").filter({ hasText: /采购|招标|公告/ }).first();
  await link.click({ timeout: 12000 });
  await page.waitForTimeout(6000);
  console.log("详情 URL:", page.url().slice(0, 150));
} catch (e) {
  console.log("ERR:", String(e).slice(0, 150));
}
console.log("==== 捕获 XHR ====");
for (const x of xhrs.slice(0, 12)) {
  console.log(x.m || "RESP", x.u || x.resp);
  if (x.post) console.log("  POST:", x.post);
  if (x.headers && Object.keys(x.headers).length) console.log("  AUTH-HEADERS:", JSON.stringify(x.headers).slice(0, 200));
  if (x.body) console.log("  BODY:", x.body.replace(/\s+/g, " ").slice(0, 400));
}
await browser.close();
console.log("DONE");
