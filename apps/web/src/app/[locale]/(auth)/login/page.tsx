import { AuthShell } from "@/components/site/auth-shell";
import { LoginFlow } from "@/components/site/login-flow";
import { enter } from "@/lib/page";

export const metadata = { title: "Log in", robots: { index: false, follow: false } };

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <AuthShell>
      <LoginFlow />
    </AuthShell>
  );
}
