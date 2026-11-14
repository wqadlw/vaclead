// 销售一日闭环走查 v2：修正选择器（contacts 行=div 卡片/驾驶舱=Modal/快标=CallResultTag
// 下拉「已接通」/转为线索/设置页 Semi Tabs 隐藏面板劫持 .first()——一律作用域限定）
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";

const require2 = createRequire("E:/AAAA/A-xiaosou/vaclead/frontend/package.json");
const { chromium } = require2("playwright");

const BASE = "http://127.0.0.1:8000";
const OUT = "E:/AAAA/A-xiaosou/vaclead/scripts/flow";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ headless: true });
const ctx = await browser.newContext({
  viewport: { width: 1440, height: 900 },
  permissions: ["clipboard-read", "clipboard-write"],
});
const page = await ctx.newPage();

const steps = [];
async function step(name, fn) {
  try {
    await fn();
    steps.push({ name, ok: true });
    console.log("PASS", name);
  } catch (e) {
    steps.push({ name, ok: false, err: String(e).slice(0, 300) });
    console.log("FAIL", name, String(e).slice(0, 200));
    await page.screenshot({ path: path.join(OUT, `FAIL-${name}.png`), fullPage: true }).catch(() => {});
  }
}
const shot = (n, full = false) => page.screenshot({ path: path.join(OUT, `${n}.png`), fullPage: full });

// 1. 登录
await step("login", async () => {
  await page.goto(`${BASE}/login`, { waitUntil: "domcontentloaded" });
  await page.evaluate(async () => {
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username: "admin", password: "admin123" }),
    });
    const d = await r.json();
    localStorage.setItem("vaclead_token", d.token);
    localStorage.setItem("vaclead_role", "admin");
    localStorage.setItem("vaclead_user", "管理员");
  });
});

// 2. 工作台
await step("home-晨检推荐", async () => {
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2200);
  const body = await page.content();
  for (const kw of ["今日行动", "今日推荐", "最新信号"]) {
    if (!body.includes(kw)) throw new Error(`工作台缺「${kw}」`);
  }
  await shot("01-home");
});

// 3. 联系人中心 → 行卡片点击 → 驾驶舱 Modal
await step("contacts-驾驶舱打开", async () => {
  await page.goto(`${BASE}/contacts`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2400);
  const row = page.locator("div.cursor-pointer.rounded-lg").first();
  await row.click({ timeout: 8000 });
  await page.waitForTimeout(1200);
  const body = await page.content();
  if (!body.includes("标记结果")) throw new Error("驾驶舱 Modal 未打开（无标记结果区）");
  await shot("02-cockpit");
});

// 4. 话术卡区
await step("cockpit-话术卡", async () => {
  const body = await page.content();
  if (!body.includes("话术")) throw new Error("驾驶舱无「话术」区");
  if (!body.includes("官方授权开场")) throw new Error("话术种子卡未出现");
  await page.locator('text=首联·官方授权开场').first().click({ timeout: 4000 });
  await page.waitForTimeout(400);
  await shot("03-cockpit-script");
});

// 5. 话术卡一键复制（aria-label 定位，防同名劫持）
await step("cockpit-话术复制", async () => {
  const btn = page.locator('button[aria-label^="复制话术"]').first();
  await btn.click({ timeout: 5000 });
  await page.waitForTimeout(500);
  const clip = await page.evaluate(() => navigator.clipboard.readText());
  if (!clip || clip.length < 20) throw new Error(`剪贴板空或过短: len=${clip.length}`);
});

// 6. 快标接通（CallResultTag 下拉）
await step("cockpit-快标接通", async () => {
  await page.locator("text=标记结果").first().waitFor({ timeout: 4000 });
  // CallResultTag 的 Tag 本体——取「打完之后」区里的 Tag 点击
  await page.locator(".semi-tag").filter({ hasText: /未拨|待拨|pending/ }).first()
    .click({ timeout: 4000 }).catch(async () => {
      // 找不到状态标签时退化为点击标记结果行内第二个元素
      await page.locator('text=标记结果').locator("..").locator(".semi-tag").first().click({ timeout: 3000 });
    });
  await page.waitForTimeout(600);
  await page.locator('text=已接通').first().click({ timeout: 4000 });
  await page.waitForTimeout(900);
  await shot("04-cockpit-marked");
});

// 7. 转为线索（仅当该联系人还没线索；有线索则验证记跟进区）
await step("cockpit-转线索或跟进", async () => {
  const body = await page.content();
  if (body.includes("转为线索")) {
    await page.locator('button:has-text("转为线索")').first().click({ timeout: 5000 });
    await page.waitForTimeout(1500);
    const b2 = await page.content();
    if (!b2.includes("记跟进")) throw new Error("转线索后未切到跟进模式");
  } else if (body.includes("记跟进")) {
    await page.locator("textarea").first().fill("走查测试：闭环验证跟进记录");
    await page.locator('button:has-text("记跟进")').first().click({ timeout: 5000 });
    await page.waitForTimeout(1200);
  } else {
    throw new Error("驾驶舱既无转为线索也无记跟进");
  }
  await shot("05-cockpit-converted");
});

