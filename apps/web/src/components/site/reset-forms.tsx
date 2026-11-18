"use client";
import { useState } from "react";
import { useSearchParams } from "next/navigation";
import { useTranslations } from "next-intl";
import { Link } from "@/i18n/navigation";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/form";
import { messageOf, post } from "@/lib/api";

export function ForgotForm() {
  const t = useTranslations("forgot");
  const [email, setEmail] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await post("/auth/password/forgot", { email });
      setDone(true);
    } catch (err) {
      setError(messageOf(err));
    }
  }
  return (
    <div className="space-y-5">
      <h1 className="text-h2">{t("title")}</h1>
      {done ? (
        <p role="status" className="rounded-control border border-line bg-slate-50 p-4">
          {t("sent")}
        </p>
      ) : (
        <form onSubmit={submit} className="space-y-5" noValidate>
          <p className="text-slate-700">{t("text")}</p>
          <Field label="Email" htmlFor="email" error={error ?? undefined}>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
          </Field>
          <Button type="submit" size="lg" className="w-full" disabled={!email}>
            {t("submit")}
          </Button>
        </form>
      )}
      <Link href="/login" className="text-sm text-brand-600 underline underline-offset-4">
        {t("back")}
      </Link>
    </div>
  );
}

export function ResetForm() {
  const t = useTranslations("reset");
  const token = useSearchParams().get("token");
  const [password, setPassword] = useState("");
  const [done, setDone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      await post("/auth/password/reset", { token, new_password: password });
      setDone(true);
    } catch (err) {
      setError(messageOf(err));
    }
  }

  if (!token) {
    return (
      <div className="space-y-4">
        <h1 className="text-h2">{t("title")}</h1>
        <p role="alert">{t("missing")}</p>
        <Link href="/forgot-password" className="text-brand-600 underline underline-offset-4">
          {t("title")}
        </Link>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <h1 className="text-h2">{t("title")}</h1>
      {done ? (
        <>
          <p role="status" className="rounded-control border border-line bg-slate-50 p-4">
            {t("done")}
          </p>
          <Link href="/login" className="text-brand-600 underline underline-offset-4">
            Log in
          </Link>
        </>
      ) : (
        <form onSubmit={submit} className="space-y-5" noValidate>
          <Field
            label={t("password")}
            htmlFor="password"
            error={error ?? undefined}
            hint="Use at least 12 characters. Avoid common words and your email address."
          >
            <Input
              id="password"
              type="password"
              autoComplete="new-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              aria-invalid={!!error}
            />
          </Field>
          <Button type="submit" size="lg" className="w-full" disabled={!password}>
            {t("submit")}
          </Button>
        </form>
      )}
    </div>
  );
}
