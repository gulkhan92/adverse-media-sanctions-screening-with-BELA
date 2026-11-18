import {
  ArrowRight,
  ShieldCheck,
  Lock,
  KeyRound,
  ScrollText,
  Layers,
  Network,
  FileSearch,
  Newspaper,
  Plug,
  Users,
  GitMerge,
  Clock,
} from "lucide-react";
import { Link } from "@/i18n/navigation";
import { buttonClass } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Stepper } from "@/components/ui/misc";
import { Tabs } from "@/components/ui/tabs";
import { Photo } from "@/components/site/art";
import { Section, SectionHeading, CtaBand, JsonLd } from "@/components/site/sections";
import {
  AlertReviewMock,
  AnalyticsMock,
  BrowserFrame,
  CriticalAlertCard,
  MiniGraph,
  NetworkMock,
} from "@/components/site/product-visuals";
import { IngestArt, LinkArt, MatchArt, ReviewArt } from "@/components/site/how-it-works-art";
import { industries, brand, siteUrl } from "@/content/site";
import { articles } from "@/lib/content";
import { proofFigures } from "@/lib/metrics";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";
import { DemoEmailForm } from "@/components/site/demo-email-form";

export const metadata = pageMeta(
  "/",
  "Adverse media and sanctions screening",
  "Entity-aware screening links names in global news to verified identities, so analysts review real risk instead of name collisions.",
);

const problems = [
  {
    icon: Users,
    title: "Name collisions",
    body: "Common names match many different people and companies. Analysts spend their day clearing hits that were never about their subject.",
  },
  {
    icon: Newspaper,
    title: "Language gaps",
    body: "Risk is reported in the language of the country where it happens. Keyword search in one language misses the rest, and transliteration breaks exact matching.",
  },
  {
    icon: Clock,
    title: "Slow manual review",
    body: "Without context, every alert needs the same manual research. Queues grow, and the genuine cases wait behind the noise.",
  },
];

const capabilities = [
  {
    icon: Layers,
    title: "Multilingual entity linking",
    body: "Mentions in 97 languages are linked to entities in a shared knowledge base.",
  },
  {
    icon: ShieldCheck,
    title: "Sanctions and PEP matching",
    body: "Names and aliases are matched against official lists with explained scores.",
  },
  {
    icon: FileSearch,
    title: "Adverse media classification",
    body: "Findings are categorized by type and severity, with the supporting passage highlighted.",
  },
  {
    icon: Network,
    title: "Ownership and network analysis",
    body: "Follow registry and leak data from a subject to its owners and associates.",
  },
  {
    icon: GitMerge,
    title: "Case management and audit trail",
    body: "Alerts become cases with notes, decisions, and a tamper-evident log.",
  },
  {
    icon: Plug,
    title: "API and batch screening",
    body: "Screen one name or a file of thousands over a documented HTTPS API.",
  },
];

const security = [
  {
    icon: Lock,
    title: "Encryption in transit and at rest",
    body: "TLS for every connection and encrypted storage for sensitive fields.",
  },
  {
    icon: KeyRound,
    title: "Role-based access with MFA",
    body: "Roles limit what each user can see, and privileged roles require multi-factor authentication.",
  },
  {
    icon: Users,
    title: "Tenant isolation",
    body: "Row-level security in the database keeps each tenant's data separate.",
  },
  {
    icon: ScrollText,
    title: "Tamper-evident audit log",
    body: "Every action is recorded in a hash-chained log that can be verified.",
  },
];

