import { test, expect, type Page } from "@playwright/test";

/** 关键交互回归（SPEC-H H3）：行内编辑 / 视图保存 / 铃铛 / 汇总条。 */

async function login(page: Page) {
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

test.beforeEach(async ({ page }) => {
  await login(page);
});

test("行内编辑注册资本", async ({ page }) => {
  await page.goto("/enterprises");
  // 找含长晶的行的资本格（title=点击编辑）
  const cell = page.locator('span[title="点击编辑"]').first();
  await cell.scrollIntoViewIfNeeded();
  await cell.click();
  const input = page.locator(".inline-cell input").first();
  await expect(input).toBeVisible();
  await input.fill("2500");
  await input.press("Enter");
  await expect(page.getByText("已更新")).toBeVisible();
});

test("汇总条渲染本页统计", async ({ page }) => {
  await page.goto("/enterprises");
  await expect(page.getByText(/本页 \d+ 家/)).toBeVisible();
  await expect(page.getByText(/注册资本合计/)).toBeVisible();
});

test("视图保存与应用", async ({ page }) => {
  await page.goto("/enterprises");
  const box = page.getByPlaceholder("当前筛选存为视图…");
  await box.fill("E2E视图");
  await page.getByRole("button", { name: /保存/, exact: true }).click();
  await expect(page.getByText("已保存")).toBeVisible();
  await expect(page.getByText("E2E视图", { exact: true })).toBeVisible();
});

test("通知铃铛存在且可点开", async ({ page }) => {
  await page.goto("/");
  // 铃铛图标（svg）可点击，点开下拉面板
  const bell = page.locator('header span[class*="cursor-pointer"]').first();
  await bell.click();
  await expect(page.getByText(/订阅动态|暂无动态/).first()).toBeVisible();
});

test("高级筛选构建器打开并执行", async ({ page }) => {
  await page.goto("/enterprises");
  await page.getByRole("button", { name: "高级筛选" }).click();
  await expect(page.getByText("条件组合")).toBeVisible();
  await page.getByRole("button", { name: /执行筛选/ }).click();
  await expect(page.getByText(/命中 \d+ 家/)).toBeVisible();
});
