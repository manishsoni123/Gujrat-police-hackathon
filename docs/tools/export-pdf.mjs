#!/usr/bin/env node
// Sentinel Gujarat - Markdown -> A4 PDF export for the submission documents.
//
// Usage (from the repository root, Node >= 20):
//   node docs/tools/export-pdf.mjs                      # HLD, SCALE-PLAN, REGISTRY-API, LICENCES, PHASE2-RUNBOOK, CONTRACT
//   node docs/tools/export-pdf.mjs HLD SCALE-PLAN       # a subset (names without .md)
//   node docs/tools/export-pdf.mjs --preview            # also writes docs/export/.build/<name>-preview.png (first pages, print media)
//   node docs/tools/export-pdf.mjs --out <dir>          # output directory (default docs/export)
//
// Dependencies: `npm i --no-save` inside docs/tools (marked, mermaid) and a Chromium: the frontend's
// Playwright install is used when present (frontend/node_modules/playwright), otherwise docs/tools/node_modules
// (`cd docs/tools && npx --yes playwright@1 install chromium`). Falls back to the Google Chrome channel.
//
// What it does:
//   * renders GitHub-flavoured Markdown with marked (tables, task lists, code, images as data URIs);
//   * mermaid fences are never left as text: the pre-rendered docs/diagrams/<name>.svg is inlined when the
//     fence matches its .mmd source, otherwise the fence is rendered in the page by mermaid (local copy from
//     node_modules or the jsDelivr CDN) and the run fails if any diagram did not produce an SVG;
//   * wide diagrams get their own landscape page; tables and code wrap inside the A4 text width;
//   * brand header "Sentinel Gujarat · Dynatech Consultancy", document title, page x of y, generation time in IST;
//   * `[FILL: ...]`, `[SCREENSHOT: ...]` and `[MEASURE]` markers are highlighted and counted so nothing
//     unfinished slips into the Drive folder unnoticed.
// Exit code 1 on any error (missing input, missing browser, unrendered diagram).

import { readFile, writeFile, mkdir, stat, access } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join, resolve, basename, extname } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const docsDir = resolve(here, '..');
const repoRoot = resolve(docsDir, '..');
const require = createRequire(import.meta.url);

const PRODUCT = 'Sentinel Gujarat';
const ORG = 'Dynatech Consultancy';
const VERSION = '1.0.0-phase1';
const MODEL_STATEMENT = 'Model 1 + Model 2 (hybrid roadmap to 3/4)';

// Output file names follow docs/submission-checklist.md §1 (Drive folder layout).
const DOCUMENTS = {
  HLD: { file: 'HLD.md', out: 'Sentinel-Gujarat_HLD.pdf', label: 'Technical Proposal / High-Level Design' },
  'SCALE-PLAN': { file: 'SCALE-PLAN.md', out: 'Sentinel-Gujarat_Plan-for-Scale.pdf', label: 'Plan for Scale' },
  'REGISTRY-API': { file: 'REGISTRY-API.md', out: 'Sentinel-Gujarat_REGISTRY-API.pdf', label: 'Model 1 registry API documentation' },
  LICENCES: { file: 'LICENCES.md', out: 'LICENCES.pdf', label: 'Open-source components and licences' },
  'PHASE2-RUNBOOK': { file: 'PHASE2-RUNBOOK.md', out: 'Sentinel-Gujarat_PHASE2-RUNBOOK.pdf', label: 'Phase 2 runbook' },
  CONTRACT: { file: 'CONTRACT.md', out: 'Sentinel-Gujarat_CONTRACT.pdf', label: 'API and data contract' },
};

function fail(message) {
  process.stderr.write(`export-pdf: ${message}\n`);
  process.exit(1);
}

function log(message) {
  process.stdout.write(`export-pdf: ${message}\n`);
}

