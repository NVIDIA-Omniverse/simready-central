#!/usr/bin/env python3

# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Render the library written by index_to_library.py as one local web page.

The page is a single HTML file with the library embedded in it, so it opens
straight from disk. It knows nothing about SimReady tools, only the shape of
a library record; every filter and grouping on it is computed in the browser
from the same fields a consumer would read from ``library.json``.

Write it next to the library, inside the workspace, so the thumbnail paths
(relative to the workspace) resolve.

Usage:
    python library_page.py --library <workspace>/library.json [--output <file>] [--title <text>]
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

# One facet per field a reader may want to filter or group by. ``values`` is a
# JavaScript function body returning the record's values for the facet; a
# record can contribute several (physics variants, failed features), or none.
FACETS = [
    {"key": "license", "label": "License (package definition)", "values": "return [r.license || '(missing)'];"},
    {"key": "owner", "label": "Owner", "values": "return [r.owner || '(missing)'];"},
    {"key": "category", "label": "Category", "values": "return [r.category || '(missing)'];"},
    {"key": "asset_type", "label": "Asset type", "values": "return [r.asset_type || '(missing)'];"},
    {"key": "physics", "label": "Physics variants", "values": "return r.physics_variants.length ? r.physics_variants : ['(none)'];"},
    {
        "key": "profile",
        "label": "Validated against",
        "values": "return [r.validation.profile ? r.validation.profile + ' ' + (r.validation.profile_version || '') : '(missing)'];",
    },
    {"key": "validation", "label": "Validation", "values": "return [verdict(r.validation.passed)];"},
    {"key": "benchmark", "label": "Benchmark", "values": "return [verdict(r.benchmark.passed)];"},
    {
        "key": "failed_feature",
        "label": "Failed validation feature",
        "values": "return r.validation.features.filter(f => f.passed === false).map(f => f.display_name + ' [' + f.id + ']');",
    },
    {
        "key": "benchmark_result",
        "label": "Benchmark test result",
        "values": "return r.benchmark.results.map(t => t.test_name + ' [' + t.engine + ']: ' + t.status);",
    },
    {"key": "thumbnail", "label": "Thumbnail", "values": "return [r.thumbnail ? 'present' : 'missing'];"},
    {"key": "missing", "label": "Missing data", "values": "return r.missing.length ? r.missing : ['(complete)'];"},
]

