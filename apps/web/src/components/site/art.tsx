import { cn } from "@/lib/cn";
import manifest from "@/content/images.json";

type ArtKind = "network" | "lines" | "grid" | "layers" | "shelves" | "waves" | "chart";

const seeded = (seed: number) => {
  let s = seed;
  return () => {
    s = (s * 1664525 + 1013904223) % 4294967296;
    return s / 4294967296;
  };
};

/** In-house abstract artwork in the brand palette. Decorative, so hidden from assistive technology. */
export function Art({
  kind,
  className,
  seed = 7,
}: {
  kind: ArtKind;
  className?: string;
  seed?: number;
}) {
  const r = seeded(seed);
  const body = (() => {
    switch (kind) {
      case "network": {
        const pts = Array.from({ length: 22 }, () => [r() * 400, r() * 240] as const);
        return (
          <>
            {pts.flatMap((a, i) =>
              pts
                .slice(i + 1)
                .filter((b) => Math.hypot(a[0] - b[0], a[1] - b[1]) < 95)
                .map((b, j) => (
                  <line
                    key={`${i}-${j}`}
                    x1={a[0]}
                    y1={a[1]}
                    x2={b[0]}
                    y2={b[1]}
                    stroke="#2F74D8"
                    strokeOpacity=".5"
                  />
                )),
            )}
            {pts.map((p, i) => (
              <circle
                key={i}
                cx={p[0]}
                cy={p[1]}
                r={i % 5 === 0 ? 6 : 3.5}
                fill={i % 5 === 0 ? "#00A3A3" : "#9CC1F5"}
              />
            ))}
          </>
        );
      }
      case "lines":
        return Array.from({ length: 18 }, (_, i) => (
          <path
            key={i}
            d={`M0 ${20 + i * 12} C 120 ${i * 6}, 260 ${60 + i * 10}, 400 ${10 + i * 12}`}
            fill="none"
            stroke="#2F74D8"
            strokeOpacity={0.25 + (i % 4) * 0.12}
          />
        ));
      case "grid":
        return Array.from({ length: 6 }, (_, y) =>
          Array.from({ length: 10 }, (_, x) => (
            <rect
              key={`${x}${y}`}
              x={x * 40 + 4}
              y={y * 40 + 4}
              width="32"
              height="32"
              rx="4"
              fill={r() > 0.6 ? "#00A3A3" : "#2F74D8"}
              fillOpacity={0.15 + r() * 0.5}
            />
          )),
        );
      case "layers":
        return Array.from({ length: 5 }, (_, i) => (
          <rect
            key={i}
            x={40 + i * 26}
            y={30 + i * 22}
            width="220"
            height="120"
            rx="10"
            fill="#fff"
            fillOpacity=".08"
            stroke="#9CC1F5"
            strokeOpacity=".6"
          />
        ));
      case "shelves":
        return Array.from({ length: 4 }, (_, row) =>
          Array.from({ length: 26 }, (_, i) => (
            <rect
              key={`${row}${i}`}
              x={12 + i * 15}
              y={20 + row * 56 + (1 - (0.4 + r() * 0.6)) * 40}
              width="10"
              height={(0.4 + r() * 0.6) * 40}
              fill={i % 7 === 0 ? "#00A3A3" : "#2F74D8"}
              fillOpacity=".55"
            />
          )),
        );
      case "waves":
        return Array.from({ length: 9 }, (_, i) => (
          <path
            key={i}
            d={`M0 ${100 + i * 12} Q 100 ${40 + i * 8} 200 ${100 + i * 12} T 400 ${100 + i * 12}`}
            fill="none"
            stroke={i % 3 === 0 ? "#00A3A3" : "#2F74D8"}
            strokeOpacity=".5"
          />
        ));
      case "chart": {
        let y = 160;
        const d = Array.from({ length: 24 }, (_, i) => {
          y = Math.max(30, Math.min(200, y + (r() - 0.55) * 40));
          return `${i === 0 ? "M" : "L"}${i * 17} ${y}`;
        }).join(" ");
        return <path d={d} fill="none" stroke="#00A3A3" strokeWidth="2.5" />;
      }
    }
  })();
  return (
    <svg
      viewBox="0 0 400 240"
      preserveAspectRatio="xMidYMid slice"
      aria-hidden
      className={cn("h-full w-full bg-ink", className)}
    >
      <defs>
        <radialGradient id={`g-${kind}-${seed}`} cx="70%" cy="20%" r="80%">
          <stop offset="0" stopColor="#1F5FBF" stopOpacity=".55" />
          <stop offset="1" stopColor="#0B1B33" stopOpacity="0" />
        </radialGradient>
      </defs>
      <rect width="400" height="240" fill={`url(#g-${kind}-${seed})`} />
      {body}
    </svg>
  );
}

type Entry = { width: number; height: number; sizes: number[]; alt: string };

/** Responsive photograph from the optimized manifest, or in-house artwork when none is present. */
export function Photo({
  slot,
  fallback,
  seed,
  className,
  sizes = "(min-width: 1024px) 33vw, 100vw",
  priority = false,
}: {
  slot: string;
  fallback: ArtKind;
  seed?: number;
  className?: string;
  sizes?: string;
  priority?: boolean;
}) {
  const entry = (manifest as Record<string, Entry>)[slot];
  if (!entry) return <Art kind={fallback} seed={seed} className={className} />;
  const set = (ext: string) =>
    entry.sizes.map((w) => `/images/${slot}-${w}.${ext} ${w}w`).join(", ");
  const largest = entry.sizes[entry.sizes.length - 1];
  return (
    <picture>
      <source type="image/avif" srcSet={set("avif")} sizes={sizes} />
      <source type="image/webp" srcSet={set("webp")} sizes={sizes} />
      <img
        src={`/images/${slot}-${largest}.webp`}
        alt={entry.alt}
        width={entry.width}
        height={entry.height}
        loading={priority ? "eager" : "lazy"}
        fetchPriority={priority ? "high" : "auto"}
        className={cn("h-full w-full object-cover", className)}
      />
    </picture>
  );
}
