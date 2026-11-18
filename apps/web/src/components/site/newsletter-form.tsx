"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";
import { messageOf, post } from "@/lib/api";

export function NewsletterForm() {
  const t = useTranslations("footer");
  const [email, setEmail] = useState("");
  const [state, setState] = useState<{ kind: "idle" | "ok" | "error"; text?: string }>({
    kind: "idle",
  });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const res = await post<{ status: string }>("/contact/newsletter", { email });
      setState({ kind: "ok", text: res.status });
      setEmail("");
    } catch (error) {
      setState({ kind: "error", text: messageOf(error) });
    }
  }

  return (
    <form onSubmit={submit} className="space-y-2" noValidate>
      <label htmlFor="newsletter-email" className="sr-only">
        {t("email")}
      </label>
      <div className="flex gap-2">
        <Input
          id="newsletter-email"
          type="email"
          required
          autoComplete="email"
          placeholder={t("email")}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <Button type="submit" variant="primary">
          {t("subscribe")}
        </Button>
      </div>
      <p
        role="status"
        aria-live="polite"
        className={`text-caption ${state.kind === "error" ? "text-[#ff9b8f]" : "text-slate-300"}`}
      >
        {state.text}
      </p>
    </form>
  );
}
