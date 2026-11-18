"use client";
import { useState, useSyncExternalStore } from "react";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { Button } from "@/components/ui/button";

const KEY = "atlas_cookie_notice";

function seen(): boolean {
  try {
    return localStorage.getItem(KEY) === "1";
  } catch {
    return false;
  }
}
const subscribe = (cb: () => void) => {
  window.addEventListener("storage", cb);
  return () => window.removeEventListener("storage", cb);
};

export function CookieNotice() {
  const t = useTranslations("cookie");
  const stored = useSyncExternalStore(subscribe, seen, () => true);
  const [dismissed, setDismissed] = useState(false);
  if (stored || dismissed) return null;
  return (
    <div
      role="region"
      aria-label={t("title")}
      className="fixed inset-x-3 bottom-3 z-50 mx-auto max-w-3xl rounded-card border border-line bg-white p-4 shadow-e3 sm:flex sm:items-center sm:gap-4"
    >
      <p className="text-sm text-slate-700">
        {t("text")}{" "}
        <Link href="/legal/cookies" className="text-brand-600 underline underline-offset-2">
          {t("more")}
        </Link>
      </p>
      <Button
        className="mt-3 shrink-0 sm:mt-0"
        size="sm"
        onClick={() => {
          try {
            localStorage.setItem(KEY, "1");
          } catch {
            /* storage unavailable: the notice returns on the next visit */
          }
          setDismissed(true);
        }}
      >
        {t("accept")}
      </Button>
    </div>
  );
}
