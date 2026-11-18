import { Check } from "lucide-react";
import { Art } from "@/components/site/art";
import {
  AlertReviewMock,
  AnalyticsMock,
  BrowserFrame,
  NetworkMock,
} from "@/components/site/product-visuals";
import { CtaBand, PageHeader, Section } from "@/components/site/sections";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/platform",
  "Platform",
  "How articles become reviewed alerts: ingestion, entity linking, matching, review, analytics, and an API.",
);

const nodes = [
  "Ingest",
  "Extract and chunk",
  "Entity linking",
  "Rerank and adjudicate",
  "Match and score",
  "Alert and review",
];

const modules = [
  {
    id: "ingestion",
    title: "Ingestion",
    body: "News metadata and article text are collected, cleaned, and stored with source and retrieval date.",
    points: [
      "Language detection per article",
      "Passage splitting with sentence boundaries",
      "Source and license recorded for every record",
    ],
    visual: <Art kind="lines" seed={2} />,
  },
  {
    id: "entity-linking",
    title: "Entity linking",
    body: "Names in text are linked to entities in a knowledge base across 97 languages, with a second stage for precision.",
    points: [
      "Mention detection and linking in one pass",
      "Cross-encoder reranking for English passages",
      "A local language model decides hard cases",
      "Unlinked mentions kept and matched by name and context",
    ],
    visual: <Art kind="network" seed={9} />,
  },
  {
    id: "matching",
    title: "Matching and scoring",
    body: "Subjects are matched to list entries and to linked media findings. Each score shows its components.",
    points: [
      "Trigram and embedding similarity on names and aliases",
      "Date of birth, country, and identifier checks",
      "Documented weighted score with levels from low to critical",
    ],
    visual: <Art kind="grid" seed={4} />,
  },
  {
    id: "review",
    title: "Review and cases",
    body: "Alerts are triaged, resolved with a reason, and grouped into cases with notes.",
    points: [
      "Evidence passage highlighted next to the score",
      "Assignment, escalation, and resolution",
      "Hash-chained audit log of every action",
    ],
    visual: (
      <BrowserFrame>
        <AlertReviewMock />
      </BrowserFrame>
    ),
  },
  {
    id: "analytics",
    title: "Analytics",
    body: "Operational dashboards show screening volume, alert quality, review time, and queue age.",
    points: [
      "Volume and alerts by day, week, or month",
      "Precision from reviewer decisions",
      "Latency and queue ageing",
    ],
    visual: (
      <BrowserFrame>
        <AnalyticsMock />
      </BrowserFrame>
    ),
  },
  {
    id: "api",
    title: "API",
    body: "Everything in the interface is available over a documented HTTPS API.",
    points: [
      "Single, batch, and text screening",
      "API keys with scopes and rate limits",
      "Idempotency keys on write requests",
    ],
    visual: (
      <BrowserFrame>
        <NetworkMock />
      </BrowserFrame>
    ),
  },
];

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <>
      <PageHeader
        eyebrow="Platform"
        title="One pipeline from news article to reviewed alert"
        lead="Entity linking runs once, at ingestion. Screening then reads stored results, so a check stays fast and every result can be traced to its source."
      />
      <Section tone="white">
        <ol className="grid gap-3 md:grid-cols-6" aria-label="Pipeline">
          {nodes.map((n, i) => (
            <li key={n} className="rounded-card border border-line bg-page p-4 text-center">
              <span className="text-caption font-semibold text-brand-600">Step {i + 1}</span>
              <p className="mt-1 font-medium text-ink">{n}</p>
            </li>
          ))}
        </ol>
        <p className="mt-4 text-sm text-slate-600">
          Interface renderings on this page are illustrative and use demo data.
        </p>
      </Section>
      {modules.map((m, i) => (
        <Section key={m.id} id={m.id} tone={i % 2 ? "white" : "page"}>
          <div
            className={`grid items-center gap-10 lg:grid-cols-2 ${i % 2 ? "lg:[&>*:first-child]:order-2" : ""}`}
          >
            <div>
              <h2 className="text-h2">{m.title}</h2>
              <p className="mt-3 text-lg text-slate-700">{m.body}</p>
              <ul className="mt-6 space-y-3">
                {m.points.map((p) => (
                  <li key={p} className="flex gap-3">
                    <Check className="mt-1 size-5 shrink-0 text-success" aria-hidden />
                    <span>{p}</span>
                  </li>
                ))}
              </ul>
            </div>
            <div className="overflow-hidden rounded-card">{m.visual}</div>
          </div>
        </Section>
      ))}
      <CtaBand
        title="See the pipeline on your cases"
        body="We will walk through screening, review, and reporting with demo data."
      />
    </>
  );
}
