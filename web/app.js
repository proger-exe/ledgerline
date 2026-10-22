'use strict';
/* Ledgerline Mini App. No build step: hash router + template strings. */

const tg = window.Telegram?.WebApp;
const app = { boot: null, draft: null, stack: [] };
let renderSeq = 0;

// ---------------------------------------------------------------- helpers

class Raw { constructor(s) { this.s = s; } toString() { return this.s; } }
const raw = s => new Raw(s);
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const val = v => v == null || v === false ? '' : v instanceof Raw ? v.s : Array.isArray(v) ? v.map(val).join('') : esc(v);
const html = (strs, ...vals) => raw(strs.reduce((out, s, i) => out + s + (i < vals.length ? val(vals[i]) : ''), ''));
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];

const num = (n, d = 2) => Number(n || 0).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
const amt = n => num(n, Number.isInteger(+n) ? 0 : 2);
const usd = n => '$' + num(n);
const short = a => a ? `${a.slice(0, 6)}…${a.slice(-4)}` : '';
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = n => String(n).padStart(2, '0');
function when(iso, withTime = true) {
  if (!iso) return '';
  const d = new Date(iso), now = new Date();
  const day = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
  const diff = Math.round((day(now) - day(d)) / 864e5);
  const t = withTime ? ` ${pad(d.getHours())}:${pad(d.getMinutes())}` : '';
  if (diff === 0) return 'Today' + t;
  if (diff === 1) return 'Yesterday' + t;
  return `${d.getDate()} ${MONTHS[d.getMonth()]}${d.getFullYear() !== now.getFullYear() ? ' ' + d.getFullYear() : ''}`;
}
const month = (d = new Date()) => `${d.getFullYear()}-${pad(d.getMonth() + 1)}`;
const monthName = p => { const [y, m] = p.split('-'); return `${['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][m - 1]} ${y}`; };

const ROLE = { owner: 'Owner', cfo: 'CFO', accountant: 'Accountant', project_manager: 'Project manager', viewer: 'Viewer' };
const STATUS = {
  DRAFT: ['Request', 'gray'], PENDING_APPROVAL: ['Pending approval', 'amber'], APPROVED: ['Approved', 'amber'],
  SIGNING: ['Sending', 'amber'], BROADCASTED: ['Sent', 'amber'], CONFIRMING: ['Confirming', 'amber'],
  CONFIRMED: ['Confirmed', 'green'], FAILED: ['Failed', 'red'], REJECTED: ['Rejected', 'red'], CANCELLED: ['Cancelled', 'gray'],
};
const IN_FLIGHT = ['APPROVED', 'SIGNING', 'BROADCASTED', 'CONFIRMING'];
const pill = s => html`<span class="pill ${(STATUS[s] || [s, 'gray'])[1]}">${(STATUS[s] || [s])[0]}</span>`;
const slotsText = slots => (slots || []).map(s => s.map(r => ROLE[r]).join(' or ')).join(' + ');

const role = () => app.boot.me.role;
const is = (...roles) => roles.includes(role());
const STAFF = ['owner', 'cfo', 'accountant'];
const READERS = ['owner', 'cfo', 'accountant', 'viewer'];
const canPay = () => is(...STAFF, 'project_manager');

const ICONS = {
  home: '<path d="M3 11l9-7 9 7v9a1 1 0 0 1-1 1h-5v-6H9v6H4a1 1 0 0 1-1-1z"/>',
  payments: '<path d="M7 7h13M16 3l4 4-4 4M17 17H4M8 13l-4 4 4 4"/>',
  projects: '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
  analytics: '<path d="M5 20V11M11 20V5M17 20v-6M3 20h18"/>',
  more: '<circle cx="5" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="19" cy="12" r="1.3"/>',
  search: '<circle cx="11" cy="11" r="7"/><path d="M20 20l-3.5-3.5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  wallet: '<path d="M3 7a2 2 0 0 1 2-2h13v4"/><path d="M3 7v11a2 2 0 0 0 2 2h15V9H5a2 2 0 0 1-2-2z"/><circle cx="16" cy="14.5" r="1"/>',
  users: '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14c2 .7 3.5 2.9 3.5 6"/>',
  file: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
  shield: '<path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z"/>',
  settings: '<path d="M4 6h9M19 6h1M4 12h3M13 12h7M4 18h11M20 18h0"/><circle cx="16" cy="6" r="2.2"/><circle cx="10" cy="12" r="2.2"/><circle cx="18" cy="18" r="2.2"/>',
  check: '<path d="M5 12.5l4.5 4.5L19 7"/>',
  chevron: '<path d="M9 6l6 6-6 6"/>',
  copy: '<rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h8"/>',
  share: '<path d="M12 3v12M7 8l5-5 5 5M5 14v5a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2v-5"/>',
  transfer: '<path d="M4 8h15l-3-3M20 16H5l3 3"/>',
  receive: '<path d="M12 4v12M7 11l5 5 5-5M5 20h14"/>',
  send: '<path d="M12 20V8M7 13l5-5 5 5M5 4h14"/>',
  tag: '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
  approve: '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l3 3 5-6"/>',
  clip: '<path d="M20 11.5l-8.2 8.2a5 5 0 0 1-7.1-7.1l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.5 8.5a1.7 1.7 0 0 1-2.4-2.4L15 7"/>',
};
const icon = (name, size = 22) => raw(`<svg class="i" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`);

function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => t.classList.remove('show'), 2600);
}
const haptic = type => tg?.HapticFeedback?.notificationOccurred?.(type);
const confirmDialog = msg => new Promise(res => tg?.showConfirm ? tg.showConfirm(msg, res) : res(window.confirm(msg)));

async function copy(text) {
  try { await navigator.clipboard.writeText(text); } catch {
    const el = Object.assign(document.createElement('textarea'), { value: text });
    document.body.append(el); el.select(); document.execCommand('copy'); el.remove();
  }
  haptic('success');
  toast('Copied');
}

// ---------------------------------------------------------------- API

