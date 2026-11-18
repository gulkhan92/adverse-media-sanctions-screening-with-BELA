import { Link } from "@/i18n/navigation";
import { buttonClass } from "@/components/ui/button";
import { Section } from "@/components/site/sections";

export default function NotFound() {
  return (
    <Section tone="white">
      <div className="mx-auto max-w-xl text-center">
        <p className="text-sm font-semibold uppercase tracking-wider text-[#007c7c]">404</p>
        <h1 className="mt-2 text-h1">Page not found</h1>
        <p className="mt-3 text-lg text-slate-700">
          The page may have moved. Start again from the home page.
        </p>
        <Link href="/" className={buttonClass("primary", "md", "mt-8")}>
          Home
        </Link>
      </div>
    </Section>
  );
}
