"use strict";
/* grzxk tg API — panel. Vanilla JS, no build step. */

const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const ico = (n, cls = "") => `<svg class="ico ${cls}"><use href="#i-${n}"/></svg>`;
const store = {
  get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch {} },
};
const fmtTime = (ts) => ts ? new Date(ts * 1000).toLocaleString("ru-RU", { dateStyle: "short", timeStyle: "short" }) : "—";
// The panel may live under a prefix (e.g. https://grzxk.ru/tgapi/): every request is relative to it.
const BASE = location.pathname.replace(/\/[^/]*$/, "");
const ORIGIN = location.origin + BASE;

const S = { auth: null, accounts: [], account: store.get("account", null), catalog: null, openapi: null, poll: null };

/* ---------------------------------------------------------------- api */
async function api(path, { method = "GET", body, form, headers = {}, raw = false } = {}) {
  const h = { ...headers };
  if (S.account && path.startsWith("/v1/")) h["X-Account-Id"] = S.account;
  let payload;
  if (form) payload = form;
  else if (body !== undefined) { h["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
  const res = await fetch(BASE + path, { method, headers: h, body: payload, credentials: "same-origin" });
  if (raw) return res;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) {
    if (res.status === 401 && !path.startsWith("/panel/api/auth")) { boot(); }
    const msg = data?.error?.message || data?.detail || res.statusText;
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status; err.data = data;
    throw err;
  }
  return data;
}

function toast(msg, bad = false) {
  const el = document.createElement("div");
  el.className = "toast" + (bad ? " bad" : "");
  el.innerHTML = `${ico(bad ? "x" : "check", "sm")}<span>${esc(msg)}</span>`;
  $("#toasts").appendChild(el);
  setTimeout(() => el.remove(), 3600);
}
const fail = (e) => toast(e.message || String(e), true);

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Скопировано"); }
  catch { const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select(); document.execCommand("copy"); t.remove(); toast("Скопировано"); }
}

function highlight(json) {
  return esc(json).replace(/(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|(-?\b\d+(?:\.\d+)?(?:e[+-]?\d+)?\b)/gi,
    (m, str, colon, bool, num) => str ? `<span class="${colon ? "k" : "s"}">${str}</span>${colon || ""}` : bool ? `<span class="b">${bool}</span>` : `<span class="n">${num}</span>`);
}
const pretty = (v) => highlight(typeof v === "string" ? v : JSON.stringify(v, null, 2));
const codebox = (code, lang = "") => `<div class="codebox"><pre class="code">${lang === "json" ? pretty(code) : esc(code)}</pre><button class="btn icon sm" data-copy="${esc(code)}" title="Копировать">${ico("copy", "sm")}</button></div>`;

document.addEventListener("click", (e) => {
  const c = e.target.closest("[data-copy]");
  if (c) { copy(c.dataset.copy); }
});

/* Mobile: tables collapse into cards; each cell gets its column title as a label. */
function labelTables(root) {
  $$("table.t", root).forEach((t) => {
    const heads = $$("tr:first-child th", t).map((th) => th.textContent.trim());
    if (!heads.length) { t.classList.add("plain"); return; }
    $$("tr", t).forEach((tr) => $$("td", tr).forEach((td, i) => { if (!td.dataset.l) td.dataset.l = heads[i] || ""; }));
  });
}
new MutationObserver((ms) => { for (const m of ms) for (const n of m.addedNodes) if (n.nodeType === 1 && (n.matches("table.t") || n.querySelector("table.t"))) labelTables(n.matches("table.t") ? n.parentNode : n); })
  .observe(document.documentElement, { childList: true, subtree: true });

/* ---------------------------------------------------------------- modal */
function modal(html, { drawer = false, onClose } = {}) {
  const ov = document.createElement("div");
  ov.className = "overlay" + (drawer ? " drawer-wrap" : "");
  ov.innerHTML = `<div class="${drawer ? "drawer" : "modal"}">${html}</div>`;
  const close = () => { ov.remove(); document.removeEventListener("keydown", onKey); onClose && onClose(); };
  const onKey = (e) => { if (e.key === "Escape") close(); };
  ov.addEventListener("mousedown", (e) => { if (e.target === ov) close(); });
  document.addEventListener("keydown", onKey);
  document.body.appendChild(ov);
  $$("[data-close]", ov).forEach((b) => b.addEventListener("click", close));
  return { el: ov.firstElementChild, close };
}

/* ---------------------------------------------------------------- boot / auth */
async function boot() {
  stopPoll();
  try { S.auth = await api("/panel/api/auth/state"); } catch (e) { $("#root").innerHTML = `<div class="auth"><div class="muted">Сервер недоступен: ${esc(e.message)}</div></div>`; return; }
  if (S.auth.setup_required) return renderSetup();
  if (!S.auth.user) return renderLogin();
  await loadAccounts();
  renderShell();
  route();
}

const authWrap = (inner) => `<div class="auth">${linesBg()}<div class="box"><div class="brand">${ico("logo")}<div><b>grzxk</b> <span>tg API</span></div></div>${inner}</div></div>`;
function linesBg() {
  let p = "";
  for (let i = 0; i < 14; i++) p += `<path d="M${-200 + i * 120} 900 C ${200 + i * 60} ${500 - i * 10}, ${600 + i * 40} ${700 - i * 30}, ${1700} ${-100 + i * 60}" />`;
  return `<svg class="lines" viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice" fill="none" stroke="#24262b" stroke-width="1">${p}</svg>`;
}

function renderSetup() {
  $("#root").innerHTML = authWrap(`<form class="card stack" id="f">
    <div><h2>Первый запуск</h2><div class="muted small">Создайте администратора панели.</div></div>
    <label class="f">Логин<input class="input" name="username" required minlength="3" autocomplete="username"></label>
    <label class="f">Пароль<input class="input" name="password" type="password" required minlength="8" autocomplete="new-password"></label>
    <div class="faint small" style="margin-top:4px">Telegram API · <a href="https://my.telegram.org/apps" target="_blank" rel="noopener">my.telegram.org</a> — можно указать позже или через env</div>
    <div class="row"><label class="f grow">api_id<input class="input" name="api_id" inputmode="numeric"></label><label class="f grow">api_hash<input class="input" name="api_hash"></label></div>
    <button class="btn primary">Создать и войти</button></form>`);
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      await api("/panel/api/auth/setup", { method: "POST", body: { username: f.username, password: f.password, api_id: f.api_id ? +f.api_id : null, api_hash: f.api_hash || null } });
      location.hash = "#/security"; boot();
    } catch (err) { fail(err); }
  };
}

function renderLogin(mode = "login") {
  const reg = mode === "register";
  $("#root").innerHTML = authWrap(`<form class="card stack" id="f">
    <div><h2>${reg ? "Регистрация" : "Вход"}</h2><div class="muted small">${reg ? "Новый аккаунт панели" : "Панель управления Telegram API"}</div></div>
    <label class="f">Логин<input class="input" name="username" required autocomplete="username" autofocus></label>
    <label class="f">Пароль<input class="input" name="password" type="password" required autocomplete="${reg ? "new-password" : "current-password"}"></label>
    <button class="btn primary">${reg ? "Создать аккаунт" : "Продолжить"}</button>
    ${S.auth.registration_open ? `<button type="button" class="btn ghost sm" id="sw">${reg ? "У меня есть аккаунт" : "Создать аккаунт"}</button>` : ""}
  </form>`);
  $("#sw") && ($("#sw").onclick = () => renderLogin(reg ? "login" : "register"));
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    const f = Object.fromEntries(new FormData(e.target));
    try {
      const r = await api(`/panel/api/auth/${reg ? "register" : "login"}`, { method: "POST", body: f });
      if (r.totp_required) return renderTotp(r.ticket);
      boot();
    } catch (err) { fail(err); }
  };
}

function renderTotp(ticket) {
  $("#root").innerHTML = authWrap(`<form class="card stack" id="f">
    <div class="row">${ico("shield", "lg")}<div><h2>Двухфакторная проверка</h2><div class="muted small">Код из приложения-аутентификатора</div></div></div>
    <input class="input otp" name="code" inputmode="numeric" autocomplete="one-time-code" maxlength="6" required autofocus placeholder="······">
    <button class="btn primary">Войти</button>
    <button type="button" class="btn ghost sm" id="back">Назад</button></form>`);
  const inp = $("[name=code]");
  inp.addEventListener("input", () => { inp.value = inp.value.replace(/\D/g, ""); if (inp.value.length === 6) $("#f").requestSubmit(); });
  $("#back").onclick = () => renderLogin();
  $("#f").onsubmit = async (e) => {
    e.preventDefault();
    try { await api("/panel/api/auth/totp", { method: "POST", body: { ticket, code: inp.value } }); boot(); }
    catch (err) { fail(err); inp.value = ""; inp.focus(); if (err.status === 401 && /expired|start/.test(err.message)) renderLogin(); }
  };
}

