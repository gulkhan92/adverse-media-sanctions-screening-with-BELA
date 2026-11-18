import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";
import { articles, legal, legalSlugs } from "@/lib/content";
import { industries } from "@/content/site";
import { industryDetails, solutions } from "@/content/pages";
import { ogSlug } from "@/lib/seo";
import { proofFigures } from "@/lib/metrics";

const EM_DASH = String.fromCharCode(0x2014);
const root = path.resolve(__dirname, "..");

function walk(dir: string): string[] {
  return fs.readdirSync(dir, { withFileTypes: true }).flatMap((e) => {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) return e.name === "node_modules" || e.name === ".next" ? [] : walk(p);
    return [p];
  });
}

describe("articles", () => {
  it("has six articles of 900 to 1,400 words", () => {
    const list = articles();
    expect(list).toHaveLength(6);
    for (const a of list) {
      const words = a.body.split(/\s+/).filter(Boolean).length;
      expect(words, a.slug).toBeGreaterThanOrEqual(900);
      expect(words, a.slug).toBeLessThanOrEqual(1400);
    }
  });
  it("has unique slugs and complete front matter", () => {
    const list = articles();
    expect(new Set(list.map((a) => a.slug)).size).toBe(list.length);
    for (const a of list) {
      expect(a.title.length).toBeGreaterThan(10);
      expect(a.description.length).toBeGreaterThan(40);
      expect(a.date).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
  });
});

describe("legal", () => {
  it("has the three policies", () => {
    for (const slug of legalSlugs) {
      const doc = legal(slug);
      expect(doc?.body.length ?? 0, slug).toBeGreaterThan(900);
      expect(doc?.updated, slug).toMatch(/^\d{4}-\d{2}-\d{2}$/);
    }
    expect(legal("missing")).toBeNull();
  });
});

describe("content conventions", () => {
  it("never uses the em dash and never leaves placeholder text", () => {
    const files = [...walk(path.join(root, "src")), ...walk(path.join(root, "messages"))].filter(
      (f) => /\.(ts|tsx|mdx|json|css|svg)$/.test(f),
    );
    for (const f of files) {
      const text = fs.readFileSync(f, "utf8");
      expect(text.includes(EM_DASH), f).toBe(false);
      expect(/lorem ipsum|\bTODO\b|\bTBD\b/i.test(text), f).toBe(false);
    }
  });
  it("has a detail record for every industry and a labeled scenario", () => {
    for (const i of industries)
      expect(
        industryDetails.some((d) => d.slug === i.slug),
        i.slug,
      ).toBe(true);
    expect(solutions).toHaveLength(3);
  });
});

describe("seo and metrics", () => {
  it("builds social image slugs", () => {
    expect(ogSlug("/")).toBe("home");
    expect(ogSlug("/solutions/adverse-media")).toBe("solutions--adverse-media");
  });
  it("shows unmeasured figures as pending and never invents a reduction", () => {
    const figs = proofFigures();
    expect(figs).toHaveLength(4);
    const reduction = figs.find((f) => f.label.startsWith("Fewer false matches"));
    expect(reduction?.pending).toBe(true);
    expect(reduction?.value).toBe("Pending");
  });
});
