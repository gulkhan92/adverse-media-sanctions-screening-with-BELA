import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Link } from "@/i18n/navigation";
import { CtaBand, PageHeader, Section, SectionHeading } from "@/components/site/sections";
import { metrics, metricsGeneratedAt, proofFigures } from "@/lib/metrics";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/methodology",
  "Methodology",
  "Data sources and licenses, models, evaluation design, results, limitations, and name-collision analysis.",
);

const sources = [
  [
    "OFAC SDN and Consolidated lists",
    "Primary sanctions watchlist",
    "U.S. government work, public domain",
  ],
  ["Wikidata subset", "Knowledge base entities and aliases", "CC0"],
  [
    "GLEIF Golden Copy",
    "Legal entity identifiers and ownership relationships",
    "GLEIF LEI data terms of use",
  ],
  [
    "ICIJ Offshore Leaks Database",
    "Offshore entities and officers for network analysis",
    "Database ODbL, contents CC BY-SA",
  ],
  [
    "GDELT DOC 2.0",
    "News metadata used to find articles",
    "Free for research use; publishers keep copyright",
  ],
  ["Mewsli-X", "Multilingual entity linking evaluation data", "Apache-2.0 per dataset card"],
];

const models = [
  ["BELA", "End-to-end multilingual mention detection and entity linking (MIT license)."],
  [
    "BLINK",
    "Two-stage bi-encoder and cross-encoder entity linking for English (MIT license; the upstream project is archived).",
  ],
  ["BAAI/bge-m3", "Multilingual embeddings used for name similarity (MIT license)."],
  [
    "Qwen3-4B-Instruct",
    "A 4 billion parameter local model that adjudicates uncertain links and classifies findings (Apache-2.0).",
  ],
];

const limitations = [
  "Evaluation stages after the fuzzy baseline have not been run, so the hybrid system's accuracy is not reported and no improvement is claimed.",
  "There is no hand-labeled gold set for adverse media yet, so precision and recall for adverse media classification are not measured.",
  "The Wikidata subset is partial. Entities missing from it cannot be linked and are kept as unlinked mentions.",
  "OpenSanctions data is not used in the commercial path because its license restricts commercial use.",
  "Score weights are defaults and are not fitted to reviewer decisions.",
  "Latency was measured on one development machine and covers watchlist matching only.",
];

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  const figures = proofFigures();
  const ev = metrics.evaluation as {
    available: boolean;
    systems: { name: string; precision?: number; recall?: number; f1?: number }[];
  };
  return (
    <>
      <PageHeader
        eyebrow="Methodology"
        title="How the platform is built and how it is measured"
        lead="Every figure on this site comes from a measurement stored with the project. This page lists the sources, the models, the evaluation design, and the limits."
      />
      <Section tone="white">
        <SectionHeading title="Data sources and licenses" />
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Table, scrollable">
          <table className="w-full min-w-[36rem] border-collapse text-start">
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className="py-2 pe-4 text-start">
                  Source
                </th>
                <th scope="col" className="px-4 py-2 text-start">
                  Use
                </th>
                <th scope="col" className="px-4 py-2 text-start">
                  License
                </th>
              </tr>
            </thead>
            <tbody>
              {sources.map(([a, b, c]) => (
                <tr key={a} className="border-b border-line">
                  <th scope="row" className="py-2 pe-4 text-start font-medium">
                    {a}
                  </th>
                  <td className="px-4 py-2">{b}</td>
                  <td className="px-4 py-2">{c}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Section>
      <Section>
        <SectionHeading title="Models" />
        <div className="grid gap-5 md:grid-cols-2">
          {models.map(([n, d]) => (
            <Card key={n}>
              <h3 className="text-h4">{n}</h3>
              <p className="mt-2 text-slate-700">{d}</p>
            </Card>
          ))}
        </div>
        <p className="mt-6 max-w-3xl text-slate-700">
          Routing: the end-to-end model links mentions first. High-confidence links are accepted,
          uncertain ones are reranked, and the hardest cases go to the local language model.
          Mentions with no knowledge base entity stay unlinked. Article text is treated as untrusted
          input to the language model.
        </p>
      </Section>
      <Section tone="white">
        <SectionHeading title="Evaluation design" />
        <div className="max-w-3xl space-y-4 text-lg text-slate-700">
          <p>
            Systems are compared on the same held-out passages: a fuzzy string baseline, the
            end-to-end linker alone, and the hybrid router. Metrics are precision, recall, and F1
            for entity linking, and the number of false matches a reviewer would see per 1,000
            screenings. Results are reported per language so that weak languages are visible.
          </p>
          <p>
            Name collisions are analyzed separately: for subjects whose names are shared by several
            entities, we count how many alerts refer to the wrong entity under each system.
          </p>
        </div>
      </Section>
      <Section>
        <SectionHeading
          title="Results"
          lead={`Read from metrics.json, generated ${metricsGeneratedAt.slice(0, 10)}.`}
        />
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Table, scrollable">
          <table className="w-full min-w-[32rem] border-collapse">
            <caption className="sr-only">Measured figures</caption>
            <thead>
              <tr className="border-b border-line">
                <th scope="col" className="py-2 pe-4 text-start">
                  Measure
                </th>
                <th scope="col" className="px-4 py-2 text-start">
                  Value
                </th>
                <th scope="col" className="px-4 py-2 text-start">
                  Basis
                </th>
              </tr>
            </thead>
            <tbody>
              {figures.map((f) => (
                <tr key={f.label} className="border-b border-line">
                  <th scope="row" className="py-2 pe-4 text-start font-medium">
                    {f.label}
                  </th>
                  <td className="px-4 py-2 tabular-nums">
                    {f.pending ? <Badge tone="warning">Pending</Badge> : f.value}
                  </td>
                  <td className="px-4 py-2 text-slate-700">{f.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {ev.available ? null : (
          <p className="mt-4 text-slate-700">
            Comparison tables for the baseline, the end-to-end linker, and the hybrid system will
            appear here when the evaluation has produced results. Until then, no accuracy claim is
            made.
          </p>
        )}
      </Section>
      <Section tone="white">
        <SectionHeading title="Known limitations" />
        <ul className="max-w-3xl list-disc space-y-3 ps-6 text-lg text-slate-700">
          {limitations.map((l) => (
            <li key={l}>{l}</li>
          ))}
        </ul>
        <p className="mt-8">
          <Link
            href="/resources/measuring-a-screening-system"
            className="text-brand-600 underline underline-offset-4"
          >
            Read: measuring a screening system
          </Link>
        </p>
      </Section>
      <CtaBand
        title="Ask us about the evaluation"
        body="We can share the evaluation design and reproduce results with you."
      />
    </>
  );
}
