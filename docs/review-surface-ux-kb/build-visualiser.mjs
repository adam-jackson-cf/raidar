#!/usr/bin/env node
// OKF visualiser generator (zero deps). Walks this bundle, parses each markdown
// file's YAML frontmatter + body, resolves cross-links into a graph, and emits
// a single self-contained visualiser.html. Run: `node build-visualiser.mjs`.
import { readdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { dirname, join, relative, posix } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = dirname(fileURLToPath(import.meta.url));
const OUT = join(ROOT, 'visualiser.html');

/** Recursively collect every .md file under the bundle root. */
function walk(dir) {
  const out = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) out.push(...walk(full));
    else if (entry.endsWith('.md')) out.push(full);
  }
  return out;
}

/** Minimal YAML frontmatter parser: scalars and inline [a, b] arrays only. */
function parseFrontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n?([\s\S]*)$/);
  if (!m) return { meta: {}, body: text };
  const meta = {};
  for (const raw of m[1].split('\n')) {
    const line = raw.replace(/\s+$/, '');
    if (!line || /^\s/.test(line) && !line.includes(':')) continue;
    const idx = line.indexOf(':');
    if (idx === -1) continue;
    const key = line.slice(0, idx).trim();
    let val = line.slice(idx + 1).trim();
    if (!key) continue;
    if (val.startsWith('[') && val.endsWith(']')) {
      meta[key] = val.slice(1, -1).split(',').map((s) => s.trim().replace(/^["']|["']$/g, '')).filter(Boolean);
    } else {
      meta[key] = val.replace(/^["']|["']$/g, '');
    }
  }
  return { meta, body: m[2] };
}

/** Normalise a relative link from a doc dir to a posix bundle-relative id. */
function resolveId(fromDir, href) {
  const clean = href.split('#')[0];
  const joined = posix.normalize(posix.join(fromDir, clean));
  return joined.replace(/^\.\//, '');
}

const files = walk(ROOT);
const docs = files.map((full) => {
  const id = relative(ROOT, full).split('\\').join('/');
  const { meta, body } = parseFrontmatter(readFileSync(full, 'utf8'));
  return { id, dir: posix.dirname(id), meta, body };
});
const idSet = new Set(docs.map((d) => d.id));

// Extract outbound links that resolve to docs inside the bundle.
for (const doc of docs) {
  const links = new Set();
  for (const m of doc.body.matchAll(/\[[^\]]+\]\(([^)]+)\)/g)) {
    const href = m[1];
    if (/^https?:|^mailto:/.test(href)) continue;
    const target = resolveId(doc.dir === '.' ? '' : doc.dir, href);
    if (idSet.has(target)) links.add(target);
  }
  doc.outlinks = [...links];
}
// Inbound links.
const inbound = new Map(docs.map((d) => [d.id, []]));
for (const doc of docs) for (const t of doc.outlinks) inbound.get(t).push(doc.id);
for (const doc of docs) doc.inlinks = inbound.get(doc.id);

const SECTION_ORDER = ['.', 'personas', 'journeys', 'pages', 'components', 'data', 'concepts'];
const SECTION_LABEL = {
  '.': 'Overview', personas: 'Personas', journeys: 'Journeys', pages: 'Pages',
  components: 'Components', data: 'Data lineage', concepts: 'Concepts',
};

docs.sort((a, b) => {
  const sa = SECTION_ORDER.indexOf(a.dir), sb = SECTION_ORDER.indexOf(b.dir);
  if (sa !== sb) return (sa < 0 ? 99 : sa) - (sb < 0 ? 99 : sb);
  if (a.id.endsWith('index.md') !== b.id.endsWith('index.md')) return a.id.endsWith('index.md') ? -1 : 1;
  return a.id.localeCompare(b.id);
});

const DATA = { docs, sections: SECTION_ORDER, labels: SECTION_LABEL };
const html = PAGE(JSON.stringify(DATA));
writeFileSync(OUT, html);
console.log(`Wrote ${OUT} from ${docs.length} concepts.`);

function PAGE(json) {
  return `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Raidar Review Surface — UX Knowledgebase</title>
<style>
  :root {
    --bg:#0d1117; --surface:#11161d; --surface2:#161c25; --border:#222a35;
    --fg:#c9d4e0; --fg-dim:#7d8a9a; --fg-faint:#5a6675; --accent:#5b8def;
    --green:#3fb950; --cyan:#39c5cf; --orange:#e3a008; --red:#f85149; --mono:ui-monospace,SFMono-Regular,Menlo,monospace;
  }
  * { box-sizing:border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
  a { color:var(--accent); text-decoration:none; }
  a:hover { text-decoration:underline; }
  .app { display:grid; grid-template-columns:300px 1fr; height:100vh; }
  /* sidebar */
  .side { border-right:1px solid var(--border); background:var(--surface); overflow-y:auto; display:flex; flex-direction:column; }
  .brand { padding:16px 18px 10px; border-bottom:1px solid var(--border); position:sticky; top:0; background:var(--surface); z-index:2; }
  .brand h1 { font-size:14px; margin:0 0 2px; letter-spacing:-0.2px; }
  .brand p { margin:0; font-size:11px; color:var(--fg-faint); }
  .search { margin:10px 14px; }
  .search input { width:100%; padding:7px 10px; border-radius:7px; border:1px solid var(--border); background:#0a0e13; color:var(--fg); font-size:12px; outline:none; }
  .nav { padding:0 8px 24px; }
  .nav .sec { font-size:10px; text-transform:uppercase; letter-spacing:.8px; color:var(--fg-faint); padding:14px 10px 4px; }
  .nav a.item { display:flex; flex-direction:column; gap:1px; padding:6px 10px; border-radius:7px; color:var(--fg-dim); }
  .nav a.item:hover { background:var(--surface2); text-decoration:none; color:var(--fg); }
  .nav a.item.active { background:rgba(91,141,239,.12); color:var(--fg); box-shadow:inset 2px 0 0 var(--accent); }
  .nav a.item .t { font-size:12.5px; }
  .nav a.item .ty { font-size:10px; color:var(--fg-faint); }
  .nav a.item.hide { display:none; }
  /* main */
  .main { overflow-y:auto; }
  .wrap { max-width:860px; margin:0 auto; padding:30px 40px 80px; }
  .crumb { font-size:11px; color:var(--fg-faint); font-family:var(--mono); margin-bottom:14px; }
  .metacard { background:var(--surface); border:1px solid var(--border); border-radius:12px; padding:16px 18px; margin-bottom:26px; }
  .metacard .ty { display:inline-block; font-size:10px; text-transform:uppercase; letter-spacing:.6px; color:var(--accent); border:1px solid var(--border); border-radius:20px; padding:2px 9px; margin-bottom:8px; }
  .metacard h2 { margin:0 0 6px; font-size:21px; letter-spacing:-0.3px; }
  .metacard .desc { color:var(--fg-dim); font-size:13.5px; margin:0 0 12px; }
  .metarows { display:flex; flex-wrap:wrap; gap:6px; }
  .tag { font-size:10.5px; font-family:var(--mono); background:var(--surface2); border:1px solid var(--border); border-radius:6px; padding:2px 8px; color:var(--fg-dim); }
  .tag.res a { color:var(--cyan); }
  .doc h1 { font-size:23px; margin:30px 0 12px; letter-spacing:-0.3px; }
  .doc h1:first-child { margin-top:0; }
  .doc h2 { font-size:17px; margin:28px 0 10px; padding-bottom:6px; border-bottom:1px solid var(--border); }
  .doc h3 { font-size:14px; margin:20px 0 8px; color:var(--fg); }
  .doc p { margin:10px 0; }
  .doc ul,.doc ol { margin:10px 0; padding-left:22px; }
  .doc li { margin:4px 0; }
  .doc code { font-family:var(--mono); font-size:12px; background:var(--surface2); border:1px solid var(--border); border-radius:5px; padding:1px 5px; }
  .doc pre { background:#0a0e13; border:1px solid var(--border); border-radius:10px; padding:14px 16px; overflow-x:auto; }
  .doc pre code { background:none; border:none; padding:0; font-size:12px; line-height:1.5; color:var(--fg-dim); }
  .doc blockquote { margin:12px 0; padding:6px 14px; border-left:3px solid var(--accent); color:var(--fg-dim); background:var(--surface); border-radius:0 8px 8px 0; }
  .doc table { border-collapse:collapse; width:100%; margin:14px 0; font-size:12.5px; display:block; overflow-x:auto; }
  .doc th,.doc td { border:1px solid var(--border); padding:7px 10px; text-align:left; vertical-align:top; }
  .doc th { background:var(--surface2); font-weight:600; color:var(--fg); }
  .doc tr:nth-child(even) td { background:rgba(255,255,255,.012); }
  .doc hr { border:none; border-top:1px solid var(--border); margin:24px 0; }
  .doc a.x { color:var(--cyan); }
  .doc a.x::after { content:"↗"; font-size:9px; vertical-align:super; opacity:.6; }
  .links { margin-top:40px; border-top:1px solid var(--border); padding-top:18px; display:grid; grid-template-columns:1fr 1fr; gap:24px; }
  .links h4 { font-size:11px; text-transform:uppercase; letter-spacing:.7px; color:var(--fg-faint); margin:0 0 8px; }
  .links a { display:block; font-size:12.5px; padding:3px 0; }
  .links .none { font-size:12px; color:var(--fg-faint); }
  @media (max-width:780px){ .app{grid-template-columns:1fr;} .side{display:none;} }
</style>
</head>
<body>
<div class="app">
  <aside class="side">
    <div class="brand"><h1>Raidar Review Surface</h1><p>UX Knowledgebase · Open Knowledge Format</p></div>
    <div class="search"><input id="q" placeholder="Filter concepts…" autocomplete="off" /></div>
    <nav class="nav" id="nav"></nav>
  </aside>
  <main class="main"><div class="wrap" id="wrap"></div></main>
</div>
<script id="kb" type="application/json">${json.replace(/<\//g, '<\\/')}</script>
<script>
const KB = JSON.parse(document.getElementById('kb').textContent);
const byId = new Map(KB.docs.map(d => [d.id, d]));

function esc(s){ return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function inline(s, dir){
  // links first (capture before escaping mangles), via placeholders
  const tokens = [];
  s = s.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, (_, text, href) => {
    const i = tokens.length; tokens.push({text, href}); return '\\u0000'+i+'\\u0001';
  });
  s = esc(s);
  s = s.replace(/\`([^\`]+)\`/g, (_, c)=>'<code>'+c+'</code>');
  s = s.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\\*([^*]+)\\*(?!\\*)/g, '$1<em>$2</em>');
  s = s.replace(/\\u0000(\\d+)\\u0001/g, (_, i) => {
    const {text, href} = tokens[+i];
    const norm = resolve(dir, href);
    if (byId.has(norm)) return '<a href="#'+encodeURIComponent(norm)+'">'+esc(text)+'</a>';
    if (/^https?:/.test(href)) return '<a class="x" target="_blank" rel="noopener" href="'+href+'">'+esc(text)+'</a>';
    return '<a class="x" href="'+href+'">'+esc(text)+'</a>';
  });
  return s;
}
function resolve(dir, href){
  const clean = href.split('#')[0];
  let base = (dir==='.'?'':dir).split('/').filter(Boolean);
  if (clean.startsWith('/')) base = [];
  for (const part of clean.split('/')){
    if (part===''||part==='.') continue;
    if (part==='..') base.pop(); else base.push(part);
  }
  return base.join('/');
}

function render(body, dir){
  const lines = body.split('\\n');
  let html = '', i = 0;
  while (i < lines.length){
    let line = lines[i];
    if (/^\`\`\`/.test(line)){
      const buf=[]; i++;
      while(i<lines.length && !/^\`\`\`/.test(lines[i])) buf.push(lines[i++]);
      i++; html += '<pre><code>'+esc(buf.join('\\n'))+'</code></pre>'; continue;
    }
    let h = line.match(/^(#{1,6})\\s+(.*)/);
    if (h){ const n=h[1].length; html += '<h'+n+'>'+inline(h[2],dir)+'</h'+n+'>'; i++; continue; }
    if (/^\\s*\\|.*\\|\\s*$/.test(line) && i+1<lines.length && /^\\s*\\|[\\s:|-]+\\|\\s*$/.test(lines[i+1])){
      const head = splitRow(line);
      i += 2; const rows=[];
      while(i<lines.length && /^\\s*\\|.*\\|\\s*$/.test(lines[i])) rows.push(splitRow(lines[i++]));
      html += '<table><thead><tr>'+head.map(c=>'<th>'+inline(c,dir)+'</th>').join('')+'</tr></thead><tbody>'+
        rows.map(r=>'<tr>'+r.map(c=>'<td>'+inline(c,dir)+'</td>').join('')+'</tr>').join('')+'</tbody></table>';
      continue;
    }
    if (/^\\s*[-*]\\s+/.test(line)){
      const buf=[];
      while(i<lines.length && /^\\s*[-*]\\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\\s*[-*]\\s+/,''));
      html += '<ul>'+buf.map(b=>'<li>'+inline(b,dir)+'</li>').join('')+'</ul>'; continue;
    }
    if (/^\\s*\\d+\\.\\s+/.test(line)){
      const buf=[];
      while(i<lines.length && /^\\s*\\d+\\.\\s+/.test(lines[i])) buf.push(lines[i++].replace(/^\\s*\\d+\\.\\s+/,''));
      html += '<ol>'+buf.map(b=>'<li>'+inline(b,dir)+'</li>').join('')+'</ol>'; continue;
    }
    if (/^>\\s?/.test(line)){
      const buf=[];
      while(i<lines.length && /^>\\s?/.test(lines[i])) buf.push(lines[i++].replace(/^>\\s?/,''));
      html += '<blockquote>'+inline(buf.join(' '),dir)+'</blockquote>'; continue;
    }
    if (/^---+\\s*$/.test(line)){ html+='<hr/>'; i++; continue; }
    if (line.trim()===''){ i++; continue; }
    const buf=[line]; i++;
    while(i<lines.length && lines[i].trim()!=='' && !/^(#{1,6}\\s|\`\`\`|>\\s?|\\s*[-*]\\s|\\s*\\d+\\.\\s)/.test(lines[i]) && !/^\\s*\\|.*\\|\\s*$/.test(lines[i])) buf.push(lines[i++]);
    html += '<p>'+inline(buf.join(' '),dir)+'</p>';
  }
  return html;
}
function splitRow(line){
  return line.trim().replace(/^\\|/,'').replace(/\\|$/,'').split('|').map(c=>c.trim());
}

function buildNav(){
  const nav = document.getElementById('nav'); nav.innerHTML='';
  for (const sec of KB.sections){
    const items = KB.docs.filter(d=>d.dir===sec);
    if (!items.length) continue;
    const h=document.createElement('div'); h.className='sec'; h.textContent=KB.labels[sec]||sec; nav.appendChild(h);
    for (const d of items){
      const a=document.createElement('a'); a.className='item'; a.href='#'+encodeURIComponent(d.id); a.dataset.id=d.id;
      a.innerHTML='<span class="t">'+esc(d.meta.title||d.id)+'</span><span class="ty">'+esc(d.meta.type||'')+'</span>';
      nav.appendChild(a);
    }
  }
}
function show(id){
  const d = byId.get(id) || byId.get('index.md'); if(!d) return;
  const m = d.meta;
  const wrap = document.getElementById('wrap');
  const tags = (Array.isArray(m.tags)?m.tags:[]).map(t=>'<span class="tag">'+esc(t)+'</span>').join('');
  const res = m.resource ? '<span class="tag res">source · <a href="'+m.resource+'">'+esc(m.resource.split('/').pop())+'</a></span>' : '';
  const ts = m.timestamp ? '<span class="tag">'+esc((m.timestamp||'').split('T')[0])+'</span>' : '';
  const out = d.outlinks.map(t=>'<a href="#'+encodeURIComponent(t)+'">'+esc((byId.get(t).meta.title)||t)+'</a>').join('') || '<span class="none">—</span>';
  const inl = d.inlinks.map(t=>'<a href="#'+encodeURIComponent(t)+'">'+esc((byId.get(t).meta.title)||t)+'</a>').join('') || '<span class="none">—</span>';
  wrap.innerHTML =
    '<div class="crumb">'+esc(d.id)+'</div>'+
    '<div class="metacard"><span class="ty">'+esc(m.type||'Concept')+'</span>'+
      '<h2>'+esc(m.title||d.id)+'</h2>'+
      (m.description?'<p class="desc">'+esc(m.description)+'</p>':'')+
      '<div class="metarows">'+res+ts+tags+'</div></div>'+
    '<div class="doc">'+render(d.body, d.dir)+'</div>'+
    '<div class="links"><div><h4>Links to →</h4>'+out+'</div><div><h4>← Linked from</h4>'+inl+'</div></div>';
  document.querySelectorAll('.nav a.item').forEach(a=>a.classList.toggle('active', a.dataset.id===d.id));
  document.querySelector('.main').scrollTop=0;
}
function route(){ const id = decodeURIComponent(location.hash.slice(1)); show(id||'index.md'); }
window.addEventListener('hashchange', route);
document.getElementById('q').addEventListener('input', e=>{
  const q=e.target.value.toLowerCase();
  document.querySelectorAll('.nav a.item').forEach(a=>{
    const d=byId.get(a.dataset.id);
    const hay=((d.meta.title||'')+' '+(d.meta.type||'')+' '+(d.meta.description||'')+' '+(Array.isArray(d.meta.tags)?d.meta.tags.join(' '):'')).toLowerCase();
    a.classList.toggle('hide', q && !hay.includes(q));
  });
});
buildNav(); route();
</script>
</body>
</html>`;
}
