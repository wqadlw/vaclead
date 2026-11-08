import { expect, test } from "@playwright/test";

/**
 * 移动视口回归（SPEC-CB）：390×844（iPhone 13 级）——抽屉式侧栏。
 * 单 worker 与桌面套件共享种子（playwright.config workers=1）。
 */

test.use({ viewport: { width: 390, height: 844 } });

async function login(page: import("@playwright/test").Page) {
  await page.goto("/login");
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
}

test("移动端：侧栏抽屉化且日活页可用", async ({ page }) => {
  await login(page);
  await page.goto("/contacts");
  await expect(page.getByText("今日拨打名单")).toBeVisible({ timeout: 15000 });

  // 侧栏初始在屏外（抽屉收起），汉堡按钮可见
  const asideBox = await page.locator("aside").boundingBox();
  expect(asideBox).not.toBeNull();
  expect(asideBox!.x).toBeLessThan(0);
  const burger = page.getByTitle("菜单");
  await expect(burger).toBeVisible();

  // 打开抽屉 → 导航可见 → 点导航项 → 抽屉关闭
  await burger.click();
  await expect(page.locator("aside")).toHaveCSS("transform", /matrix\(1, 0, 0, 1, 0, 0\)/);
  await page.getByRole("button", { name: "竞对雷达" }).first().click();
  await expect(page).toHaveURL(/competitors/);
  const asideAfter = await page.locator("aside").boundingBox();
  expect(asideAfter!.x).toBeLessThan(0); // 抽屉随导航自动收起

  // tel: 拨号链路在移动 DOM 存在（回联系人中心验证）
  await page.goto("/contacts");
  await expect(page.locator('a[href^="tel:"]').first()).toHaveAttribute("href", /tel:/);
});

// SPEC-CT：底部导航栏 + 顶栏精简
const TABBAR = "[data-mobile-tabbar]";

test("移动端：底部导航栏 5 tab 且 active 高亮", async ({ page }) => {
  await login(page);
  await page.goto("/");
  const tabbar = page.locator(TABBAR);
  await expect(tabbar).toBeVisible({ timeout: 15000 });
  // 5 个 tab：今日 / 联系人 / 地图 / 线索 / 更多
  await expect(tabbar.locator("button")).toHaveCount(5);
  for (const t of ["/", "/contacts", "/map", "/leads", "more"]) {
    await expect(tabbar.locator(`[data-btab="${t}"]`)).toBeVisible();
  }
  // active 态：当前路由（今日 /）accent 高亮加粗，非 active 不加粗
  await expect(tabbar.locator('[data-btab="/"]')).toHaveClass(/font-semibold/);
  await expect(tabbar.locator('[data-btab="/contacts"]')).not.toHaveClass(/font-semibold/);
  // 顶栏精简（高德/企查查式）：<md 无搜索文字钮、无 ? 快捷键钮、无工作区选择器；
  // 搜索换为圆角搜索条（title="全局搜索"），主题切换收进抽屉
  await expect(page.getByRole("button", { name: "搜索 Ctrl K" })).toBeHidden();
  await expect(page.locator('header button[title="全局搜索"]')).toBeVisible();
  await expect(page.locator('header button[title="键盘快捷键（?）"]')).toBeHidden();
  await expect(page.getByText("默认工作区 / 全国")).toBeHidden();
  await expect(page.getByText("搜企业 / 联系人 / 标讯")).toBeVisible();
});

test("移动端：底部导航点联系人跳 /contacts", async ({ page }) => {
  await login(page);
  await page.goto("/");
  const tabbar = page.locator(TABBAR);
  await expect(tabbar).toBeVisible({ timeout: 15000 });
  await tabbar.locator('[data-btab="/contacts"]').click();
  await expect(page).toHaveURL(/\/contacts$/);
  await expect(page.getByText("今日拨打名单")).toBeVisible({ timeout: 15000 });
  // active 随路由迁移
  await expect(tabbar.locator('[data-btab="/contacts"]')).toHaveClass(/font-semibold/);
});

test("移动端：底部导航点更多打开抽屉", async ({ page }) => {
  await login(page);
  await page.goto("/");
  const tabbar = page.locator(TABBAR);
  await expect(tabbar).toBeVisible({ timeout: 15000 });
  await tabbar.locator('[data-btab="more"]').click();
  // 抽屉滑入（translate-x-0）且点背板后收起（回到 -translate-x-full）
  // 注意：工作台页内容里还有 aside（最新信号卡片），此处限定 AppShell 抽屉 aside.z-40
  const drawer = page.locator("aside.z-40");
  await expect(drawer).toHaveCSS("transform", /matrix\(1, 0, 0, 1, 0, 0\)/);
  await page.locator("div.fixed.inset-0.z-30").click({ position: { x: 380, y: 100 } });
  await expect(drawer).toHaveCSS("transform", /matrix\(1, 0, 0, 1, -228, 0\)/);
});

test.describe("桌面视口（SPEC-CT 边界：md+ 零改动）", () => {
  test.use({ viewport: { width: 1440, height: 900 } });
  test("桌面端：底部导航不可见", async ({ page }) => {
    await login(page);
    await page.goto("/");
    await expect(page.locator(TABBAR)).toBeHidden();
    // 桌面顶栏仍保留文字搜索钮
    await expect(page.getByRole("button", { name: /搜索/ })).toBeVisible();
  });
});
