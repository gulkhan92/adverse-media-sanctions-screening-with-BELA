"use client";
import { useMemo, useState } from "react";
import { Link } from "@/i18n/navigation";
import { Card } from "@/components/ui/card";
import { cn } from "@/lib/cn";

interface Item {
  slug: string;
  title: string;
  description: string;
  date: string;
  tags: string[];
  readingMinutes: number;
}

export function ArticleList({ items }: { items: Item[] }) {
  const tags = useMemo(() => Array.from(new Set(items.flatMap((i) => i.tags))).sort(), [items]);
  const [active, setActive] = useState<string | null>(null);
  const shown = active ? items.filter((i) => i.tags.includes(active)) : items;
  return (
    <>
      <div role="group" aria-label="Filter by tag" className="mb-8 flex flex-wrap gap-2">
        {[null, ...tags].map((t) => (
          <button
            key={t ?? "all"}
            type="button"
            aria-pressed={active === t}
            onClick={() => setActive(t)}
            className={cn(
              "rounded-full border px-4 py-1.5 text-sm font-medium",
              active === t
                ? "border-ink bg-ink text-white"
                : "border-line bg-white text-slate-700 hover:border-slate-400",
            )}
          >
            {t ?? "All"}
          </button>
        ))}
      </div>
      <ul className="grid gap-6 md:grid-cols-2 lg:grid-cols-3">
        {shown.map((a) => (
          <li key={a.slug}>
            <Card lift className="flex h-full flex-col">
              <p className="text-caption text-slate-600">
                {a.date} · {a.readingMinutes} min read
              </p>
              <h2 className="mt-2 text-h4">
                <Link href={`/resources/${a.slug}`} className="hover:text-brand-600">
                  {a.title}
                </Link>
              </h2>
              <p className="mt-2 flex-1 text-slate-700">{a.description}</p>
              <p className="mt-4 text-caption text-slate-600">{a.tags.join(" · ")}</p>
            </Card>
          </li>
        ))}
      </ul>
    </>
  );
}
