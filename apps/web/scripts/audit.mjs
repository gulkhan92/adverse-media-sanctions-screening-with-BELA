// Visits every public route at five viewport widths, runs axe-core, checks for horizontal
// overflow and console errors, and saves screenshots and a JSON summary under reports/.
import { chromium } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { mkdir, readdir, writeFile } from "node:fs/promises";
import path from "node:path";

const base = process.env.BASE_URL ?? "http://127.0.0.1:3000";
const root = path.resolve(import.meta.dirname, "..");
const shots = path.join(root, "reports", "screens");
await mkdir(shots, { recursive: true });

const articles = (await readdir(path.join(root, "src/content/articles"))).map((f) =>
  f.replace(/\.mdx$/, ""),
);
const routes = [
  "/",
  "/platform",
  "/pricing",
  "/security",
  "/developers",
  "/methodology",
  "/resources",
  "/company",
  "/contact",
  "/solutions/adverse-media",
  "/solutions/sanctions-and-pep",
  "/solutions/network-analysis",
  "/industries/banking",
  "/industries/payments-and-fintech",
  "/industries/digital-assets",
  "/industries/insurance",
  "/industries/legal-and-professional-services",
  "/industries/corporate-compliance",
  ...articles.map((a) => `/resources/${a}`),
  "/legal/privacy",
  "/legal/terms",
  "/legal/cookies",
  "/login",
  "/forgot-password",
  "/reset-password?token=x",
  "/ar",
  "/ar/pricing",
];
const widths = [375, 768, 1024, 1440, 1920];

const browser = await chromium.launch();
const summary = { base, routes: routes.length, violations: [], overflow: [], consoleErrors: [] };

for (const route of routes) {
  for (const width of widths) {
    const context = await browser.newContext({ viewport: { width, height: 900 } });
    await context.addInitScript(() => localStorage.setItem("atlas_cookie_notice", "1"));
    const page = await context.newPage();
    page.on("console", (m) => {
      if (m.type() === "error")
        summary.consoleErrors.push({ route, width, text: m.text().slice(0, 200) });
    });
    await page.goto(base + route, { waitUntil: "networkidle" });
    const over = await page.evaluate(
      () => document.documentElement.scrollWidth - window.innerWidth,
    );
    if (over > 1) summary.overflow.push({ route, width, over });
    // Content clipped by an ancestor with overflow hidden does not widen the document, so
    // also compare the right edge of every heading and paragraph with the viewport.
    const clipped = await page.evaluate(() =>
      [...document.querySelectorAll("h1,h2,h3,p,li,td")]
        .filter(
          (el) =>
            el.getBoundingClientRect().right > window.innerWidth + 1 &&
            !el.closest("[aria-hidden='true'],.overflow-x-auto,pre"),
        )
        .slice(0, 3)
        .map((el) => `${el.tagName}:${(el.textContent ?? "").slice(0, 40)}`),
    );
    if (clipped.length) summary.overflow.push({ route, width, clipped });
    if (width === 375 || width === 1440) {
      const results = await new AxeBuilder({ page })
        .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
        .analyze();
      for (const v of results.violations) {
        summary.violations.push({
          route,
          width,
          id: v.id,
          impact: v.impact,
          nodes: v.nodes.length,
          target: v.nodes[0]?.target,
        });
      }
    }
    const name = (route === "/" ? "home" : route.replace(/[/?=]/g, "_")) + `-${width}.png`;
    await page.screenshot({ path: path.join(shots, name), fullPage: false });
    await context.close();
  }
}
await browser.close();
await writeFile(path.join(root, "reports", "axe.json"), JSON.stringify(summary, null, 2) + "\n");
console.log(
  `routes=${summary.routes} violations=${summary.violations.length} overflow=${summary.overflow.length} consoleErrors=${summary.consoleErrors.length}`,
);
