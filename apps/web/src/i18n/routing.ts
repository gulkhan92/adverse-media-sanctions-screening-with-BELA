import { defineRouting } from "next-intl/routing";

export const locales = ["en", "es", "fr", "de", "ar"] as const;
export type Locale = (typeof locales)[number];

export const routing = defineRouting({
  locales,
  defaultLocale: "en",
  localePrefix: "as-needed",
});

export const localeNames: Record<Locale, string> = {
  en: "English",
  es: "Español",
  fr: "Français",
  de: "Deutsch",
  ar: "العربية",
};

export const rtlLocales: readonly Locale[] = ["ar"];
