import { expect, test } from "@playwright/test";

test("member sees unknown pricing and no administrator controls", async ({ page }) => {
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({
    json: { user: { role: "member", display_name: "测试成员" }, workspace_id: "fixture" },
  }));
  await page.route("**/api/v1/usage/summary", (route) => route.fulfill({
    json: { mode: "soft", token_limit: 100, confirmed_tokens: 7, reserved_tokens: 0,
      remaining_tokens: 45, unknown_attempts: 1, uncertain_reserved_tokens: 48, unpriced_attempts: 2 },
  }));
  await page.route("**/api/v1/usage/attempts", (route) => route.fulfill({
    json: { items: [{ id: "attempt", purpose: "connection_test", state: "completed",
      input_tokens: null, output_tokens: null, cost: null }], next_cursor: null },
  }));
  await page.goto("/usage");
  await expect(page.getByText("费用未知 · 2 次未定价")).toBeVisible();
  await expect(page.getByRole("cell", { name: "未知", exact: true })).toHaveCount(3);
  await expect(page.getByRole("link", { name: "模型配置" })).toHaveCount(0);
});
