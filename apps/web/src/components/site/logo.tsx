export function Logo({ className = "", light = false }: { className?: string; light?: boolean }) {
  const ink = light ? "#FFFFFF" : "#0B1B33";
  return (
    <span className={`inline-flex items-center gap-2.5 ${className}`}>
      <svg width="30" height="30" viewBox="0 0 32 32" aria-hidden>
        <rect width="32" height="32" rx="8" fill="#12305A" />
        <circle cx="16" cy="16" r="9" fill="none" stroke="#2F74D8" strokeWidth="2" />
        <path d="M16 7v18M7 16h18" stroke="#00A3A3" strokeWidth="1.5" />
        <circle cx="16" cy="16" r="2.6" fill="#FFFFFF" />
      </svg>
      <span style={{ color: ink }} className="text-[1.05rem] font-semibold tracking-tight">
        Atlas <span style={{ color: light ? "#9CC1F5" : "#1F5FBF" }}>Screening</span>
      </span>
    </span>
  );
}
