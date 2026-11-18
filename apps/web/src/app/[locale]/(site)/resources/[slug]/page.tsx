import { notFound } from "next/navigation";
import { routing } from "@/i18n/routing";
import { Link } from "@/i18n/navigation";
import { Badge } from "@/components/ui/badge";
import { Mdx } from "@/components/site/mdx";
import { CtaBand, Disclaimer, JsonLd, PageHeader, Section } from "@/components/site/sections";
import { brand, siteUrl } from "@/content/site";
import { articles } from "@/lib/content";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const dynamicParams = false;
export const generateStaticParams = () =>
  routing.locales.flatMap((locale) => articles().map((a) => ({ locale, slug: a.slug })));

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const a = articles().find((x) => x.slug === slug);
  return a ? pageMeta(`/resources/${slug}`, a.title, a.description) : {};
}

export default async function Page({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  await enter(params);
  const { slug } = await params;
  const list = articles();
  const a = list.find((x) => x.slug === slug);
  if (!a) notFound();
  const others = list.filter((x) => x.slug !== slug).slice(0, 2);
  return (
    <>
      <JsonLd
        data={{
          "@context": "https://schema.org",
          "@type": "Article",
          headline: a.title,
          description: a.description,
          datePublished: a.date,
          author: { "@type": "Organization", name: brand },
          publisher: { "@type": "Organization", name: brand },
          mainEntityOfPage: `${siteUrl}/resources/${a.slug}`,
        }}
      />
      <PageHeader
        title={a.title}
        lead={a.description}
        crumbs={[
          { href: "/", label: "Home" },
          { href: "/resources", label: "Resources" },
          { label: a.title },
        ]}
      >
        <div className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
          <time dateTime={a.date}>{a.date}</time>
          <span aria-hidden>·</span>
          <span>{a.readingMinutes} min read</span>
          {a.tags.map((t) => (
            <Badge key={t} tone="brand">
              {t}
            </Badge>
          ))}
        </div>
      </PageHeader>
      <Section tone="white">
        <article>
          <Mdx source={a.body} />
        </article>
        <div className="mt-10 max-w-2xl">
          <Disclaimer />
        </div>
      </Section>
      <Section>
        <h2 className="mb-6 text-h3">Keep reading</h2>
        <ul className="grid gap-4 md:grid-cols-2">
          {others.map((o) => (
            <li key={o.slug} className="rounded-card border border-line bg-white p-5">
              <Link href={`/resources/${o.slug}`} className="text-h4 hover:text-brand-600">
                {o.title}
              </Link>
              <p className="mt-1 text-slate-700">{o.description}</p>
            </li>
          ))}
        </ul>
      </Section>
      <CtaBand
        title="See entity-aware screening in action"
        body="Request a walkthrough with demo data."
      />
    </>
  );
}