CSS = """
/* Dark scheme on the Kaizen UI tokens the SimReady review console uses:
   near-black surfaces, NVIDIA green as the accent, a lighter red for failures
   so it stays legible on dark. */
:root {
  --bg: #0c0c0c; --panel: #161616; --paper: #222222; --soft: #313131;
  --ink: #eeeeee; --muted: #898989; --line: #ffffff33; --line-hover: #ffffff66; --hover: #ffffff0d;
  --brand: #76b900; --ok: #a5de15; --fail: #ff8181; --warn: #f9c500; --info: #10b1fb;
  color-scheme: dark;
}
* { box-sizing: border-box; }
body { margin: 0; font: 14px/1.5 "NVIDIA Sans", ui-sans-serif, system-ui, sans-serif; color: var(--ink); background: var(--bg); }
a { color: var(--info); }
code, pre { font-family: ui-monospace, "JetBrains Mono", monospace; font-size: 12px; }
header { padding: 12px 24px; background: var(--panel); border-bottom: 1px solid var(--line); }
header h1 { margin: 0 0 4px; font-size: 18px; font-weight: 500; }
header .meta { color: var(--muted); font-size: 12px; }
main { display: grid; grid-template-columns: 280px minmax(0, 1fr); min-height: calc(100vh - 62px); }
aside.filters { padding: 12px; border-right: 1px solid var(--line); background: var(--panel); overflow-y: auto; }
aside.filters input[type=search], aside.filters select, aside.filters button {
  width: 100%; padding: 6px 8px; margin-bottom: 8px; font: inherit; color: var(--ink);
  background: var(--paper); border: 1px solid var(--line); border-radius: 4px; }
aside.filters button { cursor: pointer; margin-top: 8px; }
aside.filters button:hover, aside.filters input:focus, aside.filters select:focus { border-color: var(--line-hover); outline: none; }
aside.filters details { margin-bottom: 4px; }
aside.filters summary { cursor: pointer; font-weight: 500; padding: 6px 4px; border-radius: 4px; }
aside.filters summary:hover { background: var(--hover); }
aside.filters label.value { display: flex; gap: 8px; align-items: center; padding: 3px 4px 3px 12px; border-radius: 4px; cursor: pointer; }
aside.filters label.value:hover { background: var(--hover); }
aside.filters label.value .count { margin-left: auto; color: var(--muted); font-size: 12px; }
input[type=checkbox] { accent-color: var(--brand); }
section.content { padding: 12px 24px; }
.toolbar { display: flex; gap: 12px; align-items: center; margin-bottom: 12px; color: var(--muted); }
.group h2 { font-size: 15px; font-weight: 500; margin: 20px 0 8px; padding-bottom: 6px; border-bottom: 1px solid var(--line); }
.grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 12px; }
.tile { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; overflow: hidden; cursor: pointer; }
.tile:hover { background: var(--hover); border-color: var(--line-hover); }
.tile.selected { border-color: var(--brand); box-shadow: 0 0 0 1px var(--brand); }
.tile .thumb { aspect-ratio: 1; background: var(--paper); display: grid; place-items: center; color: var(--muted); font-size: 12px; }
.tile .thumb img { width: 100%; height: 100%; object-fit: cover; display: block; }
.tile .body { padding: 8px 10px 10px; }
.tile .name { font-weight: 500; margin-bottom: 2px; word-break: break-word; }
.tile .sub { color: var(--muted); font-size: 12px; margin-bottom: 6px; }
.status { font-size: 13px; line-height: 1.5; }
.status .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: currentColor; margin-right: 8px; vertical-align: 1px; }
.status.ok { color: var(--ok); }
.status.fail { color: var(--fail); }
.status.none { color: var(--muted); }
.status.warn { color: var(--warn); }
.status .detail { color: var(--muted); }
.pill { display: inline-block; font-size: 12px; padding: 1px 8px; border-radius: 9999px; border: 1px solid currentColor; }
.pill.ok { color: var(--ok); }
.pill.fail { color: var(--fail); }
.pill.none { color: var(--muted); }
.drawer { position: fixed; top: 0; right: 0; width: min(600px, 100vw); height: 100vh; overflow-y: auto;
          background: var(--panel); border-left: 1px solid var(--line); padding: 16px 24px; box-shadow: -8px 0 24px #00000080; }
.drawer[hidden] { display: none; }
.drawer h2 { margin: 0 0 8px; font-size: 18px; font-weight: 500; }
.drawer h3 { font-size: 12px; text-transform: uppercase; letter-spacing: .06em; color: var(--muted); margin: 22px 0 8px; font-weight: 500; }
.drawer table { border-collapse: collapse; width: 100%; font-size: 13px; }
.drawer td, .drawer th { text-align: left; vertical-align: top; padding: 5px 8px 5px 0; border-bottom: 1px solid var(--line); font-weight: 400; }
.drawer th { color: var(--muted); white-space: nowrap; }
.drawer tr:first-child th { color: var(--ink); }
.drawer .close { float: right; font: inherit; color: var(--ink); background: var(--paper); border: 1px solid var(--line); border-radius: 4px; padding: 4px 10px; cursor: pointer; }
.drawer .close:hover { border-color: var(--line-hover); }
.drawer pre { background: var(--bg); padding: 8px; border-radius: 4px; overflow-x: auto; }
.drawer code { word-break: break-all; }
.drawer .sub { color: var(--muted); font-size: 12px; }
.empty { color: var(--muted); padding: 40px 0; text-align: center; }
"""

