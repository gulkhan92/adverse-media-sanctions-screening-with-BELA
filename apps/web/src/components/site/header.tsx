"use client";
import { useEffect, useState } from "react";
import * as Nav from "@radix-ui/react-navigation-menu";
import { ChevronDown, Menu, X } from "lucide-react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { buttonClass } from "@/components/ui/button";
import { Logo } from "./logo";
import { LanguageSelector } from "./language-selector";
import {
  companyMenu,
  featured,
  industriesMenu,
  platformMenu,
  resourcesMenu,
  solutionsMenu,
  type MenuItem,
} from "@/content/site";

function Mega({
  items,
  feature,
}: {
  items: MenuItem[];
  feature: { title: string; body: string; href: string; cta: string };
}) {
  return (
    <div className="grid w-[min(52rem,90vw)] grid-cols-[1fr_16rem] gap-6 p-6">
      <ul className="grid grid-cols-2 gap-1">
        {items.map(({ href, title, description, icon: Icon }) => (
          <li key={href}>
            <Nav.Link asChild>
              <Link href={href} className="flex gap-3 rounded-control p-3 hover:bg-slate-50">
                <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-brand-100 text-brand-600">
                  <Icon className="size-5" aria-hidden />
                </span>
                <span>
                  <span className="block font-medium text-ink">{title}</span>
                  <span className="block text-sm text-slate-600">{description}</span>
                </span>
              </Link>
            </Nav.Link>
          </li>
        ))}
      </ul>
      <Nav.Link asChild>
        <Link
          href={feature.href}
          className="mesh flex flex-col justify-end rounded-card bg-ink p-5 text-white"
        >
          <span className="text-lg font-semibold">{feature.title}</span>
          <span className="mt-1 text-sm text-slate-200">{feature.body}</span>
          <span className="mt-4 text-sm font-medium underline underline-offset-4">
            {feature.cta}
          </span>
        </Link>
      </Nav.Link>
    </div>
  );
}

function SimpleList({ items }: { items: { href: string; title: string }[] }) {
  return (
    <ul className="w-56 p-2">
      {items.map((i) => (
        <li key={i.href}>
          <Nav.Link asChild>
            <Link
              href={i.href}
              className="block rounded-control px-3 py-2 text-ink hover:bg-slate-50"
            >
              {i.title}
            </Link>
          </Nav.Link>
        </li>
      ))}
    </ul>
  );
}

export function Header() {
  const t = useTranslations("nav");
  const [scrolled, setScrolled] = useState(false);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  const triggerClass =
    "group inline-flex h-10 items-center gap-1 rounded-control px-3 font-medium text-ink hover:bg-slate-100";
  const sections: { id: string; label: string; body: React.ReactNode }[] = [
    {
      id: "platform",
      label: t("platform"),
      body: <Mega items={platformMenu} feature={featured.platform} />,
    },
    {
      id: "solutions",
      label: t("solutions"),
      body: <Mega items={solutionsMenu} feature={featured.solutions} />,
    },
    {
      id: "industries",
      label: t("industries"),
      body: <Mega items={industriesMenu} feature={featured.industries} />,
    },
    { id: "resources", label: t("resources"), body: <SimpleList items={resourcesMenu} /> },
  ];

  return (
    <header
      className={`sticky top-0 z-40 h-[72px] border-b transition-colors duration-200 ${
        scrolled ? "border-line bg-white/80 backdrop-blur-md" : "border-transparent bg-page"
      }`}
    >
      <div className="container-page flex h-full items-center justify-between gap-6">
        <Link href="/" aria-label={`Atlas Screening, ${t("home")}`}>
          <Logo />
        </Link>

        <Nav.Root className="relative hidden xl:block" aria-label={t("primary")}>
          <Nav.List className="flex items-center gap-1">
            {sections.map((s) => (
              <Nav.Item key={s.id}>
                <Nav.Trigger className={triggerClass}>
                  {s.label}
                  <ChevronDown
                    className="size-4 transition-transform duration-200 group-data-[state=open]:rotate-180"
                    aria-hidden
                  />
                </Nav.Trigger>
                <Nav.Content className="absolute start-0 top-full pt-2">
                  <div className="rounded-card border border-line bg-white shadow-e3">{s.body}</div>
                </Nav.Content>
              </Nav.Item>
            ))}
            <Nav.Item>
              <Nav.Link asChild>
                <Link href="/pricing" className={triggerClass}>
                  {t("pricing")}
                </Link>
              </Nav.Link>
            </Nav.Item>
            <Nav.Item>
              <Nav.Trigger className={triggerClass}>
                {t("company")}
                <ChevronDown
                  className="size-4 transition-transform duration-200 group-data-[state=open]:rotate-180"
                  aria-hidden
                />
              </Nav.Trigger>
              <Nav.Content className="absolute start-0 top-full pt-2">
                <div className="rounded-card border border-line bg-white shadow-e3">
                  <SimpleList items={companyMenu} />
                </div>
              </Nav.Content>
            </Nav.Item>
          </Nav.List>
        </Nav.Root>

        <div className="hidden items-center gap-3 xl:flex">
          <Link href="/login" className="px-2 font-medium text-ink hover:text-brand-600">
            {t("login")}
          </Link>
          <Link href="/contact" className={buttonClass("primary", "md")}>
            {t("demo")}
          </Link>
        </div>

        <button
          type="button"
          className="inline-flex size-11 items-center justify-center rounded-control border border-line bg-white xl:hidden"
          aria-expanded={open}
          aria-controls="mobile-menu"
          aria-label={open ? t("close") : t("menu")}
          onClick={() => setOpen((v) => !v)}
        >
          {open ? <X className="size-5" aria-hidden /> : <Menu className="size-5" aria-hidden />}
        </button>
      </div>

      {open ? (
        <div
          id="mobile-menu"
          className="absolute inset-x-0 top-[72px] max-h-[calc(100vh-72px)] overflow-y-auto border-b border-line bg-white p-5 shadow-e3 xl:hidden"
        >
          <nav aria-label={t("primary")} className="space-y-6">
            {[
              {
                label: t("platform"),
                items: platformMenu.map((m) => ({ href: m.href, title: m.title })),
              },
              {
                label: t("solutions"),
                items: solutionsMenu.map((m) => ({ href: m.href, title: m.title })),
              },
              {
                label: t("industries"),
                items: industriesMenu.map((m) => ({ href: m.href, title: m.title })),
              },
              { label: t("resources"), items: resourcesMenu },
              {
                label: t("company"),
                items: [
                  { href: "/pricing", title: t("pricing") },
                  ...companyMenu.filter((c) => c.href !== "/pricing"),
                ],
              },
            ].map((g) => (
              <div key={g.label}>
                <p className="mb-2 text-caption font-semibold uppercase tracking-wider text-slate-600">
                  {g.label}
                </p>
                <ul className="space-y-1">
                  {g.items.map((i) => (
                    <li key={i.href}>
                      <Link
                        href={i.href}
                        onClick={() => setOpen(false)}
                        className="block rounded-control px-2 py-2 text-ink hover:bg-slate-50"
                      >
                        {i.title}
                      </Link>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
            <div className="flex flex-col gap-3 border-t border-line pt-4">
              <Link href="/login" className={buttonClass("secondary", "lg")}>
                {t("login")}
              </Link>
              <Link href="/contact" className={buttonClass("primary", "lg")}>
                {t("demo")}
              </Link>
            </div>
          </nav>
        </div>
      ) : null}
    </header>
  );
}
