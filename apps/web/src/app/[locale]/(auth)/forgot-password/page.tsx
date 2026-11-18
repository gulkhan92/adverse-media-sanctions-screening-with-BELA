import { AuthShell } from "@/components/site/auth-shell";
import { ForgotForm } from "@/components/site/reset-forms";
import { enter } from "@/lib/page";

export const metadata = { title: "Reset your password", robots: { index: false, follow: false } };

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <AuthShell>
      <ForgotForm />
    </AuthShell>
  );
}
