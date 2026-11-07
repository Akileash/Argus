import { expect, test } from "@playwright/test";

// Placeholder smoke spec; the PM expands e2e coverage later.
test("unauthenticated visit redirects to /login", async ({ page }) => {
  await page.goto("/");
  await expect(page).toHaveURL(/\/login/);
  await expect(page.getByRole("button", { name: /sign in/i })).toBeVisible();
});
