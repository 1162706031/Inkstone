import { expect, test } from "@playwright/test";

test("shows the API connection status", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("status")).toHaveText("API 已连接");
});
