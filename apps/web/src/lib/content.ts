import fs from "node:fs";
import path from "node:path";
import matter from "gray-matter";

const root = path.join(process.cwd(), "src", "content");

export interface Article {
  slug: string;
  title: string;
  description: string;
  date: string;
  tags: string[];
  readingMinutes: number;
  body: string;
}

/** YAML turns an unquoted date into a Date object; always return a plain YYYY-MM-DD string. */
const day = (value: unknown): string =>
  value instanceof Date ? value.toISOString().slice(0, 10) : String(value);

function read(dir: string, file: string) {
  const raw = fs.readFileSync(path.join(root, dir, file), "utf8");
  const { data, content } = matter(raw);
  return { data, content };
}

export function articles(): Article[] {
  return fs
    .readdirSync(path.join(root, "articles"))
    .filter((f) => f.endsWith(".mdx"))
    .map((file) => {
      const { data, content } = read("articles", file);
      const words = content.split(/\s+/).filter(Boolean).length;
      return {
        slug: file.replace(/\.mdx$/, ""),
        title: String(data.title),
        description: String(data.description),
        date: day(data.date),
        tags: (data.tags as string[]) ?? [],
        readingMinutes: Math.max(1, Math.round(words / 220)),
        body: content,
      };
    })
    .sort((a, b) => b.date.localeCompare(a.date));
}

export interface LegalDoc {
  slug: string;
  title: string;
  updated: string;
  body: string;
}

export function legal(slug: string): LegalDoc | null {
  const file = `${slug}.mdx`;
  if (!fs.existsSync(path.join(root, "legal", file))) return null;
  const { data, content } = read("legal", file);
  return { slug, title: String(data.title), updated: day(data.updated), body: content };
}

export const legalSlugs = ["privacy", "terms", "cookies"] as const;