class ApiError extends Error {}
async function api(path, { method = 'GET', body, rawBody, headers = {} } = {}) {
  let res;
  try {
    res = await fetch('/api' + path, {
      method,
      headers: { Authorization: 'tma ' + (tg?.initData || ''), ...(body ? { 'Content-Type': 'application/json' } : {}), ...headers },
      body: rawBody ?? (body && JSON.stringify(body)),
    });
  } catch {
    throw new ApiError("Can't reach Ledgerline. Check your connection and try again.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    let d = data.detail;
    if (Array.isArray(d)) d = d.map(e => `${e.loc?.at(-1) ?? 'value'}: ${e.msg}`).join('\n');
    throw new ApiError(d || `Ledgerline answered with error ${res.status}. Try again; if it repeats, tell the owner.`);
  }
  return data;
}

// Run an action from a button: disables it, shows the error in place (or toast), never loses the user's input.
async function act(btn, fn) {
  if (btn.disabled) return;
  const label = btn.innerHTML;
  btn.disabled = true;
  try { await fn(); } catch (e) {
    haptic('error');
    let el = btn.parentElement, box = null;
    while (el && !(box = el.querySelector(':scope > .form-error'))) el = el.parentElement;
    if (box) { box.hidden = false; box.textContent = e.message; box.scrollIntoView({ block: 'nearest' }); } else toast(e.message);
  } finally { if (btn.isConnected) { btn.disabled = false; btn.innerHTML = label; } }
}
const formError = () => html`<div class="notice error form-error" hidden></div>`;

// ---------------------------------------------------------------- shell & router

const TABS = [['/', 'Overview', 'home'], ['/payments', 'Payments', 'payments'], ['/projects', 'Projects', 'projects'],
  ['/analytics', 'Analytics', 'analytics'], ['/more', 'More', 'more']];
const SIDE = [['/', 'Overview', 'home'], ['/payments', 'Payments', 'payments'], ['/projects', 'Projects', 'projects'],
  ['/wallets', 'Wallets', 'wallet'], ['/counterparties', 'Counterparties', 'users'], ['/budgets', 'Budgets', 'tag'],
  ['/analytics', 'Analytics', 'analytics'], ['/reports', 'Reports', 'file'], ['/audit', 'Audit', 'shield']];
const ACCESS = { '/wallets': READERS, '/counterparties': READERS, '/analytics': READERS, '/reports': READERS, '/audit': ['owner', 'cfo', 'viewer'],
  '/budgets': READERS, '/settings': ['owner', 'cfo'] };
const allowed = path => !ACCESS[path] || is(...ACCESS[path]);

function shell(path) {
  const root = '/' + (path.split('/')[1] || '');
  const tab = TABS.some(t => t[0] === root) ? root : ['/search', '/transfer', '/pay'].includes(root) ? '/' : '/more';
  $('#tabs').innerHTML = TABS.filter(t => allowed(t[0])).map(([p, label, ic]) =>
    html`<a href="#${p}" class="${p === tab ? 'on' : ''}">${icon(ic, 24)}<span>${label}</span></a>`).join('');
  $('#side').innerHTML = html`<div class="brand">Ledgerline</div>
    ${SIDE.filter(s => allowed(s[0])).map(([p, label, ic]) => html`<a href="#${p}" class="${p === root ? 'on' : ''}">${icon(ic, 20)}${label}</a>`)}
    <hr>
    <a href="#/more" class="${root === '/more' ? 'on' : ''}">${icon('users', 20)}Profile</a>
    ${allowed('/settings') ? html`<a href="#/settings" class="${root === '/settings' ? 'on' : ''}">${icon('settings', 20)}Settings</a>` : ''}`;
}

const view = (content, { flow = false } = {}) => {
  $('#view').innerHTML = String(content);
  document.body.classList.toggle('flow-mode', flow);
  if (!flow) tg?.disableClosingConfirmation?.();
};
const live = () => { const seq = renderSeq; return () => seq === renderSeq; };
const topbar = (title, right = '') => html`<header class="top"><h1>${title}</h1>${right}</header>`;
const searchBtn = () => html`<a class="icon-btn" href="#/search" aria-label="Search">${icon('search', 20)}</a>`;
const skelRows = (n = 5) => html`<div class="list">${Array.from({ length: n }, () => html`<div class="skel-row"><div style="flex:1"><div class="skel" style="height:14px;width:55%"></div><div class="skel" style="height:11px;width:35%;margin-top:8px"></div></div><div class="skel" style="height:16px;width:70px"></div></div>`)}</div>`;
const skelHero = () => html`<div class="hero"><div class="skel" style="height:13px;width:30%"></div><div class="skel" style="height:40px;width:65%;margin-top:10px"></div></div>`;
const empty = (title, text, action) => html`<div class="empty"><b>${title}</b><p>${text}</p>${action ? html`<a class="btn primary" href="#${action[1]}">${action[0]}</a>` : ''}</div>`;

const ROUTES = [
  [/^\/$/, overview],
  [/^\/search$/, searchScreen],
  [/^\/payments$/, paymentsList],
  [/^\/payments\/(\d+)$/, paymentDetail],
  [/^\/approvals$/, approvalsList],
  [/^\/pay$/, payRecipient],
  [/^\/pay\/new$/, recipientForm],
  [/^\/pay\/amount$/, payAmount],
  [/^\/pay\/details$/, payDetails],
  [/^\/pay\/confirm$/, payConfirm],
  [/^\/pay\/done\/(\d+)$/, payDone],
  [/^\/projects$/, projectsList],
  [/^\/projects\/new$/, () => projectForm()],
  [/^\/projects\/(\d+)$/, projectDetail],
  [/^\/projects\/(\d+)\/edit$/, projectForm],
  [/^\/budgets$/, budgetsScreen],
  [/^\/counterparties$/, counterpartiesList],
  [/^\/counterparties\/(\d+)$/, counterpartyDetail],
  [/^\/wallets$/, walletsList],
  [/^\/wallets\/new$/, walletForm],
  [/^\/wallets\/(\d+)$/, walletDetail],
  [/^\/wallets\/(\d+)\/receive$/, receiveScreen],
  [/^\/transfer$/, transferForm],
  [/^\/analytics$/, analyticsScreen],
  [/^\/reports$/, reportsScreen],
  [/^\/audit$/, auditScreen],
  [/^\/more$/, moreScreen],
  [/^\/settings$/, settingsScreen],
  [/^\/settings\/users$/, usersScreen],
  [/^\/settings\/policy$/, policyScreen],
  [/^\/settings\/categories$/, categoriesScreen],
];

function parseHash() {
  const h = location.hash.startsWith('#/') ? location.hash.slice(1) : '/';
  const [path, qs = ''] = h.split('?');
  return { path, query: Object.fromEntries(new URLSearchParams(qs)), full: h };
}
function go(path, { replace = false } = {}) {
  if (!replace) return (location.hash = '#' + path);
  app.stack.pop();
  location.replace('#' + path);
}

function parentOf(path) {
  if (path.startsWith('/pay')) return '/';
  const parts = path.split('/').filter(Boolean);
  return parts.length > 1 ? '/' + parts.slice(0, -1).join('/') : '/';
}

async function render() {
  const seq = ++renderSeq;
  const { path, query, full } = parseHash();
  // Our own history stack so Back is predictable even after deep links from bot notifications.
  if (app.stack.at(-2) === full) app.stack.pop(); else if (app.stack.at(-1) !== full) app.stack.push(full);
  const isRoot = TABS.some(t => t[0] === path);
  if (tg?.BackButton) isRoot ? tg.BackButton.hide() : tg.BackButton.show();
  shell(path);
  const route = ROUTES.find(([re]) => re.test(path));
  window.scrollTo(0, 0);
  try {
    if (!route) return view(empty('Page not found', 'This link leads nowhere.', ['Go to overview', '/']));
    const root = '/' + path.split('/')[1];
    if (!allowed(root)) return view(empty('No access', `Your role (${ROLE[role()]}) can't open this section.`, ['Go to overview', '/']));
    await route[1](...path.match(route[0]).slice(1), query);
  } catch (e) {
    if (seq !== renderSeq) return;
    view(html`<div class="empty"><b>Couldn't load this screen</b><p>${e.message}</p><button class="btn primary" id="retry">Try again</button></div>`);
    $('#retry').onclick = render;
  }
}

function back() {
  if (parseHash().path.startsWith('/pay/done')) { app.stack = []; return go('/', { replace: true }); }
  if (app.stack.length > 1) history.back();
  else { const p = parentOf(parseHash().path); app.stack = []; go(p, { replace: true }); }
}

// ---------------------------------------------------------------- rows

function paymentRow(p) {
  const meta = [p.category, p.project, when(p.created_at)].filter(Boolean).join(' · ');
  return html`<a class="row" href="#/payments/${p.id}">
    <div class="main"><div class="title">${p.counterparty}</div><div class="meta">${meta}</div></div>
    <div class="side-val"><div class="amt">−${amt(p.amount)} ${p.asset}</div>${pill(p.status)}</div></a>`;
}

function entryRow(e) {
  const sign = !e.signed ? '' : e.amount > 0 ? '+' : '−';
  return html`<div class="row">
    <div class="main"><div class="title">${e.title}</div><div class="meta">${e.subtitle} · ${when(e.created_at)}</div></div>
    <div class="side-val"><div class="amt ${sign === '+' ? 'in' : ''}">${sign}${amt(Math.abs(e.amount))} ${e.asset}</div></div></div>`;
}
const activityRow = i => i.type === 'payment' ? paymentRow(i) : entryRow(i);

// ---------------------------------------------------------------- overview & search

async function overview() {
  const alive = live();
  const pm = is('project_manager');
  view(html`${topbar(raw('<span class="brand">Ledgerline</span>'), searchBtn())}${pm ? '' : skelHero()}${skelRows(5)}`);
  const d = await api('/overview');
  if (!alive()) return;
  const quick = [
    is('owner', 'cfo') && html`<a href="#/transfer">${icon('transfer')}Transfer between projects</a>`,
    is(...READERS) && app.boot.wallets.length && html`<a href="#/wallets/${(defaultWallet() || app.boot.wallets[0]).id}/receive">${icon('receive')}Receive</a>`,
    is(...STAFF) && html`<a href="#/approvals">${icon('approve')}Approvals</a>`,
  ].filter(Boolean);
  view(html`
    ${topbar(raw('<span class="brand">Ledgerline</span>'), searchBtn())}
    ${pm ? '' : html`<section class="hero">
      <div class="label">Total Assets</div>
      <div class="big">${usd(d.total)}</div>
      ${d.month_net != null ? html`<div class="delta ${d.month_net >= 0 ? 'up' : 'down'}">${d.month_net >= 0 ? '+' : '−'} ${usd(Math.abs(d.month_net))} this month</div>` : ''}
    </section>`}
    <div class="hscroll">
      ${pm ? '' : html`
        <a class="card mini" href="#/wallets"><div class="label">Treasury</div><div class="val">${amt(d.treasury)} USDT</div></a>
        <a class="card mini" href="#/wallets"><div class="label">Operations</div><div class="val">${amt(d.operations)} USDT</div></a>`}
      <a class="card mini" href="#/projects"><div class="label">Available budget</div><div class="val">${amt(d.available_budget)} USDT</div></a>
    </div>
    ${canPay() ? html`<a class="btn primary" href="#/pay?new=1">${icon('plus')}${pm ? 'Request money' : 'New payment'}</a>` : ''}
    ${quick.length ? html`<div class="actions">${quick}</div>` : ''}
    ${d.pending_approvals ? html`<a class="banner" href="#/approvals"><span class="dot"></span><span class="main"><b>${d.pending_approvals}</b> payment${d.pending_approvals > 1 ? 's' : ''} waiting for your approval</span>${icon('chevron', 18)}</a>` : ''}
    <h2>Recent activity</h2>
    ${d.activity.length ? html`<div class="list">${d.activity.map(activityRow)}</div>`
      : empty('No payments yet', 'Your completed and pending payments will appear here.', canPay() && ['Create payment', '/pay?new=1'])}
  `);
}

async function searchScreen(query) {
  view(html`${topbar('Search')}
    <div class="search">${icon('search', 18)}<input class="input" id="q" type="search" placeholder="Payment ID, tx hash, recipient, address…" value="${query.q || ''}" autocomplete="off"></div>
    <div id="results"></div>`);
  const input = $('#q');
  let timer;
  const run = async () => {
    const q = input.value.trim();
    history.replaceState(null, '', '#/search' + (q ? '?q=' + encodeURIComponent(q) : ''));
    if (q.length < 2) return ($('#results').innerHTML = '');
    $('#results').innerHTML = String(html`<h2>Results</h2>${skelRows(3)}`);
    const r = await api('/search?q=' + encodeURIComponent(q)).catch(e => ({ error: e.message }));
    if (input.value.trim() !== q) return;
    if (r.error) return ($('#results').innerHTML = String(html`<div class="notice error">${r.error}</div>`));
    const group = (title, items, row) => items.length ? html`<h2>${title}</h2><div class="list">${items.map(row)}</div>` : '';
    const link = (href, title, meta) => html`<a class="row" href="#${href}"><div class="main"><div class="title">${title}</div><div class="meta">${meta}</div></div>${icon('chevron', 18)}</a>`;
    const out = html`
      ${group('Payments', r.payments, paymentRow)}
      ${group('Counterparties', r.counterparties, c => link(`/counterparties/${c.id}`, c.name, `${c.payments} payments`))}
      ${group('Projects', r.projects, p => link(`/projects/${p.id}`, p.name, p.code))}
      ${group('Wallets', r.wallets, w => link(`/wallets/${w.id}`, w.name, short(w.address)))}`;
    const hits = r.payments.length + r.counterparties.length + r.projects.length + r.wallets.length;
    $('#results').innerHTML = hits ? String(out) : String(empty('Nothing found', `No payments, recipients, projects or wallets match “${q}”.`));
  };
  input.oninput = () => { clearTimeout(timer); timer = setTimeout(run, 250); };
  input.focus();
  if (query.q) run();
}

// ---------------------------------------------------------------- payments

async function paymentsList(query) {
  const alive = live();
  const status = query.status || 'all';
  const filters = [['all', 'All'], ['pending', 'Pending'], ['completed', 'Completed'], ['failed', 'Failed']];
  const head = html`${topbar('Payments', canPay() ? html`<a class="btn primary small" href="#/pay?new=1">${icon('plus', 18)}${is('project_manager') ? 'Request' : 'New'}</a>` : '')}
    <div class="search" style="margin-bottom:10px">${icon('search', 18)}<input class="input" id="q" type="search" placeholder="Search payments" value="${query.q || ''}"></div>
    <div class="seg">${filters.map(([k, l]) => html`<button data-s="${k}" class="${k === status ? 'on' : ''}">${l}</button>`)}</div>`;
  view(html`${head}${skelRows(6)}`);
  const qs = new URLSearchParams({ status, ...(query.q ? { q: query.q } : {}) });
  let rows = await api('/payments?' + qs);
  if (!alive()) return;
  const draw = () => {
    view(html`${head}
      ${rows.length ? html`<div class="list">${rows.map(paymentRow)}</div>
        ${rows.length % 30 === 0 ? html`<button class="btn ghost" id="more">Load more</button>` : ''}`
        : query.q || status !== 'all' ? empty('Nothing here', 'No payments match this filter.')
        : empty('No payments yet', 'Your completed and pending payments will appear here.', canPay() && ['Create payment', '/pay?new=1'])}`);
    $$('.seg button').forEach(b => b.onclick = () => go('/payments?' + new URLSearchParams({ status: b.dataset.s, ...(query.q ? { q: query.q } : {}) }), { replace: true }));
    $('#q').onchange = e => go('/payments?' + new URLSearchParams({ status, ...(e.target.value.trim() ? { q: e.target.value.trim() } : {}) }), { replace: true });
    const more = $('#more');
    if (more) more.onclick = () => act(more, async () => {
      const next = await api('/payments?' + new URLSearchParams({ ...Object.fromEntries(qs), before: rows.at(-1).id }));
      rows = rows.concat(next);
      draw();
    });
  };
  draw();
}

async function approvalsList() {
  const alive = live();
  view(html`${topbar('Approvals')}${skelRows(3)}`);
  const rows = await api('/approvals');
  if (!alive()) return;
  view(html`${topbar('Approvals')}
    ${rows.length ? html`<div class="list">${rows.map(paymentRow)}</div>`
      : empty('Nothing to approve', 'Payments that need your decision will appear here.')}`);
}

const EVENT = {
  CREATED: (e, p) => e.status === 'DRAFT' ? `Requested by ${e.actor}` : `Created by ${e.actor}`,
  SUBMITTED: e => `Submitted by ${e.actor}`,
  APPROVE: e => `Approved by ${e.actor}`,
  REJECTED: e => `Rejected by ${e.actor}`,
  CANCELLED: e => `Cancelled by ${e.actor}`,
  SIGNING: () => 'Sent to signer',
  SIGNED: () => 'Transaction signed',
  BROADCASTED: () => 'Broadcasted',
  CONFIRMING: e => e.comment || 'In a block',
  CONFIRMED: () => 'Confirmed',
  FAILED: () => 'Failed',
};

async function paymentDetail(id) {
  const alive = live();
  view(html`<div class="hero center" style="padding-top:24px"><div class="skel" style="height:12px;width:90px;margin:0 auto"></div><div class="skel" style="height:48px;width:60%;margin:14px auto"></div></div>${skelRows(4)}`);
  const p = await api('/payments/' + id);
  if (!alive()) return;
  const waiting = p.status === 'PENDING_APPROVAL' && p.approval?.slots?.length;
  const riskColor = { Low: 'green', Medium: 'amber', High: 'red' }[p.risk.level];
  const bc = p.blockchain;
  view(html`<div class="${p.can.vote ? 'flow' : ''}">
    <section class="summary-to">
      <div class="label">Payment #${p.code}</div>
      <div class="huge" style="margin-top:8px">${amt(p.amount)} ${p.asset}</div>
      <div style="margin-top:10px">${pill(p.status)}</div>
      <div class="muted">to</div>
      <a class="name" href="#/counterparties/${p.counterparty_id}">${p.counterparty}</a>
    </section>
    ${waiting ? html`<div class="notice"><b>Waiting for ${slotsText(p.approval.slots)} approval</b>${p.approval.rule}${p.approval.votes.length ? ` · approved by ${p.approval.votes.map(v => v.name).join(', ')}` : ''}</div>` : ''}
    ${p.status === 'DRAFT' ? html`<div class="notice info"><b>Payment request</b>Finance reviews it and submits the payment.</div>` : ''}
    ${p.error ? html`<div class="notice error"><b>Payment couldn't be completed</b>${p.error}</div>` : ''}
    ${p.can.vote ? html`<div class="card">
      <div class="kv"><span>Risk</span><span><span class="pill ${riskColor}">${p.risk.level}</span></span></div>
      <div class="kv"><span>${p.risk.reason}</span><span>${p.risk.previous} previous payment${p.risk.previous === 1 ? '' : 's'}</span></div>
      ${p.risk.last ? html`<div class="kv"><span>Last payment</span><span>${amt(p.risk.last.amount)} ${p.asset} · ${when(p.risk.last.date, false)}</span></div>` : ''}
    </div>` : ''}
    <div class="card">
      <div class="kv"><span>Created by</span><span>${p.created_by}</span></div>
      <div class="kv"><span>Project</span><a href="#/projects/${p.project_id}">${p.project}</a></div>
      <div class="kv"><span>Category</span><span>${p.category}</span></div>
      ${p.description ? html`<div class="kv"><span>Description</span><span>${p.description}</span></div>` : ''}
      <div class="kv"><span>From</span><span>${p.wallet.name}</span></div>
      <div class="kv"><span>To address</span><span class="mono">${short(p.destination.address)} ${p.destination.verified ? '' : html`<span class="pill amber">New</span>`}</span></div>
    </div>
    <h2>Timeline</h2>
    <div class="card timeline">${p.timeline.map((e, i) => html`<div class="ev ${i === p.timeline.length - 1 ? 'last' : ''}">
      <div class="t">${when(e.at)}</div><div>${(EVENT[e.action] || (() => e.action))(e, p)}</div>
      ${e.comment && e.action !== 'CONFIRMING' ? html`<div class="muted">${e.comment}</div>` : ''}</div>`)}</div>
    ${bc.tx_hash ? html`<h2>Blockchain</h2><details class="card"><summary>${bc.network} · ${bc.confirmations} confirmations</summary>
      <div class="kv"><span>Network</span><span>${bc.network}</span></div>
      <div class="kv"><span>Tx hash</span><button class="mono" style="background:none;border:0;padding:0;cursor:pointer" id="copyhash">${short(bc.tx_hash)} ${icon('copy', 14)}</button></div>
      ${bc.block ? html`<div class="kv"><span>Block</span><span>${bc.block}</span></div>` : ''}
      <div class="kv"><span>Confirmations</span><span>${bc.confirmations}</span></div>
      ${bc.fee != null ? html`<div class="kv"><span>Network fee</span><span>${num(bc.fee)} ${bc.fee_asset}</span></div>` : ''}
      <button class="btn" style="margin-top:10px" id="explorer">View explorer</button>
    </details>` : ''}
    <h2>Documents</h2>
    <div class="list">
      ${p.attachments.map(a => html`<button class="row" data-att="${a.id}"><span class="main title">${a.filename}</span><span class="muted" style="font-size:13px">Send to chat</span></button>`)}
      ${p.can.attach ? html`<label class="row file-row">${icon('clip', 20)}<span class="main">Attach invoice or receipt</span><input type="file" id="attach" hidden></label>` : ''}
      ${!p.attachments.length && !p.can.attach ? html`<div class="row muted">No documents</div>` : ''}
    </div>
    ${p.can.reclassify ? html`<h2>Correction</h2><details class="card" id="fix"><summary>Change project or category</summary>
      <p class="muted" style="margin:0 0 4px">The ledger is never edited: a reversal and a corrected entry are posted.</p>
      <label class="field"><span>Project</span><select class="input" id="fixProject">${app.boot.projects.map(x => html`<option value="${x.id}" ${x.id === p.project_id ? 'selected' : ''}>${x.name}</option>`)}</select></label>
      <label class="field"><span>Category</span><select class="input" id="fixCategory">${app.boot.categories.map(c => html`<option ${c === p.category ? 'selected' : ''}>${c}</option>`)}</select></label>
      <label class="field"><span>Reason</span><input class="input" id="fixReason" placeholder="Wrong project"></label>
      ${formError()}
      <button class="btn primary" style="margin-top:14px" id="fixSave">Post correction</button>
    </details>` : ''}
    ${p.can.submit || p.can.cancel ? html`<div style="margin-top:24px">
      ${p.can.submit ? html`<button class="btn primary" id="submit">Submit payment</button>` : ''}
      ${p.can.cancel ? html`<button class="btn ghost" id="cancel">Cancel ${p.status === 'DRAFT' ? 'request' : 'payment'}</button>` : ''}
      ${formError()}</div>` : ''}
    ${p.can.vote ? html`<div class="cta" id="voteBar">
      ${formError()}
      <div id="rejectBox" hidden><label class="field" style="margin-top:0"><span>Why are you rejecting it?</span><textarea class="input" id="rejectComment" placeholder="Comment for ${p.created_by}"></textarea></label><div style="height:10px"></div></div>
      <div class="btn-row"><button class="btn danger" id="reject">Reject</button><button class="btn primary" id="approve">Approve</button></div>
    </div>` : ''}
  </div>`, { flow: p.can.vote });

  if (bc.tx_hash) {
    $('#copyhash').onclick = () => copy(bc.tx_hash);
    $('#explorer').onclick = () => tg?.openLink ? tg.openLink(bc.explorer) : window.open(bc.explorer, '_blank');
  }
  $$('[data-att]').forEach(b => b.onclick = () => act(b, async () => {
    await api(`/attachments/${b.dataset.att}/send`, { method: 'POST' });
    toast('Sent to your Telegram chat');
  }));
  const file = $('#attach');
  if (file) file.onchange = async () => {
    const f = file.files[0];
    if (!f) return;
    try { await upload(p.id, f); toast('Attached'); render(); } catch (e) { toast(e.message); }
  };
  const on = (sel, fn) => { const el = $(sel); if (el) el.onclick = () => act(el, fn); };
  on('#submit', async () => { await api(`/payments/${p.id}/submit`, { method: 'POST', body: {} }); haptic('success'); render(); });
  on('#cancel', async () => {
    if (!await confirmDialog('Cancel this payment?')) return;
    await api(`/payments/${p.id}/cancel`, { method: 'POST', body: {} }); render();
  });
  on('#fixSave', async () => {
    await api(`/payments/${p.id}/reclassify`, { method: 'POST', body: {
      project_id: +$('#fixProject').value, category: $('#fixCategory').value, reason: $('#fixReason').value.trim() } });
    toast('Correction posted'); render();
  });
  on('#approve', async () => {
    if (!await confirmDialog(`Approve ${amt(p.amount)} ${p.asset} to ${p.counterparty}?`)) return;
    const r = await api(`/payments/${p.id}/approve`, { method: 'POST', body: {} });
    haptic('success');
    toast(r.status === 'APPROVED' ? 'Approved · sending now' : 'Approved · waiting for the next approver');
    render();
  });
  on('#reject', async () => {
    const box = $('#rejectBox');
    if (box.hidden) { box.hidden = false; $('#rejectComment').focus(); return; }
    const comment = $('#rejectComment').value.trim();
    if (!comment) throw new ApiError('Add a comment so the creator knows what to fix.');
    await api(`/payments/${p.id}/reject`, { method: 'POST', body: { comment } });
    haptic('warning'); toast('Payment rejected'); render();
  });
  if (IN_FLIGHT.includes(p.status)) setTimeout(() => alive() && render(), 10000); // live status while it's on its way
}

const upload = async (pid, f) => {
  if (f.size > 10 * 2 ** 20) throw new ApiError(`${f.name} is ${num(f.size / 2 ** 20, 1)} MB. Attach a file up to 10 MB.`);
  return api(`/payments/${pid}/attachments?filename=${encodeURIComponent(f.name)}`,
    { method: 'PUT', rawBody: f, headers: { 'Content-Type': f.type || 'application/octet-stream' } });
};

// ---------------------------------------------------------------- new payment flow

const defaultWallet = () => app.boot.wallets.find(w => w.type === 'OPERATIONS') || app.boot.wallets.find(w => w.type !== 'TREASURY');
const payWallets = () => app.boot.wallets.filter(w => w.type !== 'TREASURY');
const steps = n => html`<div class="steps">${[1, 2, 3, 4].map(i => html`<i class="${i <= n ? 'on' : ''}"></i>`)}</div>`;
const flowView = (n, title, body, cta) => {
  view(html`<div class="flow">${steps(n)}${topbar(title)}${body}<div class="cta">${formError()}${cta}</div></div>`, { flow: true });
  tg?.enableClosingConfirmation?.();
};
function needDraft() {
  if (app.draft?.cp) return true;
  go('/pay', { replace: true });
  return false;
}

async function payRecipient(query) {
  const alive = live();
  if (!app.draft || query.new || query.cp || query.wallet) app.draft = { wallet_id: +query.wallet || defaultWallet()?.id };
  if (!app.draft.wallet_id) {
    return view(empty('No wallet to pay from', 'The owner needs to add the Operations wallet first.', is('owner') && ['Add wallet', '/wallets/new']));
  }
  const wallet = app.boot.wallets.find(w => w.id === app.draft.wallet_id);
  const pick = (cp, address) => {
    Object.assign(app.draft, { cp: { id: cp.id, name: cp.name }, address, category: app.draft.category || cp.default_category });
    go('/pay/amount');
  };
  const choose = cp => {
    const addrs = cp.addresses.filter(a => a.network === wallet.network && a.asset === wallet.asset);
    if (addrs.length === 1) return pick(cp, addrs[0]);
    if (!addrs.length) return go(`/pay/new?cp=${cp.id}&name=${encodeURIComponent(cp.name)}`);
    flowView(1, cp.name, html`<p class="muted">Which address?</p><div class="list">${addrs.map(a => html`<button class="row" data-a="${a.id}">
      <div class="main"><div class="title">${a.label || `${a.asset} / ${a.network}`}</div><div class="meta mono">${a.address}</div></div>
      ${a.verified ? '' : html`<span class="pill amber">New</span>`}</button>`)}</div>`, html`<button class="btn" id="backList">Back</button>`);
    $$('[data-a]').forEach(b => b.onclick = () => pick(cp, addrs.find(a => a.id === +b.dataset.a)));
    $('#backList').onclick = render;
  };

  flowView(1, 'New payment', html`
    <label class="search">${icon('search', 18)}<input class="input" id="who" placeholder="Who are you paying?" autocomplete="off" aria-label="Who are you paying?"></label>
    <div id="cps" style="margin-top:12px">${skelRows(4)}</div>`, '');
  const cps = await api('/counterparties');
  if (!alive()) return;
  if (query.cp) { const cp = cps.find(c => c.id === +query.cp); if (cp) return choose(cp); }
  const draw = () => {
    const q = $('#who').value.trim().toLowerCase();
    const hits = cps.filter(c => !q || c.name.toLowerCase().includes(q) || c.addresses.some(a => a.address.toLowerCase().includes(q)));
    $('#cps').innerHTML = String(html`<div class="list">
      ${hits.slice(0, 30).map(c => html`<button class="row" data-cp="${c.id}"><div class="main"><div class="title">${c.name}</div>
        <div class="meta">${[c.default_category, c.payments ? `${c.payments} payments` : 'No payments yet'].filter(Boolean).join(' · ')}</div></div>${icon('chevron', 18)}</button>`)}
      ${is(...STAFF) ? html`<a class="row" href="#/pay/new${q ? '?name=' + encodeURIComponent($('#who').value.trim()) : ''}" style="color:var(--coral);font-weight:600">${icon('plus', 20)}<span class="main">New recipient</span></a>` : ''}
    </div>`);
    $$('[data-cp]').forEach(b => b.onclick = () => choose(cps.find(c => c.id === +b.dataset.cp)));
  };
  $('#who').oninput = draw;
  draw();
  if (window.innerWidth > 600) $('#who').focus();
}

async function recipientForm(query) {
  // Doubles as "add address to an existing recipient" (?cp=ID) and standalone (?standalone=1).
  const existing = query.cp ? +query.cp : null;
  const standalone = !!query.standalone;
  const title = existing ? `Add address · ${query.name || ''}` : 'New recipient';
  const body = html`
    ${existing ? '' : html`<label class="field"><span>Name</span><input class="input" id="name" value="${query.name || ''}" placeholder="Meredian Hosting" autocomplete="off"></label>`}
    <label class="field"><span>Wallet address</span><input class="input mono" id="address" placeholder="T…" autocomplete="off" spellcheck="false"></label>
    <div class="btn-row">
      <label class="field"><span>Network</span><select class="input" id="network"><option>TRON</option></select></label>
      <label class="field"><span>Asset</span><select class="input" id="asset"><option>USDT</option></select></label>
    </div>
    ${existing ? html`<label class="field"><span>Label (optional)</span><input class="input" id="label" placeholder="Main wallet"></label>`
      : html`<label class="field"><span>Category</span><select class="input" id="category">${app.boot.categories.map(c => html`<option>${c}</option>`)}</select></label>`}
`;
  const save = async () => {
    const address = { network: $('#network').value, asset: $('#asset').value, address: $('#address').value.trim(), label: $('#label')?.value.trim() || '' };
    if (!address.address) throw new ApiError('Paste the wallet address.');
    let cp, res;
    if (existing) {
      res = await api(`/counterparties/${existing}/addresses`, { method: 'POST', body: address });
      cp = { id: existing, name: query.name || '' };
      res.address_id = res.id;
    } else {
      const name = $('#name').value.trim();
      if (!name) throw new ApiError('Give the recipient a name.');
      res = await api('/counterparties', { method: 'POST', body: { name, default_category: $('#category').value, address } });
      cp = { id: res.id, name };
    }
    haptic('success');
    if (standalone) { app.boot = null; return go(`/counterparties/${cp.id}`, { replace: true }); }
    app.draft = { ...(app.draft || { wallet_id: defaultWallet()?.id }), cp, address: { id: res.address_id, address: address.address, verified: res.verified },
      category: app.draft?.category || (!existing && $('#category').value) };
    if (res.verified) return go('/pay/amount', { replace: true });
    $('.cta').innerHTML = String(html`<div class="notice"><b>New destination</b>This address has never been used before. Approval will be required.</div>
      <button class="btn primary" id="continue">Continue</button>`);
    $('#continue').onclick = () => go('/pay/amount', { replace: true });
  };
  const cta = html`<button class="btn primary" id="save">${existing ? 'Save address' : 'Add recipient'}</button>`;
  if (standalone) {
    view(html`${topbar(title)}${body}${formError()}<div style="margin-top:20px">${cta}</div>`);
  } else flowView(1, title, body, cta);
  const btn = $('#save');
  btn.onclick = () => act(btn, save);
}

function payAmount() {
  if (!needDraft()) return;
  const d = app.draft;
  const wallets = payWallets();
  const w = () => app.boot.wallets.find(x => x.id === d.wallet_id);
  const walletBox = () => is('project_manager') ? '' : html`<div class="card" style="margin-top:20px">
    ${wallets.length > 1 ? html`<select class="input" id="wallet" style="border:0;padding-left:0;min-height:0">${wallets.map(x => html`<option value="${x.id}" ${x.id === d.wallet_id ? 'selected' : ''}>${x.name} wallet</option>`)}</select>`
      : html`<div style="font-weight:600">${w().name} wallet</div>`}
    <div class="muted">Balance: ${num(w().balance)} ${w().asset}</div></div>`;
  flowView(2, `Pay ${d.cp.name}`, html`
    <div class="amount-entry">
      <input id="amount" inputmode="decimal" placeholder="0" autocomplete="off" value="${d.amount || ''}" aria-label="Amount">
      <div class="asset">${w().asset}</div>
      <div class="muted" id="usd" style="margin-top:8px">≈ ${usd(d.amount || 0)}</div>
    </div>
    <div class="center muted mono" style="font-size:12px">${short(d.address.address)} ${d.address.verified ? '' : html`<span class="pill amber">New address</span>`}</div>
    ${walletBox()}`, html`<button class="btn primary" id="next" disabled>Continue</button>`);
  const input = $('#amount'), next = $('#next');
  const parse = () => {
    const v = input.value.replace(',', '.').replace(/[^\d.]/g, '');
    return /^\d+(\.\d{0,6})?$/.test(v) ? Number(v) : NaN;
  };
  input.oninput = () => {
    const v = parse();
    $('#usd').textContent = '≈ ' + usd(v || 0);
    next.disabled = !(v > 0);
  };
  input.oninput();
  input.focus();
  const sel = $('#wallet');
  if (sel) sel.onchange = () => { d.wallet_id = +sel.value; d.amount = parse() || d.amount; payAmount(); };
  next.onclick = () => { d.amount = input.value.replace(',', '.').replace(/[^\d.]/g, ''); go('/pay/details'); };
  input.onkeydown = e => { if (e.key === 'Enter' && !next.disabled) next.click(); };
}

function payDetails() {
  if (!needDraft()) return;
  const d = app.draft;
  if (!app.boot.projects.length) return view(empty('No projects yet', 'Every payment belongs to a project. Ask a finance admin to create one.'));
  flowView(3, 'What is it for?', html`
    <label class="field"><span>Project</span><select class="input" id="project">
      ${d.project_id ? '' : html`<option value="">Choose project</option>`}
      ${app.boot.projects.map(p => html`<option value="${p.id}" ${p.id === d.project_id ? 'selected' : ''}>${p.name}</option>`)}</select></label>
    <label class="field"><span>Category</span><select class="input" id="category">
      ${app.boot.categories.map(c => html`<option ${c === (d.category || 'Other') ? 'selected' : ''}>${c}</option>`)}</select></label>
    <label class="field"><span>Description</span><textarea class="input" id="description" placeholder="September server payment">${d.description || ''}</textarea></label>
    <label class="field"><span>Invoice</span><label class="file">${icon('clip', 20)}<span id="fileName">${d.file ? d.file.name : 'Attach file'}</span><input type="file" id="file"></label></label>`,
  html`<button class="btn primary" id="next">Continue</button>`);
  if (app.boot.projects.length === 1 && !d.project_id) $('#project').value = app.boot.projects[0].id;
  $('#file').onchange = e => { d.file = e.target.files[0] || null; $('#fileName').textContent = d.file ? d.file.name : 'Attach file'; };
  $('#next').onclick = () => {
    if (!$('#project').value) { const box = $('.form-error'); box.hidden = false; box.textContent = 'Choose the project this payment is charged to.'; return; }
    Object.assign(d, { project_id: +$('#project').value, category: $('#category').value, description: $('#description').value.trim() });
    go('/pay/confirm');
  };
}

async function payConfirm() {
  if (!needDraft()) return;
  const alive = live();
  const d = app.draft;
  if (!d.project_id || !d.amount) return go('/pay/amount', { replace: true });
  const body = { wallet_id: d.wallet_id, project_id: d.project_id, counterparty_id: d.cp.id, address_id: d.address.id,
    amount: d.amount, category: d.category, description: d.description };
  const pm = is('project_manager');
  const wallet = app.boot.wallets.find(w => w.id === d.wallet_id);
  flowView(4, 'Confirm', html`<div class="card">${skelRows(1)}</div>${skelRows(4)}`, '');
  const pv = await api('/payments/preview', { method: 'POST', body }).catch(e => ({ problem: e.message }));
  if (!alive()) return;
  const project = app.boot.projects.find(p => p.id === d.project_id);
  const needs = pv.approval?.slots?.length;
  flowView(4, 'Confirm', html`
    <div class="card">
      <section class="summary-to" style="padding:8px 0 14px">
        <div class="huge">${amt(d.amount)} ${wallet.asset}</div>
        <div class="muted">to</div>
        <div class="name">${d.cp.name}</div>
        <div class="mono muted" style="margin-top:4px">${short(d.address.address)}</div>
      </section>
      ${pm ? '' : html`<div class="kv"><span>From</span><span>${wallet.name}</span></div>`}
      <div class="kv"><span>Project</span><span>${project.name}</span></div>
      <div class="kv"><span>Category</span><span>${d.category}</span></div>
      ${d.description ? html`<div class="kv"><span>Description</span><span>${d.description}</span></div>` : ''}
      <div class="kv"><span>Network</span><span>${wallet.network}</span></div>
      ${pv.fee != null ? html`<div class="kv"><span>Network fee</span><span>up to ${num(pv.fee)} ${pv.fee_asset} <span class="muted">· paid by wallet</span></span></div>` : ''}
      <div class="kv"><span>Total</span><span><b>${amt(d.amount)} ${wallet.asset}</b></span></div>
      ${d.file ? html`<div class="kv"><span>Invoice</span><span>${d.file.name}</span></div>` : ''}
    </div>
    ${pv.problem ? html`<div class="notice error"><b>Payment can't be sent</b>${pv.problem}</div>` : ''}
    ${!pv.problem && pv.new_address ? html`<div class="notice"><b>New destination</b>This address has never been used before. Approval will be required.</div>` : ''}
    ${!pv.problem && needs ? html`<div class="notice"><b>Needs ${slotsText(pv.approval.slots)} approval</b>${pv.approval.rule}. You'll get a message when it's decided.</div>` : ''}
    ${!pv.problem && pm ? html`<div class="notice info"><b>Payment request</b>Finance reviews and sends it.</div>` : ''}
    ${!pv.problem && pv.project_available != null && +d.amount > pv.project_available ? html`<div class="notice info">${project.name} has ${amt(pv.project_available)} ${wallet.asset} available.</div>` : ''}`,
  html`<button class="btn primary" id="confirm" ${pv.problem ? 'disabled' : ''}>${pm ? 'Send request' : 'Confirm payment'}</button>`);
  const btn = $('#confirm');
  btn.onclick = () => act(btn, async () => {
    const r = await api('/payments', { method: 'POST', body });
    if (d.file) await upload(r.id, d.file).catch(e => toast(`Payment created, but the invoice didn't attach: ${e.message}`));
    haptic('success');
    app.draft = null;
    go(`/pay/done/${r.id}`, { replace: true });
  });
}