function parseArgs(argv) {
  const opts = { names: [], out: join(docsDir, 'export'), preview: false };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--out') {
      opts.out = resolve(argv[++i] ?? '');
    } else if (a === '--preview') {
      opts.preview = true;
    } else if (a === '--help' || a === '-h') {
      process.stdout.write(`usage: export-pdf.mjs [--out <dir>] [--preview] [${Object.keys(DOCUMENTS).join('|')} ...]\n`);
      process.exit(0);
    } else {
      opts.names.push(a.replace(/\.md$/i, '').toUpperCase());
    }
  }
  if (opts.names.length === 0) opts.names = Object.keys(DOCUMENTS);
  for (const n of opts.names) if (!DOCUMENTS[n]) fail(`unknown document "${n}" (known: ${Object.keys(DOCUMENTS).join(', ')})`);
  return opts;
}

// ---------------------------------------------------------------------------------------------------------------
// Browser and library resolution
// ---------------------------------------------------------------------------------------------------------------

export async function loadPlaywright() {
  const candidates = [
    join(repoRoot, 'frontend', 'node_modules', 'playwright', 'index.mjs'),
    join(here, 'node_modules', 'playwright', 'index.mjs'),
  ];
  for (const c of candidates) {
    if (existsSync(c)) {
      const mod = await import(pathToFileURL(c).href);
      return { chromium: mod.chromium, from: c };
    }
  }
  fail('playwright not found. Run `cd frontend && npm ci` or `cd docs/tools && npx --yes playwright@1 install chromium` after `npm i --no-save playwright@1`.');
  return null;
}

export async function launchChromium(chromium) {
  const attempts = [
    { label: 'bundled chromium', options: {} },
    { label: 'Google Chrome channel', options: { channel: 'chrome' } },
    { label: 'Microsoft Edge channel', options: { channel: 'msedge' } },
  ];
  let lastError = null;
  for (const attempt of attempts) {
    try {
      const browser = await chromium.launch({ headless: true, ...attempt.options });
      return { browser, label: attempt.label };
    } catch (err) {
      lastError = err;
    }
  }
  fail(`no Chromium could be launched (${lastError?.message?.split('\n')[0]}). Install one with \`npx --yes playwright@1 install chromium\`.`);
  return null;
}

function loadMarked() {
  try {
    return require('marked');
  } catch {
    fail('marked not installed. Run `cd docs/tools && npm i --no-save`.');
    return null;
  }
}

function mermaidScriptSource() {
  const local = join(here, 'node_modules', 'mermaid', 'dist', 'mermaid.min.js');
  if (existsSync(local)) return { src: pathToFileURL(local).href, from: 'node_modules' };
  return { src: 'https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js', from: 'jsDelivr CDN' };
}

// ---------------------------------------------------------------------------------------------------------------
// Fonts (Inter from the frontend's @fontsource package when available; falls back to the system stack)
// ---------------------------------------------------------------------------------------------------------------

async function fontFaces() {
  const dir = join(repoRoot, 'frontend', 'node_modules', '@fontsource', 'inter', 'files');
  const weights = [400, 500, 600, 700];
  const faces = [];
  for (const w of weights) {
    const f = join(dir, `inter-latin-${w}-normal.woff2`);
    if (existsSync(f)) {
      const b64 = (await readFile(f)).toString('base64');
      faces.push(`@font-face{font-family:"Inter";font-style:normal;font-weight:${w};font-display:block;src:url(data:font/woff2;base64,${b64}) format("woff2");}`);
    }
  }
  return faces.join('\n');
}

// ---------------------------------------------------------------------------------------------------------------
// Markdown pre-processing: mermaid fences, marker highlighting
// ---------------------------------------------------------------------------------------------------------------

function normaliseMermaid(text) {
  return text
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('%%'))
    .join('\n');
}

function svgAspect(svg) {
  const m = svg.match(/viewBox="([\d.\s-]+)"/);
  if (!m) return 1.5;
  const [, , w, h] = m[1].trim().split(/\s+/).map(Number);
  return h > 0 ? w / h : 1.5;
}

