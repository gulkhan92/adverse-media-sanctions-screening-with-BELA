import type { MetadataRoute } from "next";
import { industries, siteUrl } from "@/content/site";
import { solutions } from "@/content/pages";
import { articles, legalSlugs } from "@/lib/content";
import { routing } from "@/i18n/routing";

const paths = () => [
  "/",
  "/platform",
  "/pricing",
  "/security",
  "/developers",
  "/methodology",
  "/resources",
  "/company",
  "/contact",
  ...solutions.map((s) => `/solutions/${s.slug}`),
  ...industries.map((i) => `/industries/${i.slug}`),
  ...articles().map((a) => `/resources/${a.slug}`),
  ...legalSlugs.map((l) => `/legal/${l}`),
];

const url = (locale: string, path: string) =>
  `${siteUrl}${locale === routing.defaultLocale ? "" : `/${locale}`}${path === "/" && locale !== routing.defaultLocale ? "" : path}`;

export default function sitemap(): MetadataRoute.Sitemap {
  return paths().flatMap((path) =>
    routing.locales.map((locale) => ({
      url: url(locale, path),
      changeFrequency: "monthly" as const,
      priority: path === "/" ? 1 : 0.7,
      alternates: { languages: Object.fromEntries(routing.locales.map((l) => [l, url(l, path)])) },
    })),
  );
}
