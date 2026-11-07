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
