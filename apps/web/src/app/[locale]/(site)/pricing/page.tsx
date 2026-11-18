import { Check } from "lucide-react";
import { Link } from "@/i18n/navigation";
import { buttonClass } from "@/components/ui/button";
import { Accordion } from "@/components/ui/accordion";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { CtaBand, PageHeader, Section, SectionHeading } from "@/components/site/sections";
import { comparison, included, plans, pricingFaq } from "@/content/pages";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/pricing",
  "Pricing",
  "Illustrative pricing for the demonstration environment: Starter, Professional, and Enterprise.",
);

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <>
      <PageHeader
        eyebrow="Pricing"
        title="Plans that scale with screening volume"
        lead="Illustrative pricing for the demo environment. No payments are processed."
      >
        <Badge tone="warning">Illustrative pricing for the demonstration environment</Badge>
      </PageHeader>
      <Section>
        <div className="grid gap-6 lg:grid-cols-3">
          {plans.map((p) => (
            <Card key={p.code} className="flex flex-col">
              <h2 className="text-h3">{p.name}</h2>
              <p className="mt-1 text-slate-700">{p.blurb}</p>
              <p className="mt-6 text-h1 font-semibold text-ink tabular-nums">
                {p.price.startsWith("From") ? p.price : `$${p.price}`}
                <span className="text-base font-normal text-slate-600"> per month</span>
              </p>
              <p className="mt-2 text-slate-700">{p.included} screenings included</p>
              <p className="text-slate-700">${p.overage} per additional 1,000</p>
              <Link
                href="/contact"
                className={buttonClass(
                  p.code === "professional" ? "primary" : "secondary",
                  "md",
                  "mt-6",
                )}
              >
                Request a demo
              </Link>
            </Card>
          ))}
        </div>
        <div className="mt-6 rounded-card border border-line bg-white p-6">
          <h2 className="text-h4">Included in every plan</h2>
          <ul className="mt-3 grid gap-2 sm:grid-cols-2">
            {included.map((f) => (
              <li key={f} className="flex gap-2">
                <Check className="mt-1 size-5 shrink-0 text-success" aria-hidden />
                {f}
              </li>
            ))}
          </ul>
        </div>
      </Section>
      <Section tone="white">
        <SectionHeading title="Compare plans" />
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Table, scrollable">
          <table className="w-full min-w-[32rem] border-collapse text-start">
            <caption className="sr-only">Plan comparison</caption>
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className="py-3 pe-4 text-start">
                  Feature
                </th>
                {plans.map((p) => (
                  <th scope="col" key={p.code} className="px-4 py-3 text-start">
                    {p.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {comparison.map((row) => (
                <tr key={row.feature} className="border-b border-line">
                  <th scope="row" className="py-3 pe-4 text-start font-medium">
                    {row.feature}
                  </th>
                  {row.values.map((v, i) => (
                    <td key={i} className="px-4 py-3 tabular-nums">
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <h2 className="mt-12 text-h3">Usage and overage</h2>
        <p className="mt-3 max-w-3xl text-slate-700">
          Each plan includes a monthly volume of screenings. Usage is metered per subject screened.
          Volume above the included amount is billed per 1,000 additional screenings at the overage
          rate shown above. Annual billing carries a 10 percent discount.
        </p>
      </Section>
      <Section>
        <SectionHeading title="Frequently asked questions" />
        <Accordion items={pricingFaq} />
      </Section>
      <CtaBand
        title="Talk to us about your volume"
        body="We will size a plan against your screening workload."
      />
    </>
  );
}
