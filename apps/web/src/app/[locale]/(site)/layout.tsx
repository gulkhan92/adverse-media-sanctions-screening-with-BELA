import { getTranslations } from "next-intl/server";
import { Header } from "@/components/site/header";
import { Footer } from "@/components/site/footer";
import { UtilityBar } from "@/components/site/utility-bar";
import { CookieNotice } from "@/components/site/cookie-notice";

export default async function SiteLayout({
  children,
  params,
}: {
  children: React.ReactNode;
  params: Promise<{ locale: string }>;
}) {
  const { locale } = await params;
  const t = await getTranslations({ locale, namespace: "nav" });
  return (
    <>
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:start-3 focus:top-3 focus:z-[60] focus:rounded-control focus:bg-white focus:px-4 focus:py-2 focus:shadow-e3"
      >
        {t("skip")}
      </a>
      <UtilityBar />
      <Header />
      {/* Page copy is written in English. Translated locales keep the interface chrome in their own
          language and mark the body as English so that text direction and screen readers match. */}
      <main id="main" {...(locale === "en" ? {} : { lang: "en", dir: "ltr" })}>
        {children}
      </main>
      <Footer />
      <CookieNotice />
    </>
  );
}
