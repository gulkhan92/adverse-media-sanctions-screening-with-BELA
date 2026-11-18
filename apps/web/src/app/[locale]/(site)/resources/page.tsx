import { articles } from "@/lib/content";
import { PageHeader, Section } from "@/components/site/sections";
import { ArticleList } from "@/components/site/article-list";
import { enter } from "@/lib/page";
import { pageMeta } from "@/lib/seo";

export const metadata = pageMeta(
  "/resources",
  "Resources",
  "Articles on screening practice: name collisions, entity linking, ownership networks, measurement, and responsible use.",
);

export default async function Page({ params }: { params: Promise<{ locale: string }> }) {
  await enter(params);
  const list = articles().map(({ body: _body, ...rest }) => rest);
  return (
    <>
      <PageHeader
        eyebrow="Resources"
        title="Notes on screening practice"
        lead="Plain explanations of how screening works, how to measure it, and where it needs human judgment."
      />
      <Section>
        <ArticleList items={list} />
      </Section>
    </>
  );
}