// 8. 关闭驾驶舱 → 线索看板
await step("leads-看板确认", async () => {
  await page.keyboard.press("Escape");
  await page.waitForTimeout(600);
  await page.goto(`${BASE}/leads`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2000);
  const body = await page.content();
  if (!body.includes("线索")) throw new Error("看板未渲染");
  await shot("06-leads");
});

// 9. 标讯中心转线索
await step("signals-转线索", async () => {
  await page.goto(`${BASE}/signals`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2400);
  const btn = page.locator('button:has-text("转线索"), a:has-text("转线索")').first();
  await btn.click({ timeout: 6000 });
  await page.waitForTimeout(1400);
  await shot("07-signals-convert");
});

// 10. 设置页话术库 CRUD 闭环（作用域限定防 Tabs 隐藏面板劫持）
const newCardTitle = `走查测试卡-${Date.now() % 100000}`;
await step("settings-话术库打开", async () => {
  await page.goto(`${BASE}/settings`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1800);
  await page.locator('.semi-tabs-tab', { hasText: "话术库" }).first().click({ timeout: 6000 });
  await page.waitForTimeout(1200);
  if (!(await page.content()).includes("首联")) throw new Error("话术库列表未渲染");
  await shot("08-settings-scripts");
});
await step("settings-话术新建", async () => {
  await page.locator('button:has-text("+ 新建话术")').first().click({ timeout: 6000 });
  await page.locator(".semi-modal").waitFor({ timeout: 5000 });
  await page.locator(".semi-modal input").first().fill(newCardTitle);
  await page.locator(".semi-modal textarea").first().fill("走查自动测试卡正文——验证 CRUD 闭环后即删除。");
  await page.locator('.semi-modal button:has-text("保存")').first().click({ timeout: 6000 });
  await page.waitForTimeout(1600);
  if (!(await page.content()).includes(newCardTitle)) throw new Error("新建后列表未见新卡");
  await shot("09-settings-created");
});
await step("settings-话术删除清理", async () => {
  const row = page.locator('.semi-table-row, tr', { hasText: newCardTitle }).first();
  await row.locator('button:has-text("删")').first().click({ timeout: 6000 });
  // 确认弹窗的确认键是红色「删除」（非「确定」）
  await page.locator('.semi-modal button:has-text("删除")').first().click({ timeout: 5000 });
  await page.waitForTimeout(1500);
  if ((await page.content()).includes(newCardTitle)) throw new Error("删除后仍可见");
  await shot("10-settings-cleaned");
});

// 11. 命令面板 → 大屏入口
await step("cmdk-大屏入口", async () => {
  await page.goto(`${BASE}/`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1600);
  await page.keyboard.press("ControlOrMeta+k");
  await page.waitForTimeout(600);
  await page.keyboard.type("大屏");
  await page.waitForTimeout(700);
  if (!(await page.content()).includes("数据大屏")) throw new Error("命令面板无数据大屏入口");
  await page.keyboard.press("Escape");
});

// 12. 大屏沉浸渲染
await step("screen-大屏渲染", async () => {
  await page.goto(`${BASE}/screen`, { waitUntil: "domcontentloaded" });
  await page.waitForTimeout(2800);
  const body = await page.content();
  if (!body.includes("驾驶舱")) throw new Error("大屏标题未渲染");
  if ((await page.locator("aside, nav").count()) > 0) throw new Error("大屏未沉浸（侧栏仍在）");
  await shot("12-screen");
});

// 13. prep 话术卡 API 稳定
await step("prep-稳定", async () => {
  const d = await page.evaluate(async () => {
    const t = localStorage.getItem("vaclead_token");
    const r = await fetch("/api/contacts?has_phone=1&page=1&page_size=1", {
      headers: { Authorization: `Bearer ${t}` },
    });
    const list = await r.json();
    const cid = list.items?.[0]?.id;
    const r2 = await fetch(`/api/contacts/${cid}/prep`, {
      headers: { Authorization: `Bearer ${t}` },
    });
    return { n: (await r2.json()).script_cards?.length ?? -1 };
  });
  if (d.n < 1) throw new Error(`prep 话术卡数异常: ${d.n}`);
});

await browser.close();
fs.writeFileSync(path.join(OUT, "steps.json"), JSON.stringify(steps, null, 2));
const fails = steps.filter((s) => !s.ok);
console.log(`==== ${steps.length - fails.length}/${steps.length} PASS ====`);
for (const f of fails) console.log("FAILED:", f.name, f.err || "");
