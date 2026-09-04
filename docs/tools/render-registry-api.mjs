#!/usr/bin/env node
// Render an OpenAPI 3.x document (URL or local file) as Markdown for docs/REGISTRY-API.md.
//
// Usage:
//   node docs/tools/render-registry-api.mjs https://<host>/api/openapi.json > docs/REGISTRY-API.generated.md
//   node docs/tools/render-registry-api.mjs openapi.json [--tag Cameras --tag Auth] > out.md
//
// No dependencies (Node >= 18 for global fetch). Groups operations by tag, prints parameters,
// request-body schema (flattened one level, $ref resolved) and response codes. Exit code 1 on
// any error so the submission checklist can rely on it.

import { readFile } from 'node:fs/promises';

const METHOD_ORDER = ['get', 'post', 'put', 'patch', 'delete'];

function fail(message) {
  process.stderr.write(`render-registry-api: ${message}\n`);
  process.exit(1);
}

async function loadSpec(source) {
  if (/^https?:\/\//i.test(source)) {
    const res = await fetch(source);
    if (!res.ok) fail(`HTTP ${res.status} fetching ${source}`);
    return res.json();
  }
  return JSON.parse(await readFile(source, 'utf8'));
}

function parseArgs(argv) {
  const tags = [];
  let source = null;
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === '--tag') {
      tags.push(argv[i + 1]);
      i += 1;
    } else if (!source) {
      source = argv[i];
    }
  }
  if (!source) fail('usage: render-registry-api.mjs <openapi.json | URL> [--tag <name>]...');
  return { source, tags };
}

function resolveRef(spec, ref) {
  const parts = ref.replace(/^#\//, '').split('/');
  let node = spec;
  for (const part of parts) {
    node = node?.[part];
    if (node === undefined) return null;
  }
  return node;
}

function schemaName(spec, schema) {
  if (!schema) return '';
  if (schema.$ref) return schema.$ref.split('/').pop();
  if (schema.type === 'array') return `array<${schemaName(spec, schema.items) || 'any'}>`;
  if (schema.anyOf) return schema.anyOf.map((s) => schemaName(spec, s)).join(' | ');
  if (schema.enum) return schema.enum.map((v) => `\`${v}\``).join(' / ');
  return schema.type || (schema.properties ? 'object' : 'any');
}

function escapeCell(text) {
  return String(text ?? '').replace(/\|/g, '\\|').replace(/\r?\n/g, ' ');
}

function renderSchemaTable(spec, schema, depth = 0) {
  const resolved = schema?.$ref ? resolveRef(spec, schema.$ref) : schema;
  if (!resolved || !resolved.properties) return '';
  const required = new Set(resolved.required || []);
  const lines = ['| Field | Type | Required | Description |', '|---|---|---|---|'];
  for (const [name, prop] of Object.entries(resolved.properties)) {
    lines.push(
      `| \`${name}\` | ${escapeCell(schemaName(spec, prop))} | ${required.has(name) ? 'yes' : ''} | ${escapeCell(prop.description || prop.title || '')} |`,
    );
  }
  return `${'  '.repeat(depth)}${lines.join('\n')}\n`;
}

function renderParameters(spec, params) {
  if (!params?.length) return '';
  const lines = ['| Parameter | In | Type | Required | Description |', '|---|---|---|---|---|'];
  for (const raw of params) {
    const p = raw.$ref ? resolveRef(spec, raw.$ref) : raw;
    lines.push(
      `| \`${p.name}\` | ${p.in} | ${escapeCell(schemaName(spec, p.schema))} | ${p.required ? 'yes' : ''} | ${escapeCell(p.description || '')} |`,
    );
  }
  return `${lines.join('\n')}\n`;
}

function renderOperation(spec, method, path, op) {
  const out = [];
  out.push(`### \`${method.toUpperCase()} ${path}\``);
  if (op.summary) out.push(`**${escapeCell(op.summary)}**`);
  if (op.description) out.push(op.description.trim());
  if (op.security?.length) {
    const schemes = op.security.flatMap((s) => Object.keys(s));
    out.push(`Auth: ${schemes.map((s) => `\`${s}\``).join(', ')}`);
  }
  const params = renderParameters(spec, op.parameters);
  if (params) out.push('Parameters:\n\n' + params);
  const body = op.requestBody?.content;
  if (body) {
    for (const [mime, media] of Object.entries(body)) {
      out.push(`Request body (\`${mime}\`): ${schemaName(spec, media.schema) || 'object'}`);
      const table = renderSchemaTable(spec, media.schema);
      if (table) out.push(table);
    }
  }
  const responses = Object.entries(op.responses || {});
  if (responses.length) {
    const lines = ['| Status | Description | Body |', '|---|---|---|'];
    for (const [code, res] of responses) {
      const resolved = res.$ref ? resolveRef(spec, res.$ref) : res;
      const media = resolved.content ? Object.values(resolved.content)[0] : null;
      lines.push(`| ${code} | ${escapeCell(resolved.description || '')} | ${escapeCell(media ? schemaName(spec, media.schema) : '')} |`);
    }
    out.push('Responses:\n\n' + lines.join('\n') + '\n');
  }
  return out.join('\n\n');
}

function collectOperations(spec, tagFilter) {
  const byTag = new Map();
  for (const [path, item] of Object.entries(spec.paths || {})) {
    for (const method of METHOD_ORDER) {
      const op = item[method];
      if (!op) continue;
      const tags = op.tags?.length ? op.tags : ['untagged'];
      for (const tag of tags) {
        if (tagFilter.length && !tagFilter.includes(tag)) continue;
        if (!byTag.has(tag)) byTag.set(tag, []);
        byTag.get(tag).push({ method, path, op });
      }
    }
  }
  return byTag;
}

function renderDocument(spec, tagFilter) {
  const info = spec.info || {};
  const out = [];
  out.push(`# ${info.title || 'API'} — generated API reference`);
  out.push(`Version \`${info.version || '?'}\` · OpenAPI ${spec.openapi || '3.x'} · generated ${new Date().toISOString()} by docs/tools/render-registry-api.mjs`);
  if (info.description) out.push(info.description.trim());
  const byTag = collectOperations(spec, tagFilter);
  if (!byTag.size) fail('no operations found (check --tag filters)');
  out.push('## Contents');
  out.push([...byTag.keys()].map((t) => `- ${t} (${byTag.get(t).length})`).join('\n'));
  for (const [tag, ops] of byTag) {
    out.push(`## ${tag}`);
    for (const { method, path, op } of ops) out.push(renderOperation(spec, method, path, op));
  }
  const schemas = spec.components?.schemas || {};
  if (Object.keys(schemas).length) {
    out.push('## Schemas');
    for (const [name, schema] of Object.entries(schemas)) {
      const table = renderSchemaTable(spec, schema);
      out.push(`### ${name}\n\n${table || `Type: ${schemaName(spec, schema)}`}`);
    }
  }
  return out.join('\n\n') + '\n';
}

async function main() {
  const { source, tags } = parseArgs(process.argv.slice(2));
  const spec = await loadSpec(source);
  if (!spec || typeof spec !== 'object' || !spec.paths) fail('not an OpenAPI document (no "paths")');
  process.stdout.write(renderDocument(spec, tags));
}

main().catch((err) => fail(err.message));