function isolateSvg(svg, id) {
  // mermaid-cli writes every diagram with id="my-svg" and scopes its CSS to #my-svg; give each inlined copy a
  // unique id so several diagrams on one page do not restyle each other, and let the page size it.
  return svg
    .replace(/my-svg/g, id)
    .replace(/<svg([^>]*?)\swidth="[^"]*"/, '<svg$1')
    .replace(/<svg([^>]*?)\sheight="[^"]*"/, '<svg$1')
    .replace(/style="max-width:[^"]*"/, 'style="max-width:100%;height:auto;background-color:white"');
}

async function replaceMermaidFences(markdown, docName, stats) {
  const fence = /```mermaid\r?\n([\s\S]*?)```/g;
  let out = '';
  let last = 0;
  let n = 0;
  let m;
  while ((m = fence.exec(markdown)) !== null) {
    n += 1;
    const before = markdown.slice(0, m.index);
    out += markdown.slice(last, m.index);
    last = m.index + m[0].length;
    const body = m[1];
    const sourceRef = [...before.matchAll(/docs\/diagrams\/([\w-]+)\.mmd/g)].pop();
    const id = `${docName.toLowerCase().replace(/[^a-z0-9]/g, '')}-d${n}`;
    let svg = null;
    let caption = sourceRef ? `Figure: docs/diagrams/${sourceRef[1]}.mmd` : '';
    if (sourceRef) {
      const mmdPath = join(docsDir, 'diagrams', `${sourceRef[1]}.mmd`);
      const svgPath = join(docsDir, 'diagrams', `${sourceRef[1]}.svg`);
      if (existsSync(svgPath) && existsSync(mmdPath)) {
        const mmd = await readFile(mmdPath, 'utf8');
        if (normaliseMermaid(mmd) === normaliseMermaid(body)) {
          svg = await readFile(svgPath, 'utf8');
          stats.svgInlined += 1;
        } else {
          log(`  warning: mermaid fence ${n} differs from ${sourceRef[1]}.mmd - rendering the fence text; re-run mermaid-cli to refresh the SVG`);
        }
      }
    }
    if (svg) {
      const aspect = svgAspect(svg);
      const cls = aspect > 1.45 ? 'diagram landscape' : 'diagram';
      out += `\n\n<figure class="${cls}" id="${id}">${isolateSvg(svg, id)}<figcaption>${caption}</figcaption></figure>\n\n`;
    } else {
      stats.mermaidLive += 1;
      const escaped = body.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
      out += `\n\n<figure class="diagram pending" id="${id}"><pre class="mermaid">${escaped}</pre><figcaption>${caption}</figcaption></figure>\n\n`;
    }
  }
  out += markdown.slice(last);
  return out;
}

function highlightMarkers(html, stats) {
  return html.replace(/\[(FILL|SCREENSHOT|MEASURE)(:[^\]]*)?\]/g, (whole) => {
    stats.markers += 1;
    return `<mark class="todo">${whole}</mark>`;
  });
}

async function inlineImages(html, baseDir) {
  const imgs = [...html.matchAll(/<img([^>]*?)src="([^"]+)"/g)];
  for (const im of imgs) {
    const src = im[2];
    if (/^(data:|https?:)/i.test(src)) continue;
    const p = resolve(baseDir, decodeURIComponent(src));
    try {
      await access(p);
      const ext = extname(p).toLowerCase();
      const mime = ext === '.svg' ? 'image/svg+xml' : ext === '.jpg' || ext === '.jpeg' ? 'image/jpeg' : ext === '.gif' ? 'image/gif' : 'image/png';
      const b64 = (await readFile(p)).toString('base64');
      html = html.replace(im[0], `<img${im[1]}src="data:${mime};base64,${b64}"`);
    } catch {
      log(`  warning: image not found: ${src}`);
    }
  }
  return html;
}

// ---------------------------------------------------------------------------------------------------------------
// HTML template
// ---------------------------------------------------------------------------------------------------------------

