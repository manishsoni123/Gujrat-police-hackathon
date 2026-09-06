#!/usr/bin/env node
// Screenshot pages of a PDF with the Google Chrome / Edge PDF viewer (headless) so the export can be checked
// without opening a desktop viewer. Chromium's bundled headless shell has no PDF viewer, so this needs the
// Chrome or Edge channel that Playwright can drive (the same one frontend/e2e/live_screenshots.mjs uses).
//
//   node docs/tools/preview-pdf.mjs docs/export/Sentinel-Gujarat_HLD.pdf 1 5 12     # pages -> docs/export/.build/preview/<name>-p<N>.png
//   node docs/tools/preview-pdf.mjs <pdf> all                                       # every page (page count read from the file)
//   node docs/tools/preview-pdf.mjs <pdf> 5 --zoom 70                               # landscape pages need a smaller zoom to fit the 1000 px viewport
//
// Exit code 1 when the file is missing or no viewer-capable browser is available.

import { readFile, mkdir } from 'node:fs/promises';
import { basename, dirname, join, resolve } from 'node:path';
import { pathToFileURL, fileURLToPath } from 'node:url';
import { loadPlaywright, countPdfPages } from './export-pdf.mjs';

const here = dirname(fileURLToPath(import.meta.url));

function fail(m) { process.stderr.write(`preview-pdf: ${m}\n`); process.exit(1); }

const argv = process.argv.slice(2);
const zi = argv.indexOf('--zoom');
const zoom = zi >= 0 ? Number(argv.splice(zi, 2)[1]) : 100;
const [file, ...pageArgs] = argv;
if (!file) fail('usage: preview-pdf.mjs <file.pdf> [all | page numbers...]');
const pdfPath = resolve(file);
const buf = await readFile(pdfPath).catch(() => fail(`cannot read ${pdfPath}`));
const total = countPdfPages(buf);
const pages = pageArgs.length === 0 ? [1] : pageArgs[0] === 'all' ? Array.from({ length: total }, (_, i) => i + 1) : pageArgs.map(Number);
const outDir = resolve(here, '..', 'export', '.build', 'preview');
await mkdir(outDir, { recursive: true });

const { chromium } = await loadPlaywright();
let browser = null;
for (const channel of ['chrome', 'msedge']) {
  try { browser = await chromium.launch({ channel, headless: true }); break; } catch { /* next */ }
}
if (!browser) fail('needs Google Chrome or Microsoft Edge (Playwright channel) - the bundled Chromium has no PDF viewer');
const page = await browser.newPage({ viewport: { width: 1000, height: 1400 }, deviceScaleFactor: 1 });
const stem = basename(pdfPath, '.pdf');
for (const n of pages) {
  if (n < 1 || n > total) { process.stdout.write(`skip page ${n} (document has ${total})\n`); continue; }
  // A fragment-only change does not reload the viewer plugin, so go through about:blank; big files need a moment to paint.
  await page.goto('about:blank');
  await page.goto(`${pathToFileURL(pdfPath).href}#page=${n}&zoom=${zoom}&toolbar=0`, { waitUntil: 'load' });
  await page.waitForTimeout(2500 + Math.min(6000, buf.length / 250));
  const out = join(outDir, `${stem}-p${n}.png`);
  await page.screenshot({ path: out });
  process.stdout.write(`${out}\n`);
}
await browser.close();
process.stdout.write(`preview-pdf: ${pdfPath} has ${total} page(s)\n`);
