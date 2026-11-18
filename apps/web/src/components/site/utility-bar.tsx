import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { LanguageSelector } from "./language-selector";

export function UtilityBar() {
  const t = useTranslations("utility");
  const f = useTranslations("footer");
  return (
    <div className="hidden h-10 bg-ink text-sm text-slate-300 md:block">
      <div className="container-page flex h-full items-center justify-between">
        <p className="text-caption">{f("demoLabel")}</p>
        <nav aria-label="Utility" className="flex items-center gap-5">
          <Link href="/contact" className="hover:text-white">
            {t("support")}
          </Link>
          <Link href="/security#availability" className="hover:text-white">
            {t("status")}
          </Link>
          <Link href="/developers" className="hover:text-white">
            {t("developers")}
          </Link>
          <LanguageSelector dark />
        </nav>
      </div>
    </div>
  );
}
