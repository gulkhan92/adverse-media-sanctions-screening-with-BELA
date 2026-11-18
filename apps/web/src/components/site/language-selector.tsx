"use client";
import { useLocale, useTranslations } from "next-intl";
import { Languages } from "lucide-react";
import { usePathname, useRouter } from "@/i18n/navigation";
import { localeNames, locales, type Locale } from "@/i18n/routing";

export function LanguageSelector({ dark = false }: { dark?: boolean }) {
  const locale = useLocale();
  const t = useTranslations("utility");
  const router = useRouter();
  const pathname = usePathname();
  return (
    <label
      className={`inline-flex items-center gap-1.5 text-sm ${dark ? "text-slate-300" : "text-slate-700"}`}
    >
      <Languages className="size-4" aria-hidden />
      <span className="sr-only">{t("language")}</span>
      <select
        value={locale}
        onChange={(e) => router.replace(pathname, { locale: e.target.value as Locale })}
        className={`rounded border-0 bg-transparent py-1 pe-6 text-sm focus-visible:outline-2 ${dark ? "text-slate-200" : ""}`}
      >
        {locales.map((l) => (
          <option key={l} value={l} className="text-ink">
            {localeNames[l]}
          </option>
        ))}
      </select>
    </label>
  );
}
