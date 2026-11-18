# Web application

Next.js (App Router, TypeScript strict) with Tailwind CSS 4. Phase 7 delivers the design system, the marketing site, and the authentication screens. The product application pages are built in phase 8.

## Commands

Run from the repository root (see the `web-*` targets in the Makefile) or from this directory.

| Command | Purpose |
|---|---|
| `npm run dev` | Development server on port 3000 |
| `npm run build` then `npm start` | Production build and server |
| `npm run lint`, `npm run typecheck`, `npm test` | ESLint, TypeScript, Vitest |
| `npm run metrics` | Not used directly: `make web-metrics` writes `public/metrics.json` from the database and `docs/` |
| `npm run images` | Optimize photographs in `assets/photos` (see below) |
| `node scripts/e2e.mjs` | Browser checks of forms, login, MFA, language, keyboard use |
| `node scripts/audit.mjs` | axe-core, overflow, and console checks on every route at five widths |

The browser talks to one origin. `next.config.ts` rewrites `/api/*` to `ATLAS_API_URL` (default `http://127.0.0.1:8000`), so no CORS is needed and the API's cookies are first party.

## Layout

```
src/app/[locale]/(site)   marketing pages, header and footer
src/app/[locale]/(auth)   login, forgot password, reset password (no site chrome)
src/app/og/[slug]         1200 by 630 social images, generated at build time
src/components/ui         design system components
src/components/site       site shell, forms, illustrations, interface renderings
src/content               typed content, MDX articles, legal text
src/i18n, messages        next-intl routing and translations
scripts                   image pipeline, e2e, audit
```

## Design system

Tokens are CSS variables in `src/app/globals.css` (`@theme`): colors, type scale, radii, elevations, breakpoints. Components built in this phase: Button, Input, Textarea, Select, Checkbox, Field, Tabs, Accordion, Tooltip (InfoTip), Badge and RiskBadge, Card, Skeleton, EmptyState, Breadcrumbs, Stepper, Sparkline, KpiCard, MegaMenu (in the header), BrowserFrame. Components for the product application (DataTable, Dialog, Drawer, Combobox, DatePicker, Switch, Popover, Toast, Avatar, Pagination, Timeline, CommandPalette, EvidenceViewer, ChartCard, GraphCanvas) are built in phase 8 where they are first used.

## Content rules

- Every figure comes from `public/metrics.json`, which `scripts/build_web_metrics.py` writes. A value that has not been measured is `null` and the site shows "Pending".
- Industry scenarios carry an "Illustrative scenario" label. Pricing carries "Illustrative pricing for the demonstration environment".
- Page copy is English. The interface chrome (utility bar, header, footer, cookie notice, login screens) is translated to Spanish, French, German, and Arabic with right-to-left layout. In translated locales the page body is marked `lang="en" dir="ltr"`.
- A test fails the build if the em dash character or placeholder text appears in the sources.

## Photographs

`npm run images` reads `assets/photos/<slot>.jpg` (at least 2400 px wide) and `assets/photos/credits.json` (alt text, photographer, source URL, license for each slot), writes AVIF and WebP at 640, 960, 1440, and 2400 px to `public/images`, the manifest `src/content/images.json`, and `public/images/CREDITS.md`. The `Photo` component uses a photograph when its slot is in the manifest and in-house SVG artwork otherwise. No photographs are included yet, so all imagery is in-house SVG.
