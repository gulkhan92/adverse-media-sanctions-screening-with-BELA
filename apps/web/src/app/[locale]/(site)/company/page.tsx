import { Card } from "@/components/ui/card";
import { Link } from "@/i18n/navigation";
import { CtaBand, PageHeader, Section, SectionHeading } from "@/components/site/sections";
import { contactEmail } from "@/content/site";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/company",
  "Company",
  "Our mission, approach to responsible use, and principles.",
);

const principles = [
  {
    title: "Evidence over assertion",
    body: "Every figure we publish is measured and reproducible from the repository, and anything not yet measured is marked as pending.",
  },
  {
    title: "People decide",
    body: "The system finds and explains leads. Analysts and compliance officers decide what they mean.",
  },
  {
    title: "Explain the score",
    body: "A result without its reasoning cannot be reviewed. Every score shows the components behind it.",
  },
  {
    title: "Protect the data",
    body: "Sensitive investigations deserve strong isolation, access control, and an audit trail that can be verified.",
  },
];

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  return (
    <>
      <PageHeader
        eyebrow="Company"
        title="Screening that analysts can trust and explain"
        lead="We build screening software that links names in global news to verified identities, so compliance teams spend their time on real risk."
      />
      <Section tone="white">
        <div className="grid gap-12 lg:grid-cols-2">
          <div>
            <SectionHeading title="Mission" />
            <p className="text-lg text-slate-700">
              Reduce the time compliance teams spend on name collisions, and make sure that risk
              reported in any language reaches the person responsible for the decision.
            </p>
          </div>
          <div>
            <SectionHeading title="Approach to responsible use" />
            <p className="text-lg text-slate-700">
              Automated screening produces leads, not determinations. We design for human review,
              show the evidence behind each result, measure error rates openly, and record
              limitations alongside results. See the{" "}
              <Link href="/methodology" className="text-brand-600 underline underline-offset-4">
                methodology
              </Link>{" "}
              and the article on{" "}
              <Link
                href="/resources/responsible-use-of-automated-screening"
                className="text-brand-600 underline underline-offset-4"
              >
                responsible use
              </Link>
              .
            </p>
          </div>
        </div>
      </Section>
      <Section>
        <SectionHeading title="Principles" />
        <div className="grid gap-6 md:grid-cols-2">
          {principles.map((p) => (
            <Card key={p.title}>
              <h3 className="text-h4">{p.title}</h3>
              <p className="mt-2 text-slate-700">{p.body}</p>
            </Card>
          ))}
        </div>
      </Section>
      <Section tone="white">
        <SectionHeading title="Contact" />
        <dl className="grid gap-4 sm:grid-cols-2">
          <div>
            <dt className="font-medium text-ink">Sales and demonstrations</dt>
            <dd className="text-slate-700">
              <Link href="/contact" className="text-brand-600 underline underline-offset-4">
                Request a demo
              </Link>
            </dd>
          </div>
          <div>
            <dt className="font-medium text-ink">General enquiries</dt>
            <dd className="text-slate-700">{contactEmail}</dd>
          </div>
        </dl>
        <p className="mt-6 text-sm text-slate-600">
          This is a demonstration product. The email address above is a placeholder domain and is
          not monitored.
        </p>
      </Section>
      <CtaBand title="Talk to the team" body="Tell us about your screening workflow." />
    </>
  );
}
