import type { CSSProperties } from 'react';

interface LogoProps {
  size?: number;
  /** Show the "Sentinel Gujarat" wordmark next to the mark. */
  wordmark?: boolean;
  /** Wordmark colour (default white for the dark sidebar). */
  colour?: string;
  style?: CSSProperties;
}

/** Inline SVG shield-and-eye mark plus wordmark. */
export function LogoMark({ size = 32 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 64 64" role="img" aria-label="Sentinel Gujarat">
      <path
        d="M32 4 L54 12 V30 C54 44 44 54 32 60 C20 54 10 44 10 30 V12 Z"
        fill="#0B1F3A"
        stroke="#3B6FE0"
        strokeWidth="3"
        strokeLinejoin="round"
      />
      <path d="M17 33 C21 25 27 21 32 21 C37 21 43 25 47 33 C43 41 37 45 32 45 C27 45 21 41 17 33 Z" fill="#FFFFFF" />
      <circle cx="32" cy="33" r="6.5" fill="#1E4DB7" />
      <circle cx="34" cy="31" r="2" fill="#FFFFFF" />
    </svg>
  );
}

export function Logo({ size = 32, wordmark = true, colour = '#FFFFFF', style }: LogoProps) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, ...style }}>
      <LogoMark size={size} />
      {wordmark ? (
        <div style={{ lineHeight: 1.05 }}>
          <div style={{ fontWeight: 700, fontSize: size * 0.5, color: colour, letterSpacing: 0.2 }}>Sentinel</div>
          <div style={{ fontWeight: 500, fontSize: size * 0.36, color: colour, opacity: 0.78, letterSpacing: 1.4, textTransform: 'uppercase' }}>
            Gujarat
          </div>
        </div>
      ) : null}
    </div>
  );
}
