import { notFound } from "next/navigation";
import { routing } from "@/i18n/routing";
import { Accordion } from "@/components/ui/accordion";
import { KpiCard } from "@/components/ui/misc";
import { Art } from "@/components/site/art";
import { AlertReviewMock, BrowserFrame, NetworkMock } from "@/components/site/product-visuals";
import {
  CtaBand,
  Disclaimer,
  PageHeader,
  Section,
  SectionHeading,
} from "@/components/site/sections";
import { solutions } from "@/content/pages";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const dynamicParams = false;
export const generateStaticParams = () =>
  routing.locales.flatMap((locale) => solutions.map((s) => ({ locale, slug: s.slug })));

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const s = solutions.find((x) => x.slug === slug);
  return s ? pageMeta(`/solutions/${slug}`, s.title, s.lead) : {};
}

export default async function Page({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  await enter(params);
  const { slug } = await params;
  const s = solutions.find((x) => x.slug === slug);
  if (!s) notFound();
  return (
    <>
      <PageHeader
        eyebrow="Solutions"
        title={s.title}
        lead={s.lead}
        crumbs={[{ href: "/", label: "Home" }, { label: "Solutions" }, { label: s.title }]}
      />
      <Section tone="white">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <SectionHeading title="The problem" />
            <div className="space-y-4 text-lg text-slate-700">
              {s.problem.map((p) => (
                <p key={p}>{p}</p>
              ))}
            </div>
          </div>
          <div>
            <SectionHeading title="Our approach" />
            <div className="space-y-4 text-lg text-slate-700">
              {s.approach.map((p) => (
                <p key={p}>{p}</p>
              ))}
            </div>
          </div>
        </div>
      </Section>
      <Section>
        <SectionHeading title="Workflow" lead="Illustrative interface with demo data." />
        <ol className="mb-8 grid gap-3 md:grid-cols-5">
          {s.workflow.map((w, i) => (
            <li key={w} className="rounded-card border border-line bg-white p-4">
              <span className="text-caption font-semibold text-brand-600">Step {i + 1}</span>
              <p className="font-medium text-ink">{w}</p>
            </li>
          ))}
        </ol>
        <div className="grid gap-6 lg:grid-cols-[1.4fr_1fr]">
          <BrowserFrame>
            {s.slug === "network-analysis" ? <NetworkMock /> : <AlertReviewMock />}
          </BrowserFrame>
          <div className="hidden overflow-hidden rounded-card lg:block">
            <Art kind={s.art} seed={5} />
          </div>
        </div>
      </Section>
      <Section tone="white">
        <SectionHeading
          title="Outcomes measured in this repository"
          lead="Figures come from measurements stored with the project. Figures not yet measured are shown as pending."
        />
        <div className="grid gap-5 md:grid-cols-3">
          {s.outcomes.map((o) => (
            <KpiCard
              key={o.label}
              label={o.label}
              value={o.value}
              note={o.pending ? "Evaluation not yet run" : "See the methodology page"}
            />
          ))}
        </div>
      </Section>
      <Section>
        <SectionHeading title="Frequently asked questions" />
        <Accordion items={s.faq} />
        <div className="mt-8">
          <Disclaimer />
        </div>
      </Section>
      <CtaBand
        title={`See ${s.title.toLowerCase()} in practice`}
        body="Request a walkthrough with demo data."
      />
    </>
  );
}
