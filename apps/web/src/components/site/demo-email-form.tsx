"use client";
import { useRouter } from "@/i18n/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/form";

/** Collects the email on the home page and continues on the full contact form. */
export function DemoEmailForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  return (
    <form
      className="flex flex-col gap-3 sm:flex-row"
      onSubmit={(e) => {
        e.preventDefault();
        router.push(`/contact?email=${encodeURIComponent(email)}`);
      }}
    >
      <label htmlFor="cta-email" className="sr-only">
        Work email
      </label>
      <Input
        id="cta-email"
        type="email"
        required
        autoComplete="email"
        placeholder="Work email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="sm:max-w-sm"
      />
      <Button type="submit" size="md" className="bg-white text-ink hover:bg-brand-100">
        Request a demo
      </Button>
    </form>
  );
}
