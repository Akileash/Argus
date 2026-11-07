import { createClient } from "@supabase/supabase-js";
import { expect, test } from "@playwright/test";

/**
 * A signed-in user with no profiles row must not loop between / and /login: the login page explains the problem
 * and offers sign-out. Needs local Supabase + service role key (scripts/web-e2e.sh sets them).
 */
const EMAIL = "noprofile-e2e@argus.local";
const PASSWORD = "argus-noprofile-2026";
const admin = () => createClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.SUPABASE_SERVICE_ROLE_KEY!, { auth: { persistSession: false } });

test.describe("signed in without a profile", () => {
  let userId: string | undefined;

  test.beforeAll(async () => {
    const { data, error } = await admin().auth.admin.createUser({ email: EMAIL, password: PASSWORD, email_confirm: true });
    if (error) throw error;
    userId = data.user.id;
  });
  test.afterAll(async () => {
    if (userId) await admin().auth.admin.deleteUser(userId);
  });

  test("sees a clear message instead of a redirect loop, and can sign out", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/email/i).fill(EMAIL);
    await page.getByLabel(/password/i).fill(PASSWORD);
    await page.getByRole("button", { name: /sign in/i }).click();
    await expect(page.getByTestId("no-profile")).toContainText("Your account has no Argus profile");
    await expect(page).toHaveURL(/\/login/);
    // the app and the login page both settle here
    await page.goto("/");
    await expect(page.getByTestId("no-profile")).toBeVisible();
    await page.goto("/login");
    await expect(page.getByTestId("no-profile")).toBeVisible();

    await page.getByTestId("no-profile-signout").click();
    await expect(page.getByRole("heading", { name: "Sign in" })).toBeVisible();
    await expect(page.getByTestId("no-profile")).toHaveCount(0);
  });
});