async function payDone(id) {
  const alive = live();
  const p = await api('/payments/' + id);
  if (!alive()) return;
  const text = {
    DRAFT: 'Request sent to finance.',
    PENDING_APPROVAL: `Waiting for ${slotsText(p.approval.slots)} approval.`,
    FAILED: p.error,
  }[p.status] || 'Approved. Sending to the network…';
  view(html`<div class="flow">
    <div class="summary-to" style="margin:auto 0;padding-top:48px">
      <div style="display:inline-grid;place-items:center;width:64px;height:64px;border-radius:50%;background:${p.status === 'FAILED' ? 'var(--red-soft)' : 'var(--green-soft)'};color:${p.status === 'FAILED' ? 'var(--red)' : 'var(--green)'}">${icon('check', 32)}</div>
      <div style="font-size:20px;font-weight:650;margin-top:16px">${p.status === 'DRAFT' ? 'Request submitted' : 'Payment submitted'}</div>
      <div class="huge" style="margin-top:14px">${amt(p.amount)} ${p.asset}</div>
      <div class="name" style="margin-top:6px">${p.counterparty}</div>
      <p class="muted" style="margin-top:14px">${text}</p>
    </div>
    <div class="cta"><a class="btn primary" href="#/" id="done">Done</a><a class="btn" href="#/payments/${p.id}">Open payment</a></div>
  </div>`, { flow: true });
}

