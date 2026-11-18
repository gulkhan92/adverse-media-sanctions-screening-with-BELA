import { cn } from "@/lib/cn";

export type Tone = "neutral" | "brand" | "success" | "warning" | "danger";
export type RiskLevel = "low" | "medium" | "high" | "critical";

const tones: Record<Tone, string> = {
  neutral: "bg-slate-100 text-slate-700",
  brand: "bg-brand-100 text-brand-600",
  success: "bg-[#e3f4ec] text-[#176d45]",
  warning: "bg-[#fbeedb] text-[#8f5400]",
  danger: "bg-[#f8e3e0] text-[#9c2d21]",
};
const risk: Record<RiskLevel, { tone: Tone; label: string }> = {
  low: { tone: "success", label: "Low" },
  medium: { tone: "warning", label: "Medium" },
  high: { tone: "danger", label: "High" },
  critical: { tone: "danger", label: "Critical" },
};

export function Badge({
  tone = "neutral",
  className,
  children,
}: {
  tone?: Tone;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-caption font-medium",
        tones[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Risk level badge. The level is spelled out so color is never the only signal. */
export function RiskBadge({ level }: { level: RiskLevel }) {
  const { tone, label } = risk[level];
  return (
    <Badge
      tone={tone}
      className={level === "critical" ? "font-semibold ring-1 ring-danger/40" : ""}
    >
      {label}
    </Badge>
  );
}
