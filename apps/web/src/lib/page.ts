import { setRequestLocale } from "next-intl/server";

/** Every page calls this first so that the route renders statically for its locale. */
export async function enter(params: Promise<{ locale: string }>) {
  const { locale } = await params;
  setRequestLocale(locale);
  return locale;
}
