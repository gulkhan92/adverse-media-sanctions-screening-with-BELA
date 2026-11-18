import { Suspense } from "react";
import { PageHeader, Section } from "@/components/site/sections";
import { ContactForm } from "@/components/site/contact-form";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/contact",
  "Request a demo",
  "Tell us about your screening workflow and we will arrange a walkthrough.",
);

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <>
      <PageHeader
        eyebrow="Contact"
        title="Request a demo"
        lead="Tell us about your screening workflow. We will reply by email to arrange a walkthrough with demo data."
      />
      <Section tone="white">
        <div className="max-w-2xl">
          <Suspense>
            <ContactForm />
          </Suspense>
        </div>
      </Section>
    </>
  );
}
