// Generates two short CC0 alert tones as 16-bit PCM WAV files under public/sounds.
// Run: node e2e/make_sounds.mjs
import { writeFileSync, mkdirSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const out = join(here, '..', 'public', 'sounds');
mkdirSync(out, { recursive: true });

function wav(notes, rate = 22050) {
  const total = notes.reduce((s, n) => s + n.dur, 0);
  const frames = Math.floor(total * rate);
  const data = Buffer.alloc(frames * 2);
  let idx = 0;
  for (const n of notes) {
    const len = Math.floor(n.dur * rate);
    for (let i = 0; i < len; i += 1) {
      const t = i / rate;
      const env = Math.min(1, i / (rate * 0.01)) * Math.exp(-3 * t / n.dur);
      const v = Math.sin(2 * Math.PI * n.freq * t) * env * 0.6 * (n.freq ? 1 : 0);
      data.writeInt16LE(Math.round(v * 32767), (idx + i) * 2);
    }
    idx += len;
  }
  const header = Buffer.alloc(44);
  header.write('RIFF', 0);
  header.writeUInt32LE(36 + data.length, 4);
  header.write('WAVE', 8);
  header.write('fmt ', 12);
  header.writeUInt32LE(16, 16);
  header.writeUInt16LE(1, 20);
  header.writeUInt16LE(1, 22);
  header.writeUInt32LE(rate, 24);
  header.writeUInt32LE(rate * 2, 28);
  header.writeUInt16LE(2, 32);
  header.writeUInt16LE(16, 34);
  header.write('data', 36);
  header.writeUInt32LE(data.length, 40);
  return Buffer.concat([header, data]);
}

writeFileSync(join(out, 'alert.wav'), wav([{ freq: 784, dur: 0.18 }, { freq: 988, dur: 0.22 }]));
writeFileSync(
  join(out, 'alert-critical.wav'),
  wav([{ freq: 1046, dur: 0.16 }, { freq: 0, dur: 0.04 }, { freq: 1046, dur: 0.16 }, { freq: 0, dur: 0.04 }, { freq: 1318, dur: 0.4 }]),
);
console.log('wrote', out);
