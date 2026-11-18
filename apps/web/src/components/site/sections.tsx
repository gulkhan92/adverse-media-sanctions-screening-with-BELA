import { Link } from "@/i18n/navigation";
import { buttonClass } from "@/components/ui/button";
import { Breadcrumbs } from "@/components/ui/misc";
import { cn } from "@/lib/cn";

export function Section({
  id,
  tone = "page",
  className,
  children,
}: {
  id?: string;
  tone?: "page" | "white" | "ink";
  className?: string;
  children: React.ReactNode;
}) {
  const bg = { page: "bg-page", white: "bg-white", ink: "bg-ink text-slate-200" }[tone];
  return (
    <section id={id} className={cn("section", bg, className)}>
      <div className="container-page">{children}</div>
    </section>
  );
}

export function SectionHeading({
  eyebrow,
  title,
  lead,
  align = "start",
  dark,
}: {
  eyebrow?: string;
  title: string;
  lead?: string;
  align?: "start" | "center";
  dark?: boolean;
}) {
  return (
    <div className={cn("mb-10 max-w-3xl", align === "center" && "mx-auto text-center")}>
      {eyebrow ? (
        <p
          className={cn(
            "mb-2 text-sm font-semibold uppercase tracking-wider",
            dark ? "text-[#7fd4d4]" : "text-teal-700 text-[#007c7c]",
          )}
        >
          {eyebrow}
        </p>
      ) : null}
      <h2 className={cn("text-h2", dark && "text-white")}>{title}</h2>
      {lead ? (
        <p className={cn("mt-3 text-lg", dark ? "text-slate-300" : "text-slate-700")}>{lead}</p>
      ) : null}
    </div>
  );
}

export function PageHeader({
  title,
  lead,
  eyebrow,
  crumbs,
  children,
}: {
  title: string;
  lead?: string;
  eyebrow?: string;
  crumbs?: { href?: string; label: string }[];
  children?: React.ReactNode;
}) {
  return (
    <div className="border-b border-line bg-white">
      <div className="container-page py-12 lg:py-16">
        {crumbs ? (
          <div className="mb-6">
            <Breadcrumbs items={crumbs} />
          </div>
        ) : null}
        {eyebrow ? (
          <p className="mb-2 text-sm font-semibold uppercase tracking-wider text-[#007c7c]">
            {eyebrow}
          </p>
        ) : null}
        <h1 className="max-w-4xl text-4xl sm:text-h1">{title}</h1>
        {lead ? <p className="mt-4 max-w-3xl text-lg text-slate-700">{lead}</p> : null}
        {children ? <div className="mt-8">{children}</div> : null}
      </div>
    </div>
  );
}

export function CtaBand({ title, body }: { title: string; body: string }) {
  return (
    <section className="bg-navy py-16">
      <div className="container-page flex flex-col items-start justify-between gap-8 lg:flex-row lg:items-center">
        <div className="max-w-2xl">
          <h2 className="text-h2 text-white">{title}</h2>
          <p className="mt-2 text-lg text-slate-200">{body}</p>
        </div>
        <Link
          href="/contact"
          className={buttonClass("primary", "lg", "bg-white text-ink hover:bg-brand-100")}
        >
          Request a demo
        </Link>
      </div>
    </section>
  );
}

export function Disclaimer() {
  return (
    <p className="rounded-control border border-line bg-white px-4 py-3 text-sm text-slate-700">
      Matches are leads for human review and not legal determinations.
    </p>
  );
}

export function JsonLd({ data }: { data: object }) {
  return (
    <script
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data).replace(/</g, "\\u003c") }}
    />
  );
}
