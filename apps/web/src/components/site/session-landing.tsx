"use client";
import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { Link } from "@/i18n/navigation";
import { buttonClass } from "@/components/ui/button";
import { Section } from "@/components/site/sections";
import { messageOf, post } from "@/lib/api";

/** Landing target after login. The product application is built in the next phase. */
export function SessionLanding() {
  const [me, setMe] = useState<{ email: string; role: string } | null | undefined>(undefined);
  useEffect(() => {
    fetch("/api/v1/auth/me", { credentials: "include" })
      .then((r) => (r.ok ? r.json() : null))
      .then(setMe)
      .catch(() => setMe(null));
  }, []);
  if (me === undefined)
    return (
      <Section tone="white">
        <p>Loading</p>
      </Section>
    );
  return (
    <Section tone="white">
      <div className="mx-auto max-w-xl text-center">
        {me ? (
          <>
            <h1 className="text-h2">You are signed in</h1>
            <p className="mt-3 text-slate-700">
              Signed in as {me.email} ({me.role}). The product application pages are delivered in
              the next phase.
            </p>
          </>
        ) : (
          <>
            <h1 className="text-h2">You are not signed in</h1>
            <Link href="/login" className={buttonClass("primary", "md", "mt-6")}>
              Log in
            </Link>
          </>
        )}
      </div>
    </Section>
  );
}

export function NewsletterConfirm() {
  const token = useSearchParams().get("token");
  const [state, setState] = useState<{ ok: boolean; text: string } | null>(null);
  useEffect(() => {
    if (!token) return;
    post<{ status: string }>("/contact/newsletter/confirm", undefined, { token })
      .then((r) => setState({ ok: true, text: r.status }))
      .catch((e) => setState({ ok: false, text: messageOf(e) }));
  }, [token]);
  const text = !token ? "This confirmation link is incomplete." : (state?.text ?? "Confirming");
  return (
    <Section tone="white">
      <div className="mx-auto max-w-xl text-center">
        <h1 className="text-h2">Newsletter</h1>
        <p role="status" className="mt-3 text-lg text-slate-700">
          {text}
        </p>
      </div>
    </Section>
  );
}
