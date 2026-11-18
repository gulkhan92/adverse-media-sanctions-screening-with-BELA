import { Check, X } from "lucide-react";
import { Card } from "@/components/ui/card";
import { CtaBand, PageHeader, Section, SectionHeading } from "@/components/site/sections";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/security",
  "Security",
  "Architecture, data protection, access control, audit, retention, and an honest list of what is not claimed.",
);

const blocks = [
  {
    id: "architecture",
    title: "Architecture summary",
    body: [
      "A web application and an HTTPS API sit behind a reverse proxy. The API stores data in PostgreSQL. A job queue runs ingestion, matching, and report generation in separate worker processes. Language models run locally, so article text and screening subjects are not sent to external services.",
    ],
  },
  {
    id: "data-protection",
    title: "Data protection",
    body: [
      "Connections are designed to use TLS. Sensitive fields such as dates of birth and uploaded batch rows are encrypted at the column level. Passwords are hashed with Argon2id, and API keys, reset tokens, and recovery codes are stored only as hashes.",
      "Column encryption uses a single key supplied by the environment, and key rotation is manual.",
    ],
  },
  {
    id: "access-control",
    title: "Access control and tenant isolation",
    body: [
      "Six roles limit what each user can see and do. Administrator and platform roles require multi-factor authentication with a time-based one-time password. Sessions use short-lived tokens in secure cookies with CSRF protection, refresh tokens rotate, and reuse of a refresh token revokes the session.",
      "Each tenant's data is separated by row-level security in the database, so a query cannot return another tenant's rows even if application code has a fault. This is verified by tests that run directly against the database.",
    ],
  },
  {
    id: "audit",
    title: "Audit log",
    body: [
      "Security-relevant actions are written to an append-only log. Each entry includes a hash of the previous entry, so a change to history is detected when the chain is verified. The database refuses updates and deletes on the log.",
    ],
  },
  {
    id: "retention",
    title: "Data retention",
    body: [
      "The retention period is stored per tenant. The scheduled purge and the permanent deletion of removed subjects are not yet implemented, and this page will change when they are.",
    ],
  },
  {
    id: "responsible-use",
    title: "Responsible use and limitations",
    body: [
      "Matches are leads for human review and not legal determinations. Scores are a documented weighted formula whose weights have not been fitted to reviewer outcomes. The platform uses public sources that are incomplete, and absence of a match does not show absence of risk.",
    ],
  },
];

const notClaimed = [
  "No security certification or attestation, including SOC 2 and ISO 27001, is held.",
  "An OWASP ZAP baseline scan has not yet been run; it is scheduled before release.",
  "No independent penetration test has been performed.",
  "Delivery of password reset and notification emails has not been tested against a production mail service.",
  "Availability targets and a public status page do not exist, because the service is a demonstration.",
];

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <>
      <PageHeader
        eyebrow="Security"
        title="Security that can be inspected"
        lead="The platform is designed to align with common security practices such as least privilege, defense in depth, and auditability. This page states what is implemented and what is not."
      />
      {blocks.map((b, i) => (
        <Section key={b.id} id={b.id} tone={i % 2 ? "page" : "white"}>
          <div className="grid gap-8 lg:grid-cols-[1fr_2fr]">
            <h2 className="text-h2">{b.title}</h2>
            <div className="space-y-4 text-lg text-slate-700">
              {b.body.map((p) => (
                <p key={p}>{p}</p>
              ))}
            </div>
          </div>
        </Section>
      ))}
      <Section id="availability">
        <SectionHeading
          title="What is not claimed"
          lead="Stating limits plainly is part of the security posture."
        />
        <Card>
          <ul className="space-y-3">
            {notClaimed.map((n) => (
              <li key={n} className="flex gap-3">
                <X className="mt-1 size-5 shrink-0 text-danger" aria-hidden />
                <span>{n}</span>
              </li>
            ))}
          </ul>
        </Card>
        <p className="mt-6 flex items-center gap-2 text-sm text-slate-600">
          <Check className="size-4 text-success" aria-hidden />
          Verification details are kept in the repository documentation and are re-run with every
          change.
        </p>
      </Section>
      <CtaBand
        title="Review the controls with us"
        body="We can walk through the architecture and the verification results."
      />
    </>
  );
}