// ---------------------------------------------------------------- projects & budgets

const progress = b => {
  const pct = b.allocated > 0 ? Math.round((b.spent + b.reserved) / b.allocated * 100) : 0;
  return html`<div class="progress ${pct > 100 ? 'over' : ''}"><i style="width:${Math.min(pct, 100)}%"></i></div><div class="label" style="margin-top:6px">${pct}% used</div>`;
};
const budgetStats = b => html`<div class="stats">
  <div><span class="label">Budget</span><b>${amt(b.allocated)}</b></div>
  <div><span class="label">Spent</span><b>${amt(b.spent)}</b></div>
  <div><span class="label">Remaining</span><b class="${b.remaining < 0 ? 'down' : ''}">${amt(b.remaining)}</b></div></div>`;

async function projectsList() {
  const alive = live();
  const right = is('owner', 'cfo') ? html`<a class="btn primary small" href="#/projects/new">${icon('plus', 18)}New</a>` : '';
  view(html`${topbar('Projects', right)}${skelRows(4)}`);
  const rows = await api('/projects');
  if (!alive()) return;
  const real = rows.filter(p => p.id), unalloc = rows.find(p => !p.id);
  view(html`${topbar('Projects', right)}
    ${real.length ? real.map(p => html`<a class="card" href="#/projects/${p.id}" style="display:block">
      <div style="display:flex;justify-content:space-between;align-items:baseline"><b style="font-size:17px">${p.name}</b><span class="${p.available < 0 ? 'down' : 'muted'}">${amt(p.available)} USDT available</span></div>
      ${p.budget ? html`${budgetStats(p.budget)}${progress(p.budget)}` : html`<div class="muted" style="margin-top:8px">No budget for ${monthName(month())}</div>`}
    </a>`) : empty('No projects yet', 'Projects split money and budgets between company directions.', is('owner', 'cfo') && ['Create project', '/projects/new'])}
    ${unalloc ? html`<div class="card"><div class="kv" style="padding:0"><span>Unallocated funds</span><span>${amt(unalloc.available)} USDT</span></div>
      ${is('owner', 'cfo') ? html`<a class="btn small" style="margin-top:12px" href="#/transfer">${icon('transfer', 18)}Allocate to project</a>` : ''}</div>` : ''}`);
}

