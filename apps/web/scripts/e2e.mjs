// End-to-end checks against a running web server and API: forms, login with and without
// multi-factor authentication, language switch and text direction, keyboard use of the menu.
import { chromium } from "@playwright/test";
import { createHmac } from "node:crypto";
import assert from "node:assert/strict";

const base = process.env.BASE_URL ?? "http://127.0.0.1:3000";
const password = process.env.E2E_PASSWORD;
assert(password, "set E2E_PASSWORD");

function totp(secret) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567";
  let bits = "";
  for (const ch of secret.replace(/=+$/, ""))
    bits += alphabet.indexOf(ch).toString(2).padStart(5, "0");
  const key = Buffer.from(bits.match(/.{8}/g).map((b) => parseInt(b, 2)));
  const counter = Buffer.alloc(8);
  counter.writeBigUInt64BE(BigInt(Math.floor(Date.now() / 30000)));
  const h = createHmac("sha1", key).update(counter).digest();
  const o = h[h.length - 1] & 15;
  const n = ((h[o] & 127) << 24) | (h[o + 1] << 16) | (h[o + 2] << 8) | h[o + 3];
  return String(n % 1_000_000).padStart(6, "0");
}

const browser = await chromium.launch();
const results = [];
const check = async (name, fn) => {
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  await context.addInitScript(() => localStorage.setItem("atlas_cookie_notice", "1"));
  const page = await context.newPage();
  try {
    await fn(page);
    results.push([name, "pass"]);
  } catch (e) {
    results.push([name, `FAIL ${e.message.split("\n")[0]}`]);
  }
  await context.close();
};

await check("contact form validates then submits", async (page) => {
  await page.goto(`${base}/contact`);
  await page.getByRole("button", { name: "Request a demo" }).click();
  await page.getByText("Enter your name.").waitFor();
  await page.getByLabel("Name").fill("Sam Reviewer");
  await page.getByLabel("Work email").fill("sam.reviewer@example.org");
  await page.getByLabel("Organization").fill("Example Bank");
  await page.getByLabel("Role").fill("Head of Compliance");
  await page.getByLabel("Industry").selectOption("Banking");
  await page.getByLabel("How can we help?").fill("We would like a walkthrough of alert review.");
  await page.getByRole("button", { name: "Request a demo" }).click();
  await page.getByText("Consent is required").waitFor();
  await page.getByLabel(/I agree/).click();
  await page.getByRole("button", { name: "Request a demo" }).click();
  await page.waitForURL("**/contact/thanks");
});

await check("home email field continues to contact form with the email", async (page) => {
  await page.goto(`${base}/`);
  await page.locator("#cta-email").fill("prefill@example.org");
  await page.getByRole("button", { name: "Request a demo" }).last().click();
  await page.waitForURL("**/contact?email=*");
  assert.equal(await page.getByLabel("Work email").inputValue(), "prefill@example.org");
});

await check("newsletter subscription gives the double opt-in reply", async (page) => {
  await page.goto(`${base}/`);
  await page.getByPlaceholder("Email address").fill("reader.p7@example.org");
  await page.getByRole("button", { name: "Subscribe" }).click();
  await page.getByText("Check your inbox to confirm the subscription.").waitFor();
});

await check("wrong password shows an error and stays on the form", async (page) => {
  await page.goto(`${base}/login`);
  await page.getByLabel("Work email").fill("e2e.analyst@example.org");
  await page.getByLabel("Password").fill("not-the-password-123");
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByRole("alert").waitFor();
  assert(page.url().endsWith("/login"));
});

await check("analyst logs in and lands on /app", async (page) => {
  await page.goto(`${base}/login`);
  await page.getByLabel("Work email").fill("e2e.analyst@example.org");
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.waitForURL("**/app");
  await page.getByText("You are signed in").waitFor();
});

await check("platform admin enrolls MFA, then signs in with a code", async (page) => {
  await page.goto(`${base}/login`);
  await page.getByLabel("Work email").fill("e2e.platform@example.org");
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByText("Set up two-step verification").waitFor();
  const secret = (await page.getByText(/Manual key:/).textContent()).split(":")[1].trim();
  await page.getByLabel("Authentication code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await page.getByText(/Save these recovery codes/).waitFor();
  await page.getByRole("button", { name: "Continue to the platform" }).click();
  await page.waitForURL("**/app");
  await page.context().clearCookies();
  await page.goto(`${base}/login`);
  await page.getByLabel("Work email").fill("e2e.platform@example.org");
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Log in" }).click();
  await page.getByText("Two-step verification").waitFor();
  await page.getByLabel("Authentication code").fill(totp(secret));
  await page.getByRole("button", { name: "Verify" }).click();
  await page.waitForURL("**/app");
});

await check("forgot password gives the same reply for any address", async (page) => {
  await page.goto(`${base}/forgot-password`);
  await page.getByLabel("Email").fill("nobody@example.org");
  await page.getByRole("button", { name: "Send reset link" }).click();
  await page.getByText("If the account exists, a reset message has been sent.").waitFor();
});

await check("language selector switches to Arabic with right-to-left direction", async (page) => {
  await page.goto(`${base}/`);
  await page.locator("select").first().selectOption("ar");
  await page.waitForURL("**/ar");
  assert.equal(await page.locator("html").getAttribute("dir"), "rtl");
  assert.equal(await page.locator("html").getAttribute("lang"), "ar");
});

await check("mega menu opens and closes with the keyboard", async (page) => {
  await page.goto(`${base}/`);
  const trigger = page.getByRole("button", { name: "Platform" }).first();
  await trigger.focus();
  await page.keyboard.press("Enter");
  await page
    .getByRole("link", { name: /Entity linking/ })
    .first()
    .waitFor();
  await page.keyboard.press("Escape");
  await page
    .getByRole("link", { name: /Entity linking Connect names/ })
    .waitFor({ state: "hidden" });
});

await check("skip link moves focus to main content", async (page) => {
  await page.goto(`${base}/pricing`);
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  assert((await page.evaluate(() => location.hash)) === "#main");
});

await check("cookie notice appears once and stays dismissed", async (page) => {
  const fresh = await page.context().browser().newContext();
  const p = await fresh.newPage();
  await p.goto(`${base}/`);
  await p.getByRole("button", { name: "Understood" }).click();
  await p.reload();
  assert.equal(await p.getByRole("button", { name: "Understood" }).count(), 0);
  await fresh.close();
});

await browser.close();
for (const [n, r] of results)
  console.log(`${r === "pass" ? "PASS" : "FAIL"}  ${n}${r === "pass" ? "" : "  " + r}`);
process.exit(results.some(([, r]) => r !== "pass") ? 1 : 0);
