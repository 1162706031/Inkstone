import { expect, test } from "@playwright/test";

test("logout clears the workspace and allows another account to sign in", async ({ page }) => {
  let account: string | null = "原管理员";
  const unauthorizedRequests: string[] = [];
  await page.route("**/api/v1/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    switch (path) {
      case "/api/v1/auth/csrf":
        return route.fulfill({ json: { csrf_token: "fixture" } });
      case "/api/v1/auth/logout":
        account = null;
        return route.fulfill({ status: 204 });
      case "/api/v1/auth/login":
        account = "新成员";
        return route.fulfill({ json: { next: "ready" } });
      case "/api/v1/auth/me":
        if (account) return route.fulfill({ json: {
          user: { display_name: account, role: account === "原管理员" ? "admin" : "member" }, workspace_id: account,
        } });
        return route.fulfill({ status: 401, json: {} });
      default:
        if (account) return route.fulfill({ json: { items: [], next_cursor: null } });
        unauthorizedRequests.push(path);
        return route.fulfill({ status: 401, json: {} });
    }
  });
  await page.goto("/settings/security");
  await page.getByRole("link", { name: "对话", exact: true }).click();
  await expect(page.getByRole("heading", { name: "选择智能体开始对话" })).toBeVisible();
  await page.getByRole("button", { name: "账户菜单" }).click();
  await page.getByText("退出登录", { exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(page.getByLabel("登录名")).toBeVisible();
  await page.goBack();
  await expect(page.getByLabel("登录名")).toBeVisible();
  await expect(page.getByRole("button", { name: "账户菜单" })).toHaveCount(0);
  expect(unauthorizedRequests).toEqual([]);
  await page.getByLabel("登录名").fill("member");
  await page.getByLabel("密码", { exact: true }).fill("fixture-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "账户菜单" })).toContainText("新成员");
  await expect(page.getByRole("link", { name: "成员管理" })).toHaveCount(0);
});
