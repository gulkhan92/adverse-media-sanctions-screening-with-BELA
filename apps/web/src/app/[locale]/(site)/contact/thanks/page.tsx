import { Check } from "lucide-react";
import { Link } from "@/i18n/navigation";
import { buttonClass } from "@/components/ui/button";
import { Section } from "@/components/site/sections";
import { enter } from "@/lib/page";

export const metadata = { title: "Thank you", robots: { index: false } };

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <Section tone="white">
      <div className="mx-auto max-w-xl text-center">
        <span className="mx-auto mb-5 flex size-14 items-center justify-center rounded-full bg-[#e3f4ec] text-success">
          <Check className="size-8" aria-hidden />
        </span>
        <h1 className="text-h1">Thank you</h1>
        <p className="mt-3 text-lg text-slate-700">
          Your request has been received. We will reply by email to arrange a walkthrough.
        </p>
        <Link href="/" className={buttonClass("secondary", "md", "mt-8")}>
          Back to the home page
        </Link>
      </div>
    </Section>
  );
}
