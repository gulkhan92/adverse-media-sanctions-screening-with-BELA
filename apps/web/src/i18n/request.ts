import { getRequestConfig } from "next-intl/server";
import { hasLocale } from "next-intl";
import { routing } from "./routing";

type Messages = { [key: string]: string | Messages };

/** Missing keys in a translation fall back to English, namespace by namespace. */
function merge(base: Messages, over: Messages): Messages {
  const out: Messages = { ...base };
  for (const [key, value] of Object.entries(over)) {
    const current = out[key];
    out[key] =
      typeof value === "object" && typeof current === "object" ? merge(current, value) : value;
  }
  return out;
}

export default getRequestConfig(async ({ requestLocale }) => {
  const requested = await requestLocale;
  const locale = hasLocale(routing.locales, requested) ? requested : routing.defaultLocale;
  const fallback = (await import("../../messages/en.json")).default;
  const messages =
    locale === "en" ? fallback : (await import(`../../messages/${locale}.json`)).default;
  return { locale, messages: merge(fallback as Messages, messages as Messages) };
});