async function projectDetail(id) {
  const alive = live();
  view(html`${skelHero()}${skelRows(4)}`);
  const p = await api('/projects/' + id);
  if (!alive()) return;
  const max = Math.max(...p.categories.map(c => c.amount), 1);
  const admin = is('owner', 'cfo');
  view(html`
    ${topbar(p.name, admin ? html`<a class="btn small" href="#/projects/${p.id}/edit">Edit</a>` : '')}
    <section class="hero" style="padding-top:0"><div class="label">Available</div><div class="big">${amt(p.available)} USDT</div></section>
    ${canPay() ? html`<div class="btn-row"><a class="btn primary" href="#/pay" id="payHere">${icon('plus')}${is('project_manager') ? 'Request money' : 'New payment'}</a>
      ${admin ? html`<a class="btn" href="#/transfer?to=${p.id}">${icon('transfer')}Transfer</a>` : ''}</div>` : ''}
    <h2>Budget · ${monthName(p.period)}</h2>
    <div class="card">${p.budget ? html`${budgetStats(p.budget)}${progress(p.budget)}` : html`<div class="muted">No budget set for this month.</div>`}
      ${admin ? html`<div class="btn-row" style="margin-top:14px"><input class="input" id="alloc" inputmode="decimal" placeholder="Monthly budget, USDT" value="${p.budget ? p.budget.allocated : ''}"><button class="btn small" id="saveBudget" style="min-height:50px">Save</button></div>${formError()}` : ''}
    </div>
    ${p.categories.length ? html`<h2>Categories</h2><div class="card">${p.categories.map(c => html`<div style="padding:6px 0">
      <div style="display:flex;justify-content:space-between"><span>${c.category}</span><b>${amt(c.amount)}</b></div>
      <div class="bar"><i style="width:${c.amount / max * 100}%"></i></div></div>`)}</div>` : ''}
    <h2>Transactions</h2>
    ${p.payments.length ? html`<div class="list">${p.payments.map(paymentRow)}</div>` : html`<div class="card muted">No payments yet.</div>`}
    <h2>Members</h2>
    <div class="card"><div class="kv" style="padding:0"><span>Manager</span><span>${p.manager || 'Not assigned'}</span></div></div>`);
  const pay = $('#payHere');
  if (pay) pay.onclick = e => { e.preventDefault(); app.draft = { wallet_id: defaultWallet()?.id, project_id: p.id }; go('/pay'); };
  const save = $('#saveBudget');
  if (save) save.onclick = () => act(save, async () => {
    await api('/budgets', { method: 'PUT', body: { project_id: p.id, period: p.period, allocated: $('#alloc').value.replace(',', '.') || '0' } });
    toast('Budget saved'); render();
  });
}

