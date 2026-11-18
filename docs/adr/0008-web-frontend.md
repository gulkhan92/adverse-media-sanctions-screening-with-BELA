# ADR-008: Web frontend foundation and marketing site

Status: accepted (phase 7)

## Context

The marketing site and the product application share one design system. The site must make only claims that this repository can support, work in five languages for its interface, and meet accessibility and performance targets.

## Decisions

**One Next.js application.** `apps/web` holds the marketing pages (statically generated) and, from phase 8, the client-rendered application. The `(site)` and `(auth)` route groups separate pages with the site header and footer from the full-screen login screens.

**Same-origin API.** `next.config.ts` rewrites `/api/*` to the API. The browser sees one origin, so cookies are first party, no CORS headers are needed (the API sends none), and the CSRF cookie and header work without cross-site configuration.

**Design tokens in CSS.** Tailwind CSS 4 `@theme` holds the tokens from PLAN.md section 7.1. Components are small, typed, and use Radix primitives for behavior that needs accessibility handling (accordion, tabs, tooltip, checkbox, navigation menu). They are written in the repository and not generated, so they can be changed with the design.

**Internationalization.** `next-intl` with locale prefixes except for English (`as-needed`). The interface chrome and the authentication screens are translated into Spanish, French, German, and Arabic (right-to-left). Missing keys fall back to English per namespace. Page body copy is English only, and in the other locales the body is marked `lang="en" dir="ltr"` so that text direction and screen readers are correct. Translating the page bodies is a content task that is not done.

**Figures are read, not typed.** The home page proof strip and the methodology table read `public/metrics.json`. `scripts/build_web_metrics.py` writes it from the database (watchlist entries), `docs/api_latency.md` (latency), and `docs/eval/results.json` when it exists. The reduction in false matches versus fuzzy matching is `null` until the evaluation has run, and the site shows "Pending". The language count of 97 is cited to the BELA paper.

**Forms.** The demo request stores the role, industry, and the time of consent in addition to the earlier fields, and refuses a request without consent. The newsletter is double opt-in: the API stores the address with a hashed token, mails a confirmation link, and treats the address as subscribed only after the link is used. A repeat subscription returns the same reply and sends no mail, so the endpoint does not reveal whether an address is subscribed. Both endpoints are rate limited and have a honeypot field. Migration 0007 adds the columns and table.

**Authentication screens.** Login follows the API's states: password, then MFA code or MFA enrollment (QR code and manual key, recovery codes shown once) when the API asks for it. After success the user lands on `/app`, which in this phase only confirms the session. Password reset and forgot-password follow the API's non-enumerating responses.

**Imagery.** The plan calls for photographs from Unsplash and Pexels. Both require an API key or a licensed download that was not available in the development environment, and automated scraping of their pages is not acceptable, so no photographs are included. The image pipeline (`scripts/optimize-images.mjs`) and the `Photo` component are in place: placing images and a credits file in `assets/photos` and running `npm run images` produces AVIF and WebP sets, the manifest, and `CREDITS.md`. Until then each slot uses in-house SVG artwork in the brand palette.

**Product visuals.** The product application does not exist yet, so the screenshots the plan describes cannot be captured. The pages use React renderings of the alert review, network, and analytics views, always labeled "Illustrative interface" or "Illustrative interface with demo data". They are replaced by Playwright captures of the real application in phase 8.

## Consequences

- Marketing pages are static and score 100 in all four Lighthouse categories on desktop (`docs/frontend_quality.md`).
- A claim that depends on an unrun evaluation cannot appear on the site by accident, because the value is absent from `metrics.json`.
- Phase 8 reuses the components, tokens, authentication flow, and API client.
- The web server sets `nosniff`, frame denial, a referrer policy, and a permissions policy. A Content Security Policy for the web pages is not set; it needs nonces for the framework's inline scripts and is listed for the hardening phase.
