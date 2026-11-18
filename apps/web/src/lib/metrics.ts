import metrics from "../../public/metrics.json";

export interface ProofFigure {
  label: string;
  value: string;
  note: string;
  pending?: boolean;
}

export { metrics };

const nf = new Intl.NumberFormat("en-US");

/** Figures for the home page proof strip, all read from metrics.json. Unmeasured values are pending. */
export function proofFigures(): ProofFigure[] {
  const ev = metrics.evaluation as { available: boolean; false_match_reduction_pct: number | null };
  const reduction = ev.available && ev.false_match_reduction_pct != null;
  return [
    {
      label: "Languages supported by the linking model",
      value: String(metrics.languages.value),
      note: metrics.languages.source,
    },
    {
      label: "Watchlist entries indexed",
      value: nf.format(metrics.watchlist_entries.value),
      note: "Official sanctions list entries in the demonstration index",
    },
    {
      label: "Median screening latency",
      value: `${metrics.latency_ms.p50} ms`,
      note: "Measured on one development machine, 150 sequential requests",
    },
    {
      label: "Fewer false matches than fuzzy matching",
      value: reduction ? `${ev.false_match_reduction_pct}%` : "Pending",
      note: reduction
        ? "Measured on the evaluation set"
        : "Evaluation not yet run; no figure is claimed",
      pending: !reduction,
    },
  ];
}

export const metricsGeneratedAt = metrics.generated_at;
