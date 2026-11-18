import { Link } from "@/i18n/navigation";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

export function Skeleton({ className }: { className?: string }) {
  return (
    <div aria-hidden className={cn("animate-pulse rounded-control bg-slate-200", className)} />
  );
}

export function EmptyState({ title, body }: { title: string; body: string }) {
  return (
    <div className="rounded-card border border-dashed border-slate-300 p-10 text-center">
      <p className="text-lg font-semibold text-ink">{title}</p>
      <p className="mt-1 text-slate-600">{body}</p>
    </div>
  );
}

export function Breadcrumbs({ items }: { items: { href?: string; label: string }[] }) {
  return (
    <nav aria-label="Breadcrumb" className="text-sm text-slate-600">
      <ol className="flex flex-wrap items-center gap-1">
        {items.map((item, i) => (
          <li key={item.label} className="flex items-center gap-1">
            {item.href ? (
              <Link href={item.href} className="hover:text-ink hover:underline">
                {item.label}
              </Link>
            ) : (
              <span aria-current="page" className="text-ink">
                {item.label}
              </span>
            )}
            {i < items.length - 1 ? (
              <ChevronRight className="size-4 rtl:rotate-180" aria-hidden />
            ) : null}
          </li>
        ))}
      </ol>
    </nav>
  );
}

export interface Step {
  title: string;
  body: string;
  visual?: React.ReactNode;
}

export function Stepper({ steps }: { steps: Step[] }) {
  return (
    <ol className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
      {steps.map((s, i) => (
        <li
          key={s.title}
          className="relative rounded-card border border-line bg-white p-6 shadow-e1"
        >
          <span className="mb-4 flex size-9 items-center justify-center rounded-full bg-ink text-sm font-semibold text-white">
            {i + 1}
          </span>
          {s.visual ? <div className="mb-4">{s.visual}</div> : null}
          <h3 className="text-h4">{s.title}</h3>
          <p className="mt-2 text-slate-700">{s.body}</p>
        </li>
      ))}
    </ol>
  );
}

export function Sparkline({ values, label }: { values: number[]; label: string }) {
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const points = values
    .map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / span) * 24}`)
    .join(" ");
  return (
    <svg viewBox="0 0 100 30" role="img" aria-label={label} className="h-8 w-24">
      <polyline points={points} fill="none" stroke="var(--color-chart-1)" strokeWidth="2" />
    </svg>
  );
}

export function KpiCard({
  label,
  value,
  note,
  trend,
}: {
  label: string;
  value: string;
  note?: React.ReactNode;
  trend?: number[];
}) {
  return (
    <div className="rounded-card border border-line bg-white p-5 shadow-e1">
      <p className="text-sm text-slate-600">{label}</p>
      <div className="mt-1 flex items-end justify-between gap-3">
        <p className="text-h3 font-semibold text-ink tabular-nums">{value}</p>
        {trend ? <Sparkline values={trend} label={`${label} trend`} /> : null}
      </div>
      {note ? <p className="mt-2 text-caption text-slate-600">{note}</p> : null}
    </div>
  );
}