/* ---------------------------------------------------------------- shell */
const NAV = [
  ["overview", "Обзор", "grid"],
  ["accounts", "Аккаунты", "user"],
  ["keys", "API ключи", "key"],
  ["endpoints", "Эндпоинты", "code"],
  ["automation", "Автоматизация", "bolt"],
  ["updates", "Апдейты", "pulse"],
  ["docs", "Документация", "book"],
  null,
  ["security", "Безопасность", "shield"],
];

async function loadAccounts() {
  S.accounts = await api("/panel/api/accounts");
  if (!S.accounts.find((a) => a.id === S.account)) S.account = S.accounts[0]?.id ?? null;
  store.set("account", S.account);
}

function accName(a) { return a ? (a.username ? "@" + a.username : [a.first_name, a.last_name].filter(Boolean).join(" ") || a.phone || "#" + a.id) : ""; }

function renderShell() {
  const u = S.auth.user;
  const nav = NAV.map((n) => n ? `<a href="#/${n[0]}" data-nav="${n[0]}">${ico(n[2])}<span>${n[1]}</span></a>` : `<div class="sep"></div>`).join("")
    + (u.is_admin ? `<a href="#/admin" data-nav="admin">${ico("sliders")}<span>Администрирование</span></a>` : "");
  $("#root").innerHTML = `<div class="shell">
    <aside class="side" id="side">
      <div class="brand">${ico("logo")}<div><b>grzxk</b> <span>tg API</span></div></div>
      <nav class="nav">${nav}</nav>
      <div class="foot"><div class="avatar">${esc(u.username[0].toUpperCase())}</div><div class="grow ellipsis small">${esc(u.username)}<div class="faint">${u.totp_enabled ? "2FA включена" : "2FA выключена"}</div></div>
      <button class="btn icon ghost" id="logout" title="Выйти">${ico("out")}</button></div>
    </aside>
    <main class="main">
      <div class="top"><button class="btn icon ghost menu-btn" id="menu">${ico("menu")}</button><div class="title" id="title"></div><div class="grow"></div>
        <select id="acc" style="width:auto;min-width:180px;height:34px;border-radius:999px" title="Аккаунт для запросов">${accOptions()}</select></div>
      <div class="content" id="view"></div>
    </main></div>`;
  $("#logout").onclick = async () => { await api("/panel/api/auth/logout", { method: "POST" }); boot(); };
  $("#menu").onclick = (e) => { e.stopPropagation(); $("#side").classList.toggle("open"); };
  $("#side").addEventListener("click", (e) => { if (e.target.closest("a")) $("#side").classList.remove("open"); });
  document.addEventListener("click", (e) => { const s = $("#side"); if (s && s.classList.contains("open") && !s.contains(e.target)) s.classList.remove("open"); });
  $("#acc").onchange = (e) => { S.account = +e.target.value || null; store.set("account", S.account); route(); };
}
function accOptions() {
  if (!S.accounts.length) return `<option value="">нет аккаунтов</option>`;
  return S.accounts.map((a) => `<option value="${a.id}" ${a.id === S.account ? "selected" : ""}>${esc(accName(a))}</option>`).join("");
}
function refreshAccSelect() { const s = $("#acc"); if (s) s.innerHTML = accOptions(); }

