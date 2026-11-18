import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { footerColumns, brand } from "@/content/site";
import { Logo } from "./logo";
import { LanguageSelector } from "./language-selector";
import { NewsletterForm } from "./newsletter-form";

export function Footer() {
  const t = useTranslations("footer");
  const n = useTranslations("nav");
  const year = new Date().getFullYear();
  return (
    <footer className="bg-ink text-slate-300">
      <div className="container-page py-14">
        <div className="grid gap-10 lg:grid-cols-[1.2fr_repeat(5,1fr)]">
          <div className="space-y-4">
            <Logo light />
            <h2 className="text-base font-semibold text-white">{t("newsletter")}</h2>
            <p className="text-sm">{t("newsletterText")}</p>
            <NewsletterForm />
          </div>
          {footerColumns.map((col) => (
            <nav key={col.key} aria-label={n(col.key as "platform")}>
              <h2 className="mb-3 text-sm font-semibold text-white">{n(col.key as "platform")}</h2>
              <ul className="space-y-2 text-sm">
                {col.links.map((l) => (
                  <li key={l.href}>
                    <Link href={l.href} className="hover:text-white">
                      {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="mt-12 space-y-3 border-t border-dark-line pt-6 text-sm">
          <p className="text-white">{t("disclaimer")}</p>
          <p>
            <span className="font-medium text-white">{t("sources")}.</span> {t("sourcesText")}
          </p>
          <p className="text-slate-400">{t("demoLabel")}</p>
        </div>

        <div className="mt-6 flex flex-wrap items-center justify-between gap-4 text-sm">
          <p>
            © {year} {brand}. {t("rights")}
          </p>
          <div className="flex flex-wrap items-center gap-5">
            <Link href="/legal/privacy" className="hover:text-white">
              {t("privacy")}
            </Link>
            <Link href="/legal/terms" className="hover:text-white">
              {t("terms")}
            </Link>
            <Link href="/legal/cookies" className="hover:text-white">
              {t("cookies")}
            </Link>
            <LanguageSelector dark />
          </div>
        </div>
      </div>
    </footer>
  );
}
