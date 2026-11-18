/** Four in-house illustrations for the "How it works" steps. Decorative. */
const wrap = (children: React.ReactNode) => (
  <svg viewBox="0 0 160 90" className="h-24 w-full" aria-hidden>
    <rect width="160" height="90" rx="10" fill="#E6EFFB" />
    {children}
  </svg>
);

export const IngestArt = () =>
  wrap(
    <>
      {[0, 1, 2].map((i) => (
        <g key={i} transform={`translate(${18 + i * 8} ${16 + i * 8})`}>
          <rect width="64" height="44" rx="5" fill="#fff" stroke="#2F74D8" />
          <path
            d="M8 12h40M8 22h48M8 32h30"
            stroke="#94A3B8"
            strokeWidth="3"
            strokeLinecap="round"
          />
        </g>
      ))}
      <path
        d="M104 45h30m-8-8 8 8-8 8"
        stroke="#00A3A3"
        strokeWidth="3"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>,
  );

export const LinkArt = () =>
  wrap(
    <>
      <rect x="14" y="30" width="48" height="30" rx="5" fill="#fff" stroke="#2F74D8" />
      <text x="38" y="49" textAnchor="middle" fontSize="11" fill="#0B1B33">
        محمد
      </text>
      <rect x="98" y="30" width="48" height="30" rx="5" fill="#fff" stroke="#2F74D8" />
      <text x="122" y="49" textAnchor="middle" fontSize="11" fill="#0B1B33">
        Q123
      </text>
      <path d="M64 45h32" stroke="#00A3A3" strokeWidth="3" strokeDasharray="4 4" />
      <circle cx="80" cy="45" r="6" fill="#00A3A3" />
    </>,
  );

export const MatchArt = () =>
  wrap(
    <>
      <rect x="18" y="20" width="56" height="50" rx="6" fill="#fff" stroke="#2F74D8" />
      {[0, 1, 2, 3].map((i) => (
        <rect
          key={i}
          x="26"
          y={28 + i * 10}
          width={i === 1 ? 40 : 30}
          height="4"
          rx="2"
          fill={i === 1 ? "#C0392B" : "#CBD5E1"}
        />
      ))}
      <circle cx="116" cy="45" r="22" fill="#fff" stroke="#12305A" strokeWidth="2" />
      <text x="116" y="51" textAnchor="middle" fontSize="16" fontWeight="600" fill="#12305A">
        87
      </text>
      <path d="M76 45h16" stroke="#00A3A3" strokeWidth="3" strokeLinecap="round" />
    </>,
  );

export const ReviewArt = () =>
  wrap(
    <>
      {[0, 1, 2].map((i) => (
        <g key={i}>
          <circle cx="30" cy={24 + i * 22} r="6" fill={i === 2 ? "#00A3A3" : "#2F74D8"} />
          <rect
            x="44"
            y={19 + i * 22}
            width={70 - i * 10}
            height="10"
            rx="3"
            fill="#fff"
            stroke="#CBD5E1"
          />
        </g>
      ))}
      <path d="M30 30v16m0 6v-2" stroke="#94A3B8" />
      <path
        d="M124 56l8 8 16-18"
        stroke="#1E8E5A"
        strokeWidth="4"
        fill="none"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </>,
  );
