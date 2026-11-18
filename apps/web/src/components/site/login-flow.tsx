"use client";
import { useState } from "react";
import { useTranslations } from "next-intl";
import { Link, useRouter } from "@/i18n/navigation";
import { Button } from "@/components/ui/button";
import { Field, Input } from "@/components/ui/form";
import { messageOf, post } from "@/lib/api";

type Step =
  | { kind: "password" }
  | { kind: "mfa"; token: string }
  | { kind: "setup"; token: string; qr: string; secret: string }
  | { kind: "codes"; codes: string[] };

interface LoginResult {
  status: "ok" | "mfa_required" | "mfa_setup_required";
  mfa_token?: string;
}
interface VerifyResult {
  status: string;
  recovery_codes?: string[];
}

export function LoginFlow() {
  const t = useTranslations("login");
  const router = useRouter();
  const [step, setStep] = useState<Step>({ kind: "password" });
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(fn: () => Promise<void>) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(messageOf(e));
    } finally {
      setBusy(false);
    }
  }

  const submitPassword = (e: React.FormEvent) => {
    e.preventDefault();
    return run(async () => {
      const res = await post<LoginResult>("/auth/login", { email, password });
      setPassword("");
      if (res.status === "ok") return router.push("/app");
      if (res.status === "mfa_required" && res.mfa_token)
        return setStep({ kind: "mfa", token: res.mfa_token });
      if (res.status === "mfa_setup_required" && res.mfa_token) {
        const setup = await post<{ qr_svg: string; secret: string }>("/auth/mfa/setup", {
          mfa_token: res.mfa_token,
        });
        setStep({ kind: "setup", token: res.mfa_token, qr: setup.qr_svg, secret: setup.secret });
      }
    });
  };

  const submitCode = (e: React.FormEvent) => {
    e.preventDefault();
    if (step.kind !== "mfa" && step.kind !== "setup") return;
    const token = step.kind === "mfa" || step.kind === "setup" ? step.token : "";
    return run(async () => {
      const res = await post<VerifyResult>(
        "/auth/mfa/verify",
        recovery
          ? { mfa_token: token, recovery_code: code.trim() }
          : { mfa_token: token, code: code.replace(/\s/g, "") },
      );
      setCode("");
      if (res.recovery_codes?.length) return setStep({ kind: "codes", codes: res.recovery_codes });
      router.push("/app");
    });
  };

  if (step.kind === "codes") {
    return (
      <div className="space-y-5">
        <h1 className="text-h2">{t("saveCodes")}</h1>
        <ul className="grid grid-cols-2 gap-2 rounded-card border border-line bg-slate-50 p-4 font-mono text-sm">
          {step.codes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ul>
        <Button size="lg" className="w-full" onClick={() => router.push("/app")}>
          {t("continue")}
        </Button>
      </div>
    );
  }

  if (step.kind === "mfa" || step.kind === "setup") {
    return (
      <form onSubmit={submitCode} className="space-y-5" noValidate>
        <h1 className="text-h2">{step.kind === "setup" ? t("setupTitle") : t("mfaTitle")}</h1>
        <p className="text-slate-700">{step.kind === "setup" ? t("setupText") : t("mfaText")}</p>
        {step.kind === "setup" ? (
          <div className="space-y-2">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              alt="Authenticator setup QR code"
              width={180}
              height={180}
              src={`data:image/svg+xml;utf8,${encodeURIComponent(step.qr)}`}
              className="rounded-control border border-line bg-white p-2"
            />
            <p className="text-caption text-slate-600">
              Manual key: <span className="font-mono">{step.secret}</span>
            </p>
          </div>
        ) : null}
        <Field
          label={recovery ? "Recovery code" : t("code")}
          htmlFor="code"
          error={error ?? undefined}
        >
          <Input
            id="code"
            inputMode={recovery ? "text" : "numeric"}
            autoComplete="one-time-code"
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value)}
            aria-invalid={!!error}
          />
        </Field>
        <Button type="submit" size="lg" className="w-full" disabled={busy || !code}>
          {t("verify")}
        </Button>
        {step.kind === "mfa" ? (
          <button
            type="button"
            className="text-sm text-brand-600 underline underline-offset-4"
            onClick={() => {
              setRecovery((r) => !r);
              setCode("");
            }}
          >
            {recovery ? t("code") : t("recovery")}
          </button>
        ) : null}
      </form>
    );
  }

  return (
    <form onSubmit={submitPassword} className="space-y-5" noValidate>
      <h1 className="text-h2">{t("title")}</h1>
      <Field label={t("email")} htmlFor="email">
        <Input
          id="email"
          type="email"
          autoComplete="username"
          required
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
      </Field>
      <Field label={t("password")} htmlFor="password" error={error ?? undefined}>
        <Input
          id="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          aria-invalid={!!error}
        />
      </Field>
      <Button type="submit" size="lg" className="w-full" disabled={busy || !email || !password}>
        {t("submit")}
      </Button>
      <p className="text-sm">
        <Link href="/forgot-password" className="text-brand-600 underline underline-offset-4">
          {t("forgot")}
        </Link>
      </p>
    </form>
  );
}
