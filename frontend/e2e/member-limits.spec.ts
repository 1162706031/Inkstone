import { expect, test } from "@playwright/test";

test("admin saves independent member limits and clears one override", async ({ page }) => {
  const members = ["甲", "乙"].map((name, i) => ({
    id: String(i + 1), display_name: name, username: `user${i}`, role: "member", status: "active",
  }));
  const policies = members.map((member) => ({
    workspace_id: member.id, revision: 1, mode: "soft", monthly_token_limit: 1000000,
    requests_per_minute: null as number | null, max_concurrent: null as number | null,
    default_requests_per_minute: 20, default_max_concurrent: 2, global_max_concurrent: 4,
  }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    workspace_id: "admin", user: { role: "admin", display_name: "管理员" },
  } }));
  await page.route("**/api/v1/auth/csrf", (route) => route.fulfill({ json: { csrf_token: "fixture" } }));
  await page.route("**/api/v1/admin/users", (route) => route.fulfill({ json: { items: members, next_cursor: null } }));
  await page.route("**/api/v1/admin/users/*/quota", (route) => {
    const id = new URL(route.request().url()).pathname.split("/").at(-2);
    const policy = policies.find((p) => p.workspace_id === id);
    if (!policy) return route.abort();
    if (route.request().method() === "PATCH") {
      const patch = route.request().postDataJSON();
      expect(patch.revision).toBe(policy.revision);
      Object.assign(policy, patch, { revision: policy.revision + 1 });
    }
    return route.fulfill({ json: policy });
  });
  await page.goto("/settings/security");
  await page.getByRole("link", { name: "成员管理" }).click();
  const open = (name: string) => page.getByRole("row").filter({ hasText: name }).getByRole("button", { name: "额度与限流" }).click();
  const rpm = page.getByRole("spinbutton", { name: "每分钟模型调用上限" });
  const concurrent = page.getByRole("spinbutton", { name: "同时进行的模型调用上限" });
  await open("甲");
  await rpm.fill("60");
  await concurrent.fill("3");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("额度与限流已保存")).toBeVisible();
  await page.keyboard.press("Escape");
  await open("乙");
  await expect(rpm).toHaveValue("");
  await expect(concurrent).toHaveAttribute("placeholder", "默认 2");
  await page.keyboard.press("Escape");
  await open("甲");
  await expect(rpm).toHaveValue("60");
  await concurrent.fill("");
  await page.getByRole("button", { name: "保存设置" }).click();
  await expect(page.getByText("额度与限流已保存")).toBeVisible();
  expect(policies[0]).toMatchObject({ requests_per_minute: 60, max_concurrent: null });
});