async function projectForm(id) {
  const alive = live();
  const p = id ? await api('/projects/' + id) : { name: '', code: '', manager_id: null };
  if (!alive()) return;
  const managers = app.boot.users;
  view(html`${topbar(id ? 'Edit project' : 'New project')}
    <label class="field"><span>Name</span><input class="input" id="name" value="${p.name}" placeholder="MineJust"></label>
    <label class="field"><span>Code</span><input class="input" id="code" value="${p.code}" placeholder="MJ" style="text-transform:uppercase"></label>
    <label class="field"><span>Project manager</span><select class="input" id="manager"><option value="">Not assigned</option>
      ${managers.map(u => html`<option value="${u.id}" ${u.id === p.manager_id ? 'selected' : ''}>${u.name} · ${ROLE[u.role]}</option>`)}</select></label>
    ${id ? html`<label class="field"><span>Status</span><select class="input" id="status"><option value="active">Active</option><option value="archived">Archived</option></select></label>` : ''}
    ${formError()}
    <button class="btn primary" style="margin-top:20px" id="save">${id ? 'Save' : 'Create project'}</button>`);
  const btn = $('#save');
  btn.onclick = () => act(btn, async () => {
    const body = { name: $('#name').value.trim(), code: $('#code').value.trim(), manager_id: +$('#manager').value || null, status: $('#status')?.value || 'active' };
    const r = await api(id ? `/projects/${id}` : '/projects', { method: id ? 'PUT' : 'POST', body });
    await loadBoot();
    go(`/projects/${id || r.id}`, { replace: true });
  });
}

async function budgetsScreen() {
  const alive = live();
  view(html`${topbar('Budgets')}${skelRows(4)}`);
  const rows = (await api('/projects')).filter(p => p.id);
  if (!alive()) return;
  const admin = is('owner', 'cfo');
  const total = rows.reduce((t, p) => ({ a: t.a + +(p.budget?.allocated || 0), s: t.s + +(p.budget?.spent || 0) }), { a: 0, s: 0 });
  view(html`${topbar('Budgets')}
    <div class="card"><div class="label">${monthName(month())}</div>
      <div class="stats"><div><span class="label">Budget</span><b>${amt(total.a)}</b></div><div><span class="label">Spent</span><b>${amt(total.s)}</b></div><div><span class="label">Remaining</span><b>${amt(total.a - total.s)}</b></div></div></div>
    <h2>Projects</h2>
    <div class="list">${rows.map(p => html`<div class="row" style="flex-wrap:wrap">
      <a class="main" href="#/projects/${p.id}"><div class="title">${p.name}</div><div class="meta">${p.budget ? `Spent ${amt(p.budget.spent)} · Remaining ${amt(p.budget.remaining)}` : 'No budget'}</div></a>
      ${admin ? html`<input class="input" data-p="${p.id}" inputmode="decimal" style="width:120px;min-height:40px;text-align:right" value="${p.budget ? p.budget.allocated : ''}" placeholder="0">` : html`<b>${amt(p.budget?.allocated || 0)}</b>`}
    </div>`)}</div>
    ${admin ? html`${formError()}<button class="btn primary" style="margin-top:16px" id="save">Save budgets</button>` : ''}`);
  const btn = $('#save');
  if (btn) btn.onclick = () => act(btn, async () => {
    for (const input of $$('[data-p]')) {
      const p = rows.find(r => r.id === +input.dataset.p);
      const v = input.value.replace(',', '.').trim();
      if (v === '' || +v === +(p.budget?.allocated ?? -1)) continue;
      await api('/budgets', { method: 'PUT', body: { project_id: p.id, period: month(), allocated: v } });
    }
    toast('Budgets saved'); render();
  });
}

// ---------------------------------------------------------------- counterparties

async function counterpartiesList(query) {
  const alive = live();
  const right = is(...STAFF) ? html`<a class="btn primary small" href="#/pay/new?standalone=1">${icon('plus', 18)}New</a>` : '';
  const head = html`${topbar('Counterparties', right)}<div class="search" style="margin-bottom:12px">${icon('search', 18)}<input class="input" id="q" type="search" placeholder="Search by name or address" value="${query.q || ''}"></div>`;
  view(html`${head}${skelRows(5)}`);
  const rows = await api('/counterparties' + (query.q ? '?q=' + encodeURIComponent(query.q) : ''));
  if (!alive()) return;
  view(html`${head}${rows.length ? html`<div class="list">${rows.map(c => html`<a class="row" href="#/counterparties/${c.id}">
      <div class="main"><div class="title">${c.name}</div><div class="meta">${[c.default_category, `${c.payments} transactions`].filter(Boolean).join(' · ')}</div></div>
      <div class="side-val"><div class="amt">${amt(c.total)} USDT</div><div class="meta">total</div></div></a>`)}</div>`
    : empty(query.q ? 'Nothing found' : 'No counterparties yet', query.q ? 'Try another name or address.' : 'Recipients you pay will be listed here.')}`);
  $('#q').onchange = e => go('/counterparties' + (e.target.value.trim() ? '?q=' + encodeURIComponent(e.target.value.trim()) : ''), { replace: true });
}

async function counterpartyDetail(id) {
  const alive = live();
  view(html`${skelHero()}${skelRows(3)}`);
  const c = await api('/counterparties/' + id);
  if (!alive()) return;
  view(html`
    ${topbar(c.name)}
    <div style="margin:-6px 4px 16px">${c.verified ? html`<span class="pill green">Verified</span>` : html`<span class="pill amber">Not verified</span>`} <span class="muted">${c.default_category || ''}</span></div>
    ${is(...STAFF, 'project_manager') ? html`<a class="btn primary" href="#/pay?cp=${c.id}">${icon('send')}Pay ${c.name}</a>` : ''}
    <h2>Wallets</h2>
    <div class="list">${c.addresses.map(a => html`<button class="row" data-copy="${a.address}">
      <div class="main"><div class="title">${a.asset} / ${a.network}${a.label ? ` · ${a.label}` : ''}</div><div class="meta mono">${a.address}</div></div>
      ${a.verified ? html`<span class="pill green">Verified</span>` : html`<span class="pill amber">New</span>`}</button>`)}
      ${is(...STAFF) ? html`<a class="row" href="#/pay/new?standalone=1&cp=${c.id}&name=${encodeURIComponent(c.name)}" style="color:var(--coral);font-weight:600">${icon('plus', 20)}<span class="main">Add address</span></a>` : ''}
    </div>
    <h2>Statistics</h2>
    <div class="card">
      <div class="kv"><span>Total paid</span><span>${amt(c.total || 0)} USDT</span></div>
      <div class="kv"><span>Transactions</span><span>${c.payments || 0}</span></div>
      <div class="kv"><span>Last payment</span><span>${c.last_payment ? when(c.last_payment, false) : '—'}</span></div>
    </div>
    ${c.notes ? html`<h2>Notes</h2><div class="card">${c.notes}</div>` : ''}
    <h2>History</h2>
    ${c.recent.length ? html`<div class="list">${c.recent.map(paymentRow)}</div>` : html`<div class="card muted">No payments yet.</div>`}`);
  $$('[data-copy]').forEach(b => b.onclick = () => copy(b.dataset.copy));
}

// ---------------------------------------------------------------- wallets & transfers

const WALLET_TYPE = { TREASURY: 'Cold wallet · Treasury', OPERATIONS: 'Hot wallet · Operations', PROJECT: 'Project wallet', OTHER: 'Other' };

async function walletsList() {
  const alive = live();
  const right = is('owner') ? html`<a class="btn primary small" href="#/wallets/new">${icon('plus', 18)}Add</a>` : '';
  view(html`${topbar('Wallets', right)}${skelRows(3)}`);
  const rows = await api('/wallets');
  if (!alive()) return;
  view(html`${topbar('Wallets', right)}
    ${rows.length ? html`<div class="list">${rows.map(w => html`<a class="row" href="#/wallets/${w.id}">
      <div class="main"><div class="title">${w.name}</div><div class="meta">${WALLET_TYPE[w.type]} · ${w.network}</div></div>
      <div class="side-val"><div class="amt">${num(w.balance)} ${w.asset}</div><div class="meta">${num(w.native_balance)} TRX</div></div></a>`)}</div>`
    : empty('No wallets yet', 'Add the Operations wallet the signer holds, and Treasury to watch reserves.', is('owner') && ['Add wallet', '/wallets/new'])}`);
}

async function walletDetail(id) {
  const alive = live();
  view(html`${skelHero()}${skelRows(3)}`);
  const w = await api('/wallets/' + id);
  if (!alive()) return;
  view(html`
    ${topbar(w.name)}
    <section class="hero" style="padding-top:0"><div class="big">${num(w.balance)} ${w.asset}</div>
      <div class="muted" style="margin-top:4px">${num(w.available)} available · ${num(w.native_balance)} ${w.native} for fees</div></section>
    <div class="actions">
      ${is(...STAFF) && w.type !== 'TREASURY' ? html`<a href="#/pay?wallet=${w.id}">${icon('send')}Send</a>` : ''}
      <a href="#/wallets/${w.id}/receive">${icon('receive')}Receive</a>
      ${is('owner', 'cfo') ? html`<a href="#/transfer?wallet=${w.id}">${icon('transfer')}Transfer</a>` : ''}
    </div>
    <h2>Information</h2>
    <div class="card">
      <div class="kv"><span>Network</span><span>${w.network}</span></div>
      <div class="kv"><span>Address</span><button class="mono" style="background:none;border:0;padding:0;cursor:pointer" id="addr">${short(w.address)} ${icon('copy', 14)}</button></div>
      <div class="kv"><span>Type</span><span>${WALLET_TYPE[w.type]}</span></div>
      ${Math.abs(w.ledger_balance - w.balance) > 0.000001 ? html`<div class="kv"><span>Ledger balance</span><span class="down">${num(w.ledger_balance)} ${w.asset}</span></div>` : ''}
    </div>
    <h2>Internal balances</h2>
    ${w.internal.length ? html`<div class="card">${w.internal.map(i => html`<div class="kv"><span>${i.project}</span><span>${num(i.amount)} ${w.asset}</span></div>`)}</div>`
      : html`<div class="card muted">Nothing booked to this wallet yet.</div>`}`);
  $('#addr').onclick = () => copy(w.address);
}

