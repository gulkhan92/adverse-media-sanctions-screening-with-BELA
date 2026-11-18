# Frontend quality results (phase 7)

Measured against the production build (`npm run build`, `npm start`) with the API running, on a development laptop. Reports are in `apps/web/reports` (`axe.json`, `lighthouse-home.report.json`; the audit script and `make web-audit` regenerate the rest).

## Lighthouse (desktop preset)

| Page | Performance | Accessibility | Best practices | SEO | CLS | LCP |
|---|---|---|---|---|---|---|
| Home | 100 | 100 | 100 | 100 | 0 | 0.2 s |
| Platform | 100 | 100 | 100 | 100 | 0 | 0.2 s |
| Pricing | 100 | 100 | 100 | 100 | 0 | 0.2 s |
| Methodology | 100 | 100 | 100 | 100 | 0 | 0.2 s |

Targets from PLAN.md: performance 90 or above, accessibility 95 or above, no layout shift on the hero. The first run showed a layout shift of 0.019 from the web font swap; a metric-adjusted fallback font removed it.

## axe-core and layout

`node scripts/audit.mjs` visits 32 routes (all marketing pages, six articles, legal pages, authentication screens, and two Arabic pages) at widths 375, 768, 1024, 1440, and 1920 pixels. It runs axe-core (WCAG 2.0, 2.1, and 2.2 A and AA rules) at 375 and 1440, checks for horizontal overflow and for text clipped by an ancestor, and records console errors.

Result: 0 axe violations, 0 overflow cases, 0 console errors. Earlier runs found and fixed: a definition list with the wrong child order, scrollable regions without keyboard focus, a header that overflowed at 1024 pixels (the full navigation now starts at 1280), and a hero that clipped its text on phones.

## Behavior checks

`node scripts/e2e.mjs` (11 checks, all passing): contact form validation and submission, email carried from the home page to the contact form, newsletter double opt-in reply, failed login, analyst login, MFA enrollment followed by a login with a code, non-enumerating forgot-password reply, language switch to Arabic with `dir="rtl"`, keyboard use of the mega menu, skip link, and the cookie notice. `npm test` (7 Vitest tests) checks article length (900 to 1,400 words), front matter, legal text, the em dash rule, absence of placeholder text, and that an unmeasured figure is shown as pending.

## Not covered

- Visual review was done on screenshots of each route at the five widths; there was no review on physical devices or in Safari and Firefox.
- Lighthouse was run on four representative pages, not all of them. Mobile Lighthouse was not run.
- Screen reader testing was not done. Automated checks find only part of the accessibility issues.
- Arabic and the other translations were written without a native review.