export default async function Home({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  const figures = proofFigures();
  const latest = articles().slice(0, 3);
  return (
    <>
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Organization",
          name: brand,
          url: siteUrl,
          description: "Adverse media and sanctions screening software.",
        }}
      />

      <section className="relative overflow-hidden bg-page">
        <div className="mesh absolute inset-0 opacity-70" aria-hidden />
        <div className="container-page relative grid grid-cols-1 items-center gap-12 py-16 lg:grid-cols-[1.05fr_1fr] lg:py-24">
          <div className="min-w-0">
            <p className="mb-3 text-sm font-semibold uppercase tracking-wider text-[#007c7c]">
              Adverse media and sanctions screening
            </p>
            <h1 className="text-4xl sm:text-display">
              Know exactly who you are dealing with, in every language.
            </h1>
            <p className="mt-5 max-w-xl text-lg text-slate-700">
              Entity-aware screening links names in global news to verified identities, so your
              analysts review real risk instead of name collisions.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/contact" className={buttonClass("primary", "lg")}>
                Request a demo
              </Link>
              <Link href="/login" className={buttonClass("secondary", "lg")}>
                Log in to the platform
              </Link>
            </div>
          </div>
          <div className="relative min-h-[22rem] min-w-0">
            <BrowserFrame caption="Illustrative interface with demo data">
              <AlertReviewMock />
            </BrowserFrame>
            <div className="absolute -start-4 -bottom-6 hidden md:block">
              <CriticalAlertCard />
            </div>
            <div className="absolute -bottom-14 end-6 hidden lg:block">
              <MiniGraph />
            </div>
          </div>
        </div>
      </section>

      <Section tone="white" className="!py-12">
        <dl className="grid gap-8 sm:grid-cols-2 lg:grid-cols-4">
          {figures.map((f) => (
            <div key={f.label} className="flex flex-col">
              <dt className="order-2 mt-1 text-slate-700">{f.label}</dt>
              <dd
                className={`order-1 text-h1 font-semibold tabular-nums ${f.pending ? "text-slate-500" : "text-ink"}`}
              >
                {f.value}
              </dd>
              <dd className="order-3 mt-1 text-caption text-slate-600">
                {f.note}.{" "}
                <Link href="/methodology" className="text-brand-600 underline underline-offset-2">
                  Methodology
                </Link>
              </dd>
            </div>
          ))}
        </dl>
      </Section>

      <Section>
        <SectionHeading
          eyebrow="The problem"
          title="Most alerts are about someone else"
          lead="Screening that matches on text alone cannot tell which person or company a news story describes."
        />
        <div className="grid gap-6 md:grid-cols-3">
          {problems.map(({ icon: Icon, title, body }) => (
            <Card key={title} lift>
              <Icon className="mb-4 size-8 text-brand-600" aria-hidden />
              <h3 className="text-h4">{title}</h3>
              <p className="mt-2 text-slate-700">{body}</p>
            </Card>
          ))}
        </div>
      </Section>

      <Section tone="white">
        <SectionHeading eyebrow="How it works" title="From global news to a reviewed alert" />
        <Stepper
          steps={[
            {
              title: "Ingest global news",
              body: "Articles are collected, cleaned, language-detected, and split into passages.",
              visual: <IngestArt />,
            },
            {
              title: "Link entities across languages",
              body: "Names are linked to knowledge base entities, or kept as unlinked mentions when none exists.",
              visual: <LinkArt />,
            },
            {
              title: "Match and score against lists",
              body: "Linked entities are matched to your watchlists and given an explained risk score.",
              visual: <MatchArt />,
            },
            {
              title: "Review and report",
              body: "Analysts decide on each alert. Every action is recorded in a full audit trail.",
              visual: <ReviewArt />,
            },
          ]}
        />
      </Section>

      <Section>
        <SectionHeading
          eyebrow="Capabilities"
          title="One workflow from first match to final report"
        />
        <div className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
          {capabilities.map(({ icon: Icon, title, body }) => (
            <Card key={title} lift>
              <span className="mb-4 flex size-11 items-center justify-center rounded-lg bg-brand-100 text-brand-600">
                <Icon className="size-6" aria-hidden />
              </span>
              <h3 className="text-h4">{title}</h3>
              <p className="mt-2 text-slate-700">{body}</p>
            </Card>
          ))}
        </div>
      </Section>

      <Section tone="white">
        <SectionHeading
          eyebrow="The product"
          title="See the work analysts do every day"
          lead="Illustrative renderings of the interface with demo data. Live product screenshots replace these once the application ships."
        />
        <Tabs
          label="Product views"
          items={[
            {
              id: "alerts",
              label: "Alert review",
              content: (
                <BrowserFrame caption="Alert review: the evidence passage is highlighted next to the score.">
                  <AlertReviewMock />
                </BrowserFrame>
              ),
            },
            {
              id: "network",
              label: "Entity network",
              content: (
                <BrowserFrame caption="Entity network: owners and associates around a subject.">
                  <NetworkMock />
                </BrowserFrame>
              ),
            },
            {
              id: "analytics",
              label: "Analytics",
              content: (
                <BrowserFrame caption="Analytics: volume, alerts, and review time.">
                  <AnalyticsMock />
                </BrowserFrame>
              ),
            },
          ]}
        />
      </Section>

      <Section>
        <SectionHeading eyebrow="Industries" title="Built for regulated teams" />
        <div className="grid gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {industries.map((i, n) => (
            <Link
              key={i.slug}
              href={`/industries/${i.slug}`}
              className="group relative block h-60 overflow-hidden rounded-card"
            >
              <div className="absolute inset-0 transition-transform duration-300 ease-out group-hover:scale-105">
                <Photo
                  slot={i.image}
                  fallback={
                    (["lines", "network", "chart", "layers", "shelves", "grid"] as const)[n % 6]!
                  }
                  seed={n + 3}
                />
              </div>
              <div className="absolute inset-0 bg-gradient-to-t from-ink/90 via-ink/30 to-transparent" />
              <div className="absolute inset-x-0 bottom-0 p-5 text-white">
                <p className="text-h4">{i.title}</p>
                <p className="mt-1 flex items-center gap-1 text-sm text-slate-200">
                  {i.short}
                  <ArrowRight className="size-4 shrink-0 rtl:rotate-180" aria-hidden />
                </p>
              </div>
            </Link>
          ))}
        </div>
      </Section>

      <Section tone="ink">
        <SectionHeading
          dark
          eyebrow="Security"
          title="Designed to protect sensitive investigations"
          lead="The platform is designed to align with common security frameworks. It holds no certifications, and the security page lists what is and is not claimed."
        />
        <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          {security.map(({ icon: Icon, title, body }) => (
            <div key={title} className="rounded-card border border-dark-line bg-dark-surface p-6">
              <Icon className="mb-4 size-7 text-[#7fd4d4]" aria-hidden />
              <h3 className="text-lg text-white">{title}</h3>
              <p className="mt-2 text-slate-300">{body}</p>
            </div>
          ))}
        </div>
        <Link
          href="/security"
          className="mt-8 inline-flex items-center gap-1 font-medium text-white underline underline-offset-4"
        >
          Read about security <ArrowRight className="size-4 rtl:rotate-180" aria-hidden />
        </Link>
      </Section>

      <Section>
        <SectionHeading eyebrow="Insights" title="Notes on screening practice" />
        <div className="grid gap-6 md:grid-cols-3">
          {latest.map((a) => (
            <Card key={a.slug} lift className="flex flex-col">
              <p className="text-caption text-slate-600">
                {a.date} · {a.readingMinutes} min read
              </p>
              <h3 className="mt-2 text-h4">
                <Link href={`/resources/${a.slug}`} className="hover:text-brand-600">
                  {a.title}
                </Link>
              </h3>
              <p className="mt-2 flex-1 text-slate-700">{a.description}</p>
            </Card>
          ))}
        </div>
      </Section>

      <section className="bg-navy py-16">
        <div className="container-page grid items-center gap-8 lg:grid-cols-2">
          <div>
            <h2 className="text-h2 text-white">See it on your own screening cases</h2>
            <p className="mt-2 text-lg text-slate-200">
              Share your work email and we will arrange a walkthrough.
            </p>
          </div>
          <DemoEmailForm />
        </div>
      </section>
    </>
  );
}
