import { Suspense } from "react";
import { NewsletterConfirm } from "@/components/site/session-landing";
import { enter } from "@/lib/page";

export const metadata = { title: "Confirm subscription", robots: { index: false, follow: false } };

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <Suspense>
      <NewsletterConfirm />
    </Suspense>
  );
}
