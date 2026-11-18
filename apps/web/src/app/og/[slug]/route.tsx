import { ImageResponse } from "next/og";
import { industries, brand } from "@/content/site";
import { articles } from "@/lib/content";
import { ogSlug } from "@/lib/seo";
import { solutions } from "@/content/pages";

export const dynamicParams = false;

const pages: Record<string, { title: string; sub: string }> = {
  home: {
    title: "Know exactly who you are dealing with, in every language",
    sub: "Adverse media and sanctions screening",
  },
  platform: { title: "One pipeline from news article to reviewed alert", sub: "Platform" },
  pricing: { title: "Plans that scale with screening volume", sub: "Illustrative pricing" },
  security: { title: "Security that can be inspected", sub: "Security" },
  developers: { title: "Screen from your own systems", sub: "Developers" },
  methodology: { title: "How the platform is built and measured", sub: "Methodology" },
  resources: { title: "Notes on screening practice", sub: "Resources" },
  company: { title: "Screening that analysts can trust and explain", sub: "Company" },
  contact: { title: "Request a demo", sub: "Contact" },
};

function registry() {
  const all = { ...pages };
  for (const s of solutions)
    all[ogSlug(`/solutions/${s.slug}`)] = { title: s.title, sub: "Solutions" };
  for (const i of industries)
    all[ogSlug(`/industries/${i.slug}`)] = {
      title: `Screening for ${i.title.toLowerCase()}`,
      sub: "Industries",
    };
  for (const a of articles())
    all[ogSlug(`/resources/${a.slug}`)] = { title: a.title, sub: "Resources" };
  for (const l of ["privacy", "terms", "cookies"])
    all[ogSlug(`/legal/${l}`)] = { title: l[0]!.toUpperCase() + l.slice(1), sub: "Legal" };
  return all;
}

export const generateStaticParams = () => Object.keys(registry()).map((slug) => ({ slug }));

export async function GET(_: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  const page = registry()[slug];
  if (!page) return new Response("Not found", { status: 404 });
  return new ImageResponse(
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        flexDirection: "column",
        justifyContent: "space-between",
        padding: 72,
        background: "linear-gradient(135deg,#0B1B33 0%,#12305A 60%,#1F5FBF 100%)",
        color: "#fff",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", fontSize: 30, fontWeight: 600 }}>
        <div
          style={{
            width: 40,
            height: 40,
            borderRadius: 10,
            background: "#2F74D8",
            marginRight: 16,
          }}
        />
        {brand}
      </div>
      <div style={{ display: "flex", flexDirection: "column" }}>
        <div style={{ fontSize: 28, color: "#7fd4d4", marginBottom: 16 }}>{page.sub}</div>
        <div style={{ fontSize: 64, fontWeight: 700, lineHeight: 1.1, letterSpacing: -1 }}>
          {page.title}
        </div>
      </div>
      <div style={{ fontSize: 24, color: "#cbd5e1" }}>
        Matches are leads for human review and not legal determinations.
      </div>
    </div>,
    { width: 1200, height: 630 },
  );
}
