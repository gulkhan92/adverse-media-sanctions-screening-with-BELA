"use client";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { Logo } from "./logo";
import { LanguageSelector } from "./language-selector";

const statements = [
  {
    title: "Linked to the right entity",
    body: "Names in news are connected to verified identities, so you review the person you are screening.",
  },
  {
    title: "Explained, not opaque",
    body: "Every score shows the components behind it and the passage that supports it.",
  },
  {
    title: "Human decisions, recorded",
    body: "Analysts decide. Each action is written to a tamper-evident audit log.",
  },
];

export function AuthShell({ children }: { children: React.ReactNode }) {
  const t = useTranslations("login");
  const [i, setI] = useState(0);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = setInterval(() => setI((n) => (n + 1) % statements.length), 6000);
    return () => clearInterval(id);
  }, []);
  const s = statements[i]!;
  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      <div className="flex flex-col bg-white px-6 py-8 sm:px-12">
        <div className="flex items-center justify-between">
          <Link href="/" aria-label="Atlas Screening">
            <Logo />
          </Link>
          <LanguageSelector />
        </div>
        <main
          id="main"
          className="mx-auto flex w-full max-w-md flex-1 flex-col justify-center py-12"
        >
          {children}
        </main>
        <p className="text-caption text-slate-600">{t("demoNotice")}</p>
      </div>
      <aside
        className="mesh relative hidden flex-col justify-between bg-ink p-12 text-white lg:flex"
        aria-label="About the platform"
      >
        <p className="text-sm font-semibold uppercase tracking-wider text-[#7fd4d4]">
          Atlas Screening
        </p>
        <div aria-live="polite" className="max-w-md">
          <h2 className="text-h2 text-white">{s.title}</h2>
          <p className="mt-3 text-lg text-slate-200">{s.body}</p>
          <div className="mt-8 flex gap-2" aria-hidden>
            {statements.map((_, n) => (
              <span
                key={n}
                className={`h-1 w-10 rounded ${n === i ? "bg-white" : "bg-white/30"}`}
              />
            ))}
          </div>
        </div>
        <p className="text-sm text-slate-300">
          Matches are leads for human review and not legal determinations.
        </p>
      </aside>
    </div>
  );
}