function istNow() {
  return new Intl.DateTimeFormat('en-GB', {
    timeZone: 'Asia/Kolkata', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date()).replace(',', '') + ' IST';
}

function css(fonts) {
  return `
${fonts}
:root { --navy:#0B1F3A; --blue:#1E4DB7; --ink:#1A202C; --muted:#4A5568; --line:#D5DBE5; --soft:#EEF2FA; --code:#F5F7FB; }
@page { size: A4 portrait; margin: 22mm 16mm 20mm 16mm; }
@page landscape { size: A4 landscape; margin: 18mm 14mm 16mm 14mm; }
html { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: "Inter", "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; font-size: 9.6pt; line-height: 1.42; color: var(--ink); margin: 0; }
.cover { border-bottom: 3px solid var(--navy); padding: 0 0 6mm 0; margin: 0 0 8mm 0; page-break-after: avoid; }
.cover .brand { color: var(--blue); font-weight: 600; font-size: 9pt; letter-spacing: .04em; text-transform: uppercase; }
.cover h1 { font-size: 21pt; line-height: 1.2; margin: 3mm 0 2mm 0; color: var(--navy); }
.cover .meta { color: var(--muted); font-size: 8.6pt; }
h1 { font-size: 17pt; color: var(--navy); margin: 9mm 0 3mm 0; page-break-after: avoid; line-height: 1.25; }
h2 { font-size: 13.5pt; color: var(--navy); margin: 8mm 0 2.5mm 0; padding-bottom: 1mm; border-bottom: 1.5px solid var(--blue); page-break-after: avoid; line-height: 1.25; }
h3 { font-size: 11pt; color: var(--blue); margin: 5.5mm 0 2mm 0; page-break-after: avoid; }
h4, h5, h6 { font-size: 10pt; margin: 4mm 0 1.5mm 0; page-break-after: avoid; }
p { margin: 0 0 2.4mm 0; orphans: 3; widows: 3; }
a { color: var(--blue); text-decoration: none; word-break: break-all; }
ul, ol { margin: 0 0 2.6mm 0; padding-left: 5.5mm; }
li { margin: 0 0 .8mm 0; }
li > ul, li > ol { margin-top: .8mm; }
li.task { list-style: none; margin-left: -4.5mm; }
li.task input { margin: 0 1.5mm 0 0; vertical-align: -1px; }
hr { border: 0; border-top: 1px solid var(--line); margin: 5mm 0; }
blockquote { margin: 0 0 3mm 0; padding: 1.5mm 4mm; border-left: 3px solid var(--blue); background: var(--soft); color: var(--muted); }
code { font-family: "JetBrains Mono", Consolas, "Courier New", monospace; font-size: 8.3pt; background: var(--code); padding: 0 .35em; border-radius: 2px; overflow-wrap: anywhere; }
pre { background: var(--code); border: 1px solid var(--line); border-radius: 3px; padding: 2.5mm 3mm; margin: 0 0 3mm 0; font-size: 7.9pt; line-height: 1.38; white-space: pre-wrap; word-break: break-word; overflow-wrap: anywhere; page-break-inside: auto; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 0 0 3.5mm 0; font-size: 8.3pt; line-height: 1.34; page-break-inside: auto; table-layout: auto; }
thead { display: table-header-group; }
tr { page-break-inside: avoid; }
th, td { border: 1px solid var(--line); padding: 1.1mm 1.7mm; vertical-align: top; text-align: left; overflow-wrap: break-word; hyphens: manual; }
td code, td a, th code { overflow-wrap: anywhere; }
th { background: var(--navy); color: #fff; font-weight: 600; }
tbody tr:nth-child(even) td { background: #F7F9FD; }
td code, th code { font-size: 7.6pt; }
th code { background: rgba(255,255,255,.18); color: #fff; }
img { max-width: 100%; height: auto; }
figure { margin: 3mm 0 4mm 0; text-align: center; page-break-inside: avoid; }
figure svg { max-width: 100%; height: auto; display: block; margin: 0 auto; }
figure.diagram svg { max-height: 240mm; }
figure.landscape { page: landscape; break-before: page; break-after: page; margin: 0; }
figure.landscape svg { max-height: 175mm; max-width: 100%; }
figcaption { font-size: 8pt; color: var(--muted); margin-top: 1.5mm; }
mark.todo { background: #FFE58F; color: #7A4B00; padding: 0 .3em; border-radius: 2px; font-weight: 600; }
.mermaid { text-align: center; background: white; border: 0; padding: 0; font-size: 8pt; }
strong { font-weight: 600; }
.small { font-size: 8pt; color: var(--muted); }
`;
}

function pageHtml({ title, label, body, fonts, mermaidSrc, needsMermaid }) {
  const mermaidTag = needsMermaid
    ? `<script src="${mermaidSrc}"></script>
<script>
  window.__mermaidDone = false;
  (async () => {
    try {
      mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose', flowchart: { htmlLabels: true } });
      await mermaid.run({ querySelector: '.mermaid' });
    } catch (e) { window.__mermaidError = String(e); }
    window.__mermaidDone = true;
  })();
</script>`
    : '<script>window.__mermaidDone = true;</script>';
  return `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>${title}</title><style>${css(fonts)}</style></head>
<body>
<div class="cover">
  <div class="brand">${PRODUCT} · ${ORG}</div>
  <h1>${title}</h1>
  <div class="meta">${label} · version ${VERSION} · ${MODEL_STATEMENT} · Gujarat Police Innovation Challenge 2026 · generated ${istNow()}</div>
</div>
${body}
${mermaidTag}
</body></html>`;
}

const esc = (s) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

function headerTemplate(title) {
  return `<div style="width:100%;font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:7.5pt;color:#4A5568;padding:0 16mm;display:flex;justify-content:space-between;align-items:center;-webkit-print-color-adjust:exact;">
  <span style="color:#1E4DB7;font-weight:600;">${esc(PRODUCT)} · ${esc(ORG)}</span>
  <span style="max-width:60%;text-align:right;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;">${esc(title)}</span>
</div>`;
}

function footerTemplate() {
  return `<div style="width:100%;font-family:Inter,'Segoe UI',Arial,sans-serif;font-size:7.5pt;color:#4A5568;padding:0 16mm;display:flex;justify-content:space-between;align-items:center;">
  <span>${esc(VERSION)} · ${esc(MODEL_STATEMENT)} · confidential to the Gujarat Police Innovation Challenge jury</span>
  <span>Page <span class="pageNumber"></span> of <span class="totalPages"></span></span>
</div>`;
}

// ---------------------------------------------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------------------------------------------

function markedInstance(marked) {
  const m = new marked.Marked({ gfm: true, breaks: false });
  m.use({
    renderer: {
      listitem(item) {
        // task-list items ("- [ ] ...") render as real checkboxes
        if (item.task) {
          const box = `<input type="checkbox" disabled${item.checked ? ' checked' : ''}>`;
          const inner = this.parser.parse(item.tokens, !!item.loose);
          return `<li class="task">${box}${inner}</li>\n`;
        }
        return false;
      },
    },
  });
  return m;
}

export function countPdfPages(buffer) {
  const text = buffer.toString('latin1');
  // Chromium nests /Pages nodes; the root carries the largest /Count. Fall back to counting page objects.
  const counts = [...text.matchAll(/\/Type\s*\/Pages[^>]*?\/Count\s+(\d+)/g)].map((m) => Number(m[1]));
  if (counts.length) return Math.max(...counts);
  return (text.match(/\/Type\s*\/Page(?![s])/g) || []).length;
}

async function renderDocument(name, opts, ctx) {
  const doc = DOCUMENTS[name];
  const src = join(docsDir, doc.file);
  if (!existsSync(src)) fail(`missing ${src}`);
  const stats = { svgInlined: 0, mermaidLive: 0, markers: 0 };
  let markdown = await readFile(src, 'utf8');
  const titleMatch = markdown.match(/^#\s+(.+)$/m);
  const title = titleMatch ? titleMatch[1].replace(/[*_`]/g, '').trim() : name;
  markdown = markdown.replace(/^#\s+.+\r?\n/, '');
  markdown = await replaceMermaidFences(markdown, name, stats);
  let body = ctx.marked.parse(markdown);
  body = highlightMarkers(body, stats);
  body = await inlineImages(body, docsDir);
  const html = pageHtml({
    title, label: doc.label, body, fonts: ctx.fonts, mermaidSrc: ctx.mermaid.src, needsMermaid: stats.mermaidLive > 0,
  });
  const buildDir = join(opts.out, '.build');
  await mkdir(buildDir, { recursive: true });
  const htmlPath = join(buildDir, `${name}.html`);
  await writeFile(htmlPath, html, 'utf8');

  const page = await ctx.browser.newPage();
  page.on('pageerror', (e) => log(`  page error: ${e.message}`));
  await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load', timeout: 120000 });
  await page.waitForFunction(() => window.__mermaidDone === true, null, { timeout: 120000 });
  await page.evaluate(() => document.fonts.ready);
  const diag = await page.evaluate(() => ({
    error: window.__mermaidError || null,
    pending: [...document.querySelectorAll('figure.pending')].filter((f) => !f.querySelector('svg')).length,
    total: document.querySelectorAll('figure.diagram').length,
  }));
  if (diag.error) fail(`${name}: mermaid failed: ${diag.error}`);
  if (diag.pending > 0) fail(`${name}: ${diag.pending} mermaid diagram(s) did not render - refusing to write a PDF with raw mermaid text`);
  // Live-rendered diagrams: decide their orientation now that their size is known and strip the placeholder class.
  await page.evaluate(() => {
    for (const f of document.querySelectorAll('figure.pending')) {
      const svg = f.querySelector('svg');
      const vb = svg?.getAttribute('viewBox')?.split(/\s+/).map(Number);
      const aspect = vb && vb[3] > 0 ? vb[2] / vb[3] : 1;
      svg.removeAttribute('width'); svg.removeAttribute('height');
      svg.style.maxWidth = '100%'; svg.style.height = 'auto';
      f.classList.remove('pending');
      if (aspect > 1.45) f.classList.add('landscape');
    }
  });
  const pdfPath = join(opts.out, doc.out);
  const pdf = await page.pdf({
    path: pdfPath,
    format: 'A4',
    printBackground: true,
    preferCSSPageSize: true,
    displayHeaderFooter: true,
    headerTemplate: headerTemplate(title),
    footerTemplate: footerTemplate(),
    margin: { top: '22mm', right: '16mm', bottom: '20mm', left: '16mm' },
  });
  let previewPath = null;
  if (opts.preview) {
    await page.emulateMedia({ media: 'print' });
    await page.setViewportSize({ width: 794, height: 1123 });
    previewPath = join(buildDir, `${name}-preview.png`);
    await page.screenshot({ path: previewPath, clip: { x: 0, y: 0, width: 794, height: 2246 }, fullPage: true });
  }
  await page.close();
  const size = (await stat(pdfPath)).size;
  const pages = countPdfPages(pdf);
  log(`${doc.file} -> ${pdfPath} (${pages} pages, ${(size / 1024).toFixed(0)} KB; diagrams: ${stats.svgInlined} inlined SVG, ${stats.mermaidLive} rendered live; ${stats.markers} unfinished marker(s)${previewPath ? `; preview ${basename(previewPath)}` : ''})`);
  return { name, pdfPath, pages, size, ...stats };
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const marked = loadMarked();
  const pw = await loadPlaywright();
  const { browser, label } = await launchChromium(pw.chromium);
  const mermaid = mermaidScriptSource();
  log(`browser: ${label} (playwright from ${pw.from}); mermaid from ${mermaid.from}`);
  await mkdir(opts.out, { recursive: true });
  const ctx = { browser, marked: markedInstance(marked), fonts: await fontFaces(), mermaid };
  const results = [];
  try {
    for (const name of opts.names) results.push(await renderDocument(name, opts, ctx));
  } finally {
    await browser.close();
  }
  const unfinished = results.filter((r) => r.markers > 0);
  if (unfinished.length) log(`note: unfinished markers highlighted in ${unfinished.map((r) => `${r.name} (${r.markers})`).join(', ')} - resolve them before upload`);
  log(`done: ${results.length} PDF(s) in ${opts.out}`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch((err) => fail(err?.stack || String(err)));
}
