import { notFound } from "next/navigation";
import { routing } from "@/i18n/routing";
import { Mdx } from "@/components/site/mdx";
import { PageHeader, Section } from "@/components/site/sections";
import { legal, legalSlugs } from "@/lib/content";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const dynamicParams = false;
export const generateStaticParams = () =>
  routing.locales.flatMap((locale) => legalSlugs.map((slug) => ({ locale, slug })));

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const doc = legal(slug);
  return doc
    ? pageMeta(`/legal/${slug}`, doc.title, `${doc.title} for the demonstration environment.`)
    : {};
}

export default async function Page({
  params,
}: {
  params: Promise<{ locale: string; slug: string }>;
}) {
  await enter(params);
  const { slug } = await params;
  const doc = legal(slug);
  if (!doc) notFound();
  return (
    <>
      <PageHeader
        title={doc.title}
        lead={`Last updated ${doc.updated}.`}
        crumbs={[{ href: "/", label: "Home" }, { label: "Legal" }, { label: doc.title }]}
      />
      <Section tone="white">
        <Mdx source={doc.body} />
      </Section>
    </>
  );
}
