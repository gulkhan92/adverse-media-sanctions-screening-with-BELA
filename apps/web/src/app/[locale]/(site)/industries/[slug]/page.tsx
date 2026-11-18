import { notFound } from "next/navigation";
import { Check } from "lucide-react";
import { routing } from "@/i18n/routing";
import { Link } from "@/i18n/navigation";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Photo } from "@/components/site/art";
import {
  CtaBand,
  Disclaimer,
  PageHeader,
  Section,
  SectionHeading,
} from "@/components/site/sections";
import { industries } from "@/content/site";
import { industryDetails } from "@/content/pages";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const dynamicParams = false;
export const generateStaticParams = () =>
  routing.locales.flatMap((locale) => industries.map((i) => ({ locale, slug: i.slug })));

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const i = industries.find((x) => x.slug === slug);
  return i
    ? pageMeta(`/industries/${slug}`, `Screening for ${i.title.toLowerCase()}`, i.short)
    : {};
}

export default async function Page({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  await enter(params);
  const { slug } = await params;
  const i = industries.find((x) => x.slug === slug);
  const d = industryDetails.find((x) => x.slug === slug);
  if (!i || !d) notFound();
  const index = industries.indexOf(i);
  return (
    <>
      <PageHeader
        eyebrow="Industries"
        title={`Screening for ${i.title.toLowerCase()}`}
        lead={i.short}
        crumbs={[{ href: "/", label: "Home" }, { label: "Industries" }, { label: i.title }]}
      />
      <div className="h-56 overflow-hidden sm:h-72">
        <Photo
          slot={i.image}
          fallback={
            (["lines", "network", "chart", "layers", "shelves", "grid"] as const)[index % 6]!
          }
          seed={index + 3}
          sizes="100vw"
          priority
        />
      </div>
      <Section tone="white">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <SectionHeading title="Typical risk themes" />
            <ul className="space-y-3">
              {d.themes.map((t) => (
                <li key={t} className="flex gap-3">
                  <Check className="mt-1 size-5 shrink-0 text-success" aria-hidden />
                  <span>{t}</span>
                </li>
              ))}
            </ul>
          </div>
          <div>
            <SectionHeading title="Recommended workflow" />
            <ol className="space-y-3">
              {d.workflow.map((w, n) => (
                <li key={w} className="flex gap-3">
                  <span className="flex size-7 shrink-0 items-center justify-center rounded-full bg-ink text-sm font-semibold text-white">
                    {n + 1}
                  </span>
                  <span className="pt-0.5">{w}</span>
                </li>
              ))}
            </ol>
          </div>
        </div>
      </Section>
      <Section>
        <Card className="max-w-3xl">
          <Badge tone="brand">Illustrative scenario</Badge>
          <h2 className="mt-3 text-h3">{d.scenario.title}</h2>
          <p className="mt-3 text-lg text-slate-700">{d.scenario.text}</p>
          <p className="mt-4 text-sm text-slate-600">
            This scenario is constructed to explain the workflow. It does not describe a real
            customer.
          </p>
        </Card>
        <div className="mt-10">
          <SectionHeading title="Related capabilities" />
          <div className="flex flex-wrap gap-3">
            {d.related.map((r) => (
              <Badge key={r} tone="neutral" className="px-3 py-1 text-sm">
                {r}
              </Badge>
            ))}
          </div>
          <p className="mt-6">
            <Link href="/platform" className="text-brand-600 underline underline-offset-4">
              Explore the platform
            </Link>
          </p>
        </div>
        <div className="mt-8">
          <Disclaimer />
        </div>
      </Section>
      <CtaBand
        title={`Screening for ${i.title.toLowerCase()}, with demo data`}
        body="Request a walkthrough tailored to your sector."
      />
    </>
  );
}