JS = r"""
const facets = FACET_DEFS.map(f => ({ key: f.key, label: f.label, values: new Function('r', 'verdict', f.values) }));
const state = { q: '', sort: 'asset_name', group: '', selected: {}, open: null };

function verdict(passed) { return passed === true ? 'passed' : passed === false ? 'failed' : 'missing'; }
function esc(text) { return String(text ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function facetValues(facet, r) { return facet.values(r, verdict); }

function matches(r) {
  const q = state.q.toLowerCase();
  if (q) {
    const hay = [r.asset_name, r.package.package_id, r.category, r.asset_type, r.owner, r.license,
                 r.usd_metadata && r.usd_metadata.qcode].join(' ').toLowerCase();
    if (!hay.includes(q)) return false;
  }
  return facets.every(f => {
    const wanted = state.selected[f.key];
    if (!wanted || wanted.size === 0) return true;
    return facetValues(f, r).some(v => wanted.has(v));
  });
}

function sortKey(r) {
  switch (state.sort) {
    case 'license': return r.license || '';
    case 'category': return r.category || '';
    case 'mass': return Number(r.usd_metadata && r.usd_metadata.mass) || 0;
    default: return r.asset_name;
  }
}

function pill(text, passed) {
  const cls = passed === true ? 'ok' : passed === false ? 'fail' : 'none';
  return `<span class="pill ${cls}">${esc(text)}</span>`;
}

// One line per check, in words: "Validation failed · 3 features", never an abbreviation.
function status(label, passed, count, noun) {
  const cls = passed === true ? 'ok' : passed === false ? 'fail' : 'none';
  const word = passed === true ? 'passed' : passed === false ? 'failed' : 'no record';
  const detail = count ? ` <span class="detail">· ${count} ${noun}${count > 1 ? 's' : ''}</span>` : '';
  return `<div class="status ${cls}"><span class="dot"></span>${esc(label)} ${word}${detail}</div>`;
}

function tile(r) {
  const failed = r.validation.features.filter(f => f.passed === false).length;
  const failedTests = r.benchmark.results.filter(t => t.status === 'fail').length;
  const thumb = r.thumbnail
    ? `<img src="${esc(r.thumbnail)}" alt="" loading="lazy">`
    : `<span>${r.missing.includes('root_usd') ? 'no root USD' : 'no thumbnail'}</span>`;
  return `<div class="tile${state.open === r.asset_name ? ' selected' : ''}" data-asset="${esc(r.asset_name)}">
    <div class="thumb">${thumb}</div>
    <div class="body">
      <div class="name">${esc(r.asset_name)}</div>
      <div class="sub">${esc(r.license || 'license missing')} · ${esc(r.category || 'no category')}</div>
      ${status('Validation', r.validation.passed, failed, 'feature')}
      ${status('Benchmark', r.benchmark.passed, failedTests, 'test')}
      ${r.missing.length ? `<div class="status warn"><span class="dot"></span>${r.missing.length} source${r.missing.length > 1 ? 's' : ''} missing</div>` : ''}
    </div>
  </div>`;
}

function renderGrid(records) {
  const el = document.getElementById('content');
  if (!records.length) { el.innerHTML = '<div class="empty">No asset matches the current filters.</div>'; return; }
  const grid = rs => `<div class="grid">${rs.map(tile).join('')}</div>`;
  const facet = facets.find(f => f.key === state.group);
  if (!facet) { el.innerHTML = grid(records); return; }
  const groups = new Map();
  for (const r of records) for (const v of facetValues(facet, r)) (groups.get(v) || groups.set(v, []).get(v)).push(r);
  el.innerHTML = [...groups.keys()].sort().map(v => {
    const rs = groups.get(v);
    return `<div class="group"><h2>${esc(facet.label)}: ${esc(v)} <span class="sub">(${rs.length})</span></h2>${grid(rs)}</div>`;
  }).join('');
}

function renderFacets(all) {
  const counts = facets.map(f => {
    const c = new Map();
    for (const r of all) for (const v of facetValues(f, r)) c.set(v, (c.get(v) || 0) + 1);
    return { f, c };
  });
  // Groups start collapsed; one that carries a selection (a restored view) opens.
  const isOpen = f => state.selected[f.key] && state.selected[f.key].size > 0;
  document.getElementById('facets').innerHTML = counts.map(({ f, c }) => `
    <details${isOpen(f) ? ' open' : ''}><summary>${esc(f.label)}</summary>
      ${[...c.keys()].sort().map(v => `<label class="value">
        <input type="checkbox" data-facet="${esc(f.key)}" data-value="${esc(v)}"
          ${state.selected[f.key] && state.selected[f.key].has(v) ? 'checked' : ''}>
        <span>${esc(v)}</span><span class="count">${c.get(v)}</span></label>`).join('')}
    </details>`).join('');
  document.getElementById('group').innerHTML = '<option value="">No grouping</option>' +
    facets.map(f => `<option value="${esc(f.key)}"${state.group === f.key ? ' selected' : ''}>Group by ${esc(f.label)}</option>`).join('');
}

// The asset's state in plain words, from the record alone. The review report linked
// beneath it carries the detail and the specification links, so nothing is repeated.
function summary(r, failedFeatures, failedTests) {
  const parts = [];
  const total = r.validation.features.length;
  if (r.validation.passed === null) parts.push('No validation result was submitted with this package.');
  else if (failedFeatures.length === 0) parts.push(`All ${total} validated features passed${r.validation.profile ? ` for ${esc(r.validation.profile)}` : ''}.`);
  else parts.push(`${failedFeatures.length} of ${total} validated features failed${r.validation.profile ? ` for ${esc(r.validation.profile)}` : ''}: ` +
                  failedFeatures.map(f => esc(f.display_name)).join(', ') + '.');
  const skipped = r.benchmark.results.filter(t => t.status === 'skipped').length;
  if (r.benchmark.passed === null) parts.push('No benchmark result was submitted.');
  else if (failedTests.length === 0) parts.push(`All benchmark tests passed${skipped ? ` (${skipped} skipped)` : ''}.`);
  else parts.push(`${failedTests.length} of ${r.benchmark.results.length} benchmark runs failed: ` +
                  failedTests.map(t => `${esc(t.test_name)} on ${esc(t.engine)}`).join(', ') + `${skipped ? `; ${skipped} skipped` : ''}.`);
  if (r.missing.length) parts.push(`The package lacks: ${r.missing.map(esc).join(', ')}.`);
  return parts.join(' ');
}

function kv(rows) {
  return `<table>${rows.filter(([, v]) => v !== undefined && v !== null && v !== '')
    .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${v}</td></tr>`).join('')}</table>`;
}

function renderDrawer() {
  const drawer = document.getElementById('drawer');
  const r = LIBRARY.assets.find(a => a.asset_name === state.open);
  if (!r) { drawer.hidden = true; return; }
  const m = r.usd_metadata || {};
  const link = r.source_url ? `<a href="${esc(r.source_url)}">${esc(r.package.repo_id)}@${esc((r.package.commit || '').slice(0, 8))}</a>` : null;
  const featureRows = r.validation.features.map(f => `<tr>
      <td>${esc(f.display_name)}<br><code>${esc(f.id)} ${esc(f.version || '')}</code></td>
      <td>${f.passed === true ? '<span class="pill ok">passed</span>' : f.passed === false ? '<span class="pill fail">failed</span>' : '—'}</td>
      <td>${f.failing_requirements.map(esc).join('<br>')}${f.optional_requirements.length ? `<div class="sub">optional: ${f.optional_requirements.map(esc).join(', ')}</div>` : ''}</td>
    </tr>`).join('');
  const benchRows = r.benchmark.results.map(t => `<tr>
      <td>${esc(t.test_name)}</td><td>${esc(t.engine)} ${esc(t.engine_version || '')}</td>
      <td><span class="pill ${t.status === 'pass' ? 'ok' : t.status === 'fail' ? 'fail' : 'none'}">${esc(t.status)}</span></td>
      <td>${esc(t.message || '')}</td></tr>`).join('');
  const stamped = m.validation ? `<pre><code>${esc(JSON.stringify(m.validation, null, 1))}</code></pre>` : '';
  const other = Object.entries(m).filter(([k]) => !['validation', 'Variants'].includes(k))
    .map(([k, v]) => [k, k === 'qcode' && v ? `<a href="https://www.wikidata.org/wiki/${esc(v)}">${esc(v)}</a>` : esc(typeof v === 'object' ? JSON.stringify(v) : v)]);
  const failedFeatures = r.validation.features.filter(f => f.passed === false);
  const failedTests = r.benchmark.results.filter(t => t.status === 'fail');
  const reports = [['review report of the workspace', LIBRARY.review_report]]
    .filter(([, href]) => href).map(([label, href]) => `<a href="${esc(href)}">${label}</a>`);
  drawer.innerHTML = `
    <button class="close" id="close">Close</button>
    <h2>${esc(r.asset_name)}</h2>
    ${status('Validation', r.validation.passed, failedFeatures.length, 'feature')}
    ${status('Benchmark', r.benchmark.passed, failedTests.length, 'test')}
    <h3>Summary</h3>
    <p>${summary(r, failedFeatures, failedTests)}</p>
    ${reports.length ? `<p class="sub">Details, with links to the specification: ${reports.join(' · ')}.</p>` : ''}
    <h3>Retained fields</h3>
    ${kv([['Source URL', r.source_url ? `<a href="${esc(r.source_url)}"><code>${esc(r.source_url)}</code></a>` : '<em>missing</em>'],
          ['Owner', esc(r.owner) + (r.usd_author ? ` <span class="sub">(USD author: ${esc(r.usd_author)})</span>` : '')],
          ['Version', esc(r.version)],
          ['License', esc(r.license || 'missing') + (r.usd_license ? ` <span class="sub">(USD asset_license: ${esc(r.usd_license)})</span>` : '')],
          ['Validated against', r.validation.profile ? `${esc(r.validation.profile)} ${esc(r.validation.profile_version || '')}` : '<em>missing</em>'],
          ['Package id', `<code>${esc(r.package.package_id)}</code>`], ['Pulled from', link]])}
    ${r.missing.length ? `<h3>Missing in this package</h3><div>${r.missing.map(esc).join(', ')}</div>` : ''}
    <h3>Validation features</h3>
    ${featureRows ? `<table><tr><th>Feature</th><th>Result</th><th>Failing requirements</th></tr>${featureRows}</table>` : '<em>no validation sidecar</em>'}
    <div class="sub">simready-validate ${esc(r.validation.simready_validate_version || '?')}, tiers ${esc(JSON.stringify(r.validation.tier_versions || {}))}</div>
    <h3>Benchmark tests</h3>
    ${benchRows ? `<table><tr><th>Test</th><th>Engine</th><th>Status</th><th>Message</th></tr>${benchRows}</table>` : '<em>no benchmark sidecar</em>'}
    <h3>USD SimReady_Metadata</h3>
    ${other.length ? kv([['Physics variants', esc(r.physics_variants.join(', ') || 'none')], ...other]) : '<em>none read</em>'}
    ${stamped ? `<h3>Validation stamped in the USD by the producer</h3><div class="sub">Not the submitted run: the sidecar above is.</div>${stamped}` : ''}
    <h3>Files</h3>
    ${kv(Object.entries(r.files).map(([k, v]) => [k, v ? `<code>${esc(v)}</code>` : '<em>missing</em>']))}`;
  drawer.hidden = false;
  document.getElementById('close').onclick = () => { state.open = null; render(); };
}

function render() {
  const shown = LIBRARY.assets.filter(matches).sort((a, b) => (sortKey(a) > sortKey(b)) - (sortKey(a) < sortKey(b)));
  document.getElementById('shown').textContent = `${shown.length} of ${LIBRARY.assets.length} assets`;
  renderGrid(shown);
  renderDrawer();
  location.hash = encodeURIComponent(JSON.stringify({
    q: state.q, sort: state.sort, group: state.group, open: state.open,
    selected: Object.fromEntries(Object.entries(state.selected).map(([k, v]) => [k, [...v]])),
  }));
}

function restore() {
  try {
    const saved = JSON.parse(decodeURIComponent(location.hash.slice(1)));
    Object.assign(state, { q: saved.q || '', sort: saved.sort || 'asset_name', group: saved.group || '', open: saved.open || null });
    state.selected = Object.fromEntries(Object.entries(saved.selected || {}).map(([k, v]) => [k, new Set(v)]));
  } catch (e) { /* no saved view */ }
}

document.addEventListener('DOMContentLoaded', () => {
  restore();
  renderFacets(LIBRARY.assets);
  document.getElementById('q').value = state.q;
  document.getElementById('sort').value = state.sort;
  document.getElementById('q').oninput = e => { state.q = e.target.value; render(); };
  document.getElementById('sort').onchange = e => { state.sort = e.target.value; render(); };
  document.getElementById('group').onchange = e => { state.group = e.target.value; render(); };
  document.getElementById('clear').onclick = () => { state.selected = {}; state.q = ''; document.getElementById('q').value = ''; renderFacets(LIBRARY.assets); render(); };
  document.getElementById('facets').onchange = e => {
    const { facet, value } = e.target.dataset;
    const set = state.selected[facet] || (state.selected[facet] = new Set());
    e.target.checked ? set.add(value) : set.delete(value);
    render();
  };
  document.getElementById('content').onclick = e => {
    const t = e.target.closest('.tile');
    if (t) { state.open = t.dataset.asset; render(); }
  };
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && state.open) { state.open = null; render(); } });
  render();
});
"""

PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>{css}</style>
</head>
<body>
<header>
  <h1>{title}</h1>
  <div class="meta">{meta}</div>
</header>
<main>
  <aside class="filters">
    <input id="q" type="search" placeholder="Search name, package id, category, owner, qcode">
    <select id="sort">
      <option value="asset_name">Sort by name</option>
      <option value="license">Sort by license</option>
      <option value="category">Sort by category</option>
      <option value="mass">Sort by mass</option>
    </select>
    <select id="group"></select>
    <div id="facets"></div>
    <button id="clear">Clear filters</button>
  </aside>
  <section class="content">
    <div class="toolbar"><span id="shown"></span></div>
    <div id="content"></div>
  </section>
</main>
<aside class="drawer" id="drawer" hidden></aside>
<script>
const LIBRARY = {library};
const FACET_DEFS = {facets};
{js}
</script>
</body>
</html>
"""


def embed_json(value) -> str:
    """JSON that is safe inside a ``<script>`` element, whatever text the records hold."""
    text = json.dumps(value, ensure_ascii=True)
    return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def page_meta(library: dict) -> str:
    """The header line: where the library came from and what read it."""
    parts = [
        f"{len(library.get('assets', []))} assets from {', '.join(library.get('datasets') or ['?'])}",
        f"submission file {html.escape(Path(library.get('submission_file', '?')).name)}",
        f"built {html.escape(library.get('generated', '?'))}",
    ]
    versions = library.get("tool_versions") or {}
    if versions:
        parts.append("with " + ", ".join(f"{html.escape(k)} {html.escape(v)}" for k, v in versions.items()))
    for key, label in (("not_pulled", "not pulled"), ("not_listed", "not in the submission file")):
        if library.get(key):
            parts.append(f"{label}: {html.escape(', '.join(library[key]))}")
    return " · ".join(parts)


def render_html(library: dict, title: str) -> str:
    return PAGE.format(
        title=html.escape(title),
        meta=page_meta(library),
        css=CSS,
        library=embed_json(library),
        facets=embed_json(FACETS),
        js=JS,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--library", required=True, type=Path, help="the library.json written by index_to_library.py")
    parser.add_argument("--output", type=Path, help="where to write the page (default: library.html next to the library)")
    parser.add_argument("--title", help="page title (default: the dataset names)")
    args = parser.parse_args(argv)

    library = json.loads(args.library.read_text(encoding="utf-8"))
    title = args.title or "SimReady assets: " + ", ".join(library.get("datasets") or ["library"])
    output = args.output or args.library.with_name("library.html")
    output.write_text(render_html(library, title), encoding="utf-8")
    print(f"{len(library.get('assets', []))} asset(s) rendered to {output}")
    print(f"Open it directly, or serve the workspace: python -m http.server --directory {output.parent} 8000")
    return 0


if __name__ == "__main__":
    sys.exit(main())
