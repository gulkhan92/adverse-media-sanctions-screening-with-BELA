import { RiskBadge } from "@/components/ui/badge";

/** Browser frame used around illustrative interface renderings. */
export function BrowserFrame({
  children,
  caption,
}: {
  children: React.ReactNode;
  caption?: string;
}) {
  return (
    <figure className="overflow-hidden rounded-card border border-line bg-white shadow-e3">
      <div className="flex items-center gap-2 border-b border-line bg-slate-100 px-4 py-2.5">
        <span className="size-2.5 rounded-full bg-slate-300" />
        <span className="size-2.5 rounded-full bg-slate-300" />
        <span className="size-2.5 rounded-full bg-slate-300" />
        <span className="ms-3 truncate rounded bg-white px-3 py-0.5 text-caption text-slate-500">
          app.atlas-screening.example
        </span>
      </div>
      {children}
      {caption ? (
        <figcaption className="border-t border-line bg-slate-50 px-4 py-2 text-caption text-slate-600">
          {caption}
        </figcaption>
      ) : null}
    </figure>
  );
}

const ROWS = [
  { name: "Al-Mansouri Trading FZE", list: "OFAC SDN", level: "critical" as const, score: 92 },
  { name: "Northgate Maritime Ltd", list: "Adverse media", level: "high" as const, score: 71 },
  { name: "Viktor Hollenbeck", list: "PEP", level: "medium" as const, score: 54 },
  { name: "Sunrise Provisions Co", list: "Adverse media", level: "low" as const, score: 28 },
];

export function AlertReviewMock() {
  return (
    <div className="grid min-h-[22rem] grid-cols-1 text-sm sm:grid-cols-[1fr_1.1fr]" aria-hidden>
      <div className="border-b border-line sm:border-b-0 sm:border-e">
        <p className="border-b border-line bg-slate-50 px-4 py-2 text-caption font-semibold uppercase tracking-wide text-slate-600">
          Alert queue
        </p>
        {ROWS.map((r, i) => (
          <div
            key={r.name}
            className={`flex items-center justify-between gap-2 border-b border-line px-4 py-3 ${i === 0 ? "bg-brand-100" : ""}`}
          >
            <div className="min-w-0">
              <p className="truncate font-medium text-ink">{r.name}</p>
              <p className="text-caption text-slate-600">{r.list}</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="tabular-nums text-slate-700">{r.score}</span>
              <RiskBadge level={r.level} />
            </div>
          </div>
        ))}
      </div>
      <div className="p-4">
        <p className="text-caption font-semibold uppercase tracking-wide text-slate-600">
          Evidence
        </p>
        <p className="mt-2 leading-7 text-slate-800">
          The regulator confirmed that{" "}
          <mark className="rounded bg-[#fde8b8] px-1">Al-Mansouri Trading</mark> had processed
          shipments through a{" "}
          <mark className="rounded bg-[#cfe3fb] px-1">designated intermediary</mark> in 2024.
        </p>
        <dl className="mt-4 grid grid-cols-2 gap-3 text-caption">
          <div className="rounded-control border border-line p-3">
            <dt className="text-slate-600">Match confidence</dt>
            <dd className="mt-0.5 text-base font-semibold text-ink">0.94</dd>
          </div>
          <div className="rounded-control border border-line p-3">
            <dt className="text-slate-600">Link route</dt>
            <dd className="mt-0.5 text-base font-semibold text-ink">Entity linking</dd>
          </div>
        </dl>
        <p className="mt-4 text-caption text-slate-600">Matches are leads for human review.</p>
      </div>
    </div>
  );
}

export function NetworkMock() {
  const nodes = [
    [50, 50, "#00A3A3", 9],
    [20, 25, "#2F74D8", 6],
    [80, 22, "#2F74D8", 6],
    [15, 75, "#6C7A89", 5],
    [82, 72, "#D55E5E", 7],
    [50, 88, "#2F74D8", 5],
    [35, 48, "#6C7A89", 4],
    [66, 46, "#2F74D8", 5],
  ] as const;
  const edges = [
    [0, 1],
    [0, 2],
    [0, 4],
    [0, 5],
    [0, 6],
    [0, 7],
    [1, 3],
    [2, 7],
    [4, 7],
    [3, 6],
  ];
  return (
    <svg
      viewBox="0 0 100 100"
      role="img"
      aria-label="Illustrative ownership network"
      className="h-full min-h-[22rem] w-full bg-slate-50"
    >
      {edges.map(([a, b], i) => (
        <line
          key={i}
          x1={nodes[a!]![0]}
          y1={nodes[a!]![1]}
          x2={nodes[b!]![0]}
          y2={nodes[b!]![1]}
          stroke="#94A3B8"
          strokeWidth=".5"
        />
      ))}
      {nodes.map(([x, y, c, r], i) => (
        <circle key={i} cx={x} cy={y} r={r} fill={c} />
      ))}
    </svg>
  );
}

export function AnalyticsMock() {
  const bars = [38, 52, 44, 61, 58, 73, 66, 81, 77, 90, 84, 96];
  return (
    <div className="grid min-h-[22rem] gap-4 p-5" aria-hidden>
      <div className="grid grid-cols-3 gap-3">
        {[
          ["Screenings", "12,480"],
          ["Alerts", "1,326"],
          ["Median review", "3.8 h"],
        ].map(([k, v]) => (
          <div key={k} className="rounded-control border border-line p-3">
            <p className="text-caption text-slate-600">{k}</p>
            <p className="text-lg font-semibold text-ink tabular-nums">{v}</p>
          </div>
        ))}
      </div>
      <svg
        viewBox="0 0 240 90"
        className="w-full"
        role="img"
        aria-label="Illustrative volume chart"
      >
        {bars.map((h, i) => (
          <rect
            key={i}
            x={i * 20 + 4}
            y={90 - h * 0.85}
            width="14"
            height={h * 0.85}
            rx="2"
            fill={i === bars.length - 1 ? "#00A3A3" : "#2F74D8"}
          />
        ))}
      </svg>
    </div>
  );
}

export function CriticalAlertCard() {
  return (
    <div className="w-64 rounded-card border border-line bg-white p-4 shadow-e3">
      <div className="flex items-center justify-between">
        <p className="text-caption font-semibold uppercase tracking-wide text-slate-600">
          New alert
        </p>
        <RiskBadge level="critical" />
      </div>
      <p className="mt-2 font-semibold text-ink">Al-Mansouri Trading FZE</p>
      <p className="text-caption text-slate-600">Sanctions list match, score 92</p>
    </div>
  );
}

export function MiniGraph() {
  return (
    <div className="w-44 overflow-hidden rounded-card border border-line bg-white p-2 shadow-e3">
      <svg viewBox="0 0 100 70" role="img" aria-label="Illustrative network" className="w-full">
        <g stroke="#94A3B8" strokeWidth=".8">
          <line x1="50" y1="35" x2="18" y2="14" />
          <line x1="50" y1="35" x2="84" y2="18" />
          <line x1="50" y1="35" x2="76" y2="58" />
          <line x1="50" y1="35" x2="22" y2="56" />
        </g>
        <circle cx="50" cy="35" r="8" fill="#00A3A3" />
        {[
          [18, 14],
          [84, 18],
          [76, 58],
          [22, 56],
        ].map(([x, y]) => (
          <circle key={`${x}${y}`} cx={x} cy={y} r="5" fill="#2F74D8" />
        ))}
      </svg>
    </div>
  );
}