const VIEWS = {};
function route() {
  stopPoll();
  $$(".overlay").forEach((o) => o.remove());
  const name = (location.hash.replace(/^#\/?/, "").split("/")[0]) || "overview";
  const v = VIEWS[name] ? name : "overview";
  $$("[data-nav]").forEach((a) => a.classList.toggle("on", a.dataset.nav === v));
  const n = NAV.find((x) => x && x[0] === v);
  $("#title").textContent = n ? n[1] : v === "admin" ? "Администрирование" : "";
  $("#view").innerHTML = "";
  VIEWS[v]($("#view")).catch?.(fail);
}
window.addEventListener("hashchange", () => { if (S.auth?.user) route(); });

function needAccount(el) {
  if (S.accounts.length) return false;
  el.innerHTML = `<div class="card empty">${ico("user")}<div>Сначала подключите Telegram-аккаунт</div><div style="margin-top:14px"><a class="btn primary" href="#/accounts" style="border:0">${ico("plus", "sm")} Подключить</a></div></div>`;
  return true;
}

/* ---------------------------------------------------------------- overview */
VIEWS.overview = async (el) => {
  const s = await api("/panel/api/stats");
  const hours = Array.from({ length: 24 }, (_, i) => { const h = Math.floor(Date.now() / 3600000) * 3600 - (23 - i) * 3600; return s.per_hour.find((x) => x.h === h) || { h, n: 0, err: 0 }; });
  const max = Math.max(1, ...hours.map((h) => h.n));
  el.innerHTML = `<div class="stack lg">
    <div class="grid g4">
      ${stat("Аккаунты", s.accounts, "user")}${stat("API ключи", s.keys, "key")}${stat("Запросов за 24ч", s.requests_24h, "pulse")}${stat("Ошибок за 24ч", s.errors_24h, "x")}
    </div>
    <div class="grid g2">
      <div class="card"><div class="card-h"><h3>Активность</h3><span class="faint small">последние 24 часа</span></div>
        <div class="bars">${hours.map((h) => `<span class="${h.err ? "err" : ""}" style="height:${(h.n / max) * 100}%" title="${new Date(h.h * 1000).getHours()}:00 — ${h.n}"></span>`).join("")}</div></div>
      <div class="card"><div class="card-h"><h3>Быстрый старт</h3></div>
        ${codebox(`curl ${ORIGIN}/v1/me \\\n  -H "Authorization: Bearer tgk_…"`)}
        <div class="row" style="margin-top:12px"><a class="btn sm" href="#/keys" style="border-color:var(--line-2)">${ico("key", "sm")} Создать ключ</a><a class="btn sm" href="#/docs" style="border-color:var(--line-2)">${ico("book", "sm")} Документация</a></div></div>
    </div>
    <div class="card tight"><div class="card-h" style="padding:18px 18px 0"><h3>Последние запросы</h3></div>
      ${s.recent.length ? `<div class="scroll-x"><table class="t compact"><tr><th>Метод</th><th>Путь</th><th>Статус</th><th>мс</th><th>Время</th></tr>${s.recent.map((r) => `<tr><td><span class="verb ${r.method}">${r.method}</span></td><td class="mono">${esc(r.path)}</td><td><span class="pill ${r.status < 400 ? "ok" : "bad"}">${r.status}</span></td><td class="muted">${r.ms}</td><td class="muted">${fmtTime(r.ts)}</td></tr>`).join("")}</table></div>`
        : `<div class="empty">Запросов пока нет</div>`}</div></div>`;
};
const stat = (l, v, i) => `<div class="card stat"><div class="l">${ico(i, "sm")}${l}</div><div class="v">${v}</div></div>`;

/* ---------------------------------------------------------------- accounts */
VIEWS.accounts = async (el) => {
  await loadAccounts(); refreshAccSelect();
  el.innerHTML = `<div class="stack lg">
    <div class="row ph"><div class="grow"><h1>Telegram аккаунты</h1><div class="muted small">Сессии хранятся зашифрованными. Клиент: «grzxk tg API».</div></div>
      <button class="btn primary" id="add">${ico("plus", "sm")} Подключить</button></div>
    <div class="card tight list">${S.accounts.length ? S.accounts.map((a) => `
      <div class="item"><div class="avatar">${esc((a.first_name || "?")[0])}</div>
        <div class="grow"><div>${esc([a.first_name, a.last_name].filter(Boolean).join(" ") || "—")} <span class="muted">${a.username ? "@" + esc(a.username) : ""}</span></div>
        <div class="faint small mono">id ${a.tg_id} · +${esc(a.phone || "")} · account_id ${a.id}</div></div>
        <span class="pill ${a.status === "online" ? "ok" : ""}">${ico("dot")}${a.status}</span>
        <button class="btn icon ghost" data-re="${a.id}" title="Переподключить">${ico("refresh", "sm")}</button>
        <button class="btn icon ghost danger" data-del="${a.id}" title="Отключить">${ico("trash", "sm")}</button></div>`).join("")
      : `<div class="empty">${ico("user")}<div>Нет подключённых аккаунтов</div></div>`}</div></div>`;
  $("#add").onclick = () => addAccount();
  $$("[data-del]", el).forEach((b) => b.onclick = async () => {
    if (!confirm("Отключить аккаунт? Сессия будет завершена в Telegram, ключи привязанные к нему удалятся.")) return;
    try { await api(`/panel/api/accounts/${b.dataset.del}`, { method: "DELETE" }); toast("Аккаунт отключён"); VIEWS.accounts(el); } catch (e) { fail(e); }
  });
  $$("[data-re]", el).forEach((b) => b.onclick = async () => {
    try { const r = await api(`/panel/api/accounts/${b.dataset.re}/reconnect`, { method: "POST" }); toast("Статус: " + r.status); VIEWS.accounts(el); } catch (e) { fail(e); }
  });
};

function addAccount() {
  let flow = null, timer = null;
  const m = modal(`<div class="stack"><div class="row"><h2 class="grow">Подключить Telegram</h2><button class="btn icon ghost" data-close>${ico("x")}</button></div>
    <div class="seg" id="mode"><button class="on" data-m="phone">${ico("phone", "sm")} Номер</button><button data-m="qr">${ico("qr", "sm")} QR-код</button></div>
    <div id="body"></div></div>`, { onClose: () => { clearInterval(timer); if (flow && flow.state !== "done") api(`/panel/api/login/${flow.login_id}`, { method: "DELETE" }).catch(() => {}); } });
  const body = $("#body", m.el);
  const steps = (n) => `<div class="steps">${[1, 2, 3].map((i) => `<i class="${i <= n ? "on" : ""}"></i>`).join("")}</div>`;
  const done = async () => { clearInterval(timer); m.close(); toast("Аккаунт подключён"); await loadAccounts(); S.account = flow.account_id || S.account; store.set("account", S.account); refreshAccSelect(); route(); };

  const phoneStep = () => {
    body.innerHTML = `<form class="stack">${steps(1)}<label class="f">Номер телефона<input class="input" name="v" placeholder="+7 999 000 00 00" required autofocus inputmode="tel"></label><button class="btn primary">Получить код</button></form>`;
    $("form", body).onsubmit = async (e) => {
      e.preventDefault(); const btn = $("button", body); btn.disabled = true;
      try { flow = await api("/panel/api/login/phone", { method: "POST", body: { phone: $("[name=v]", body).value } }); codeStep(flow.sent_via); }
      catch (err) { fail(err); btn.disabled = false; }
    };
  };
  const codeStep = (via) => {
    body.innerHTML = `<form class="stack">${steps(2)}<div class="muted small">Код отправлен ${via === "app" ? "в Telegram на другом устройстве" : "по " + esc(via)}</div>
      <input class="input otp" name="v" inputmode="numeric" maxlength="6" required autofocus placeholder="·····"><button class="btn primary">Подтвердить</button></form>`;
    $("form", body).onsubmit = async (e) => {
      e.preventDefault();
      try { flow = await api(`/panel/api/login/${flow.login_id}/code`, { method: "POST", body: { code: $("[name=v]", body).value } }); flow.state === "done" ? done() : passwordStep(); }
      catch (err) { fail(err); }
    };
  };
  const passwordStep = () => {
    clearInterval(timer);
    body.innerHTML = `<form class="stack">${steps(3)}<div class="row">${ico("lock")}<div><div>Облачный пароль (2FA)</div><div class="muted small">${flow.password_hint ? "Подсказка: " + esc(flow.password_hint) : "На аккаунте включена двухэтапная проверка"}</div></div></div>
      <input class="input" type="password" name="v" required autofocus autocomplete="off"><button class="btn primary">Войти</button></form>`;
    $("form", body).onsubmit = async (e) => {
      e.preventDefault();
      try { flow = await api(`/panel/api/login/${flow.login_id}/password`, { method: "POST", body: { password: $("[name=v]", body).value } }); done(); }
      catch (err) { fail(err); }
    };
  };
  const qrStep = async () => {
    body.innerHTML = `<div class="empty">Генерация QR…</div>`;
    try { flow = await api("/panel/api/login/qr", { method: "POST" }); } catch (e) { body.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
    const draw = () => { body.innerHTML = `<div class="stack" style="text-align:center"><div class="qr">${flow.qr_svg || ""}</div><div class="muted small">Telegram → Настройки → Устройства → Подключить устройство</div></div>`; };
    draw();
    timer = setInterval(async () => {
      try {
        const prev = flow.qr_svg; flow = await api(`/panel/api/login/${flow.login_id}`);
        if (flow.state === "done") done(); else if (flow.state === "password") passwordStep();
        else if (flow.state === "error") { clearInterval(timer); body.innerHTML = `<div class="empty">${esc(flow.error)}</div>`; }
        else if (flow.qr_svg !== prev) draw();
      } catch (e) { clearInterval(timer); fail(e); }
    }, 2000);
  };
  $$("#mode button", m.el).forEach((b) => b.onclick = () => {
    $$("#mode button", m.el).forEach((x) => x.classList.toggle("on", x === b)); clearInterval(timer);
    if (flow && flow.state !== "done") api(`/panel/api/login/${flow.login_id}`, { method: "DELETE" }).catch(() => {});
    flow = null; b.dataset.m === "qr" ? qrStep() : phoneStep();
  });
  phoneStep();
}

/* ---------------------------------------------------------------- keys */
VIEWS.keys = async (el) => {
  const keys = await api("/panel/api/keys");
  el.innerHTML = `<div class="stack lg">
    <div class="row ph"><div class="grow"><h1>API ключи</h1><div class="muted small">Ключ даёт полный доступ к аккаунту. Показывается один раз.</div></div>
      <button class="btn primary" id="new">${ico("plus", "sm")} Новый ключ</button></div>
    <div class="card tight">${keys.length ? `<div class="scroll-x"><table class="t"><tr><th>Название</th><th>Ключ</th><th>Аккаунт</th><th>Создан</th><th>Использован</th><th></th></tr>
      ${keys.map((k) => `<tr><td>${esc(k.name)}</td><td class="mono muted">${esc(k.prefix)}</td><td>${k.account_id ? esc(k.account_username ? "@" + k.account_username : "+" + k.account_phone) : `<span class="muted">все</span>`}</td>
      <td class="muted">${fmtTime(k.created_at)}</td><td class="muted">${fmtTime(k.last_used_at)}</td><td style="text-align:right"><button class="btn sm danger" data-rev="${k.id}">Отозвать</button></td></tr>`).join("")}</table></div>`
      : `<div class="empty">${ico("key")}<div>Ключей пока нет</div></div>`}</div></div>`;
  $$("[data-rev]", el).forEach((b) => b.onclick = async () => {
    if (!confirm("Отозвать ключ? Приложения, использующие его, перестанут работать.")) return;
    await api(`/panel/api/keys/${b.dataset.rev}`, { method: "DELETE" }).catch(fail); VIEWS.keys(el);
  });
  $("#new").onclick = () => {
    const m = modal(`<form class="stack"><div class="row"><h2 class="grow">Новый API ключ</h2><button type="button" class="btn icon ghost" data-close>${ico("x")}</button></div>
      <label class="f">Название<input class="input" name="name" required placeholder="автоответчик, парсер…" autofocus></label>
      <label class="f">Доступ к аккаунту<select name="account_id"><option value="">Все мои аккаунты (выбор через X-Account-Id)</option>${S.accounts.map((a) => `<option value="${a.id}">${esc(accName(a))}</option>`).join("")}</select></label>
      <button class="btn primary">Создать</button></form>`);
    $("form", m.el).onsubmit = async (e) => {
      e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
      try {
        const r = await api("/panel/api/keys", { method: "POST", body: { name: f.name, account_id: f.account_id ? +f.account_id : null } });
        m.el.innerHTML = `<div class="stack"><div class="row">${ico("check")}<h2 class="grow">Ключ создан</h2></div><div class="muted small">Сохраните его сейчас — больше он показан не будет.</div>
          ${codebox(r.key)}${codebox(`curl ${ORIGIN}/v1/me -H "Authorization: Bearer ${r.key}"`)}<button class="btn primary" data-close>Готово</button></div>`;
        $("[data-close]", m.el).onclick = () => { m.close(); VIEWS.keys(el); };
      } catch (err) { fail(err); }
    };
  };
};

/* ---------------------------------------------------------------- endpoints */
async function loadCatalog() {
  if (!S.catalog) S.catalog = await api("/v1/methods");
  if (!S.openapi) S.openapi = await api("/openapi.json");
}
const sampleFor = (t) => ({ int: 0, long: 0, str: "", string: "", bool: true, date: Math.floor(Date.now() / 1000), bytes: "", double: 0, InputPeer: "me", InputUser: "me", InputChannel: "@channel" })[t] ?? (t.startsWith("Vector<") ? [] : { _: t[0].toLowerCase() + t.slice(1) });

VIEWS.endpoints = async (el) => {
  el.innerHTML = `<div class="empty">Загрузка каталога…</div>`;
  await loadCatalog();
  const tab = store.get("ep-tab", "rest");
  el.innerHTML = `<div class="stack lg">
    <div class="row ph"><div class="grow"><h1>Эндпоинты</h1><div class="muted small">${restOps().length} REST методов · ${S.catalog.count} методов MTProto (layer ${S.catalog.layer})</div></div>
      <div class="seg" id="tab"><button data-t="rest" class="${tab === "rest" ? "on" : ""}">REST</button><button data-t="rpc" class="${tab === "rpc" ? "on" : ""}">MTProto</button></div></div>
    <div class="search">${ico("search")}<input class="input" id="q" placeholder="Поиск: sendMessage, history, channels…" value="${esc(store.get("ep-q", ""))}"></div>
    <div id="ns" class="chips"></div>
    <div class="card tight" id="list"></div></div>`;
  let ns = null;
  const draw = () => {
    const t = $(".seg button.on", el).dataset.t, q = $("#q").value.trim().toLowerCase();
    store.set("ep-tab", t); store.set("ep-q", q);
    const list = $("#list");
    if (t === "rest") {
      $("#ns").innerHTML = "";
      const ops = restOps().filter((o) => !q || (o.path + o.summary + o.tag).toLowerCase().includes(q));
      const groups = {};
      ops.forEach((o) => (groups[o.tag] ||= []).push(o));
      list.innerHTML = Object.entries(groups).map(([g, items]) => `<div class="ep-group">${esc(g)}</div>` + items.map((o) =>
        `<div class="ep" data-op="${esc(o.method + " " + o.path)}"><span class="verb ${o.method}">${o.method}</span><span class="path">${esc(o.path)}</span><span class="sum">${esc(o.summary)}</span></div>`).join("")).join("") || `<div class="empty">Ничего не найдено</div>`;
      $$("[data-op]", list).forEach((r) => r.onclick = () => openRest(restOps().find((o) => o.method + " " + o.path === r.dataset.op)));
    } else {
      const counts = {};
      S.catalog.methods.forEach((m) => counts[m.namespace] = (counts[m.namespace] || 0) + 1);
      $("#ns").innerHTML = `<button class="chip ${!ns ? "on" : ""}" data-ns="">все<span class="ns-count">${S.catalog.count}</span></button>` + Object.keys(counts).sort().map((n) => `<button class="chip ${ns === n ? "on" : ""}" data-ns="${n}">${n}<span class="ns-count">${counts[n]}</span></button>`).join("");
      $$("[data-ns]", el).forEach((b) => b.onclick = () => { ns = b.dataset.ns || null; draw(); });
      const items = S.catalog.methods.filter((m) => (!ns || m.namespace === ns) && (!q || m.name.toLowerCase().includes(q)));
      list.innerHTML = items.slice(0, 400).map((m) => `<div class="ep" data-m="${m.name}"><span class="verb RPC">RPC</span><span class="path">${esc(m.name)}</span><span class="sum mono">${esc(m.result || "")}</span></div>`).join("")
        + (items.length > 400 ? `<div class="empty small">ещё ${items.length - 400} — уточните поиск</div>` : "") + (items.length ? "" : `<div class="empty">Ничего не найдено</div>`);
      $$("[data-m]", list).forEach((r) => r.onclick = () => openRpc(S.catalog.methods.find((m) => m.name === r.dataset.m)));
    }
  };
  $$("#tab button", el).forEach((b) => b.onclick = () => { $$("#tab button", el).forEach((x) => x.classList.toggle("on", x === b)); draw(); });
  $("#q").oninput = draw;
  draw();
};

function restOps() {
  const ops = [];
  for (const [path, item] of Object.entries(S.openapi.paths)) {
    if (!path.startsWith("/v1/")) continue;
    for (const [method, op] of Object.entries(item)) ops.push({ path, method: method.toUpperCase(), summary: op.summary || "", tag: (op.tags || ["other"])[0], op });
  }
  return ops;
}
function resolveRef(s) { if (s && s.$ref) return S.openapi.components.schemas[s.$ref.split("/").pop()]; return s; }
function schemaType(s) {
  s = resolveRef(s) || {};
  if (s.anyOf) return s.anyOf.map(schemaType).filter((x) => x !== "null").join(" | ");
  if (s.enum) return s.enum.map((x) => JSON.stringify(x)).join(" | ");
  if (s.type === "array") return schemaType(s.items) + "[]";
  return s.type || (s.format === "binary" ? "file" : "any");
}

function openRest(o) {
  const params = (o.op.parameters || []).filter((p) => !["x-account-id"].includes(p.name.toLowerCase()));
  const rb = o.op.requestBody?.content || {};
  const jsonSchema = resolveRef(rb["application/json"]?.schema);
  const formSchema = resolveRef(rb["multipart/form-data"]?.schema);
  const bodySchema = jsonSchema || formSchema;
  const example = jsonSchema ? Object.fromEntries(Object.entries(jsonSchema.properties || {}).map(([k, v]) => [k, (v.examples || [])[0] ?? v.default ?? (k === "peer" || k.endsWith("_peer") ? "me" : k === "text" ? "Привет из grzxk tg API" : k === "ids" ? [1] : sampleFor(schemaType(v) === "integer" ? "int" : schemaType(v) === "boolean" ? "bool" : "str"))])) : null;
  const m = modal(`<div class="stack lg">
    <div class="row"><span class="verb ${o.method}">${o.method}</span><h2 class="grow mono" style="font-size:14px">${esc(o.path)}</h2><button class="btn icon ghost" data-close>${ico("x")}</button></div>
    <div class="muted">${esc(o.summary)}</div>${o.op.description ? `<div class="small muted" style="white-space:pre-wrap">${esc(o.op.description)}</div>` : ""}
    ${params.length ? `<div><h3 style="margin-bottom:8px">Параметры</h3><div class="card tight"><table class="t">${params.map((p) => `<tr><td class="mono">${esc(p.name)}${p.required ? " *" : ""}</td><td class="muted small">${esc(p.in)}</td><td class="mono small">${esc(schemaType(p.schema))}</td><td><input class="input" style="height:32px" data-p="${esc(p.name)}" data-in="${p.in}" placeholder="${esc(p.schema?.default ?? (p.name === "peer" ? "me" : ""))}"></td></tr>`).join("")}</table></div></div>` : ""}
    ${bodySchema ? `<div><h3 style="margin-bottom:8px">Тело запроса ${formSchema ? "(multipart)" : "(JSON)"}</h3><div class="card tight"><table class="t compact">${Object.entries(bodySchema.properties || {}).map(([k, v]) => `<tr><td class="mono">${esc(k)}${(bodySchema.required || []).includes(k) ? " *" : ""}</td><td class="mono small">${esc(schemaType(v))}</td><td class="muted small">${esc(resolveRef(v)?.description || "")}</td></tr>`).join("")}</table></div></div>` : ""}
    ${formSchema ? `<div class="muted small">Файлы отправляются multipart — пример в документации.</div>` : ""}
    ${jsonSchema ? `<textarea id="body" rows="8">${esc(JSON.stringify(example, null, 2))}</textarea>` : ""}
    ${formSchema ? "" : `<div class="row"><button class="btn primary" id="run">${ico("play", "sm")} Выполнить</button><span class="faint small">от имени выбранного аккаунта</span></div>`}
    <div id="out"></div>
    <div><h3 style="margin-bottom:8px">curl</h3><div id="curl"></div></div></div>`, { drawer: true });
  const build = () => {
    let path = o.path; const qs = new URLSearchParams();
    $$("[data-p]", m.el).forEach((i) => { if (!i.value) return; if (i.dataset.in === "path") path = path.replace(`{${i.dataset.p}}`, encodeURIComponent(i.value)); else if (i.dataset.in === "query") qs.set(i.dataset.p, i.value); });
    path = path.replace(/\{(\w+)\}/g, (_, n) => n === "peer" ? "me" : `{${n}}`);
    return path + (qs.toString() ? "?" + qs : "");
  };
  const curl = () => {
    const url = ORIGIN + build(); const b = $("#body", m.el)?.value;
    let c = `curl -X ${o.method} "${url}" \\\n  -H "Authorization: Bearer $TGAPI_KEY"`;
    if (b) c += ` \\\n  -H "Content-Type: application/json" \\\n  -d '${b.replace(/\n\s*/g, " ")}'`;
    if (formSchema) c += Object.keys(formSchema.properties || {}).map((k) => ` \\\n  -F "${k}=${k === "file" ? "@photo.jpg" : k === "peer" ? "me" : ""}"`).join("");
    $("#curl", m.el).innerHTML = codebox(c);
  };
  m.el.addEventListener("input", curl); curl();
  $("#run", m.el) && ($("#run", m.el).onclick = async () => {
    const out = $("#out", m.el); out.innerHTML = `<div class="muted small">Выполняется…</div>`;
    let body; try { body = $("#body", m.el) ? JSON.parse($("#body", m.el).value) : undefined; } catch { return fail("JSON тела некорректен"); }
    const t0 = performance.now();
    try {
      const res = await api(build(), { method: o.method, body, raw: true });
      const ct = res.headers.get("content-type") || "";
      const data = ct.includes("json") ? await res.json() : `<${ct}, ${(await res.blob()).size} bytes>`;
      out.innerHTML = `<div class="row" style="margin-bottom:8px"><span class="pill ${res.ok ? "ok" : "bad"}">${res.status}</span><span class="faint small">${Math.round(performance.now() - t0)} мс</span></div>${codebox(typeof data === "string" ? data : JSON.stringify(data, null, 2), "json")}`;
    } catch (e) { out.innerHTML = `<div class="pill bad">${esc(e.message)}</div>`; }
  });
}

function openRpc(meth) {
  const req = meth.params.filter((p) => p.required);
  const sample = Object.fromEntries(req.map((p) => [p.name, p.name === "peer" ? "me" : p.name === "hash" ? 0 : p.name === "limit" ? 20 : sampleFor(p.type)]));
  const m = modal(`<div class="stack lg">
    <div class="row"><span class="verb RPC">RPC</span><h2 class="grow mono" style="font-size:15px">${esc(meth.name)}</h2><button class="btn icon ghost" data-close>${ico("x")}</button></div>
    <div class="kv"><span>Возвращает</span><span class="mono">${esc(meth.result || "—")}</span><span>Документация</span><span><a href="${meth.doc}" target="_blank" rel="noopener">core.telegram.org ${ico("link", "sm")}</a></span></div>
    <div><h3 style="margin-bottom:8px">Параметры</h3>${meth.params.length ? `<div class="card tight"><table class="t compact"><tr><th>Имя</th><th>Тип</th><th></th></tr>${meth.params.map((p) => `<tr><td class="mono">${esc(p.name)}</td><td class="mono small"><a href="#" data-type="${esc(p.type.replace(/^Vector<|>$/g, ""))}" style="border:0">${esc(p.type)}</a></td><td>${p.required ? `<span class="pill">обязательный</span>` : `<span class="faint small">опционально</span>`}</td></tr>`).join("")}</table></div>` : `<div class="muted small">Без параметров</div>`}</div>
    <div id="typebox"></div>
    <div><h3 style="margin-bottom:8px">params (JSON)</h3><textarea id="params" rows="8">${esc(JSON.stringify(sample, null, 2))}</textarea></div>
    <div class="row"><button class="btn primary" id="run">${ico("play", "sm")} Выполнить</button><span class="faint small">реальный вызов от имени выбранного аккаунта</span></div>
    <div id="out"></div><div><h3 style="margin-bottom:8px">curl</h3><div id="curl"></div></div></div>`, { drawer: true });
  const curl = () => { $("#curl", m.el).innerHTML = codebox(`curl -X POST ${ORIGIN}/v1/invoke \\\n  -H "Authorization: Bearer $TGAPI_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '${JSON.stringify({ method: meth.name, params: safeJson($("#params", m.el).value) })}'`); };
  $("#params", m.el).oninput = curl; curl();
  $$("[data-type]", m.el).forEach((a) => a.onclick = async (e) => {
    e.preventDefault();
    const t = a.dataset.type; const box = $("#typebox", m.el);
    if (["int", "long", "str", "bool", "bytes", "date", "double", "string"].includes(t)) { box.innerHTML = ""; return; }
    try { const d = await api(`/v1/types/${encodeURIComponent(t)}`); box.innerHTML = `<div class="card"><div class="card-h"><h3 class="mono">${esc(d.name)}</h3><span class="faint small">${d.kind === "type" ? "конструкторы" : ""}</span></div><div class="stack">${(d.constructors || [d]).map((c) => `<div class="small"><span class="mono">{"_": "${esc(c.name[0].toLowerCase() + c.name.slice(1))}"${c.params.map((p) => `, <span class="muted">${esc(p.name)}</span>: <span class="faint">${esc(p.type)}${p.required ? "" : "?"}</span>`).join("")}}</span></div>`).join("")}</div></div>`; }
    catch (err) { box.innerHTML = `<div class="faint small">${esc(err.message)}</div>`; }
  });
  $("#run", m.el).onclick = async () => {
    const out = $("#out", m.el);
    let params; try { params = JSON.parse($("#params", m.el).value || "{}"); } catch { return fail("JSON некорректен"); }
    out.innerHTML = `<div class="muted small">Выполняется…</div>`; const t0 = performance.now();
    try { const r = await api("/v1/invoke", { method: "POST", body: { method: meth.name, params } }); out.innerHTML = `<div class="row" style="margin-bottom:8px"><span class="pill ok">200</span><span class="faint small">${Math.round(performance.now() - t0)} мс</span></div>${codebox(JSON.stringify(r.result, null, 2), "json")}`; }
    catch (e) { out.innerHTML = `<div class="row" style="margin-bottom:8px"><span class="pill bad">${e.status || "error"}</span></div>${codebox(JSON.stringify(e.data || e.message, null, 2), "json")}`; }
  };
}
const safeJson = (s) => { try { return JSON.parse(s); } catch { return {}; } };

/* ---------------------------------------------------------------- automation */
VIEWS.automation = async (el) => {
  if (needAccount(el)) return;
  const [rules, hooks] = await Promise.all([api("/v1/autoreplies"), api("/v1/webhooks")]);
  const mt = { contains: "содержит", exact: "равно", startswith: "начинается с", regex: "regex", any: "любое" };
  const ct = { private: "личные", group: "группы", channel: "каналы", all: "все чаты" };
  el.innerHTML = `<div class="stack lg">
    <div class="row ph"><div class="grow"><h1>Автоответчик</h1><div class="muted small">Правила для ${esc(accName(S.accounts.find((a) => a.id === S.account)))}. Срабатывает первое подходящее.</div></div><button class="btn primary" id="addr">${ico("plus", "sm")} Правило</button></div>
    <div class="card tight list">${rules.length ? rules.map((r) => `<div class="item"><div class="switch ${r.enabled ? "on" : ""}" data-tog="${r.id}"></div>
      <div class="grow"><div>${esc(r.name || "Правило #" + r.id)} <span class="faint small">· ${ct[r.chat_type]} · ${mt[r.match_type]} ${r.pattern ? "«" + esc(r.pattern) + "»" : ""}${r.cooldown ? " · раз в " + r.cooldown + "с" : ""}</span></div><div class="muted small ellipsis">↳ ${esc(r.reply)}</div></div>
      <button class="btn icon ghost" data-edit="${r.id}">${ico("sliders", "sm")}</button><button class="btn icon ghost danger" data-delr="${r.id}">${ico("trash", "sm")}</button></div>`).join("") : `<div class="empty">${ico("bolt")}<div>Правил нет</div></div>`}</div>
    <div class="row ph" style="margin-top:10px"><div class="grow"><h1>Вебхуки</h1><div class="muted small">Все апдейты аккаунта POST-ом на ваш URL, подпись <span class="mono">X-TgApi-Signature: sha256=HMAC(secret, body)</span></div></div><button class="btn" id="addw">${ico("plus", "sm")} Вебхук</button></div>
    <div class="card tight list">${hooks.length ? hooks.map((h) => `<div class="item"><div class="grow"><div class="mono small">${esc(h.url)}</div><div class="faint small">${h.events ? esc(h.events) : "все события"} · secret <span class="mono" data-copy="${esc(h.secret)}" style="cursor:pointer">${esc(h.secret.slice(0, 12))}… ${ico("copy", "sm")}</span></div></div>
      <button class="btn icon ghost danger" data-delw="${h.id}">${ico("trash", "sm")}</button></div>`).join("") : `<div class="empty">Вебхуков нет</div>`}</div></div>`;
  const ruleForm = (r = {}) => {
    const m = modal(`<form class="stack"><div class="row"><h2 class="grow">${r.id ? "Правило" : "Новое правило"}</h2><button type="button" class="btn icon ghost" data-close>${ico("x")}</button></div>
      <label class="f">Название<input class="input" name="name" value="${esc(r.name || "")}" placeholder="Не в сети"></label>
      <div class="row"><label class="f grow">Где<select name="chat_type">${Object.entries(ct).map(([k, v]) => `<option value="${k}" ${r.chat_type === k ? "selected" : ""}>${v}</option>`).join("")}</select></label>
      <label class="f grow">Условие<select name="match_type">${Object.entries(mt).map(([k, v]) => `<option value="${k}" ${(r.match_type || "contains") === k ? "selected" : ""}>${v}</option>`).join("")}</select></label></div>
      <label class="f">Шаблон <span class="faint">(для «содержит» — слова через запятую)</span><input class="input" name="pattern" value="${esc(r.pattern || "")}" placeholder="цена, прайс, сколько стоит"></label>
      <label class="f">Ответ <span class="faint">({name} — имя, {text} — текст сообщения)</span><textarea name="reply" rows="4" required style="font-family:var(--font);font-size:14px">${esc(r.reply || "")}</textarea></label>
      <label class="f">Пауза в одном чате, сек<input class="input" name="cooldown" type="number" min="0" value="${r.cooldown ?? 300}"></label>
      <button class="btn primary">Сохранить</button></form>`);
    $("form", m.el).onsubmit = async (e) => {
      e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
      const body = { ...f, cooldown: +f.cooldown || 0, enabled: r.id ? !!r.enabled : true };
      try { await api(r.id ? `/v1/autoreplies/${r.id}` : "/v1/autoreplies", { method: r.id ? "PUT" : "POST", body }); m.close(); VIEWS.automation(el); } catch (err) { fail(err); }
    };
  };
  $("#addr").onclick = () => ruleForm();
  $$("[data-edit]", el).forEach((b) => b.onclick = () => ruleForm(rules.find((r) => r.id === +b.dataset.edit)));
  $$("[data-tog]", el).forEach((b) => b.onclick = async () => { const r = rules.find((x) => x.id === +b.dataset.tog); await api(`/v1/autoreplies/${r.id}`, { method: "PUT", body: { ...r, enabled: !r.enabled } }).catch(fail); VIEWS.automation(el); });
  $$("[data-delr]", el).forEach((b) => b.onclick = async () => { if (confirm("Удалить правило?")) { await api(`/v1/autoreplies/${b.dataset.delr}`, { method: "DELETE" }).catch(fail); VIEWS.automation(el); } });
  $$("[data-delw]", el).forEach((b) => b.onclick = async () => { if (confirm("Удалить вебхук?")) { await api(`/v1/webhooks/${b.dataset.delw}`, { method: "DELETE" }).catch(fail); VIEWS.automation(el); } });
  $("#addw").onclick = () => {
    const m = modal(`<form class="stack"><div class="row"><h2 class="grow">Новый вебхук</h2><button type="button" class="btn icon ghost" data-close>${ico("x")}</button></div>
      <label class="f">URL<input class="input" name="url" required placeholder="https://example.com/tg-hook"></label>
      <label class="f">События <span class="faint">(пусто = все)</span><input class="input" name="events" placeholder="UpdateNewMessage, UpdateNewChannelMessage"></label>
      <button class="btn primary">Создать</button></form>`);
    $("form", m.el).onsubmit = async (e) => { e.preventDefault(); try { await api("/v1/webhooks", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); m.close(); VIEWS.automation(el); } catch (err) { fail(err); } };
  };
};

/* ---------------------------------------------------------------- updates */
function stopPoll() { if (S.poll) { S.poll.abort = true; S.poll = null; } }
VIEWS.updates = async (el) => {
  if (needAccount(el)) return;
  el.innerHTML = `<div class="stack lg"><div class="row ph"><div class="grow"><h1>Апдейты</h1><div class="muted small">Живой поток событий аккаунта — то же, что отдаёт <span class="mono">GET /v1/updates</span></div></div>
    <input class="input" id="flt" placeholder="фильтр по типу" style="max-width:220px"><span class="pill ok" id="live">${ico("dot")}live</span></div>
    <div class="card tight" id="ups"><div class="empty" id="wait">Ожидание событий…</div></div></div>`;
  const poll = { abort: false }; S.poll = poll;
  let offset = 0; const box = $("#ups");
  while (!poll.abort) {
    try {
      const r = await api(`/v1/updates?offset=${offset}&timeout=25`);
      if (poll.abort) break;
      for (const u of r.updates) {
        offset = Math.max(offset, u.update_id);
        $("#wait")?.remove();
        const d = document.createElement("details"); d.className = "upd"; d.dataset.type = u.type.toLowerCase();
        d.innerHTML = `<summary><span class="faint mono small">#${u.update_id}</span><span class="mono">${esc(u.type)}</span><span class="grow"></span><span class="faint small">${fmtTime(u.ts)}</span></summary><pre class="code">${pretty(JSON.stringify(u.update, null, 2))}</pre>`;
        const f = $("#flt").value.toLowerCase(); if (f && !d.dataset.type.includes(f)) d.classList.add("hide");
        box.prepend(d);
        while (box.children.length > 300) box.lastElementChild.remove();
      }
    } catch (e) { if (poll.abort) break; $("#live") && ($("#live").className = "pill bad"); await new Promise((r) => setTimeout(r, 4000)); }
  }
};
document.addEventListener("input", (e) => { if (e.target.id === "flt") { const f = e.target.value.toLowerCase(); $$(".upd").forEach((d) => d.classList.toggle("hide", !!f && !d.dataset.type.includes(f))); } });

/* ---------------------------------------------------------------- security */
VIEWS.security = async (el) => {
  const st = await api("/panel/api/auth/state"); S.auth = st;
  const sessions = await api("/panel/api/auth/sessions");
  const u = st.user;
  el.innerHTML = `<div class="stack lg">
    <div class="grid g2">
      <div class="card stack"><div class="card-h">${ico("shield")}<h3 class="grow">Двухфакторная аутентификация</h3><span class="pill ${u.totp_enabled ? "ok" : "bad"}">${u.totp_enabled ? "включена" : "выключена"}</span></div>
        <div class="muted small">TOTP-код из Google Authenticator, Aegis, 1Password и т.п. при каждом входе в панель.</div>
        <div id="totp">${u.totp_enabled ? `<form class="row" id="dis"><input class="input otp" style="height:40px;font-size:15px" name="code" maxlength="6" inputmode="numeric" placeholder="код" required><button class="btn danger">Отключить</button></form>` : `<button class="btn primary" id="en">Включить 2FA</button>`}</div></div>
      <form class="card stack" id="pw"><div class="card-h">${ico("lock")}<h3>Пароль</h3></div>
        <input class="input" type="password" name="current" placeholder="Текущий пароль" required autocomplete="current-password">
        <input class="input" type="password" name="new" placeholder="Новый пароль (от 8 символов)" required minlength="8" autocomplete="new-password">
        <button class="btn">Сменить пароль</button></form></div>
    <div class="card tight"><div class="card-h" style="padding:18px 18px 0"><h3>Активные сессии панели</h3></div><div class="list">
      ${sessions.map((s) => `<div class="item"><div class="grow"><div class="small ellipsis">${esc(s.user_agent || "—")}</div><div class="faint small">${esc(s.ip)} · ${fmtTime(s.created_at)}</div></div>${s.current ? `<span class="pill ok">текущая</span>` : `<button class="btn sm danger" data-kill="${s.id}">Завершить</button>`}</div>`).join("")}</div></div></div>`;
  $("#en") && ($("#en").onclick = async () => {
    const r = await api("/panel/api/auth/totp/init", { method: "POST" }).catch(fail); if (!r) return;
    $("#totp").innerHTML = `<div class="stack"><div class="qr" style="width:180px;height:180px">${r.qr_svg}</div>
      <div class="faint small" style="text-align:center">или ключ вручную</div>${codebox(r.secret)}
      <form class="row" id="conf"><input class="input otp" style="height:40px;font-size:15px" name="code" maxlength="6" inputmode="numeric" placeholder="код" required autofocus><button class="btn primary">Подтвердить</button></form></div>`;
    $("#conf").onsubmit = async (e) => { e.preventDefault(); try { await api("/panel/api/auth/totp/enable", { method: "POST", body: { code: e.target.code.value } }); toast("2FA включена"); S.auth.user.totp_enabled = 1; renderShell(); route(); } catch (err) { fail(err); } };
  });
  $("#dis") && ($("#dis").onsubmit = async (e) => { e.preventDefault(); try { await api("/panel/api/auth/totp/disable", { method: "POST", body: { code: e.target.code.value } }); toast("2FA отключена"); S.auth.user.totp_enabled = 0; renderShell(); route(); } catch (err) { fail(err); } });
  $("#pw").onsubmit = async (e) => { e.preventDefault(); try { await api("/panel/api/auth/password", { method: "POST", body: Object.fromEntries(new FormData(e.target)) }); toast("Пароль изменён"); e.target.reset(); } catch (err) { fail(err); } };
  $$("[data-kill]", el).forEach((b) => b.onclick = async () => { await api(`/panel/api/auth/sessions/${b.dataset.kill}`, { method: "DELETE" }).catch(fail); VIEWS.security(el); });
};

/* ---------------------------------------------------------------- admin */
VIEWS.admin = async (el) => {
  if (!S.auth.user.is_admin) { location.hash = "#/overview"; return; }
  const [st, users] = await Promise.all([api("/panel/api/settings"), api("/panel/api/users")]);
  el.innerHTML = `<div class="stack lg">
    <form class="card stack" id="tg"><div class="card-h">${ico("sliders")}<h3 class="grow">Telegram API</h3><span class="pill ${st.tg_api_id && st.tg_api_hash_set ? "ok" : "bad"}">${st.tg_api_id && st.tg_api_hash_set ? "настроено" : "не настроено"}</span></div>
      <div class="muted small">Данные приложения с <a href="https://my.telegram.org/apps" target="_blank" rel="noopener">my.telegram.org</a>. Переменные окружения TG_API_ID / TG_API_HASH имеют приоритет.</div>
      <div class="row wrap"><label class="f grow">api_id<input class="input" name="tg_api_id" value="${esc(st.tg_api_id || "")}"></label><label class="f grow">api_hash<input class="input" name="tg_api_hash" placeholder="${esc(st.tg_api_hash_hint || "")}"></label></div>
      <div class="row"><div class="switch ${st.registration_open ? "on" : ""}" id="reg"></div><span>Открытая регистрация в панели</span></div>
      <div><button class="btn primary">Сохранить</button></div></form>
    <div class="row ph"><h2 class="grow">Пользователи панели</h2><button class="btn" id="addu">${ico("plus", "sm")} Пользователь</button></div>
    <div class="card tight"><div class="scroll-x"><table class="t"><tr><th>Логин</th><th>Роль</th><th>2FA</th><th>Аккаунтов</th><th>Создан</th><th></th></tr>
      ${users.map((u) => `<tr><td>${esc(u.username)}</td><td class="muted">${u.is_admin ? "админ" : "пользователь"}</td><td>${u.totp_enabled ? `<span class="pill ok">да</span>` : `<span class="pill">нет</span>`}</td><td class="muted">${u.accounts}</td><td class="muted">${fmtTime(u.created_at)}</td>
      <td style="text-align:right">${u.id === S.auth.user.id ? "" : `<button class="btn sm danger" data-delu="${u.id}">Удалить</button>`}</td></tr>`).join("")}</table></div></div></div>`;
  let reg = st.registration_open;
  $("#reg").onclick = (e) => { reg = !reg; e.target.classList.toggle("on", reg); };
  $("#tg").onsubmit = async (e) => {
    e.preventDefault(); const f = Object.fromEntries(new FormData(e.target));
    try { await api("/panel/api/settings", { method: "POST", body: { tg_api_id: f.tg_api_id ? +f.tg_api_id : null, tg_api_hash: f.tg_api_hash || null, registration_open: reg } }); toast("Сохранено"); VIEWS.admin(el); } catch (err) { fail(err); }
  };
  $$("[data-delu]", el).forEach((b) => b.onclick = async () => { if (confirm("Удалить пользователя и все его аккаунты/ключи?")) { await api(`/panel/api/users/${b.dataset.delu}`, { method: "DELETE" }).catch(fail); VIEWS.admin(el); } });
  $("#addu").onclick = () => {
    const m = modal(`<form class="stack"><div class="row"><h2 class="grow">Новый пользователь</h2><button type="button" class="btn icon ghost" data-close>${ico("x")}</button></div>
      <input class="input" name="username" placeholder="Логин" required minlength="3"><input class="input" name="password" type="password" placeholder="Пароль" required minlength="8">
      <label class="row small"><input type="checkbox" name="is_admin"> администратор</label><button class="btn primary">Создать</button></form>`);
    $("form", m.el).onsubmit = async (e) => { e.preventDefault(); const f = Object.fromEntries(new FormData(e.target)); try { await api("/panel/api/users", { method: "POST", body: { username: f.username, password: f.password, is_admin: !!f.is_admin } }); m.close(); VIEWS.admin(el); } catch (err) { fail(err); } };
  };
};

/* ---------------------------------------------------------------- docs */
VIEWS.docs = async (el) => {
  const B = ORIGIN;
  const sections = [
    ["start", "Начало работы"], ["auth", "Аутентификация"], ["accounts", "Несколько аккаунтов"], ["invoke", "Любой метод MTProto"],
    ["messages", "Сообщения"], ["parser", "Парсер постов"], ["search", "Поиск по каналам"], ["autoreply", "Автоответчик"],
    ["updates", "Апдейты и вебхуки"], ["errors", "Ошибки и лимиты"], ["sdk", "Примеры: Python / JS"],
  ];
  el.innerHTML = `<div class="grid" style="grid-template-columns:minmax(0,1fr) 200px;align-items:start"><div class="doc">
  <h1>Документация</h1>
  <p>grzxk tg API превращает ваш Telegram-аккаунт в HTTP API. Под капотом — полноценный MTProto-клиент, поэтому доступно всё, что может официальное приложение: ${S.catalog?.count || "~820"} методов, плюс удобные REST-эндпоинты для частых задач. Интерактивная схема: <a href="${B}/swagger" target="_blank">Swagger</a> · <a href="${B}/redoc" target="_blank">ReDoc</a> · <a href="${B}/openapi.json" target="_blank">openapi.json</a>.</p>

  <h2 id="start">Начало работы</h2>
  <ul><li>Подключите Telegram-аккаунт в разделе <a href="#/accounts">Аккаунты</a> — по номеру (код + облачный пароль 2FA) или QR-коду.</li>
  <li>Создайте ключ в <a href="#/keys">API ключи</a>.</li><li>Базовый URL: <code>${B}/v1</code></li></ul>
  ${codebox(`curl ${B}/v1/me -H "Authorization: Bearer tgk_..."`)}

  <h2 id="auth">Аутентификация</h2>
  <p>Передавайте ключ в заголовке <code>Authorization: Bearer tgk_…</code> (или <code>X-API-Key</code>). Ключ даёт полный доступ к аккаунту — храните его как пароль, при утечке отзовите в панели.</p>

  <h2 id="accounts">Несколько аккаунтов</h2>
  <p>Ключ может быть привязан к одному аккаунту или ко всем вашим. Во втором случае выбирайте аккаунт заголовком <code>X-Account-Id: 2</code> или параметром <code>?account=2</code>; без него используется первый. Список — <code>GET /v1/accounts</code>.</p>

  <h2 id="invoke">Любой метод MTProto</h2>
  <p><code>POST /v1/invoke</code> вызывает любой метод из <a href="https://core.telegram.org/methods" target="_blank">core.telegram.org/methods</a>. Каталог с типами параметров — <code>GET /v1/methods</code>, описание типа — <code>GET /v1/types/InputPeer</code>, интерактивно — во вкладке <a href="#/endpoints">Эндпоинты → MTProto</a>.</p>
  <ul><li>Пиры (<code>peer</code>, <code>user_id</code>, <code>channel</code>…) принимают <code>"@username"</code>, <code>"me"</code>, телефон или числовой id — резолвятся автоматически.</li>
  <li>Вложенные объекты: <code>{"_": "inputMessagesFilterPhotos"}</code>; списки — JSON-массивы.</li>
  <li>bytes — строка, <code>"base64:…"</code> или массив чисел; даты — unix time или ISO 8601.</li><li>В ответе TL-объекты приходят с полем <code>_</code>, bytes — в base64.</li></ul>
  ${codebox(`curl -X POST ${B}/v1/invoke \\
  -H "Authorization: Bearer $TGAPI_KEY" -H "Content-Type: application/json" \\
  -d '{"method": "messages.getHistory", "params": {"peer": "@durov", "limit": 5, "offset_id": 0, "offset_date": null, "add_offset": 0, "max_id": 0, "min_id": 0, "hash": 0}}'`)}
  ${codebox(`# сменить статус «в сети»
{"method": "account.updateStatus", "params": {"offline": false}}

# отправить опрос
{"method": "messages.sendMedia", "params": {"peer": "me", "message": "",
  "media": {"_": "inputMediaPoll", "poll": {"_": "poll", "id": 0, "question": {"_": "textWithEntities", "text": "Да?", "entities": []},
  "answers": [{"_": "pollAnswer", "text": {"_": "textWithEntities", "text": "Да", "entities": []}, "option": "base64:MA=="},
              {"_": "pollAnswer", "text": {"_": "textWithEntities", "text": "Нет", "entities": []}, "option": "base64:MQ=="}]}}}}`)}

  <h2 id="messages">Сообщения</h2>
  ${codebox(`# отправить
curl -X POST ${B}/v1/messages/send -H "Authorization: Bearer $TGAPI_KEY" -H "Content-Type: application/json" \\
  -d '{"peer": "@username", "text": "**Привет**", "parse_mode": "md"}'

# файл
curl -X POST ${B}/v1/messages/send-file -H "Authorization: Bearer $TGAPI_KEY" \\
  -F peer=me -F caption="отчёт" -F file=@report.pdf

# редактировать / удалить / переслать / реакция / прочитать
POST /v1/messages/edit     {"peer": "me", "id": 123, "text": "новый текст"}
POST /v1/messages/delete   {"peer": "me", "ids": [123, 124]}
POST /v1/messages/forward  {"from_peer": "@channel", "to_peer": "me", "ids": [10]}
POST /v1/messages/react    {"peer": "@channel", "id": 10, "emoji": "🔥"}
POST /v1/messages/read     {"peer": "@friend"}
GET  /v1/media/{peer}/{id}  → файл медиа (thumb=true — превью)`)}

  <h2 id="parser">Парсер постов</h2>
  <p><code>GET /v1/messages/{peer}</code> — история чата или канала. Пагинация: передавайте <code>offset_id</code> = <code>next_offset_id</code> из прошлого ответа. Фильтры: <code>search</code>, <code>from_user</code>, <code>filter</code> (photos, videos, documents, links, voice…), <code>min_id</code>/<code>max_id</code>, <code>offset_date</code>, <code>reverse</code>. <code>raw=true</code> добавит полный TL-объект. Комментарии к посту: <code>GET /v1/messages/{peer}/{id}?comments=100</code>.</p>
  ${codebox(`curl "${B}/v1/messages/durov?limit=100" -H "Authorization: Bearer $TGAPI_KEY"`)}
  ${codebox(`{"chat": {"id": 1006503122, "type": "channel", "title": "Durov's Channel", "username": "durov"},
 "count": 100, "next_offset_id": 312,
 "messages": [{"id": 412, "date": "2026-10-20T12:00:00+00:00", "text": "…", "views": 3100000,
               "forwards": 5200, "replies": 900, "reactions": [{"emoji": "👍", "count": 41000}],
               "media": {"type": "photo"}, "link": "https://t.me/durov/412"}]}`, "json")}

  <h2 id="search">Поиск по каналам</h2>
  ${codebox(`GET /v1/search/chats?q=crypto          # найти публичные каналы, группы, пользователей
GET /v1/search/posts?q=airdrop&limit=50 # поиск постов по всем публичным каналам
GET /v1/search/messages?q=договор       # поиск по всем вашим чатам
GET /v1/search/messages?q=цена&peer=@shop
POST /v1/chats/join {"peer": "t.me/+AbCdEf"}   # вступить (в т.ч. по инвайту)
GET /v1/chats/{peer}/participants?limit=500`)}

  <h2 id="autoreply">Автоответчик</h2>
  <p>Правила хранятся на сервере и работают 24/7, без вашего кода. Можно настроить в разделе <a href="#/automation">Автоматизация</a> или через API.</p>
  ${codebox(`curl -X POST ${B}/v1/autoreplies -H "Authorization: Bearer $TGAPI_KEY" -H "Content-Type: application/json" -d '{
  "name": "прайс", "chat_type": "private", "match_type": "contains",
  "pattern": "цена, прайс, сколько стоит", "reply": "{name}, прайс тут: https://…", "cooldown": 600}'`)}
  <p>match_type: <code>contains</code> (слова через запятую), <code>exact</code>, <code>startswith</code>, <code>regex</code>, <code>any</code>. chat_type: <code>private</code>, <code>group</code>, <code>channel</code>, <code>all</code>. Для сложной логики используйте апдейты/вебхуки и отвечайте через <code>/v1/messages/send</code>.</p>

  <h2 id="updates">Апдейты и вебхуки</h2>
  <p>Long-poll как в Bot API: <code>GET /v1/updates?offset=N&timeout=25</code> возвращает события с <code>update_id</code> &gt; offset. Хранится последние 2000 событий на аккаунт. Фильтр: <code>types=UpdateNewMessage,UpdateNewChannelMessage</code>.</p>
  <p>Вебхук: <code>POST /v1/webhooks {"url": "https://…", "events": ""}</code>. Каждое событие отправляется POST-ом с заголовком <code>X-TgApi-Signature: sha256=…</code> — HMAC-SHA256 тела с секретом вебхука.</p>
  ${codebox(`import hmac, hashlib
def verify(body: bytes, header: str, secret: str) -> bool:
    good = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(good, header)`)}

  <h2 id="errors">Ошибки и лимиты</h2>
  ${codebox(`{"error": {"code": "FLOOD_WAIT", "message": "A wait of 31 seconds is required", "retry_after": 31}}`, "json")}
  <ul><li><code>401</code> — нет/неверный ключ; <code>404</code> — пир или аккаунт не найден; <code>422</code> — неверные параметры.</li>
  <li><code>429 FLOOD_WAIT</code> — лимит Telegram, повторите через <code>retry_after</code> секунд (заголовок <code>Retry-After</code>).</li>
  <li>Ошибки Telegram приходят с исходным кодом: <code>CHAT_WRITE_FORBIDDEN</code>, <code>USERNAME_NOT_OCCUPIED</code>, …</li>
  <li>Это пользовательский аккаунт: массовые рассылки и спам приводят к блокировке номера Telegram'ом. Делайте паузы.</li></ul>

  <h2 id="sdk">Примеры: Python / JS</h2>
  ${codebox(`import requests, time
API, KEY = "${B}/v1", "tgk_..."
s = requests.Session(); s.headers["Authorization"] = f"Bearer {KEY}"

# парсер всего канала
offset, posts = 0, []
while True:
    r = s.get(f"{API}/messages/durov", params={"limit": 100, "offset_id": offset}).json()
    posts += r["messages"]
    if not r["messages"]: break
    offset = r["next_offset_id"]; time.sleep(1)

# свой автоответчик через long-poll
upd = 0
while True:
    for u in s.get(f"{API}/updates", params={"offset": upd, "timeout": 25, "types": "UpdateShortMessage"}).json()["updates"]:
        upd = u["update_id"]; m = u["update"]
        if not m.get("out"):
            s.post(f"{API}/messages/send", json={"peer": m["user_id"], "text": "Я пока занят, отвечу позже"})`)}
  ${codebox(`const API = "${B}/v1", KEY = "tgk_...";
const tg = (path, body) => fetch(API + path, {
  method: body ? "POST" : "GET",
  headers: { Authorization: "Bearer " + KEY, "Content-Type": "application/json" },
  body: body && JSON.stringify(body),
}).then(r => r.json());

await tg("/messages/send", { peer: "me", text: "hello" });
await tg("/invoke", { method: "contacts.search", params: { q: "news", limit: 10 } });`)}
  </div>
  <div class="toc card flat" style="position:sticky;top:80px">${sections.map(([id, t]) => `<a href="#" data-toc="${id}">${t}</a>`).join("")}</div></div>`;
  $$("[data-toc]", el).forEach((a) => a.onclick = (e) => { e.preventDefault(); document.getElementById(a.dataset.toc).scrollIntoView({ behavior: "smooth", block: "start" }); });
  if (innerWidth < 900) { $(".toc", el).remove(); el.firstElementChild.style.gridTemplateColumns = "minmax(0,1fr)"; }
};

boot();
