/**
 * Plate normalisation (CONTRACT §3.4) — a faithful port of the shared
 * algorithm so the UI can preview what the API will search for, and
 * format plates for display identically to the PDF renderer.
 */

export interface NormResult {
  plate_norm: string;
  is_valid_format: boolean;
  pattern: 'standard' | 'bh' | null;
  substitutions: number;
}

const TO_LETTER: Record<string, string> = { '0': 'O', '1': 'I', '5': 'S', '8': 'B', '2': 'Z', '6': 'G' };
const TO_DIGIT: Record<string, string> = { O: '0', I: '1', S: '5', B: '8', Z: '2', G: '6', Q: '0', D: '0' };
const STANDARD = /^[A-Z]{2}[0-9]{1,2}[A-Z]{1,3}[0-9]{4}$/;
const BH = /^[0-9]{2}BH[0-9]{4}[A-Z]{1,2}$/;

const fixLetter = (c: string): string => TO_LETTER[c] ?? c;
const fixDigit = (c: string): string => TO_DIGIT[c] ?? c;

function countSubs(a: string, b: string): number {
  let n = 0;
  for (let i = 0; i < a.length; i += 1) if (a[i] !== b[i]) n += 1;
  return n;
}

export function normalisePlate(raw: string): NormResult {
  let s = (raw ?? '').toUpperCase().replace(/[^A-Z0-9]/g, '');
  if (s.startsWith('IND')) s = s.slice(3);
  if (!s) return { plate_norm: '', is_valid_format: false, pattern: null, substitutions: 0 };
  if (s.length < 7 || s.length > 10) return { plate_norm: s, is_valid_format: false, pattern: null, substitutions: 0 };

  let standard: { cand: string; subs: number } | null = null;
  const mid = s.slice(2, -4);
  for (const k of [2, 1]) {
    const digits = mid.slice(0, k);
    const series = mid.slice(k);
    if (digits.length < k) continue;
    if (series.length < 1 || series.length > 3) continue;
    const cand =
      fixLetter(s[0]) +
      fixLetter(s[1]) +
      digits.split('').map(fixDigit).join('') +
      series.split('').map(fixLetter).join('') +
      s.slice(-4).split('').map(fixDigit).join('');
    if (!STANDARD.test(cand)) continue;
    if (k === 1 && cand[2] === '0') continue;
    const subs = countSubs(cand, s);
    if (!standard || subs < standard.subs) standard = { cand, subs };
  }

  let bh: { cand: string; subs: number } | null = null;
  if (s.length === 9 || s.length === 10) {
    const cand =
      fixDigit(s[0]) +
      fixDigit(s[1]) +
      fixLetter(s[2]) +
      fixLetter(s[3]) +
      s.slice(4, 8).split('').map(fixDigit).join('') +
      s.slice(8).split('').map(fixLetter).join('');
    if (BH.test(cand)) bh = { cand, subs: countSubs(cand, s) };
  }

  const acceptedStd = standard && standard.subs <= 2 ? standard : null;
  const acceptedBh = bh && bh.subs <= 2 ? bh : null;
  if (acceptedStd && (!acceptedBh || acceptedStd.subs <= acceptedBh.subs)) {
    return { plate_norm: acceptedStd.cand, is_valid_format: true, pattern: 'standard', substitutions: acceptedStd.subs };
  }
  if (acceptedBh) return { plate_norm: acceptedBh.cand, is_valid_format: true, pattern: 'bh', substitutions: acceptedBh.subs };
  return { plate_norm: s, is_valid_format: false, pattern: null, substitutions: 0 };
}

/** "GJ01AB1234" → "GJ 01 AB 1234"; "22BH4321AA" → "22 BH 4321 AA"; invalid → unchanged. */
export function formatPlate(norm: string | null | undefined): string {
  if (!norm) return '—';
  const std = /^([A-Z]{2})([0-9]{1,2})([A-Z]{1,3})([0-9]{4})$/.exec(norm);
  if (std) return `${std[1]} ${std[2]} ${std[3]} ${std[4]}`;
  const bh = /^([0-9]{2})(BH)([0-9]{4})([A-Z]{1,2})$/.exec(norm);
  if (bh) return `${bh[1]} ${bh[2]} ${bh[3]} ${bh[4]}`;
  return norm;
}