async function receiveScreen(id) {
  const alive = live();
  view(html`${skelHero()}`);
  const w = await api('/wallets/' + id);
  if (!alive()) return;
  view(html`${topbar(w.name)}
    <div class="card center">
      <div class="label">${w.asset} / ${w.network}</div>
      <div class="qr">${raw(w.qr)}</div>
      <div class="mono" style="padding:0 8px">${w.address}</div>
    </div>
    <div class="notice big">Send only ${w.asset} using ${w.network} network.</div>
    <div class="btn-row"><button class="btn primary" id="copyAddr">${icon('copy', 20)}Copy address</button><button class="btn" id="share">${icon('share', 20)}Share</button></div>`);
  $('#copyAddr').onclick = () => copy(w.address);
  $('#share').onclick = () => {
    const text = `${w.asset} (${w.network}) address for the company:\n${w.address}`;
    if (navigator.share) navigator.share({ text }).catch(() => {});
    else if (tg?.openTelegramLink) tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(w.address)}&text=${encodeURIComponent(`Send only ${w.asset} using ${w.network} network`)}`);
    else copy(w.address);
  };
}

function walletForm() {
  view(html`${topbar('Add wallet')}
    <p class="muted" style="margin:0 4px">Ledgerline watches the balance and books deposits. Only the Operations wallet key lives in the signer; Treasury stays cold.</p>
    <label class="field"><span>Name</span><input class="input" id="name" placeholder="Operations"></label>
    <label class="field"><span>Type</span><select class="input" id="type">${Object.entries(WALLET_TYPE).map(([k, l]) => html`<option value="${k}" ${k === 'OPERATIONS' ? 'selected' : ''}>${l}</option>`)}</select></label>
    <label class="field"><span>Address</span><input class="input mono" id="address" placeholder="T…" spellcheck="false"></label>
    <div class="btn-row"><label class="field"><span>Network</span><select class="input"><option>TRON</option></select></label><label class="field"><span>Asset</span><select class="input"><option>USDT</option></select></label></div>
    ${formError()}
    <button class="btn primary" style="margin-top:20px" id="save">Add wallet</button>`);
  const btn = $('#save');
  btn.onclick = () => act(btn, async () => {
    const r = await api('/wallets', { method: 'POST', body: { name: $('#name').value.trim(), type: $('#type').value, address: $('#address').value.trim() } });
    await loadBoot();
    go(`/wallets/${r.id}`, { replace: true });
  });
}

async function transferForm(query) {
  const alive = live();
  view(html`${topbar('Transfer between projects')}${skelRows(3)}`);
  const balances = await api('/projects');
  if (!alive()) return;
  const wallets = app.boot.wallets;
  if (!wallets.length) return view(empty('No wallets yet', 'Add a wallet first.', is('owner') && ['Add wallet', '/wallets/new']));
  const opt = (sel) => balances.map(p => html`<option value="${p.id ?? ''}" ${String(p.id ?? '') === String(sel ?? '') ? 'selected' : ''}>${p.name} · ${amt(p.available)} available</option>`);
  view(html`<div class="flow">${topbar('Transfer between projects')}
    <p class="muted" style="margin:0 4px">Moves money between internal balances. Nothing is sent on-chain.</p>
    <label class="field"><span>Wallet</span><select class="input" id="wallet">${wallets.map(w => html`<option value="${w.id}" ${w.id === (+query.wallet || defaultWallet()?.id) ? 'selected' : ''}>${w.name}</option>`)}</select></label>
    <label class="field"><span>From</span><select class="input" id="from">${opt(query.from ?? '')}</select></label>
    <label class="field"><span>To</span><select class="input" id="to">${opt(query.to ?? balances.find(p => p.id)?.id)}</select></label>
    <label class="field"><span>Amount, USDT</span><input class="input" id="amount" inputmode="decimal" placeholder="0"></label>
    <label class="field"><span>Description</span><input class="input" id="description" placeholder="September allocation"></label>
    <div class="cta">${formError()}<button class="btn primary" id="save">Transfer</button></div></div>`, { flow: true });
  const btn = $('#save');
  btn.onclick = () => act(btn, async () => {
    const id = v => v === '' ? null : +v;
    await api('/transfers', { method: 'POST', body: { wallet_id: +$('#wallet').value, from_project_id: id($('#from').value),
      to_project_id: id($('#to').value), amount: $('#amount').value.replace(',', '.'), description: $('#description').value.trim() } });
    haptic('success'); toast('Transferred');
    back();
  });
}

// ---------------------------------------------------------------- analytics & reports

function cashChart(series) {
  if (!series.length) return html`<div class="muted center" style="padding:32px 0">No income or expenses in this period.</div>`;
  const W = 640, H = 200, P = 8, B = 22;
  const vals = series.flatMap(r => [+r.income, +r.expenses, +r.net]);
  const max = Math.max(...vals, 1), min = Math.min(...vals, 0);
  const y = v => P + (max - v) / (max - min) * (H - P - B);
  const step = (W - 2 * P) / series.length, bw = Math.max(Math.min(step / 3, 18), 2);
  const bars = series.map((r, i) => {
    const x = P + i * step + step / 2;
    return `<rect x="${x - bw - 1}" y="${y(+r.income)}" width="${bw}" height="${Math.max(y(0) - y(+r.income), 0)}" rx="2" fill="#2f9e6b"/>` +
      `<rect x="${x + 1}" y="${y(+r.expenses)}" width="${bw}" height="${Math.max(y(0) - y(+r.expenses), 0)}" rx="2" fill="#b9b7b2"/>`;
  }).join('');
  const line = series.map((r, i) => `${P + i * step + step / 2},${y(+r.net)}`).join(' ');
  const label = t => { const d = new Date(t); return `${d.getDate()} ${MONTHS[d.getMonth()]}`; };
  return raw(`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Cash flow chart">
    <line x1="${P}" x2="${W - P}" y1="${y(0)}" y2="${y(0)}" stroke="#e1dfda"/>
    ${bars}<polyline points="${line}" fill="none" stroke="#1c1c1e" stroke-width="2" stroke-linejoin="round"/>
    <text x="${P}" y="${H - 4}" font-size="12" fill="#8a8a8f">${label(series[0].t)}</text>
    <text x="${W - P}" y="${H - 4}" font-size="12" fill="#8a8a8f" text-anchor="end">${label(series.at(-1).t)}</text></svg>`);
}

async function analyticsScreen(query) {
  const alive = live();
  const period = query.period || '30d';
  const seg = html`<div class="seg">${[['7d', '7D'], ['30d', '30D'], ['3m', '3M'], ['6m', '6M'], ['1y', '1Y']].map(([k, l]) => html`<button data-p="${k}" class="${k === period ? 'on' : ''}">${l}</button>`)}</div>`;
  view(html`${topbar('Analytics')}${seg}${skelRows(3)}`);
  const a = await api('/analytics?period=' + period);
  if (!alive()) return;
  const max = Math.max(...a.categories.map(c => c.amount), 1);
  view(html`${topbar('Analytics')}${seg}
    <div class="hscroll">
      <div class="card mini"><div class="label">Income</div><div class="val">${usd(a.income)}</div></div>
      <div class="card mini"><div class="label">Expenses</div><div class="val">${usd(a.expenses)}</div></div>
      <div class="card mini"><div class="label">Net cash flow</div><div class="val ${a.net >= 0 ? 'up' : 'down'}">${a.net >= 0 ? '+' : '−'}${usd(Math.abs(a.net))}</div></div>
      <div class="card mini"><div class="label">Treasury</div><div class="val">${usd(a.treasury)}</div></div>
      <div class="card mini"><div class="label">Operational balance</div><div class="val">${usd(a.operations)}</div></div>
    </div>
    <h2>Cash flow</h2>
    <div class="card chart">${cashChart(a.series)}
      <div class="legend"><span><i style="background:#2f9e6b"></i>Income</span><span><i style="background:#b9b7b2"></i>Expenses</span><span><i style="background:#1c1c1e;height:2px"></i>Net</span></div></div>
    <h2>Expenses breakdown</h2>
    ${a.categories.length ? html`<div class="card">${a.categories.map(c => html`<div style="padding:7px 0">
      <div style="display:flex;justify-content:space-between"><span>${c.category}</span><b>${usd(c.amount)}</b></div>
      <div class="bar"><i style="width:${c.amount / max * 100}%"></i></div></div>`)}</div>` : html`<div class="card muted">No expenses in this period.</div>`}
    ${is(...READERS) ? html`<a class="btn" style="margin-top:16px" href="#/reports">${icon('file', 20)}Reports</a>` : ''}`);
  $$('.seg button').forEach(b => b.onclick = () => go('/analytics?period=' + b.dataset.p, { replace: true }));
}

function reportsScreen() {
  view(html`${topbar('Reports')}
    <div class="card"><b>Monthly Finance Report</b><p class="muted" style="margin:4px 0 0">Every ledger movement of the month with project, category, counterparty and tx hash. CSV, opens in Excel and Google Sheets.</p></div>
    <label class="field"><span>Month</span><input class="input" type="month" id="month" value="${month()}" max="${month()}"></label>
    <label class="field"><span>Project</span><select class="input" id="project"><option value="">All projects</option>${app.boot.projects.map(p => html`<option value="${p.id}">${p.name}</option>`)}</select></label>
    ${formError()}
    <button class="btn primary" style="margin-top:20px" id="send">${icon('send', 20)}Send CSV to chat</button>
    <p class="muted center" style="font-size:13px">The file arrives in your chat with the Ledgerline bot.</p>`);
  const btn = $('#send');
  btn.onclick = () => act(btn, async () => {
    const r = await api('/reports/monthly', { method: 'POST', body: { month: $('#month').value, project_id: +$('#project').value || null } });
    haptic('success'); toast(`Report sent · ${r.rows} lines`);
  });
}

async function auditScreen() {
  const alive = live();
  view(html`${topbar('Audit log')}${skelRows(6)}`);
  let rows = await api('/audit');
  if (!alive()) return;
  const fmt = o => o ? Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(', ') : '';
  const draw = () => {
    view(html`${topbar('Audit log')}
      <p class="muted" style="margin:0 4px 12px">Read-only. Every change in Ledgerline is recorded here.</p>
      <div class="list">${rows.map(a => html`<details class="row" style="display:block">
        <summary style="list-style:none;cursor:pointer"><div style="display:flex;justify-content:space-between;gap:12px"><b>${a.action.replaceAll('_', ' ').toLowerCase().replace(/^./, c => c.toUpperCase())}</b><span class="muted" style="font-size:13px;white-space:nowrap">${when(a.at)}</span></div>
          <div class="meta">${a.user || 'System'} · ${a.object}</div></summary>
        <div class="meta" style="white-space:normal;margin-top:8px">${a.old ? html`<div>Before: ${fmt(a.old)}</div>` : ''}${a.new ? html`<div>After: ${fmt(a.new)}</div>` : ''}<div>Telegram ${a.telegram_id || '—'} · IP ${a.ip || '—'}</div></div>
      </details>`)}</div>
      ${rows.length && rows.length % 100 === 0 ? html`<button class="btn ghost" id="more">Load more</button>` : ''}`);
    const more = $('#more');
    if (more) more.onclick = () => act(more, async () => { rows = rows.concat(await api('/audit?before=' + rows.at(-1).id)); draw(); });
  };
  draw();
}

// ---------------------------------------------------------------- more & settings

function moreScreen() {
  const me = app.boot.me;
  const link = (href, ic, label) => allowed('/' + href.split('/')[1]) ? html`<a class="row" href="#${href}">${icon(ic, 20)}<span class="main">${label}</span>${icon('chevron', 18)}</a>` : '';
  view(html`${topbar('More')}
    <div class="card"><div style="font-size:17px;font-weight:650">${me.name}</div><div class="muted">${ROLE[me.role]} · Telegram ${me.telegram_id}</div></div>
    <h2>Finance</h2>
    <div class="list">
      ${is(...STAFF) ? link('/approvals', 'approve', 'Approvals') : ''}
      ${link('/wallets', 'wallet', 'Wallets')}
      ${link('/counterparties', 'users', 'Counterparties')}
      ${link('/budgets', 'tag', 'Budgets')}
      ${link('/reports', 'file', 'Reports')}
      ${link('/audit', 'shield', 'Audit log')}
    </div>
    ${allowed('/settings') ? html`<h2>Admin</h2><div class="list">${link('/settings', 'settings', 'Settings')}</div>` : ''}`);
}

function settingsScreen() {
  const link = (href, ic, label, sub) => html`<a class="row" href="#${href}">${icon(ic, 20)}<div class="main"><div class="title">${label}</div><div class="meta">${sub}</div></div>${icon('chevron', 18)}</a>`;
  view(html`${topbar('Settings')}<div class="list">
    ${is('owner') ? link('/settings/users', 'users', 'Users & roles', 'Who has access and what they can do') : ''}
    ${link('/settings/policy', 'shield', 'Payment policy', 'Limits and approval rules')}
    ${link('/settings/categories', 'tag', 'Categories', 'Expense categories')}
    ${is('owner') ? link('/wallets/new', 'wallet', 'Add wallet', 'Watch a new blockchain wallet') : ''}
  </div>`);
}

async function usersScreen() {
  const alive = live();
  view(html`${topbar('Users & roles')}${skelRows(4)}`);
  const users = await api('/users');
  if (!alive()) return;
  const roleSelect = (id, cur) => html`<select class="input" data-role="${id}" style="width:auto;min-height:40px">${Object.entries(ROLE).map(([k, l]) => html`<option value="${k}" ${k === cur ? 'selected' : ''}>${l}</option>`)}</select>`;
  view(html`${topbar('Users & roles')}
    <div class="list">${users.map(u => html`<div class="row">
      <div class="main"><div class="title">${u.name}${u.status !== 'active' ? html` <span class="pill gray">Disabled</span>` : ''}</div><div class="meta">${u.username ? '@' + u.username + ' · ' : ''}${u.telegram_id}</div></div>
      ${u.id === app.boot.me.id ? html`<span class="muted">${ROLE[u.role]}</span>` : html`${roleSelect(u.id, u.role)}
        <button class="btn small" data-toggle="${u.id}">${u.status === 'active' ? 'Disable' : 'Enable'}</button>`}</div>`)}</div>
    <h2>Add user</h2>
    <div class="card">
      <p class="muted" style="margin:0">They open the bot, it shows their Telegram ID; paste it here.</p>
      <label class="field"><span>Telegram ID</span><input class="input" id="tid" inputmode="numeric" placeholder="123456789"></label>
      <label class="field"><span>Name</span><input class="input" id="uname" placeholder="Puff"></label>
      <label class="field"><span>Role</span><select class="input" id="urole">${Object.entries(ROLE).map(([k, l]) => html`<option value="${k}" ${k === 'accountant' ? 'selected' : ''}>${l}</option>`)}</select></label>
      ${formError()}
      <button class="btn primary" style="margin-top:16px" id="add">Add user</button>
    </div>`);
  const put = (u, patch) => api(`/users/${u.id}`, { method: 'PUT', body: { telegram_id: u.telegram_id, display_name: u.name, role: u.role, status: u.status, ...patch } });
  $$('[data-role]').forEach(s => s.onchange = async () => {
    const u = users.find(x => x.id === +s.dataset.role);
    try { await put(u, { role: s.value }); toast('Role updated'); await loadBoot(); render(); } catch (e) { toast(e.message); s.value = u.role; }
  });
  $$('[data-toggle]').forEach(b => b.onclick = () => act(b, async () => {
    const u = users.find(x => x.id === +b.dataset.toggle);
    await put(u, { status: u.status === 'active' ? 'disabled' : 'active' });
    render();
  }));
  const add = $('#add');
  add.onclick = () => act(add, async () => {
    await api('/users', { method: 'POST', body: { telegram_id: +$('#tid').value, display_name: $('#uname').value.trim(), role: $('#urole').value } });
    toast('User added'); await loadBoot(); render();
  });
}

async function policyScreen() {
  const alive = live();
  view(html`${topbar('Payment policy')}${skelRows(4)}`);
  const p = await api('/policy');
  if (!alive()) return;
  const lim = v => v == null ? 'no limit' : `${amt(v)} USDT`;
  const cond = c => Object.entries(c).map(([k, v]) => ({
    amount_over: `over ${amt(v)} USDT`, wallet_type: `from ${String(v).toLowerCase()} wallet`, new_address: v ? 'to a new address' : 'to a known address',
    over_limit: v ? "over the initiator's limit" : 'within limits', over_budget: v ? "over the project's balance" : 'within project balance', role: `by ${ROLE[v] || v}`,
  }[k] || `${k}=${v}`)).join(', ');
  view(html`${topbar('Payment policy')}
    <p class="muted" style="margin:0 4px">Every payment goes through these rules. Rules are checked top to bottom; the first match decides who approves. The signer re-checks them before signing.</p>
    <h2>Limits without approval</h2>
    <div class="card">${Object.entries(p.limits).map(([r, l]) => html`<div class="kv"><span>${ROLE[r]}</span><span>${lim(l.transaction)} per payment · ${lim(l.daily)} per 24h</span></div>`)}</div>
    <h2>Approval rules</h2>
    <div class="list">${p.rules.map((r, i) => html`<div class="row"><span class="muted">${i + 1}</span><div class="main"><div class="title">${r.name}</div><div class="meta" style="white-space:normal">${cond(r.if)} → ${slotsText(r.approvers)}</div></div></div>`)}
      <div class="row"><span class="muted">·</span><div class="main"><div class="title">Everything else</div><div class="meta">Approved automatically</div></div></div></div>
    <h2>Wallet access</h2>
    <div class="card">${Object.entries(p.wallet_access).map(([r, t]) => html`<div class="kv"><span>${ROLE[r]}</span><span>${t.join(', ').toLowerCase() || 'none'}</span></div>`)}</div>
    ${is('owner') ? html`<h2>Edit (JSON)</h2>
      <textarea class="input mono" id="json" style="min-height:360px" spellcheck="false">${JSON.stringify(p, null, 2)}</textarea>
      ${formError()}
      <button class="btn primary" style="margin-top:12px" id="save">Save policy</button>` : ''}`);
  const btn = $('#save');
  if (btn) btn.onclick = () => act(btn, async () => {
    let data;
    try { data = JSON.parse($('#json').value); } catch (e) { throw new ApiError(`Policy not saved: invalid JSON (${e.message}).`); }
    await api('/policy', { method: 'PUT', body: data });
    haptic('success'); toast('Policy saved'); render();
  });
}

async function categoriesScreen() {
  const admin = is('owner', 'cfo');
  view(html`${topbar('Categories')}
    <div class="list">${app.boot.categories.map(c => html`<div class="row"><span class="main">${c}</span></div>`)}</div>
    ${admin ? html`<h2>Add category</h2><div class="btn-row"><input class="input" id="name" placeholder="Events"><button class="btn primary small" id="add" style="min-height:50px;flex:0 0 auto">Add</button></div>${formError()}` : ''}`);
  const add = $('#add');
  if (add) add.onclick = () => act(add, async () => {
    await api('/categories', { method: 'POST', body: { name: $('#name').value.trim() } });
    await loadBoot(); render();
  });
}

// ---------------------------------------------------------------- boot

async function loadBoot() { app.boot = await api('/bootstrap'); }

async function start() {
  if (tg) {
    tg.ready();
    tg.expand();
    tg.setHeaderColor?.('#f4f3f1');
    tg.setBackgroundColor?.('#f4f3f1');
    tg.disableVerticalSwipes?.();
    tg.BackButton?.onClick(back);
  }
  // Deep links from the bot come as ?r=/payments/12 (Telegram owns the URL hash on launch).
  const r = new URLSearchParams(location.search).get('r');
  if (r?.startsWith('/')) history.replaceState(null, '', location.pathname + '#' + r);
  else if (!location.hash.startsWith('#/')) history.replaceState(null, '', location.pathname + '#/');
  view(html`${skelHero()}${skelRows(5)}`);
  try {
    if (!tg?.initData) throw new ApiError('Open Ledgerline from the Telegram bot.');
    await loadBoot();
  } catch (e) {
    return view(html`<div class="empty" style="padding-top:30vh"><b>Ledgerline</b><p>${e.message}</p></div>`);
  }
  window.addEventListener('hashchange', render);
  render();
}

start();
