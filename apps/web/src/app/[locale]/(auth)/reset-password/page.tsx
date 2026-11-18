import { Suspense } from "react";
import { AuthShell } from "@/components/site/auth-shell";
import { ResetForm } from "@/components/site/reset-forms";
import { enter } from "@/lib/page";

export const metadata = { title: "Choose a new password", robots: { index: false, follow: false } };

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <AuthShell>
      <Suspense>
        <ResetForm />
      </Suspense>
    </AuthShell>
  );
}
