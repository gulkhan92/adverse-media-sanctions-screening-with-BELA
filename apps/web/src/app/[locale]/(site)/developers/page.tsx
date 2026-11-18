import { buttonClass } from "@/components/ui/button";
import { Tabs } from "@/components/ui/tabs";
import { CtaBand, PageHeader, Section, SectionHeading } from "@/components/site/sections";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/developers",
  "Developers",
  "Screen subjects over a documented HTTPS API: authentication, samples, rate limits, and the live reference.",
);

const body = `{"subject": {"name": "Viktor Example", "kind": "person", "country": "RU", "dob": "1970-01-01"}}`;

const samples = [
  {
    id: "curl",
    label: "curl",
    code: `curl -X POST https://api.example.com/api/v1/screenings \\\n  -H "X-API-Key: $ATLAS_API_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${body}'`,
  },
  {
    id: "python",
    label: "Python",
    code: `import os, httpx\n\nresponse = httpx.post(\n    "https://api.example.com/api/v1/screenings",\n    headers={"X-API-Key": os.environ["ATLAS_API_KEY"]},\n    json={"subject": {"name": "Viktor Example", "kind": "person", "country": "RU", "dob": "1970-01-01"}},\n    timeout=10,\n)\nresponse.raise_for_status()\nprint(response.json()["risk_level"])`,
  },
  {
    id: "js",
    label: "JavaScript",
    code: `const response = await fetch("https://api.example.com/api/v1/screenings", {\n  method: "POST",\n  headers: {\n    "X-API-Key": process.env.ATLAS_API_KEY,\n    "Content-Type": "application/json",\n  },\n  body: JSON.stringify({ subject: { name: "Viktor Example", kind: "person", country: "RU", dob: "1970-01-01" } }),\n});\nif (!response.ok) throw new Error(await response.text());\nconsole.log((await response.json()).risk_level);`,
  },
];

const Code = ({ children }: { children: string }) => (
  <pre
    tabIndex={0}
    className="overflow-x-auto rounded-card bg-ink p-5 font-mono text-sm leading-6 text-slate-100"
  >
    <code>{children}</code>
  </pre>
);

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <>
      <PageHeader
        eyebrow="Developers"
        title="Screen from your own systems"
        lead="A JSON API over HTTPS covers single, batch, and text screening, alerts, cases, and reports. The host in the samples is a placeholder."
      >
        {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
        <a href="/api/docs" className={buttonClass("primary", "md")}>
          Open the live API reference
        </a>
      </PageHeader>
      <Section tone="white">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <SectionHeading title="API overview" />
            <ul className="space-y-2 text-lg text-slate-700">
              <li>
                Base path <code className="font-mono text-base">/api/v1</code>, JSON request and
                response bodies.
              </li>
              <li>Errors use the problem details format with a request identifier.</li>
              <li>Lists use cursor pagination.</li>
              <li>
                The OpenAPI document is served at{" "}
                <code className="font-mono text-base">/api/openapi.json</code>.
              </li>
            </ul>
          </div>
          <div>
            <SectionHeading title="Authentication" />
            <div className="space-y-3 text-lg text-slate-700">
              <p>
                Server-to-server calls use an API key sent in the{" "}
                <code className="font-mono text-base">X-API-Key</code> header. Keys belong to one
                tenant, carry scopes, and are shown once when created. Only a hash is stored.
              </p>
              <p>The web application uses short-lived session cookies with a CSRF token instead.</p>
            </div>
          </div>
        </div>
      </Section>
      <Section>
        <SectionHeading
          title="Screen one subject"
          lead="Batch requests require an Idempotency-Key header, so a retry returns the original result instead of screening twice."
        />
        <Tabs
          label="Code samples"
          items={samples.map((s) => ({ id: s.id, label: s.label, content: <Code>{s.code}</Code> }))}
        />
      </Section>
      <Section tone="white">
        <SectionHeading title="Rate limits" />
        <div className="overflow-x-auto" tabIndex={0} role="region" aria-label="Table, scrollable">
          <table className="w-full min-w-[28rem] border-collapse">
            <thead>
              <tr className="border-b border-line text-start">
                <th className="py-2 pe-4 text-start" scope="col">
                  Caller
                </th>
                <th className="px-4 py-2 text-start" scope="col">
                  Default limit
                </th>
              </tr>
            </thead>
            <tbody>
              {[
                ["API key", "600 requests per minute"],
                ["Signed-in user", "240 requests per minute"],
                ["IP address", "300 requests per minute"],
                ["Sign-in attempts per IP", "20 per 5 minutes"],
              ].map(([a, b]) => (
                <tr key={a} className="border-b border-line">
                  <th scope="row" className="py-2 pe-4 text-start font-medium">
                    {a}
                  </th>
                  <td className="px-4 py-2 tabular-nums">{b}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="mt-4 text-slate-700">
          Responses over the limit use status 429 with a{" "}
          <code className="font-mono text-base">Retry-After</code> header. Batch requests accept up
          to 1,000 subjects, and CSV uploads up to 10,000.
        </p>
      </Section>
      <CtaBand
        title="Need access for a trial?"
        body="Request a demo and we will set up a demonstration tenant and API key."
      />
    </>
  );
}
