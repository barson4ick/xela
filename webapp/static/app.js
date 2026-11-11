'use strict';

const tg = window.Telegram && window.Telegram.WebApp;
if (tg) { tg.ready(); tg.expand(); }

const $ = (sel) => document.querySelector(sel);
const view = $('#view');
const esc = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const S = {
  me: null, chat: null, tab: 'home', board: null,
  open: [], live: [], betArena: 3, betMax: 5, room: null, mafia: null, mafiaOpen: [], sig: '', prevStatus: '', tasks: null, rp: null, rpLast: '', rpTimer: null, giveAt: 0,
  bet: 3, wheelAngle: 0, busy: false, poll: null,
  home: null, homeSig: '', gameSeg: 'arena', casinoGame: 'wheel', rpEarn: 'jobs', meSeg: 'ach', meData: null,
};

// ───────── тема и шапка ─────────
function applyTheme() {
  const dark = (tg && tg.colorScheme === 'dark')
    || (!tg && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches);
  document.documentElement.dataset.theme = dark ? 'dark' : 'light';
  const bg = dark ? '#0c0e13' : '#f3f0e9';
  try {
    if (tg && tg.setHeaderColor) tg.setHeaderColor(bg);
    if (tg && tg.setBackgroundColor) tg.setBackgroundColor(bg);
    if (tg && tg.setBottomBarColor) tg.setBottomBarColor(bg);
  } catch (e) { /* старые клиенты */ }
  const t = S.me && S.me.theme;
  if (t) {
    let bar = document.getElementById('themeBar');
    if (!bar) {
      bar = document.createElement('div');
      bar.id = 'themeBar';
      bar.className = 'theme-bar';
      view.parentNode.insertBefore(bar, view);
    }
    bar.textContent = `${t.icon} ${t.banner}`;
  }
}

// ───────── утилиты ─────────
async function api(path, method = 'GET', data) {
  const res = await fetch(path, {
    method,
    headers: { 'X-Init-Data': tg ? tg.initData : '', 'Content-Type': 'application/json' },
    body: data ? JSON.stringify(data) : undefined,
  });
  let json = {};
  try { json = await res.json(); } catch (e) { /* пустой ответ */ }
  if (!res.ok) throw new Error(json.error || 'Ошибка ' + res.status);
  return json;
}

let toastTimer;
function toast(text) {
  const el = $('#toast');
  el.textContent = text;
  el.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add('hidden'), 2600);
}

function haptic(kind) {
  const h = tg && tg.HapticFeedback;
  if (!h) return;
  try {
    if (kind === 'ok') h.notificationOccurred('success');
    else if (kind === 'bad') h.notificationOccurred('error');
    else h.impactOccurred('light');
  } catch (e) { /* не критично */ }
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const signed = (n) => (n > 0 ? '+' + n : String(n));
const avatar = (p) => esc(p.emoji || (p.username || '?').replace('@', '').charAt(0).toUpperCase());

function remember(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* без хранилища */ } }
function recall(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

const moneyFmt = (n) => Number(n || 0).toLocaleString('ru-RU');
const dailyKey = () => new Date().toLocaleDateString('sv-SE', { timeZone: 'Europe/Moscow' });
const isDailyReady = (me) => !me || me.last_daily !== dailyKey();

function updateChip() {
  const me = S.board && S.board.me;
  $('#sizeChip').textContent = me ? moneyFmt(me.size) : '—';
}

function chatInfo() {
  return ((S.me && S.me.chats) || []).find((c) => c.chat_id === S.chat) || null;
}

function updateHeader() {
  const c = chatInfo();
  const title = (c && c.title) || 'Твой район';
  const titleEl = $('#chatTitle');
  if (titleEl) titleEl.textContent = title;
  const ava = $('#chatAva');
  if (ava) ava.textContent = (title.trim()[0] || 'Р').toUpperCase();
  const players = (S.board && S.board.players) || [];
  const quests = S.home && S.home.tasks && S.home.tasks.quests;
  const ready = quests ? quests.items.filter((i) => i.ready).length + (quests.bonus_ready ? 1 : 0) : 0;
  const sub = $('#chatSub');
  if (sub) {
    if (!S.board) sub.textContent = 'Открываем район';
    else sub.textContent = `${players.length} ${playersWord(players.length)}${ready ? ` · ${ready} ${rewardWord(ready)}` : ' · онлайн-район'}`;
  }
  const dot = $('#navTasksDot');
  if (dot) dot.classList.toggle('hidden', !ready);
}

function playersWord(n) {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? 'игрок' : [2, 3, 4].includes(m10) && ![12, 13, 14].includes(m100) ? 'игрока' : 'игроков';
}
function rewardWord(n) {
  const m10 = n % 10, m100 = n % 100;
  return m10 === 1 && m100 !== 11 ? 'награда' : [2, 3, 4].includes(m10) && ![12, 13, 14].includes(m100) ? 'награды' : 'наград';
}

function closeModal() {
  $('#modal').classList.add('hidden');
  $('#modal').setAttribute('aria-hidden', 'true');
}

function openSheet(html) {
  $('#modalBody').innerHTML = html;
  $('#modal').classList.remove('hidden');
  $('#modal').setAttribute('aria-hidden', 'false');
}

function openChats() {
  const rows = ((S.me && S.me.chats) || []).map((c) => {
    const on = c.chat_id === S.chat;
    return `<button class="list-item" data-a="pick-chat" data-v="${c.chat_id}" style="width:100%;text-align:left">
      <div class="av" style="background:${on ? 'var(--brand-soft)' : 'var(--surface-2)'}">${esc((c.title || 'Ч').trim()[0].toUpperCase())}</div>
      <div class="grow"><div class="name">${esc(c.title)}</div><div class="muted">${moneyFmt(c.size)} см${on ? ' · открыт' : ''}</div></div>
      <span class="badge ${on ? 'brand' : ''}">${on ? '✓' : '→'}</span></button>`;
  }).join('');
  openSheet(`<div class="sheet-title">Твои чаты</div>
    <div class="sheet-sub">Выбери, чей район смотрим. Прогресс у каждого чата свой.</div>
    ${rows || '<div class="empty">Ты пока не в одном чате с ботом.</div>'}`);
}

async function selectChat(id) {
  id = Number(id);
  if (!id || id === S.chat) { closeModal(); return; }
  S.chat = id;
  remember('chat', String(id));
  S.board = null; S.home = null; S.tasks = null; S.rp = null; S.room = null; S.mafia = null; S.pk = null; S.sig = '';
  closeModal();
  haptic();
  try { await loadBoard(); } catch (e) { /* покажем на экране */ }
  updateHeader();
  setTab(S.tab === 'games' ? 'games' : S.tab);
}

async function loadBoard() {
  S.board = await api(`/api/chat/${S.chat}/board`);
  S.giveAt = Date.now() + (S.board.give_in || 0) * 1000;
  updateChip();
  updateHeader();
}

// ───────── общие помощники ─────────
function ago(ts) {
  const m = Math.max(1, Math.round((Date.now() / 1000 - ts) / 60));
  return m < 60 ? m + ' мин' : m < 1440 ? Math.round(m / 60) + ' ч' : Math.round(m / 1440) + ' д';
}

// ───────── профиль ─────────
function sparkline(history) {
  if (history.length < 2) return '<div class="muted">График появится после нескольких замеров.</div>';
  const vals = history.map((h) => h.size_after);
  const min = Math.min(...vals), max = Math.max(...vals);
  const span = Math.max(max - min, 1);
  const pts = vals.map((v, i) => `${(i / (vals.length - 1)) * 276 + 2},${66 - ((v - min) / span) * 60}`).join(' ');
  return `<svg class="chart" viewBox="0 0 280 70" preserveAspectRatio="none" role="img" aria-label="Рост размера">
    <polyline points="${pts}" fill="none" stroke="var(--btn)" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/></svg>
    <div class="muted">От ${min} до ${max} см за последние ${vals.length} изменений</div>`;
}

function profileHeroHtml(d) {
  const p = d.player, look = d.look || {};
  const style = [];
  if (look.bg) style.push(`background:linear-gradient(160deg,${look.bg[0]},${look.bg[1]});color:#fff;border-color:transparent;`);
  if (look.frame) style.push(`box-shadow:0 0 0 3px ${look.frame[0]},0 0 22px ${look.frame[1]};`);
  return `<div class="profile-hero" style="${style.join('')}">
    <div class="row">
      <div class="p-ava ${[...avatar(p)].length > 1 ? 'multi' : ''}" ${look.bg ? 'style="background:rgba(255,255,255,.16);border-color:rgba(255,255,255,.2)"' : ''}>${avatar(p)}</div>
      <div class="grow"><div class="name">${esc(p.username)}</div>
        ${p.title ? `<span class="badge">${esc(p.title)}</span>` : ''}
        <div class="muted" style="margin-top:3px">${d.stats.messages} сообщений · ${d.stats.heroes} герой дня</div></div>
      <div class="p-size"><b>${p.size}</b><span>см</span></div>
    </div>
  </div>`;
}

function profileStatsHtml(d) {
  const p = d.player, st = d.stats;
  return `<div class="stats">
    <div class="stat"><b>⭐ ${p.rep}</b><span>карма</span></div>
    <div class="stat"><b>🔥 ${p.streak}</b><span>серия</span></div>
    <div class="stat"><b>💬 ${st.messages}</b><span>сообщений</span></div>
    <div class="stat"><b>⚔️ ${st.duel_wins}/${st.duel_losses}</b><span>дуэли В/П</span></div>
    <div class="stat"><b>🏟 ${st.arena_wins}</b><span>арена</span></div>
    <div class="stat"><b>🌟 ${st.heroes}</b><span>герой дня</span></div>
  </div>`;
}

function achievementsListHtml(d) {
  const ach = d.achievements.map((a) => `
    <div class="ach ${a.done ? '' : 'lock'}">
      <div class="ic">${a.icon}</div>
      <div class="grow"><b>${esc(a.title)}</b><div class="muted">${esc(a.desc)}</div>
        ${a.done ? '' : `<div class="bar"><i style="width:${Math.round((a.value / a.goal) * 100)}%"></i></div>`}</div>
      <div class="${a.done ? 'badge good' : 'muted'}" style="font-size:11px">${a.done ? 'готово' : a.value + '/' + a.goal}</div>
    </div>`).join('');
  const done = d.achievements.filter((a) => a.done).length;
  return `<div class="sec-head"><h2>Достижения <small>${done} из ${d.achievements.length}</small></h2></div>
    <div class="card">${ach}</div>`;
}

function profileHtml(d) {
  return `${profileHeroHtml(d)}${profileStatsHtml(d)}
    <div class="sec-head"><h2>Рост размера</h2></div>
    <div class="card">${sparkline(d.history)}</div>
    ${achievementsListHtml(d)}`;
}

async function openProfile(uid) {
  try {
    const d = await api(`/api/chat/${S.chat}/player/${uid}`);
    const mine = S.board && S.board.me;
    const giveForm = mine && Number(uid) !== mine.user_id
      ? `<div class="sec-head"><h2>Подарить см</h2></div><div class="card">
          <div class="muted" style="margin-bottom:9px">У тебя ${mine.size} см. Любая сумма до этого значения, раз в 2 часа. <span id="giveT"></span></div>
          <input type="hidden" id="giveTo" value="${uid}">
          <div class="row"><input id="giveAmt" class="num" type="number" inputmode="numeric" min="1" max="${Math.max(1, mine.size)}" placeholder="Сколько см" aria-label="Сколько см передать">
          <button class="btn sm give-go" data-a="give">Отправить</button></div></div>`
      : '';
    openSheet(`<div class="sheet-title">Профиль игрока</div><div class="sheet-sub">${esc(d.player.username)}</div>${profileHtml(d)}${giveForm}`);
  } catch (e) { toast(e.message); }
}

async function viewMe() {
  view.innerHTML = `<div class="screen"><div class="sk hero"></div><div class="sk row"></div><div class="sk row"></div></div>`;
  let d;
  try { d = await api(`/api/chat/${S.chat}/player/${S.me.id}`); }
  catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  S.meData = d;
  const seg = S.meSeg;
  const cur = d.player.emoji;
  const emojis = S.me.emojis.map((e) => `<button data-a="emoji" data-v="${e}" class="${e === cur ? 'on' : ''}" aria-label="Значок ${e}">${e}</button>`).join('');
  const subnav = `<div class="subnav">
      <button data-a="me-seg" data-v="ach" class="${seg === 'ach' ? 'on' : ''}">🏆 Достижения</button>
      <button data-a="me-seg" data-v="look" class="${seg === 'look' ? 'on' : ''}">🎨 Внешность</button>
      <button data-a="me-seg" data-v="rel" class="${seg === 'rel' ? 'on' : ''}">💞 Отношения</button>
    </div>`;
  let body = '';
  if (seg === 'look') {
    body = `<div id="cosBox" class="card"><div class="muted">Загрузка…</div></div>
      <div class="sec-head"><h2>Мой значок</h2></div>
      <div class="card"><div class="emojis">${emojis}</div><div class="muted" style="margin-top:9px">Значок виден в рейтинге и рядом с ником.</div></div>`;
  } else if (seg === 'rel') {
    body = `<div id="relBox" class="card"><div class="muted">Загрузка…</div></div>
      <div class="card muted">Отношения растут от РП-действий в чате: /hug, /treat, /dance ответом на сообщение, а ещё от «+» на сообщение друга.</div>`;
  } else {
    body = `<div class="sec-head"><h2>Рост размера</h2><span class="badge">${d.history.length} замеров</span></div>
      <div class="card">${sparkline(d.history)}</div>${achievementsListHtml(d)}`;
  }
  view.innerHTML = `<div class="screen">${profileHeroHtml(d)}${subnav}${body}
    <button class="btn ghost" style="margin-top:14px" data-a="share-card">📣 Показать карточку в чате</button></div>`;
  if (seg === 'look') loadCos();
  if (seg === 'rel') loadRel();
}

async function loadRel() {
  const box = $('#relBox');
  if (!box) return;
  try {
    const rows = (await api(`/api/chat/${S.chat}/relations`)).relations;
    box.innerHTML = rows.length
      ? rows.map((r) => `<div class="row" style="padding:4px 0"><span style="font-size:20px">${r.icon}</span><div class="grow"><b>${esc(r.name)}</b><div class="muted">${esc(r.stage)} · ${r.pts} очк.${r.next ? ' · до «' + esc(r.next_name) + '» ещё ' + r.next : ''}</div></div></div>`).join('')
      : '<div class="muted">Пока пусто. Попробуйте РП-действия в чате: /hug, /treat, /dance (ответом на сообщение) или поставьте «+» на сообщение друга.</div>';
  } catch (e) { box.innerHTML = '<div class="muted">Не удалось загрузить</div>'; }
}

// ───────── внешность ─────────
S.cos = null;

async function loadCos() {
  try { S.cos = (await api(`/api/chat/${S.chat}/cosmetics`)).cosmetics; } catch (e) { S.cos = null; }
  renderCos();
}

function renderCos() {
  const box = $('#cosBox');
  if (!box) return;
  if (!S.cos) { box.innerHTML = '<div class="muted">Не удалось загрузить</div>'; return; }
  const tile = (kind, it, cur) => {
    const on = cur === it.id;
    const sw = kind === 'frame'
      ? `border:4px solid ${it.colors[0]};box-shadow:0 0 10px ${it.colors[1]};`
      : `background:linear-gradient(160deg,${it.colors[0]},${it.colors[1]});`;
    return `<div class="cos ${on ? 'on' : ''}"><div class="cos-sw" style="${sw}"></div><div class="name">${esc(it.title)}</div>
      ${it.owned
        ? `<button class="btn sm ${on ? '' : 'ghost'}" data-a="cos-equip" data-k="${kind}" data-v="${on ? '' : it.id}">${on ? 'Снять' : 'Надеть'}</button>`
        : `<button class="btn sm" data-a="cos-buy" data-k="${kind}" data-v="${it.id}">${it.price} см</button>`}</div>`;
  };
  box.innerHTML = `<div class="muted" style="margin-bottom:6px">Рамки профиля и карточки</div>
    <div class="cos-grid">${S.cos.frames.map((i) => tile('frame', i, S.cos.frame)).join('')}</div>
    <div class="muted" style="margin:12px 0 6px">Фоны</div>
    <div class="cos-grid">${S.cos.bgs.map((i) => tile('bg', i, S.cos.bg)).join('')}</div>`;
}

async function cosAction(path, el) {
  try {
    const d = await api(`/api/chat/${S.chat}/cosmetics/${path}`, 'POST', { kind: el.dataset.k, id: el.dataset.v });
    S.cos = d.cosmetics;
    if (d.size != null && S.board) { S.board.me.size = d.size; updateChip(); }
    haptic('ok'); toast(d.text);
    await viewMe();
  } catch (e) { toast(e.message); haptic('bad'); }
}

// ───────── казино ─────────
function casinoHtml() {
  const me = S.board && S.board.me;
  const ready = isDailyReady(me);
  const game = S.casinoGame;
  const wheel = `<div class="wheel-wrap"><canvas id="wheel" width="560" height="560" aria-label="Колесо удачи"></canvas></div>
    <button class="btn gold" id="spinBtn" data-a="spin">Крутить колесо</button>
    <div class="result" id="wheelRes"></div>`;
  const slots = `<div class="reels">${[0, 1, 2].map((i) => `<div class="reel" id="reel${i}">🎰</div>`).join('')}</div>
    <button class="btn gold" id="slotBtn" data-a="slots">Крутить слоты</button>
    <div class="result" id="slotRes"></div>`;
  return `<div class="card row" style="align-items:center">
      <div style="width:40px;height:40px;display:grid;place-items:center;font-size:22px;border-radius:13px;background:var(--gold-soft)">🎁</div>
      <div class="grow"><b>Ежедневный бонус</b><div class="muted">Серия дней делает награду больше</div></div>
      ${ready ? '<button class="btn sm gold" data-a="daily">Забрать</button>' : '<span class="badge good">сегодня ✓</span>'}
    </div>
    <div class="casino-panel">
      <div class="row">
        <div class="grow"><b style="font-size:17px">${game === 'wheel' ? '🎡 Колесо удачи' : '🎰 Слоты'}</b>
          <div class="muted">${game === 'wheel' ? 'Множители x0 · x1 · x2 · x5' : 'Пара — возврат, три в ряд — x15, три семёрки — x30'}</div></div>
        <span class="badge gold">${game === 'wheel' ? 'x5' : 'x30'}</span>
      </div>
      <div class="seg" style="margin-top:13px">
        <button data-a="casino-game" data-v="wheel" class="${game === 'wheel' ? 'on' : ''}">🎡 Колесо</button>
        <button data-a="casino-game" data-v="slots" class="${game === 'slots' ? 'on' : ''}">🎰 Слоты</button>
      </div>
      <div class="bet"><button data-a="bet" data-v="-1" aria-label="Меньше">−</button><b id="betVal">${S.bet} см</b><button data-a="bet" data-v="1" aria-label="Больше">+</button></div>
      ${game === 'wheel' ? wheel : slots}
      <div class="casino-note">Ставка — от 1 до ${S.me.max_bet} см. Чем больше у тебя сантиметров, тем скромнее множитель, а за сутки можно выиграть не больше 60 см.</div>
    </div>
    <div class="card"><div class="row"><span style="font-size:20px">💡</span><div class="grow muted">Крути по чуть-чуть: серия маленьких ставок живёт дольше одной большой. И да, казино всегда немного в плюсе — это не баг, это бизнес.</div></div></div>`;
}

function renderCasino() {
  const box = $('#gameBody');
  if (!box) return;
  box.innerHTML = casinoHtml();
  drawWheel();
  const w = $('#wheel');
  if (w) w.style.transform = `rotate(${S.wheelAngle}deg)`;
}

function drawWheel() {
  const cv = $('#wheel');
  if (!cv) return;
  const ctx = cv.getContext('2d');
  const wheel = S.me.wheel;
  const n = wheel.length, step = (Math.PI * 2) / n, c = cv.width / 2;
  const colors = { 0: '#4b5563', 1: '#3b82f6', 2: '#22a55b', 5: '#f5a524' };
  wheel.forEach((m, i) => {
    const a0 = -Math.PI / 2 + i * step;
    ctx.beginPath(); ctx.moveTo(c, c); ctx.arc(c, c, c - 4, a0, a0 + step); ctx.closePath();
    ctx.fillStyle = colors[m] || '#888'; ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.55)'; ctx.lineWidth = 3; ctx.stroke();
    ctx.save(); ctx.translate(c, c); ctx.rotate(a0 + step / 2);
    ctx.fillStyle = '#fff'; ctx.font = 'bold 46px sans-serif'; ctx.textAlign = 'right'; ctx.textBaseline = 'middle';
    ctx.fillText('x' + m, c - 34, 0); ctx.restore();
  });
}

async function spinWheel() {
  if (S.busy) return;
  S.busy = true;
  const res = $('#wheelRes'); res.textContent = ''; res.className = 'result';
  try {
    const d = await api(`/api/chat/${S.chat}/wheel`, 'POST', { bet: S.bet });
    const center = (d.index + 0.5) * (360 / S.me.wheel.length);
    S.wheelAngle = S.wheelAngle - (S.wheelAngle % 360) + 360 * 5 + (360 - center);
    const w = $('#wheel');
    if (w) w.style.transform = `rotate(${S.wheelAngle}deg)`;
    haptic();
    await sleep(4300);
    if (S.board) { S.board.me.size = d.size; updateChip(); }
    showResult(res, d.delta, d.mult);
  } catch (e) { toast(e.message); haptic('bad'); }
  S.busy = false;
}

async function spinSlots() {
  if (S.busy) return;
  S.busy = true;
  const res = $('#slotRes'); res.textContent = ''; res.className = 'result';
  const syms = S.me.slots;
  const reels = [0, 1, 2].map((i) => $('#reel' + i));
  if (reels.some((r) => !r)) { S.busy = false; return; }
  try {
    const d = await api(`/api/chat/${S.chat}/slots`, 'POST', { bet: S.bet });
    reels.forEach((r) => r.classList.add('spin'));
    const timer = setInterval(() => reels.forEach((r) => { if (r.classList.contains('spin')) r.textContent = syms[Math.floor(Math.random() * syms.length)]; }), 70);
    for (let i = 0; i < 3; i++) {
      await sleep(700 + i * 400);
      reels[i].classList.remove('spin'); reels[i].textContent = syms[d.reels[i]]; haptic();
    }
    clearInterval(timer);
    if (S.board) { S.board.me.size = d.size; updateChip(); }
    showResult(res, d.delta, d.mult);
  } catch (e) { toast(e.message); haptic('bad'); reels.forEach((r) => r.classList.remove('spin')); }
  S.busy = false;
}

function showResult(el, delta, mult) {
  if (!el) return;
  if (delta > 0) { el.textContent = `Выигрыш x${mult}: ${signed(delta)} см 🎉`; el.className = 'result win'; haptic('ok'); }
  else if (delta === 0) { el.textContent = 'Ставка вернулась 🤝'; el.className = 'result'; }
  else { el.textContent = `Проигрыш: ${delta} см`; el.className = 'result lose'; haptic('bad'); }
}

// ───────── вкладка «Арена» ─────────
const iconOf = (game) => (S.me.games[game] || {}).icon || '🎮';

function viewArena() { viewGames(); }

function viewGames() {
  if (S.room) return renderRoom();
  if (S.mafia) return renderMafia();
  if (S.pk) return renderPokerTable();
  renderGamesLobby();
}

const GAME_TABS = [
  ['casino', '🎰', 'Казино'],
  ['arena', '⚔️', 'Дуэли'],
  ['mafia', '🕵️', 'Мафия'],
];

function renderGamesLobby() {
  const seg = S.gameSeg;
  view.innerHTML = `<div class="screen">
    <div class="screen-head"><span class="eyebrow">🎲 активности</span><h1>Игры</h1>
      <p>Дуэли, мафия, покер и казино.</p></div>
    <div class="seg" id="gameSeg">${GAME_TABS.map(([key, ic, title]) => `<button data-a="game-seg" data-v="${key}" class="${seg === key ? 'on' : ''}">${ic} ${title}</button>`).join('')}</div>
    <div id="gameBody"></div></div>`;
  renderGameBody();
  manageGamesPoll();
}

function renderGameBody() {
  const box = $('#gameBody');
  if (!box) return;
  if (S.gameSeg === 'casino') return renderCasino();
  if (S.gameSeg === 'arena') { box.innerHTML = arenaLobbyHtml(); return; }
  if (S.gameSeg === 'mafia') { box.innerHTML = mafiaLobbyHtml(); return; }
  S.gameSeg = 'casino';
  renderCasino();
}

function manageGamesPoll() {
  if (S.tab !== 'games') { stopPoll(); return; }
  if (S.gameSeg === 'casino' && !S.room && !S.mafia && !S.pk) stopPoll();
  else startPoll();
}

function setGameSeg(seg) {
  if (!['casino', 'arena', 'mafia'].includes(seg)) return;
  S.gameSeg = seg;
  S.sig = '';
  haptic();
  if (S.tab !== 'games') { setTab('games'); return; }
  renderGamesLobby();
  window.scrollTo(0, 0);
}

function arenaLobbyHtml() {
  const g = S.me.games.duel || { title: 'Дуэль', icon: '⚔️', stake: 3 };
  const open = S.open.length
    ? S.open.map((r) => `<div class="live-card"><div class="live-ic">${iconOf(r.game)}</div>
        <div class="live-meta"><b>${esc(r.host_name)}</b><span>${esc((S.me.games[r.game] || g).title)} · ставка ${r.stake} см</span></div>
        <button class="btn sm" data-a="join" data-v="${r.rid}">Принять</button></div>`).join('')
    : `<div class="empty-live"><div class="e-ic">🕰</div><b>Открытых вызовов нет</b><p>Создай свой — бот позовёт чат на бой.</p><button class="btn sm" data-a="create" data-v="duel">Бросить вызов</button></div>`;
  const chips = [1, 2, 3, 5].filter((n) => n <= S.betMax).map((n) => `<button data-a="bet-amount" data-v="${n}" class="${S.betArena === n ? 'on' : ''}">${n}</button>`).join('');
  const liveHtml = S.live.length
    ? `<div class="sec-head"><h2>Ставки на чужие партии <small>до ${S.betMax} см</small></h2></div><div class="chips">Ставка: ${chips}</div>` + S.live.map((r) => {
        const [a, b] = r.players;
        const btn = (p) => r.mine
          ? `<button class="btn sm ghost" disabled>${r.mine.on === p.id ? '✓ ' : ''}${esc(p.name)} · ${r.pool[p.id] || 0}</button>`
          : `<button class="btn sm" data-a="bet-room" data-r="${r.rid}" data-v="${p.id}">На ${esc(p.name)} · ${r.pool[p.id] || 0}</button>`;
        return `<div class="card"><div class="muted">${iconOf(r.game)} ${esc((S.me.games[r.game] || g).title)} · идёт партия</div><div class="row" style="margin-top:9px;gap:8px">${btn(a)}${btn(b)}</div></div>`;
      }).join('')
    : '';
  return `<div class="card">
      <div class="game-card"><div class="ic">⚔️</div><div class="grow"><b>Дуэль один на один</b><div class="muted">Ставка ${g.stake} см · победитель забирает всё · бот позовёт чат</div></div></div>
      <div class="btn-row" style="margin-top:12px">
        <button class="btn" data-a="create" data-v="duel">Вызвать чат</button>
        <button class="btn ghost" data-a="create-bot" data-v="duel">С ботом</button>
      </div>
      <div class="muted" style="margin-top:8px">«С ботом» — бесплатная тренировка, чтобы размяться.</div>
    </div>
    ${liveHtml}
    <div class="sec-head"><h2>Открытые вызовы</h2><span class="badge">${S.open.length}</span></div>${open}
    <div class="sec-head"><h2>Другие игры</h2></div>
    <div class="act-grid">
      <button class="act" style="--c1:#8f6bff;--c2:#c66bff" data-a="game-seg" data-v="mafia"><span class="act-ic">🕵️</span><span class="act-title">Мафия</span><span class="act-sub">4–12 игроков · роли в тайне</span><span class="act-go">→</span></button>
    </div>`;
}

function mafiaLobbyHtml() {
  const mOpen = S.mafiaOpen.map((m) => `<div class="live-card"><div class="live-ic">🕵️</div>
      <div class="live-meta"><b>Мафия · ${esc(m.host_name)}</b><span>${m.count} / ${m.max} игроков</span></div>
      <button class="btn sm" data-a="mafia-join" data-v="${m.rid}">Войти</button></div>`).join('')
    || `<div class="empty-live"><div class="e-ic">🎭</div><b>Город ещё не собрался</b><p>Создай игру — бот позовёт чат, начиная с 4 игроков.</p><button class="btn sm" data-a="mafia-create">Собрать мафию</button></div>`;
  return `<div class="card" style="background:radial-gradient(100% 100% at 100% 0%, var(--brand-soft), transparent 60%), var(--surface)">
      <div class="row"><div style="font-size:40px;line-height:1">🕵️</div><div class="grow"><b style="font-size:17px">Мафия</b>
        <div class="muted">4–12 игроков. Роли видит только каждый у себя: мафия, доктор, комиссар и мирные.</div></div></div>
      <div class="row" style="margin-top:12px"><span class="pill">🌙 ночь</span><span class="pill">☀️ день</span><span class="pill">🗳 голосование</span></div>
      <button class="btn" style="margin-top:12px" data-a="mafia-create">Собрать игру</button>
    </div>
    <div class="sec-head"><h2>Собираются сейчас</h2><span class="badge">${S.mafiaOpen.length}</span></div>${mOpen}
    <div class="card muted" style="margin-top:12px">Победа: мафия — перевес в голосах, город — вычислить всех. Победителям +3 см. Не выходи из мини-аппа во время ночи, чтобы не пропустить ход.</div>`;
}

function renderRoom() {
  const r = S.room;
  const me = r.you;
  const names = Object.fromEntries(r.players.map((p) => [p.id, p.name]));
  let body = '';
  if (r.status === 'open') {
    body = `<div class="card" style="text-align:center;padding:22px 16px">
        <div style="font-size:46px;line-height:1.1">⚔️</div>
        <b style="display:block;font-size:18px;margin-top:6px">Ждём соперника…</b>
        <div class="muted" style="margin-top:4px">Бот уже позвал чат. Ставка ${r.stake} см — победитель забирает всё.</div>
      </div>
      <button class="btn ghost" data-a="leave">Отменить вызов</button>
      <div class="card muted" style="margin-top:10px">Совет: не закрывай мини-апп — соперник может принять вызов в любую секунду.</div>`;
  } else {
    body = duelHtml(r, names);
    if (r.status === 'done') {
      const cls = r.winner === me ? 'win' : r.winner == null ? '' : 'lose';
      const text = r.winner === me ? '🏆 Победа!' : r.winner == null ? '🤝 Ничья' : '💀 Поражение';
      const stake = r.stake && r.winner != null ? `<div class="muted" style="font-size:14px;font-weight:500">${r.winner === me ? '+' : '−'}${r.stake} см</div>` : '';
      body += `<div class="banner ${cls}">${text}${stake}</div><button class="btn" data-a="leave">В лобби</button>`;
    } else {
      const waiting = waitingOn(r);
      if (waiting && waiting !== me && r.idle >= r.claim_after && !r.vs_bot) {
        body += `<button class="btn bad" style="margin-top:12px" data-a="act" data-v="claim">Соперник молчит — забрать победу</button>`;
      }
      body += `<button class="btn ghost" style="margin-top:12px" data-a="leave">${r.vs_bot ? 'Выйти' : 'Сдаться'}</button>`;
    }
  }
  const log = r.log.length ? `<div class="log">${r.log.map((l) => `<div>${esc(l)}</div>`).join('')}</div>` : '';
  const g = S.me.games[r.game] || { title: 'Дуэль', icon: '⚔️' };
  view.innerHTML = `<div class="screen"><div class="screen-head"><span class="eyebrow">⚔️ арена</span><h1>${g.icon} ${esc(g.title)}${r.vs_bot ? ' · тренировка' : ''}</h1><p>${r.status === 'open' ? 'Бот уже позвал чат — ждём смельчака.' : 'Побеждает тот, кто первым собьёт соперника.'}</p></div>${body}${log}</div>`;
}

function waitingOn(r) {
  if (r.status !== 'playing') return null;
  return r.turn;
}

function duelHtml(r, names) {
  const [a, b] = r.players;
  const side = (p) => `<div class="p ${r.turn === p.id && r.status === 'playing' ? 'turn' : ''}">
      <div class="face">${p.id === 0 ? '🤖' : '🥷'}</div><div class="name">${esc(p.name)}${p.id === r.you ? ' (ты)' : ''}</div>
      <div class="hp"><i style="width:${Math.round((r.hp[p.id] / 30) * 100)}%"></i></div><div class="muted">${r.hp[p.id]} / 30 HP</div></div>`;
  const mine = r.status === 'playing' && r.turn === r.you;
  const hint = r.status === 'playing' ? `<div class="muted" style="text-align:center;margin-top:10px">${mine ? 'Твой ход!' : 'Ход соперника: ' + esc(names[r.turn])}</div>` : '';
  const actions = r.status === 'playing' ? `<div class="actions">
      <button class="btn" data-a="act" data-v="lunge" ${mine ? '' : 'disabled'}>🗡 Выпад<br><small>7–12 HP</small></button>
      <button class="btn" data-a="act" data-v="heli" ${mine ? '' : 'disabled'}>🌪 Вертолёт<br><small>14–20, риск</small></button></div>` : '';
  return `<div class="duel">${side(a)}${side(b)}</div>${hint}${actions}`;
}

async function refreshArena(render = true) {
  try {
    if (S.room) {
      S.room = (await api(`/api/room/${S.room.rid}`)).room;
    } else {
      const d = await api(`/api/chat/${S.chat}/rooms`);
      S.open = d.open; S.room = d.mine; S.mafia = d.mafia_mine; S.mafiaOpen = d.mafia_open; S.live = d.live || []; S.betMax = d.bet_max || 5;
    }
  } catch (e) {
    S.room = null;
  }
  try {
    const pk = await api(`/api/chat/${S.chat}/poker`);
    S.pk = pk.mine; S.pkOpen = pk.open; S.pkLevels = pk.levels;
  } catch (e) { S.pk = null; }
  if (S.pk && S.pk.your_turn && !S.pkWasTurn) haptic('ok');
  S.pkWasTurn = !!(S.pk && S.pk.your_turn);
  if (S.pk) { const t = $('#pkT'); if (t) t.textContent = S.pk.left; }
  if (S.mafia && S.mafia.status === 'done' && S.prevMafia !== 'done') { loadBoard().catch(() => {}); haptic(S.mafia.winners.includes(S.mafia.you) ? 'ok' : 'bad'); }
  S.prevMafia = S.mafia ? S.mafia.status : '';
  if (S.room && S.room.status === 'done' && S.prevStatus !== 'done') {
    loadBoard().catch(() => {});
    haptic(S.room.winner === S.room.you ? 'ok' : 'bad');
  }
  S.prevStatus = S.room ? S.room.status : '';
  const sig = JSON.stringify([S.room, S.open, S.mafia, S.mafiaOpen, S.live, S.pk, S.pkOpen].map((x) => x && x.left !== undefined ? { ...x, left: 0 } : x));
  if (sig !== S.sig) {
    S.sig = sig;
    if (render && S.tab === 'games') {
      if (S.room || S.mafia || S.pk) viewGames();
      else if (S.gameSeg !== 'casino') renderGameBody();
    }
  }
}

function startPoll() { stopPoll(); S.poll = setInterval(refreshArena, 2000); }
function stopPoll() { if (S.poll) { clearInterval(S.poll); S.poll = null; } }


// ───────── мафия ─────────
const MAFIA_ACTION = { mafia: ['kill', '🔪', 'Выбери жертву'], doctor: ['heal', '💉', 'Кого спасаем?'], sheriff: ['check', '🔎', 'Кого проверяем?'] };

function renderMafia() {
  const m = S.mafia;
  const me = m.you;
  const byId = Object.fromEntries(m.players.map((p) => [p.id, p]));
  let body = '';
  if (m.status === 'open') {
    const list = m.players.map((p) => `<div class="list-item"><div class="av">🙂</div><div class="grow"><div class="name">${esc(p.name)}${p.id === me ? ' (ты)' : ''}</div></div>${p.id === m.host ? '<span class="badge">хост</span>' : ''}</div>`).join('');
    const isHost = m.host === me;
    body = `<div class="banner">🕵️ Лобби<div class="muted" style="font-size:14px;font-weight:500">${m.players.length} игроков · нужно от ${m.min}</div></div>${list}
      <div class="card" style="margin-top:10px"><b>Кто в городе</b><div class="muted" style="margin-top:5px">🔪 Мафия убивает ночью · 💉 доктор лечит · 🔎 комиссар проверяет · 🙂 мирные голосуют днём. Роль видит только её владелец.</div></div>
      ${isHost ? `<button class="btn" style="margin-top:10px" data-a="mafia-start" ${m.players.length < m.min ? 'disabled' : ''}>▶️ Начать игру</button>` : '<div class="muted" style="text-align:center;margin-top:10px">Ждём, пока хост начнёт…</div>'}
      <button class="btn ghost" style="margin-top:10px" data-a="mafia-leave">Выйти</button>`;
  } else {
    const r = m.role;
    const roleCard = r ? `<div class="card role ${r.key}"><div class="row"><div style="font-size:34px">${r.icon}</div><div class="grow"><b>Ты — ${esc(r.title)}</b><div class="muted">${esc(r.hint)}</div></div></div></div>` : '';
    const phase = m.status === 'night' ? `🌙 Ночь ${m.round}` : m.status === 'day' ? `☀️ День ${m.round}` : '🏁 Игра окончена';
    const timer = m.status === 'done' ? '' : `<span class="timer" id="mTimer" data-end="${Date.now() + m.left * 1000}">${m.left} с</span>`;
    let prompt = '';
    let pick = null;
    if (m.status === 'done') {
      const won = m.winners.includes(me);
      prompt = `<div class="banner ${won ? 'win' : 'lose'}">${m.winner === 'mafia' ? '🔪 Победила мафия' : '🎉 Победили мирные'}<div class="muted" style="font-size:14px;font-weight:500">${won ? '+' + m.reward + ' см тебе' : 'В этот раз не повезло'}</div></div>`;
    } else if (!m.is_alive) {
      prompt = '<div class="muted" style="text-align:center;margin:8px 0">Ты выбыл и наблюдаешь за игрой 👻</div>';
    } else if (m.status === 'night') {
      if (m.needs_night && !m.acted) {
        const [kind, icon, text] = MAFIA_ACTION[r.key];
        prompt = `<div class="muted" style="text-align:center;margin:8px 0"><b>${text}</b></div>`;
        pick = (p) => p.alive && !(r.key === 'mafia' && (p.mate || p.id === me)) && !(r.key === 'sheriff' && p.id === me) ? { act: `${kind}:${p.id}`, icon } : null;
      } else if (m.needs_night) {
        const t = byId[m.my_pick];
        prompt = `<div class="muted" style="text-align:center;margin:8px 0">Выбор принят${t ? ': ' + esc(t.name) : ''}. Ждём остальных…</div>`;
      } else prompt = '<div class="muted" style="text-align:center;margin:8px 0">Город спит 😴 Ждём утра…</div>';
    } else {
      prompt = `<div class="muted" style="text-align:center;margin:8px 0"><b>Обсуждайте в чате и голосуйте за казнь</b>${m.acted ? '<br>Твой голос принят' : ''}</div>`;
      pick = (p) => p.alive && p.id !== me ? { act: `vote:${p.id}`, icon: '🗳' } : null;
    }
    const players = m.players.map((p) => {
      const a = pick && !(m.status === 'day' && m.acted && false) ? pick(p) : null;
      const chosen = (m.status === 'day' && m.my_vote === p.id) || (m.status === 'night' && m.my_pick === p.id);
      const tag = [p.id === me ? 'ты' : '', p.mate ? 'напарник' : '', p.role && (m.status === 'done' || p.id === me) ? (m.status === 'done' ? roleName(p.role) : '') : '', m.status === 'day' && p.voted ? 'проголосовал' : ''].filter(Boolean).join(' · ');
      return `<div class="list-item ${p.alive ? '' : 'dead'}"><div class="av">${p.alive ? (p.role === 'mafia' ? '🔪' : '🙂') : '💀'}</div>
        <div class="grow"><div class="name">${esc(p.name)}</div><div class="muted">${esc(tag)}</div></div>
        ${a ? `<button class="btn sm ${chosen ? 'ghost' : ''}" data-a="mafia-act" data-v="${a.act}">${a.icon}${chosen ? ' ✓' : ''}</button>` : chosen ? '✓' : ''}</div>`;
    }).join('');
    const skip = m.status === 'day' && m.is_alive ? `<button class="btn ghost" style="margin-top:6px" data-a="mafia-act" data-v="vote:0">🤷 Воздержаться${m.my_vote === 0 ? ' ✓' : ''}</button>` : '';
    const checks = (m.checks || []).length ? `<h2>Проверки комиссара</h2>${m.checks.map((c) => `<div class="muted">Ночь ${c.round}: ${esc(c.name)} — ${c.mafia ? '🔪 МАФИЯ' : '🙂 мирный'}</div>`).join('')}` : '';
    const leave = m.status === 'done' ? '<button class="btn" style="margin-top:12px" data-a="mafia-leave">В лобби</button>' : '<button class="btn ghost" style="margin-top:12px" data-a="mafia-leave">Сбежать из игры</button>';
    body = `${roleCard}<div class="banner" style="margin:8px 0">${phase} ${timer}</div>${prompt}${players}${skip}${checks}${leave}`;
  }
  const log = m.log.length ? `<div class="log">${m.log.map((l) => `<div>${esc(l)}</div>`).join('')}</div>` : '';
  view.innerHTML = `<div class="screen"><div class="screen-head"><span class="eyebrow">🕵️ мафия</span><h1>Мафия</h1><p>${m.status === 'open' ? 'Собираем город: от 4 игроков.' : m.status === 'done' ? 'Партия окончена.' : 'Роли розданы. Город засыпает — просыпается мафия.'}</p></div>${body}${log}</div>`;
}

function roleName(key) { return ({ mafia: 'мафия', doctor: 'доктор', sheriff: 'комиссар', town: 'мирный' })[key] || ''; }

setInterval(() => {
  const el = document.getElementById('mTimer');
  if (el) el.textContent = Math.max(0, Math.round((Number(el.dataset.end) - Date.now()) / 1000)) + ' с';
}, 500);


// ───────── главная ─────────
function greeting() {
  const h = new Date().getHours();
  return h < 5 ? 'Доброй ночи' : h < 12 ? 'Доброе утро' : h < 18 ? 'Добрый день' : 'Добрый вечер';
}

function homeSkeleton() {
  return `<div class="screen"><div class="sk hero"></div>
    <div class="sk line w40"></div><div class="sk row"></div><div class="sk row"></div>
    <div class="sk line w60" style="margin-top:18px"></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:9px"><div class="sk tile"></div><div class="sk tile"></div><div class="sk tile"></div><div class="sk tile"></div></div>
    <div class="sk line w40" style="margin-top:18px"></div><div class="sk row"></div><div class="sk row"></div></div>`;
}

async function loadHome() {
  const [b, r, p, rp] = await Promise.allSettled([
    loadBoard(),
    api(`/api/chat/${S.chat}/rooms`),
    api(`/api/chat/${S.chat}/poker`),
    api(`/api/chat/${S.chat}/rp`),
  ]);
  if (b.status === 'rejected') throw b.reason;
  S.home = {
    rooms: r.status === 'fulfilled' ? r.value : null,
    poker: p.status === 'fulfilled' ? p.value : null,
    rp: rp.status === 'fulfilled' ? rp.value : null,
  };
  updateHeader();
}

async function viewHome() {
  stopPoll(); clearInterval(S.rpTimer);
  if (!S.home) view.innerHTML = homeSkeleton();
  try { await loadHome(); }
  catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  renderHome();
}

function homeLiveHtml() {
  const h = S.home || {};
  const rows = [];
  const room = h.rooms && h.rooms.mine;
  if (room) rows.push({ ic: '⚔️', title: room.status === 'open' ? 'Дуэль: ждём соперника' : 'Дуэль идёт', sub: `ставка ${room.stake} см · ${room.status === 'open' ? 'бот позвал чат' : 'твой ход или ход соперника'}`, a: 'open-act', v: 'arena', label: 'Продолжить' });
  const mm = h.rooms && h.rooms.mafia_mine;
  if (mm) rows.push({ ic: '🕵️', title: 'Ты в мафии', sub: mm.status === 'open' ? `собираемся · ${mm.players.length} игроков` : mm.status === 'night' ? 'ночь в городе' : mm.status === 'day' ? 'день и голосование' : 'партия окончена', a: 'open-act', v: 'mafia', label: 'К игре' });
  const boss = h.tasks && h.tasks.boss && h.tasks.boss.boss;
  if (boss && boss.status === 'alive') {
    const canHit = (h.tasks.boss.hits_left || 0) > 0;
    rows.push({ ic: boss.emoji || '👹', title: `Босс: ${boss.name}`, sub: `${boss.hp} / ${boss.max_hp} HP · осталось ${hoursLeft(boss.ends_in)}`, a: canHit ? 'hit' : 'goto', v: canHit ? 'boss' : 'tasks', label: canHit ? 'Ударить' : 'К боссу', bad: canHit });
  }
  if (!rows.length) {
    return `<div class="empty-live"><div class="e-ic">🌙</div><b>В районе тихо</b><p>Самое время начать движ — чат подхватит.</p>
      <div class="btn-row" style="max-width:280px;margin:0 auto"><button class="btn sm" data-a="open-act" data-v="arena">⚔️ Дуэль</button><button class="btn sm ghost" data-a="open-act" data-v="mafia">🕵️ Мафия</button></div></div>`;
  }
  return rows.map((r) => `<div class="live-card"><div class="live-ic">${r.ic}</div>
    <div class="live-meta"><span class="live-tag">сейчас</span><b>${esc(r.title)}</b><span>${esc(r.sub)}</span></div>
    <button class="btn sm ${r.bad ? 'bad' : ''}" data-a="${r.a}" data-v="${r.v}">${esc(r.label)}</button></div>`).join('');
}

const HOME_ACTS = [
  { id: 'casino', ic: '🎰', title: 'Казино', c1: '#ffb648', c2: '#ff6b6b', sub: () => 'Колесо и слоты' },
  { id: 'arena', ic: '⚔️', title: 'Дуэли', c1: '#ff6b6b', c2: '#d94b8a', sub: (h) => (h.rooms && h.rooms.open.length) ? `${h.rooms.open.length} вызов(а)` : '1×1 · 3 см' },
  { id: 'mafia', ic: '🕵️', title: 'Мафия', c1: '#8f6bff', c2: '#c66bff', sub: (h) => (h.rooms && h.rooms.mafia_open.length) ? `${h.rooms.mafia_open.length} лобби` : '4–12 игроков' },
  { id: 'work', ic: '💼', title: 'Район', c1: '#4c8dff', c2: '#56c8e8', sub: (h) => { const m = h.rp && h.rp.me; return m ? `⚡ ${Math.round(m.energy)} · ${moneyFmt(m.money)} ₽` : 'Работа, дела, ОПГ'; } },
];

function homeActsHtml() {
  const h = S.home || {};
  return `<div class="act-grid">${HOME_ACTS.map((a) => {
    const live = (a.id === 'arena' && h.rooms && h.rooms.mine) || (a.id === 'mafia' && h.rooms && h.rooms.mafia_mine) || (a.id === 'poker' && h.poker && h.poker.mine);
    return `<button class="act" style="--c1:${a.c1};--c2:${a.c2}" data-a="open-act" data-v="${a.id}">
      <span class="act-ic">${a.ic}</span><span class="act-title">${a.title}</span><span class="act-sub">${esc(a.sub(h))}</span>
      ${live ? '<span class="dot"></span>' : '<span class="act-go">→</span>'}</button>`;
  }).join('')}</div>`;
}

function questPreviewHtml() {
  const q = S.home && S.home.tasks && S.home.tasks.quests;
  if (!q) return '<div class="card muted">Задания временно недоступны.</div>';
  const rows = q.items.map((i) => `<div class="quest-row ${i.ready ? 'ready' : ''} ${i.claimed ? 'done' : ''}">
      <div class="q-ic">${i.icon}</div>
      <div class="q-main"><div class="q-title">${esc(i.title)}</div>
        <div class="q-meta"><div class="q-bar"><i style="width:${Math.round((i.have / i.need) * 100)}%"></i></div><span class="q-reward">${i.have}/${i.need} · +${i.reward} см</span></div></div>
      ${i.claimed ? '<span class="badge good">✓</span>' : i.ready ? `<button class="btn xs" data-a="quest" data-v="${i.id}">Забрать</button>` : `<span class="badge">+${i.reward}</span>`}
    </div>`).join('');
  const bonus = `<div class="quest-row ${q.bonus_ready ? 'ready' : ''} ${q.bonus_claimed ? 'done' : ''}">
      <div class="q-ic">🎁</div><div class="q-main"><div class="q-title">Бонус за все три</div><div class="q-meta"><span class="q-reward">+${q.bonus} см сверху</span></div></div>
      ${q.bonus_claimed ? '<span class="badge good">✓</span>' : q.bonus_ready ? `<button class="btn xs" data-a="quest" data-v="bonus">Забрать</button>` : '<span class="badge">+3</span>'}
    </div>`;
  return `<div class="card" style="padding:9px">${rows}${bonus}</div>`;
}

function renderHome() {
  const b = S.board;
  if (!b) { view.innerHTML = '<div class="empty">Не удалось загрузить район.</div>'; return; }
  const me = b.me;
  const h = S.home || {};
  const rp = h.rp && h.rp.me;
  const ready = isDailyReady(me);
  const char = (rp && rp.char && rp.char.text) || me.emoji || (me.username || '?').replace('@', '').charAt(0).toUpperCase();
  const jail = rp && rp.jail_left > 0;
  const chips = [
    `🔥 ${me.streak} дн.`,
    `⭐ ${me.rep}`,
    rp ? `💵 ${moneyFmt(rp.money)} ₽` : '',
    rp ? `⚡ ${Math.round(rp.energy)}/${rp.max_energy}` : '',
    jail ? `🚔 ${fmtLeft(rp.jail_left)}` : '',
  ].filter(Boolean).map((x) => `<span class="hero-chip">${x}</span>`).join('');

  view.innerHTML = `<div class="screen">
    <div class="hero">
      <svg class="skyline" viewBox="0 0 400 100" preserveAspectRatio="none" aria-hidden="true">
        <path fill="rgba(255,255,255,.55)" d="M0 100V74h18V58h14v16h12V66h22v12h10V48h18v30h12V70h20v12h12V54h16v24h14V62h18v16h12V44h20v34h14V72h18v14h12V60h22v18h10V80h18v20z"/>
        <path fill="rgba(255,209,102,.55)" d="M60 78h4v6h-4zm42-18h4v6h-4zm64 20h4v6h-4zm58-26h4v6h-4zm52 32h4v6h-4zm50-16h4v6h-4z"/>
      </svg>
      <div class="hero-top">
        <div class="hero-ava ${[...char].length > 1 ? 'multi' : ''}">${esc(char)}</div>
        <div class="grow"><div class="hero-hello">${greeting()}</div><div class="hero-name">${esc(me.username)}</div>
          ${me.title ? `<div class="hero-title"><span class="badge">${esc(me.title)}</span></div>` : ''}</div>
        <div class="hero-size"><b>${me.size}</b><span>сантиметров</span></div>
      </div>
      <div class="hero-chips">${chips}</div>
      <div class="hero-bonus"><span class="b-ic">🎁</span>
        <div class="grow"><b>${ready ? 'Бонус ждёт' : 'Бонус уже твой'}</b><span>${ready ? `Серия ${me.streak} дн. · забери свои см` : 'Возвращайся завтра за новым'}</span></div>
        ${ready ? '<button class="btn sm gold" data-a="daily">Забрать</button>' : '<span class="badge good">✓</span>'}
      </div>
    </div>

    <div class="sec-head"><h2>Активности</h2><button class="sec-action" data-a="goto" data-v="games">Все игры</button></div>
    ${homeActsHtml()}
    <div class="center muted" style="padding:18px 0 4px">Район · твоя игра</div>
  </div>`;
  updateHeader();
}

// ───────── вкладка «Район» ─────────
const money = (n) => Number(n).toLocaleString('ru-RU') + ' ₽';

function fmtLeft(sec) {
  const m = Math.floor(sec / 60), s = sec % 60;
  return m >= 60 ? Math.floor(m / 60) + ' ч ' + (m % 60) + ' мин' : m + ':' + String(s).padStart(2, '0');
}

function barHtml(b, jailed) {
  const size = S.board && S.board.me ? S.board.me.size : 0;
  const out = b.key === 'out';
  const btn = (kind, it, extra) => `<button class="btn sm" data-a="bar-buy" data-id="${it.id}" ${out || size < it.price || (extra === 'hang' && b.key !== 'hang') || (extra === 'snack' && b.level < 1) ? 'disabled' : ''}>${it.price} см</button>`;
  const row = (it, sub, kind, extra) => `<div class="list-item"><div class="av">${it.icon}</div><div class="grow"><div class="name">${esc(it.title)}</div><div class="muted">${sub}</div></div>${btn(kind, it, extra)}</div>`;
  const left = b.left ? ` · ещё ${Math.max(1, Math.round(b.left / 60))} мин` : '';
  return `<h2>🍻 Бар</h2>
    <div class="card"><div class="row"><div style="font-size:34px">${b.icon}</div><div class="grow"><b>${esc(b.name)}</b>${left}<div class="muted">градусов: ${b.level} / 100 · выпито: ${b.drinks}</div></div></div>
      <div class="bar" style="height:10px;margin-top:8px"><i style="width:${b.level}%"></i></div>
      <div class="muted" style="margin-top:8px">Чем выше градус, тем сильнее заплетается речь в чате. На похмелье энергия восстанавливается медленнее, а под градусом дела проваливаются чаще.</div></div>
    ${b.config.drinks.map((it) => row(it, `+${it.proof} градусов`, 'drink')).join('')}
    <div class="muted" style="margin:8px 0 4px">Закуски снижают градус</div>
    ${b.config.snacks.map((it) => row(it, `−${it.cut} градусов`, 'snack', 'snack')).join('')}
    <div class="muted" style="margin:8px 0 4px">От похмелья</div>
    ${b.config.cures.map((it) => row(it, 'снимает похмелье', 'cure', 'hang')).join('')}`;
}

function gangExtraHtml(g, m, jailed, tab) {
  const dur = (s) => `${Math.floor(s / 3600)} ч ${Math.floor(s % 3600 / 60)} мин`;
  let out = '';
  const w = m && g.war;
  if (w) {
    const tot = Math.max(1, w.mine + w.theirs);
    out += `<div class="card"><b>⚔️ Война с «${esc(w.enemy)}»</b><div class="muted">Осталось ${dur(w.left)}. Очки за дела +3, налёт банды +25, дуэль +5, рейд +10.</div>
      <div style="display:flex;height:12px;border-radius:8px;overflow:hidden;margin:10px 0;background:var(--bg)"><div style="width:${Math.round(100 * w.mine / tot)}%;background:var(--accent, #4caf50)"></div></div>
      <div class="row"><div class="grow"><b>${w.mine}</b> наши</div><div><b>${w.theirs}</b> враги</div></div>
      <button class="btn bad" style="margin-top:8px" data-a="rp" data-p="gang/raid" ${jailed || w.raid_left ? 'disabled' : ''}>⚔️ Рейд на врага (⚡ −${w.energy})</button>
      ${w.raid_left ? `<div class="muted" style="margin-top:6px">Следующий рейд через ${Math.ceil(w.raid_left / 60)} мин.</div>` : ''}</div>`;
  } else if (m && m.is_boss && g.war_cd) {
    out += `<div class="card muted">Банде нужно отдохнуть после войны: ещё ${Math.ceil(g.war_cd / 3600)} ч.</div>`;
  }
  const warOut = out;
  out = '';
  const gl = g.gangs.map((x) => `<div class="list-item"><div class="av">${x.icon || (x.is_ai ? '🧓' : '🕴')}</div><div class="grow"><div class="name">${esc(x.name)}${x.mine ? ' (твоя)' : ''} ${x.terr}</div><div class="muted">${x.n} чел. · побед ${x.wins}, поражений ${x.losses}</div></div>
    ${m && m.is_boss && !x.mine && !g.war && !g.war_cd ? `<button class="btn sm bad" data-a="gang-act" data-p="gang/war" data-g="${x.id}" data-c="Объявить войну «${esc(x.name)}»? Она длится 12 часов.">Война</button>` : ''}
    ${m && m.is_boss && !x.mine && !(m.pact_info && m.pact_info.busy.includes(x.id)) ? `<button class="btn sm" data-a="p3" data-p="gang/pact" data-j='{"gid":${x.id}}' data-c="Предложить союз «${esc(x.name)}»?">🤝 Союз</button>` : ''}</div>`).join('');
  const tl = g.terr.map((t) => `<div class="list-item"><div class="av">${t.icon}</div><div class="grow"><div class="name">${esc(t.name)}</div><div class="muted">${t.owner ? 'Контролирует «' + esc(t.owner_name) + '»' : 'Свободен'} · +${t.income} ₽/ч каждому в банде</div></div></div>`).join('');
  out += `<h2>Банды района</h2>${gl}${m && m.p5 ? '' : `<h2>Районы города</h2>${tl}<div class="muted" style="margin-top:6px">Победитель войны получает дань и захватывает район: тот приносит деньги каждый час.</div>`}`;
  const listOut = out;
  const ratOut = ratHtml(g, m, jailed);
  if (tab === 'ops') return warOut;
  if (tab === 'map') return listOut;
  if (tab === 'spy') return ratOut;
  return warOut + listOut + ratOut;
}

function gangDur(s) { return `${Math.floor(s / 3600)} ч ${Math.floor(s % 3600 / 60)} мин`; }

function duesHtml(m) {
  const du = m.dues || {};
  return `<div class="card"><b>🧾 Обязательный взнос</b>
    <div class="muted">${money(du.amount)} в общак раз в сутки. Не внесёшь через 6 часов после срока: авторитет −15 и 50% шанс, что тебя побьют (20 минут без дел, как в тюрьме).</div>
    <div class="muted" style="margin-top:4px">${du.overdue ? '⚠️ Срок прошёл, плати скорее!' : 'Срок через ' + gangDur(du.left) + (du.can_pay ? '' : ' (платить можно за 12 часов до срока)')}</div>
    <button class="btn sm" style="margin-top:8px" data-a="rp" data-p="gang/dues" ${du.can_pay ? '' : 'disabled'}>💰 Внести ${money(du.amount)}</button></div>`;
}

function plotHtml(m, jailed) {
  if (m.my_role === 'boss') return m.plot_hint ? '<div class="card">🕵 В банде что-то затевается. Присмотрись к своим: у заговорщиков есть шаги и пауза между ними.</div>' : '';
  const pl = m.plot;
  if (pl) {
    const steps = pl.steps.map((x, i) => `<div class="feed"><span>${x.done ? '✅' : i === pl.step ? '👉' : '▫️'}</span><div class="grow"><b>${esc(x.title)}</b><div class="muted">${esc(x.desc)}</div></div></div>`).join('');
    return `<div class="card"><b>🕶 Заговор: ${esc(pl.method)}</b>${steps}
      ${pl.status === 'vote' ? '<div class="muted" style="margin-top:6px">Банда голосует, смотри карточку разборок.</div>'
        : `<div class="row" style="margin-top:8px"><button class="btn sm bad" data-a="rp" data-p="gang/plot/step" ${pl.wait || jailed ? 'disabled' : ''}>Сделать шаг</button><button class="btn sm ghost" data-a="rp" data-p="gang/plot/abort">Свернуть</button></div>${pl.wait ? `<div class="muted" style="margin-top:6px">Следующий шаг через ${gangDur(pl.wait)}</div>` : ''}`}</div>`;
  }
  if (!(m.plot_methods || []).length) return '';
  const rows = m.plot_methods.map((x) => `<div class="list-item"><div class="grow"><div class="name">${esc(x.title)}</div><div class="muted">${esc(x.desc)}</div></div><button class="btn sm bad" data-a="gang-plot" data-v="${x.key}" ${m.plot_cd || jailed ? 'disabled' : ''}>Начать</button></div>`).join('');
  return `<div class="card"><b>🕶 Свергнуть босса без авторитета</b><div class="muted">Три способа, у каждого свои шаги с паузами и риском. Раскроют: штраф 1000 ₽, побои, авторитет −30. Получится: ты новый босс.${m.plot_cd ? ' После провала нужно лежать на дне ещё ' + gangDur(m.plot_cd) + '.' : ''}</div></div>${rows}`;
}

function rheistHtml(m, jailed) {
  const r = m.rheist;
  if (!r) return '';
  const slots = r.slots.map((x) => `<div class="feed"><span>${x.who ? '✅' : '⬜'}</span><div class="grow">${esc(x.title)}${x.who ? ' · ' + esc(x.who) : ''}</div></div>`).join('');
  return `<div class="card"><b>🕴 Налёт с ролями</b>
    <div class="muted">Босс планирует, зам вскрывает, холоп стоит на шухере. Добыча ×${r.mult}, шанс выше обычного налёта, ⚡ −${r.energy} каждому. Раз в 3 часа.</div>
    ${r.plan ? `${slots}<div class="muted">Осталось ${Math.ceil(r.left / 60)} мин.</div><button class="btn sm" style="margin-top:6px" data-a="rp" data-p="gang/rheist/join" ${r.can_join && !jailed ? '' : 'disabled'}>Занять свою роль</button>`
      : `<button class="btn sm" style="margin-top:8px" data-a="rp" data-p="gang/rheist" ${r.can_plan && !jailed ? '' : 'disabled'}>Составить план</button>${r.cd ? `<div class="muted" style="margin-top:4px">Отдых ещё ${gangDur(r.cd)}</div>` : ''}${r.can_plan ? '' : '<div class="muted" style="margin-top:4px">План составляют босс и замы.</div>'}`}</div>`;
}

function meetHtml(m) {
  const t = m.meet;
  if (!t) return '';
  return `<div class="card"><b>🕯 Тайная сходка</b><div class="muted">Раз в неделю её созывает босс. Пришедшим: +30 энергии, +10 авторитета и банкет из общака (до 100 ₽). Кто пришёл, видит только босс.</div>
    ${t.open ? `<div class="muted" style="margin-top:6px">Идёт сходка: осталось ${gangDur(t.left)}, пришло ${t.count}.</div><button class="btn sm" style="margin-top:6px" data-a="rp" data-p="gang/meet/join" ${t.came ? 'disabled' : ''}>${t.came ? 'Ты уже на сходке' : 'Прийти на сходку'}</button>${(t.attendees || []).length ? `<div class="muted" style="margin-top:6px">Пришли: ${t.attendees.map(esc).join(', ')}</div>` : ''}`
      : m.my_role === 'boss' ? `<button class="btn sm" style="margin-top:8px" data-a="rp" data-p="gang/meet" ${t.can_call ? '' : 'disabled'}>Созвать сходку</button>${t.wait ? `<div class="muted" style="margin-top:4px">Следующая через ${gangDur(t.wait)}</div>` : ''}`
      : '<div class="muted" style="margin-top:6px">Сейчас сходки нет.</div>'}</div>`;
}

function amnestyHtml(m) {
  if (m.my_role !== 'boss') return m.probation_left ? `<div class="card">🧪 Испытательный срок: ещё ${gangDur(m.probation_left)}. Половина доли идёт в общак, заговоры и разборки закрыты.</div>` : '';
  const a = m.amnesty || {};
  const reps = (m.repents || []).map((x) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">🙏 ${esc(x.name)}</div><div class="muted">внёс ${money(x.paid)} и просит принять обратно</div></div>
      <button class="btn sm" data-a="gang-repent" data-u="${x.id}" data-v="1">Принять</button><button class="btn sm ghost" data-a="gang-repent" data-u="${x.id}" data-v="0">Отказать</button></div>`).join('');
  return `<div class="card"><b>🕊 Амнистия</b><div class="muted">Раз в неделю за ${money(a.cost)} из общака: все выходят из тюрьмы и от побоев, долги и испытательные сроки обнуляются.</div>
    <button class="btn sm" style="margin-top:8px" data-a="gang-act" data-p="gang/amnesty" data-c="Объявить амнистию за ${a.cost} ₽ из общака?" ${a.can ? '' : 'disabled'}>Объявить амнистию</button>${a.wait ? `<div class="muted" style="margin-top:4px">Следующая через ${gangDur(a.wait)}</div>` : ''}</div>${reps ? '<h2>Просят принять обратно</h2>' + reps : ''}`;
}

function copHtml(g, m, jailed) {
  const c = g.cop;
  if (!c) return '';
  let acts = '';
  if (m) {
    acts = c.agent
      ? `<div class="row" style="margin-top:8px"><button class="btn sm bad" data-a="rp" data-p="gang/agent/report" ${c.report_wait || m.my_role === 'boss' || jailed ? 'disabled' : ''}>🕶 Донесение</button><button class="btn sm ghost" data-a="rp" data-p="gang/agent/quit">Выйти из агентов</button></div>${c.report_wait ? `<div class="muted" style="margin-top:4px">Следующее донесение через ${gangDur(c.report_wait)}</div>` : ''}`
      : `<button class="btn sm" style="margin-top:8px" data-a="rp" data-p="gang/agent/enlist" ${m.my_role === 'boss' ? 'disabled' : ''}>🕶 Завербоваться в информаторы</button>`;
  }
  return `<div class="card"><b>🚓 Полиция: ${esc(c.rank)} · ${c.rep} очк.${c.next ? ' (до следующего звания ' + (c.next - c.rep) + ')' : ''}</b>
    <div class="muted">Очки полицейского растут от работы полицейским, доносов и донесений агента и не связаны с авторитетом в банде. Агент: розыск спадает вдвое быстрее. Опер (30): тюрьма короче на 30%, на работе полицейским +20%. Майор (60): взятка вдвое дешевле.</div>
    ${m ? '<div class="muted" style="margin-top:4px">Двойной агент живёт в банде, но за деньги стучит полиции. Донесение раз в 3 часа: +300–600 ₽ и +2 очка, но 12% риск быть вычисленным (изгнание, штраф, очки −7). Босс агентом быть не может.</div>' : ''}${acts}</div>`;
}

function repentHtml(g) {
  const r = g.repent;
  if (!r || !(r.options || []).length) return '';
  return `<div class="card"><b>🙏 Раскаяние</b><div class="muted">Изгнали за донос? Можно проситься обратно: ${money(r.cost)} (двойной штраф), решает босс. Примут: 48 часов испытательного срока.</div>
    ${r.options.map((x) => `<button class="btn sm" style="margin-top:6px;margin-right:6px" data-a="gang-act" data-p="gang/repent" data-g="${x.id}" data-c="Просить прощения у «${esc(x.name)}» за ${r.cost} ₽?">Просить место в «${esc(x.name)}»</button>`).join('')}</div>`;
}

function p3Html(d) {
  const p3 = (d.gang && d.gang.p3) || {};
  let out = '';
  const v = p3.visit;
  if (v) {
    out += `<div class="card" style="border:2px solid #e5484d"><b>${esc(v.title)}</b> · осталось ${Math.max(1, Math.ceil(v.left / 60))} мин
      <div class="muted" style="margin-top:4px">${esc(v.text)}</div>
      <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px">${v.options.map((o) => `<button class="btn sm ${o.key === 'bribe' ? '' : 'ghost'}" data-a="p3" data-p="visit" data-j='{"vid":${v.id},"choice":"${o.key}"}' ${o.disabled ? 'disabled' : ''}>${esc(o.label)}</button>`).join('')}</div></div>`;
  } else if (p3.omon_warn) {
    out += '<div class="banner lose" style="margin:0 0 10px">🚨 К вашей банде едет ОМОН. Босс решает, как встречать: откупиться, драться или звать адвоката.</div>';
  }
  const gs = p3.guest;
  if (gs) {
    out += `<div class="card" style="border:2px solid #f5a623"><div class="row"><div style="font-size:34px">${gs.icon}</div><div class="grow"><b>Гость района: ${esc(gs.name)}</b>
      <div class="muted">${esc(gs.intro)} Осталось ${Math.max(1, Math.ceil(gs.left / 60))} мин.</div></div></div>
      ${gs.taken ? '<div class="muted" style="margin-top:8px">Ты уже принял его предложение.</div>' : gs.offers.map((o) => `<div class="list-item" style="margin-top:6px"><div class="grow"><div class="name">${esc(o.title)}</div><div class="muted">${esc(o.desc)}</div></div>
        <button class="btn sm" data-a="p3" data-p="guest" data-j='{"offer":"${o.key}"}'>${o.cost ? money(o.cost) : 'Взять'}</button></div>`).join('')}</div>`;
  }
  return out;
}

function lawyerHtml(d) {
  const p3 = (d.gang && d.gang.p3) || {};
  const l = p3.lawyer;
  if (!l) return '';
  const me = d.me;
  const jailed = me.jail_left > 0;
  const services = l.active ? `<div class="muted" style="margin-top:6px">Адвокат при деле ещё ${gangDur(l.left)}.</div>
    <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px">
      <button class="btn sm" data-a="p3" data-p="lawyer/release" ${jailed && !l.release_wait ? '' : 'disabled'}>🔓 Вытащить из тюрьмы · ${money(l.release_cost)}</button>
      <button class="btn sm" data-a="p3" data-p="lawyer/clear" ${me.wanted > 0 && !l.clear_wait ? '' : 'disabled'}>🧹 Снять 2⭐ розыска · ${money(l.clear_cost)}</button></div>
    ${l.release_wait ? `<div class="muted" style="margin-top:4px">Освобождение снова через ${gangDur(l.release_wait)}</div>` : ''}${l.clear_wait ? `<div class="muted" style="margin-top:4px">Чистка дела снова через ${gangDur(l.clear_wait)}</div>` : ''}` : '';
  return `<h2>Адвокат</h2><div class="card"><b>⚖️ Адвокат на ${l.hours} часов · ${money(l.price)}</b>
    <div class="muted">Налоговая берёт вдвое меньше, срок за дела короче на 40%, можно оспорить проверку, а ОМОН по твоему делу не приходит без бумаг. Услуги: освобождение из тюрьмы, чистка розыска.</div>
    ${services}
    <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="lawyer/hire" ${me.money < l.price ? 'disabled' : ''}>${l.active ? 'Продлить на сутки' : 'Нанять адвоката'}</button></div>`;
}

function pactsHtml(g, m) {
  const pi = m.pact_info || {};
  const list = m.pacts || [];
  const boss = m.is_boss;
  const rows = list.map((x) => {
    const incoming = x.status === 'proposed' && !x.mine;
    const info = x.status === 'proposed' ? (x.mine ? 'ждём ответа босса' : 'предлагает союз') : `союз ещё ${gangDur(x.left)}${x.ally_war ? ' · союзник воюет' : ''}`;
    let acts = '';
    if (boss) {
      if (incoming) acts = `<button class="btn sm" data-a="p3" data-p="gang/pact/answer" data-j='{"pid":${x.id},"accept":true}'>Принять</button><button class="btn sm ghost" data-a="p3" data-p="gang/pact/answer" data-j='{"pid":${x.id},"accept":false}'>Отказать</button>`;
      else if (x.status === 'active') acts = `${x.ally_war ? `<button class="btn sm" data-a="p3" data-p="gang/pact/aid" data-j='{"pid":${x.id}}' ${pi.aid_wait ? 'disabled' : ''}>🎁 Помочь · ${money(pi.aid_cost)}</button>` : ''}
        <button class="btn sm bad" data-a="p3" data-p="gang/pact/stab" data-c="Ударить в спину «${esc(x.other)}»? Начнётся война, а вашу банду на 3 дня сочтут предателями." data-j='{"pid":${x.id}}' ${x.can_stab ? '' : 'disabled'}>🗡 Удар в спину</button>
        <button class="btn sm ghost" data-a="p3" data-p="gang/pact/break" data-c="Мирно расторгнуть союз?" data-j='{"pid":${x.id}}'>Расторгнуть</button>`;
      else acts = `<button class="btn sm ghost" data-a="p3" data-p="gang/pact/break" data-j='{"pid":${x.id}}'>Отозвать</button>`;
    }
    return `<div class="list-item" style="flex-wrap:wrap"><div class="av">${x.icon}</div><div class="grow"><div class="name">${esc(x.other)}</div><div class="muted">${info}</div></div>
      ${acts ? `<div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap">${acts}</div>` : ''}</div>`;
  }).join('');
  return `<div class="card"><b>🤝 Союзы</b>
    <div class="muted">До ${pi.max} союзов на ${pi.hours} ч. Доход районов +${pi.bonus}% за каждый, в войне союзник даёт очки. Позвать в союз можно в списке «Банды района». А ещё можно ударить в спину: старт войны ${pi.stab_points}:0 и кусок общака, но 3 дня вас никто не любит, а авторитет падает.</div>
    ${pi.traitor_left ? `<div class="muted" style="margin-top:4px">⚠️ Вас считают предателями ещё ${gangDur(pi.traitor_left)}.</div>` : ''}
    ${rows || '<div class="muted" style="margin-top:6px">Союзов пока нет.</div>'}</div>`;
}

function projectsHtml(m) {
  const rows = (m.projects || []).map((x) => `<div class="list-item"><div class="av">${x.icon}</div><div class="grow"><div class="name">${esc(x.title)} · ${x.level}/3</div>
      <div class="muted">${esc(x.desc)}${x.now ? ' · сейчас: ' + esc(x.now) : ''}${x.next ? ' · дальше: ' + esc(x.next) : ''}</div></div>
      ${m.is_boss ? (x.level < 3 ? `<button class="btn sm" data-a="p3" data-p="gang/project" data-j='{"key":"${x.key}"}' ${(m.treasury || 0) >= x.cost ? '' : 'disabled'}>${money(x.cost)}</button>` : '<span class="muted">макс.</span>') : ''}</div>`).join('');
  return `<div class="card"><b>🏗 Общие проекты</b><div class="muted">Копите общак и стройте: деньги списываются из общака, бонусы действуют на всю банду. Строит босс.</div>${rows}</div>`;
}


function p4Html(d) {
  const p4 = (d.gang && d.gang.p4) || {};
  const a = p4.attack;
  if (a) {
    return `<div class="card" style="border:2px solid #e5484d"><b>${a.icon} ${esc(a.title)}</b> · осталось ${Math.max(1, Math.ceil(a.left / 60))} мин
      <div class="muted" style="margin-top:4px">${esc(a.text)}</div>
      <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px"><button class="btn sm" data-a="p3" data-p="attack" data-j='{"aid":${a.id},"choice":"pay"}'>💸 Откупиться · ${money(a.price)}</button>
      <button class="btn sm bad" data-a="p3" data-p="attack" data-j='{"aid":${a.id},"choice":"fight"}'>⚔️ Отбиваться (~${a.chance}%)</button></div></div>`;
  }
  return p4.attack_warn ? '<div class="banner lose" style="margin:0 0 10px">⚠️ На вашу банду нападают. Босс решает, откупаться или драться.</div>' : '';
}

function defenseHtml(d) {
  const df = ((d.gang || {}).p4 || {}).defense;
  if (!df) return '';
  const row = (kind, icon, name, title, lvl, nxt) => `<div class="list-item"><div class="av">${icon}</div><div class="grow"><div class="name">${name}: ${esc(title)}</div><div class="muted">уровень ${lvl}/3</div></div>
    ${nxt ? `<button class="btn sm" data-a="p3" data-p="defense" data-j='{"kind":"${kind}"}' ${d.me.money < nxt[1] ? 'disabled' : ''}>${esc(nxt[0])} · ${money(nxt[1])}</button>` : '<span class="muted">макс.</span>'}</div>`;
  return `<h2>Защита от нападений</h2><div class="card"><div class="muted">Банды нападают раз в 3 часа на банды и на одиночек. Шанс отбиться сейчас ~${df.chance}%. Оружие повышает шанс, охрана и адвокат снижают потери. Участников банды защищает охрана банды (проекты).</div>
    ${row('weapon', '🔫', 'Оружие', df.weapon_title, df.weapon, df.weapon_next)}
    ${row('guard', '🚪', 'Охрана', df.guard_title, df.guard, df.guard_next)}
    <div class="list-item"><div class="av">⚖️</div><div class="grow"><div class="name">Адвокат: уровень ${df.lawyer}/3</div><div class="muted">растёт от предоплаты: до 2 суток 1 ур., от 2 суток 2 ур., от 4 суток 3 ур.</div></div></div></div>`;
}

function specsHtml(m, jailed) {
  const s = m.p4;
  if (!s) return '';
  const pick = s.specs.map((x) => `<button class="btn sm ${x.key === s.spec ? '' : 'ghost'}" data-a="p3" data-p="gang/spec" data-j='{"key":"${x.key}"}' ${s.spec_wait && x.key !== s.spec ? 'disabled' : ''}>${x.icon} ${esc(x.title)}</button>`).join('');
  const acts = s.acts.map((a) => {
    const lst = a.need ? (s.targets[a.need] || []) : [null];
    const cd = a.left ? `<div class="muted">снова через ${gangDur(a.left)}</div>` : '';
    const btns = lst.map((t) => `<button class="btn sm" data-a="p3" data-p="gang/spec/act" data-j='{"act":"${a.key}"${t ? ',"arg":' + t.id : ''}}' ${a.left || jailed ? 'disabled' : ''}>${esc(a.title)}${t ? ' · ' + esc(t.name) : ''} (⚡${a.energy})</button>`).join('');
    return `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">${esc(a.title)}</div><div class="muted">${esc(a.desc)}</div>${cd}</div><div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap">${btns || '<span class="muted">нет целей</span>'}</div></div>`;
  }).join('');
  const w = s.watch, pot = s.pot, tr = s.tourney;
  const top = tr.top.map((x, i) => `<div class="feed"><span>${['🥇', '🥈', '🥉'][i]}</span><div class="grow">${esc(x.name)}</div><span class="muted">${x.pts} поб. / ${x.fights}</span></div>`).join('') || '<div class="muted">Пока никто не дрался.</div>';
  const pct = Math.min(100, Math.round(pot.total / pot.goal * 100));
  return `<div class="card"><b>🎭 Специализация</b><div class="muted">Выбери роль в банде (менять можно раз в сутки): у каждой свои умения.</div>
      <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px">${pick}</div>
      ${s.spec ? '<div style="margin-top:8px">' + acts + '</div>' : '<div class="muted" style="margin-top:8px">Выбери специализацию, чтобы открыть умения.</div>'}</div>
    <div class="card"><b>🌙 Ночной дозор</b><div class="muted">Дежурство на ${4} ч: ОМОН реже приезжает, шанс отбиться от нападения выше. Сейчас на посту: ${w.n}.</div>
      <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="gang/watch" ${w.wait || jailed ? 'disabled' : ''}>Встать в дозор (⚡${w.energy})</button>${w.wait ? `<div class="muted" style="margin-top:4px">Снова через ${gangDur(w.wait)}</div>` : ''}</div>
    <div class="card"><b>🍯 Общий котёл недели</b><div class="muted">Скиньтесь на ${money(pot.goal)}: сумма уйдёт в общак, каждому по 300 ₽ и +8 авторитета.</div>
      <div class="bar" style="height:10px;margin-top:8px"><i style="width:${pct}%"></i></div><div class="muted">${money(pot.total)} / ${money(pot.goal)}${pot.done ? ' · ✅ собран' : ''}</div>
      <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px"><button class="btn sm" data-a="p3" data-p="gang/pot" data-j='{"amount":100}' ${pot.done ? 'disabled' : ''}>+100 ₽</button><button class="btn sm" data-a="p3" data-p="gang/pot" data-j='{"amount":500}' ${pot.done ? 'disabled' : ''}>+500 ₽</button><button class="btn sm" data-a="p3" data-p="gang/pot" data-j='{"amount":1000}' ${pot.done ? 'disabled' : ''}>+1000 ₽</button></div></div>
    <div class="card"><b>🥊 Турнир банды</b><div class="muted">Спарринги между своими: победа даёт очко. В воскресенье лучший боец получает 500 ₽ и +15 авторитета.</div>${top}
      <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="gang/tourney" ${tr.wait || jailed ? 'disabled' : ''}>Спарринг (⚡${tr.energy})</button>${tr.wait ? `<div class="muted" style="margin-top:4px">Снова через ${gangDur(tr.wait)}</div>` : ''}</div>`;
}


const GANG_TABS = [['hq', '🏰 Штаб'], ['main', '📋 Банда'], ['ops', '📋 Дела'], ['war', '⚔️ Разборки'], ['eco', '📦 Экономика'], ['map', '🗺 Карта'], ['team', '🎭 Роли'], ['spy', '🕵 Разведка']];

function keepScroll(fn) { const y = window.scrollY; fn(); window.scrollTo(0, y); }

function repHtml(m) {
  const p = m.p5;
  if (!p) return '';
  const bar = (v, c) => `<div class="bar" style="height:8px;margin:4px 0 8px"><i style="width:${v}%;background:${c}"></i></div>`;
  return `<div class="card"><b>⭐ Репутация банды</b>
    <div class="muted">Давление ИИ-банд ×${(p.pressure || 1).toFixed(2)}: чем дольше живёт город и больше районов у ИИ, тем жёстче нападения и войны.</div>
    <div style="margin-top:8px">🤝 Уважение ${p.respect}/100${bar(p.respect, '#3fb950')}😨 Страх ${p.fear}/100${bar(p.fear, '#e5484d')}</div>
    <div class="muted">Уважение 30+: цены сделок +5% и союзы охотнее; 60+: мир с ИИ дешевле на 30%. Страх 30+: нападают реже и приносят дань раз в сутки; 70+: уважение растёт вдвое медленнее. Оба показателя медленно падают.</div></div>`;
}

function stockHtml(m, jailed) {
  const p = m.p5;
  if (!p) return '';
  const can = m.my_role !== 'grunt';
  const items = p.items.map((x) => `<div class="feed"><span>${x.icon}</span><div class="grow">${esc(x.title)}${x.product ? ' <span class="muted">(товар)</span>' : ''}</div><b>${x.qty}</b></div>`).join('');
  const recipes = p.recipes.map((r) => `<div class="list-item"><div class="av">${r.icon}</div><div class="grow"><div class="name">${esc(r.title)}</div>
      <div class="muted">${r.need.map((n) => `${n.icon} ${n.n} (есть ${n.have})`).join(' · ')}</div></div>
      <button class="btn sm" data-a="p3" data-p="gang/craft" data-j='{"recipe":"${r.key}","qty":1}' ${can && r.can ? '' : 'disabled'}>Сделать</button></div>`).join('');
  const buyers = p.buyers.map((b) => `<option value="${b.id}">${b.icon} ${esc(b.name)}</option>`).join('');
  const prices = p.items.filter((x) => x.qty > 0).map((x) => `<div class="muted">${x.icon} ${esc(x.title)}: ${p.buyers.map((b) => `${esc(b.name)} ${(p.prices[String(b.id)] || {})[x.key] || 0}`).join(' · ')}</div>`).join('');
  const sells = p.items.filter((x) => x.qty > 0).map((x) => `<div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap"><div class="grow">${x.icon} ${esc(x.title)}</div>
      <button class="btn sm ghost" data-a="sell" data-i="${x.key}" data-q="1" ${can ? '' : 'disabled'}>1</button><button class="btn sm ghost" data-a="sell" data-i="${x.key}" data-q="10" ${can && x.qty >= 10 ? '' : 'disabled'}>10</button><button class="btn sm" data-a="sell" data-i="${x.key}" data-q="${x.qty}" ${can ? '' : 'disabled'}>всё (${x.qty})</button></div>`).join('');
  return `<div class="card"><div class="row"><b class="grow">📦 Склад</b><button class="btn sm" data-a="p3" data-p="gang/sellall" ${can ? '' : 'disabled'}>💱 Сбыть всё</button></div><div class="muted">Районы сами копят сырьё (3 ед./ч, связанный район +20%). Лимит ${p.cap} на вид. Нападающие могут вынести часть склада.</div>${items}</div>
    <div class="card"><b>🏭 Производство</b><div class="muted">Сырьё превращается в дорогой товар. Работают босс и замы.</div>${recipes}</div>
    <div class="card"><b>💱 Продажа</b><div class="muted">Цены меняются каждый день, у каждого покупателя свой спрос.</div>
      <select id="sellBuyer" style="width:100%;margin-top:8px;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit">${buyers}</select>
      <div style="margin-top:6px">${prices || '<div class="muted">Склад пуст: подождите поставок.</div>'}</div>${sells}</div>`;
}

function dealsHtml(m) {
  const p = m.p5;
  if (!p) return '';
  const boss = m.is_boss;
  const rows = p.deals.map((d) => {
    const what = d.kind === 'trade' ? `${d.mine ? 'продаёшь' : 'продаёт'} ${esc(d.item)} ×${d.qty} за ${money(d.price)}` : d.status === 'active' ? 'мирный договор' : `мир за ${money(d.price)}`;
    const acts = boss && !d.mine && d.status === 'open' ? `<button class="btn sm" data-a="p3" data-p="gang/deal/answer" data-j='{"did":${d.id},"accept":true}'>Принять</button><button class="btn sm ghost" data-a="p3" data-p="gang/deal/answer" data-j='{"did":${d.id},"accept":false}'>Отказать</button>` : '';
    return `<div class="list-item" style="flex-wrap:wrap"><div class="av">${d.kind === 'trade' ? '📜' : '🕊'}</div><div class="grow"><div class="name">${esc(d.other)}</div><div class="muted">${what} · ещё ${gangDur(d.left)}</div></div>${acts ? `<div class="row" style="flex-basis:100%;gap:6px;margin-top:6px">${acts}</div>` : ''}</div>`;
  }).join('') || '<div class="muted">Договоров нет.</div>';
  const ais = p.buyers.filter((b) => b.id > 0);
  const truces = ais.map((a) => `<button class="btn sm" style="margin:4px 6px 0 0" data-a="p3" data-p="gang/truce" data-j='{"gid":${a.id}}' data-c="Купить мир у «${esc(a.name)}» на 24 часа за ~${p.truce_price} ₽ из общака?" ${boss && !p.truces.includes(a.id) ? '' : 'disabled'}>🕊 ${esc(a.name)}${p.truces.includes(a.id) ? ' ✅' : ''}</button>`).join('');
  const gangs = p.others.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
  const items = p.items.filter((x) => x.qty > 0).map((x) => `<option value="${x.key}">${x.icon} ${esc(x.title)} (${x.qty})</option>`).join('');
  const form = p.others.length && boss ? `<div class="card"><b>📜 Предложить договор банде игроков</b>
      <select id="dealGang" style="width:100%;margin-top:8px;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit">${gangs}</select>
      <select id="dealKind" style="width:100%;margin-top:6px;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><option value="trade">Продать товар</option><option value="truce">Предложить мир (платим мы)</option></select>
      <select id="dealItem" style="width:100%;margin-top:6px;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit">${items || '<option value="">нет товара</option>'}</select>
      <div class="row" style="margin-top:6px"><input id="dealQty" type="number" min="1" value="1" placeholder="Кол-во" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><input id="dealPrice" type="number" min="100" value="500" placeholder="Цена" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"></div>
      <button class="btn sm" style="margin-top:8px" data-a="deal-send">Отправить</button></div>` : '';
  return `<div class="card"><b>🤝 Договоры</b><div class="muted">Торговля с игроками и покупка мира у ИИ-банд (на сутки они не нападают и не воюют).</div>${rows}<div style="margin-top:6px">${truces}</div></div>${form}`;
}

function mapHtml(g, m) {
  const p = m.p5;
  if (!p) return '';
  const boss = m.is_boss;
  const rows = p.map.filter((t) => t.mine || t.can_claim || t.can_block);
  const fmt = (t) => {
    const acts = [];
    if (t.can_claim) acts.push(`<button class="btn sm" data-a="p3" data-p="gang/claim" data-j='{"key":"${t.key}"}' ${p.claim_wait ? 'disabled' : ''}>🏴 Занять · ${money(p.claim_cost)}</button>`);
    if (t.can_block) acts.push(`<button class="btn sm bad" data-a="p3" data-p="gang/blockade" data-j='{"key":"${t.key}"}' ${p.block_wait ? 'disabled' : ''}>🚧 Блокада · ${money(p.block_cost)}</button>`);
    if (t.can_fortify && boss) acts.push(`<button class="btn sm ghost" data-a="p3" data-p="gang/fortify" data-j='{"key":"${t.key}"}' data-c="Укрепить район за ${money(p.fort_cost)}?" ${p.fort_wait ? 'disabled' : ''}>🧱 Укрепить</button>`);
    if (Number(t.police || 0) > 0 && (boss || m.my_role === 'deputy')) {
      acts.push(`<button class="btn sm ghost" data-a="p3" data-p="gang/sabotage" data-j='{"key":"${t.key}","mode":"quiet"}' data-c="Подкупить патруль за 500 ₽?">💵 Подкупить</button>`);
      acts.push(`<button class="btn sm bad" data-a="p3" data-p="gang/sabotage" data-j='{"key":"${t.key}","mode":"force"}' data-c="Напасть на патруль? Риск розыска.">🥊 Напасть</button>`);
    }
    return `<div class="list-item" style="flex-wrap:wrap"><div class="av">${t.icon}</div><div class="grow"><div class="name">${esc(t.name)}${t.mine ? ' · наш' : ''}${t.route ? ' 🔗' : ''}</div>
      <div class="muted">${t.owner ? 'Держит «' + esc(t.owner_name) + '»' : 'Свободен'} · контроль ${t.control || 0}% · полиция ${t.police || 0}%</div></div>
      ${acts.length ? `<div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap">${acts.join('')}</div>` : '<span class="muted" style="font-size:11px">нет действий</span>'}</div>`;
  };
  return `<div class="sec-head"><h2>Границы и цели</h2><span class="badge">${rows.length}</span></div>
    ${rows.length ? rows.map(fmt).join('') : '<div class="card muted">Рядом с вами пока нет спорных районов. Карта покажет, куда двигаться дальше.</div>'}`;
}

function opsHtml(m, jailed) {
  const p = m.p5;
  if (!p) return '';
  const o = p.ops;
  if (o.active) {
    const a = o.active;
    const steps = a.stages.map((s, i) => `<div class="feed"><span>${i < a.stage ? '✅' : i === a.stage ? '👉' : '▫️'}</span><div class="grow"><b>${esc(s.title)}</b><div class="muted">лучше всего: ${esc(s.spec)} · ⚡${s.energy}</div></div></div>`).join('');
    return `<div class="card"><b>${a.icon} Большое дело: ${esc(a.title)}</b><div class="muted">Осталось ${gangDur(a.left)} · качество ${a.quality.toFixed(1)} · каждый может сделать до 2 этапов (твоих: ${a.my_done})</div>${steps}
      <button class="btn bad" style="margin-top:8px" data-a="p3" data-p="gang/op/stage" ${jailed || a.my_done >= 2 ? 'disabled' : ''}>Выполнить этап: ${esc(a.stages[a.stage].title)}</button></div>`;
  }
  const list = o.list.map((x) => `<div class="list-item"><div class="av">${x.icon}</div><div class="grow"><div class="name">${esc(x.title)}</div><div class="muted">добыча ${x.loot[0]}–${x.loot[1]} ₽ · от ${x.need} человек</div></div>
      ${m.is_boss ? `<button class="btn sm" data-a="p3" data-p="gang/op" data-j='{"kind":"${x.key}"}' ${o.cd ? 'disabled' : ''}>Начать · ${money(o.cost)}</button>` : ''}</div>`).join('');
  return `<div class="card"><b>🏦 Большие дела</b><div class="muted">Четыре этапа за 6 часов: разведка → подготовка → взлом → отход. Профильные специалисты (разведчик, водитель, взломщик, боец) справляются лучше. Чем выше качество, тем больше добыча; делят все, исполнителям премия.</div>${o.cd ? `<div class="muted" style="margin-top:4px">Банда на дне ещё ${gangDur(o.cd)}</div>` : ''}${list}</div>`;
}

function ciHtml(m) {
  const p = m.p5;
  if (!p) return '';
  const c = p.ci;
  const boss = m.is_boss;
  const found = c.found ? `<div class="banner lose" style="margin:8px 0">🕵 Вскрыт чужой агент! Реши его судьбу.</div><div class="row" style="gap:6px;flex-wrap:wrap"><button class="btn sm" data-a="p3" data-p="gang/mole" data-j='{"mode":"turn"}' ${boss ? '' : 'disabled'}>🎭 Перевербовать (24 ч без нападений ИИ)</button><button class="btn sm bad" data-a="p3" data-p="gang/mole" data-j='{"mode":"kill"}' ${boss ? '' : 'disabled'}>💀 Убрать (страх +8)</button></div>` : '';
  const frames = c.targets.map((x) => `<button class="btn sm ghost" style="margin:4px 6px 0 0" data-a="p3" data-p="gang/frame" data-j='{"gid":${x.id}}' data-c="Подставить «${esc(x.name)}» за ${c.frame_cost} ₽? Шанс провала 35%: тогда ОМОН поедет к вам." ${boss && !c.frame_wait ? '' : 'disabled'}>🚨 ${esc(x.name)}</button>`).join('');
  return `<div class="card"><b>🛡 Контрразведка</b><div class="muted">Конкуренты внедряют кротов: те тянут деньги из общака. Проверка стоит ${money(c.scan_cost)} и ⚡20, шанс растёт с числом разведчиков в банде.</div>
    ${c.hint ? '<div class="muted" style="margin-top:6px">⚠️ В общаке подозрительно пропадают деньги: в банде завёлся чужой.</div>' : ''}${c.disinfo ? `<div class="muted" style="margin-top:6px">🎭 Дезинформация действует ещё ${gangDur(c.disinfo)}.</div>` : ''}
    <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="gang/scan" ${boss && !c.scan_wait ? '' : 'disabled'}>🕵 Проверить банду</button>${c.scan_wait ? `<div class="muted" style="margin-top:4px">Снова через ${gangDur(c.scan_wait)}</div>` : ''}${found}</div>
    ${frames ? `<div class="card"><b>🚨 Подстава</b><div class="muted">Анонимный донос на другую банду: к ним едет ОМОН. ${money(c.frame_cost)} из общака, раз в сутки.${c.frame_wait ? ' Снова через ' + gangDur(c.frame_wait) + '.' : ''}</div><div>${frames}</div></div>` : ''}`;
}

function gangTabs(g, m, jailed, head, invite, roster) {
  const sub = S.gangSub || 'hq';
  const nav = `<div class="row" style="margin:0 0 12px;flex-wrap:wrap;gap:6px">${GANG_TABS.map(([k, t]) => `<button class="btn sm ${k === sub ? '' : 'ghost'}" data-a="gang-sub" data-v="${k}">${t}</button>`).join('')}</div>`;
  let body = '';
  if (sub === 'hq') body = hqHtml(m, jailed);
  else if (sub === 'main') body = head + gangPlusHtml(g, m, jailed, 'main') + invite + roster + gangMgmtHtml(m) + dynastyHtml(m) + emblemHtml(m) + codexHtml(m);
  else if (sub === 'ops') body = gangExtraHtml(g, m, jailed, 'ops') + gangPlusHtml(g, m, jailed, 'ops');
  else if (sub === 'war') body = showdownHtml(g, m, jailed);
  else if (sub === 'eco') body = gangPlusHtml(g, m, jailed, 'eco');
  else if (sub === 'map') body = gangPlusHtml(g, m, jailed, 'map') + gangExtraHtml(g, m, jailed, 'map');
  else if (sub === 'team') body = gangPlusHtml(g, m, jailed, 'team');
  else body = gangExtraHtml(g, m, jailed, 'spy') + gangPlusHtml(g, m, jailed, 'spy');
  return nav + body;
}



const SEL = 'width:100%;margin-top:6px;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit';

function contractsHtml(d) {
  const pc = (d.gang && d.gang.pc) || {};
  const list = pc.contracts || [];
  if (!list.length) return '';
  const npc = pc.npc || [];
  const rows = list.map((c) => {
    const reward = c.rkind === 'money' ? `+${c.reward} ₽` : c.rkind === 'merit' ? `+${c.reward} авторитета` : c.rkind === 'cop' ? `+${c.reward} очк. полицейского` : 'подарок в квартиру';
    let act = '';
    if (c.status === 'offer') {
      act = `<button class="btn sm" data-a="p3" data-p="contract/accept" data-j='{"cid":${c.id},"npc":0}'>Взять</button>`;
      if (npc.length) act += `<select id="npc${c.id}" style="border:0;border-radius:10px;padding:8px;background:var(--bg);color:var(--text);font:inherit">${npc.map((n) => `<option value="${n.id}">с «${esc(n.nick)}»</option>`).join('')}</select><button class="btn sm ghost" data-a="contract-npc" data-c="${c.id}">С помощником</button>`;
    } else if (c.status === 'active') {
      act = c.ready ? `<button class="btn sm bad" data-a="p3" data-p="contract/finish" data-j='{"cid":${c.id}}'>Забрать итог</button>` : `<span class="muted">идёт, ещё ${Math.max(1, Math.ceil(c.left / 60))} мин</span>`;
    } else act = `<span class="muted">${c.status === 'done' ? '✅ сделано' : '❌ сорвалось'}</span>`;
    return `<div class="list-item" style="flex-wrap:wrap"><div class="av">${c.icon}</div><div class="grow"><div class="name">${esc(c.title)}</div><div class="muted">${esc(c.desc)} · ⚡${c.energy} · ${c.minutes} мин · шанс ~${c.chance}% · награда: ${reward}${c.wanted ? ' · розыск +1⭐' : ''}</div></div>
      <div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap">${act}</div></div>`;
  }).join('');
  return `<h2>📋 Доска контрактов</h2><div class="card"><div class="muted">Три дела в день от знакомого решалы: соло или с помощником из бригады (+15% к шансу). Босс не нужен.</div>${rows}</div>`;
}

function prisonHtml(d) {
  const p = ((d.gang || {}).pc || {}).prison;
  if (!p || !p.jailed) return '';
  const mates = p.cellmates.map((x) => `<div class="feed"><span>👤</span><div class="grow">${esc(x.name)} · авторитет ${x.rep}</div><span class="muted">${Math.ceil(x.left / 60)} мин</span><button class="btn sm ghost" data-a="p3" data-p="prison/parcel" data-j='{"target":${x.id}}'>📦 ${p.parcel_cost} ₽</button></div>`).join('') || '<div class="muted">Ты один в камере.</div>';
  return `<div class="card"><b>⛓ Тюремная жизнь</b><div class="muted">🚬 ${p.cigs} сигарет (местная валюта) · авторитет в камере ${p.rep}</div>
    <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px"><button class="btn sm" data-a="p3" data-p="prison/work" ${p.work_wait ? 'disabled' : ''}>🧹 Работа</button><button class="btn sm" data-a="p3" data-p="prison/smuggle" ${p.smuggle_wait ? 'disabled' : ''}>📦 Контрабанда</button>
      <button class="btn sm ghost" data-a="p3" data-p="prison/buy" data-j='{"minutes":5}'>⏱ −5 мин (${5 * p.per_min} 🚬)</button></div>
    ${p.work_wait ? `<div class="muted" style="margin-top:4px">Смена через ${Math.ceil(p.work_wait / 60)} мин</div>` : ''}<h2 style="margin-top:10px">Сокамерники</h2>${mates}</div>`;
}

function prisonHelpHtml(m) {
  const p = ((S.rp.gang || {}).pc || {}).prison;
  if (!p || !p.can_help || !p.mates.length) return '';
  return `<div class="card"><b>🚨 Свои за решёткой</b>${p.mates.map((x) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">${esc(x.name)}</div><div class="muted">ещё ${Math.ceil(x.left / 60)} мин</div></div>
      <div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap"><button class="btn sm bad" data-a="p3" data-p="gang/prison/breakout" data-j='{"target":${x.id}}' data-c="Устроить побег за ${p.breakout_cost} ₽ из общака?">🏃 Побег · ${money(p.breakout_cost)}</button><button class="btn sm" data-a="p3" data-p="gang/prison/lawyer" data-j='{"target":${x.id}}'>⚖️ Адвокат · ${money(p.lawyer_cost)}</button></div></div>`).join('')}</div>`;
}

function p8Html(d) {
  const pc = (d.gang && d.gang.pc) || {};
  let out = '';
  if (pc.defect_in) {
    const x = pc.defect_in;
    out += `<div class="card" style="border:2px solid #9b59b6"><b>🕵 Тебя переманивают</b><div class="muted">«${esc(x.gang)}» предлагает ${money(x.offer)} и должность (${x.role === 'deputy' ? 'зам' : 'холоп'}). Ответ в течение ${Math.max(1, Math.ceil(x.left / 3600))} ч. Предашь своих? Старая банда назначит награду.</div>
      <div class="row" style="margin-top:8px;gap:6px"><button class="btn sm bad" data-a="p3" data-p="defect/answer" data-j='{"did":${x.id},"accept":true}'>Перейти</button><button class="btn sm ghost" data-a="p3" data-p="defect/answer" data-j='{"did":${x.id},"accept":false}'>Отказаться</button></div></div>`;
  }
  (pc.my_debts || []).forEach((x) => {
    out += `<div class="card" style="border:2px solid #e5484d"><b>📒 Твой долг банде «${esc(x.gang)}»</b><div class="muted">Вернуть ${money(x.owed)} · ${x.late ? '⚠️ просрочен!' : 'ещё ' + gangDur(Math.max(0, x.left))}</div>
      <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="debt/repay" data-j='{"did":${x.id}}'>Погасить</button></div>`;
  });
  return out;
}

function councilHtml(d) {
  const c = ((d.gang || {}).pc || {}).council;
  if (!c) return '';
  let out = '';
  const e = c.election;
  if (e) {
    out += `<div class="card" style="border:2px solid #6c8cff"><b>🏛 Выборы мэра</b><div class="muted">Ещё ${gangDur(e.left)}. Голосуют все жители; кандидаты — только полицейские.</div>
      ${e.candidates.map((x) => `<div class="list-item"><div class="grow"><div class="name">${x.ai ? '🧓' : '🕴'} ${esc(x.name)}</div></div><button class="btn sm ${e.mine === x.id ? '' : 'ghost'}" data-a="p3" data-p="council/vote" data-j='{"cand":${x.id}}' ${e.can_vote ? '' : 'disabled'}>${e.mine === x.id ? '✅ Мой' : 'Голос'}</button></div>`).join('')}</div>`;
  }
  const m = c.mayor;
  if (m && !m.is_me && m.can_impeach) {
    out += `<div class="card"><b>🗳 Импичмент мэру</b><div class="muted">Боссы банд могут снять мэра «${esc(m.gname)}». Голосов сейчас: ${m.impeach}.</div><button class="btn sm bad" style="margin-top:8px" data-a="p3" data-p="mayor/impeach" data-c="Объявить импичмент мэру?">Голосовать за отставку</button></div>`;
  }
  return out;
}

function debtBookHtml() { return ''; }

function charterHtml(m) {
  const c = m.charter;
  if (!c) return '';
  return `<div class="card" style="border-color:color-mix(in srgb,var(--gold) 55%,var(--line));background:linear-gradient(135deg,color-mix(in srgb,var(--gold) 12%,var(--card)),var(--card))">
    <div class="row"><div style="font-size:28px">📜</div><div class="grow"><b>${esc(c.title)}</b><div class="muted">Привилегии основателей района: действуют всегда, пока банда жива.</div></div></div>
    ${c.perks.map((x) => `<div class="feed"><span>${x.icon}</span><div class="grow"><b>${esc(x.title)}</b><div class="muted" style="font-size:12px">${esc(x.desc)}</div></div></div>`).join('')}</div>`;
}

function hqHtml(m, jailed) {
  const h = m.p8 && m.p8.hq;
  if (!h) return '<div class="empty">Штаб строится…</div>';
  const go = { office: 'map', vault: 'eco', armory: 'team', kitchen: 'team', basement: 'spy', garage: 'ops', yard: 'team' };
  const boss = m.is_boss || m.my_role === 'deputy';
  const rooms = h.rooms.map((r) => `<div class="card" style="margin:0;${r.wrecked ? 'border:2px solid #e5484d;' : ''}"><div class="row"><div style="font-size:28px">${r.icon}</div><div class="grow"><b>${esc(r.title)}</b><div class="muted" style="font-size:12px">${esc(r.stat)}</div></div></div>
      ${r.wrecked ? `<div class="muted" style="margin-top:6px;color:#e5484d">💥 Разгромлено: ${esc(r.effect)}. Ещё ${gangDur(r.wrecked)}</div><button class="btn sm bad" style="margin-top:6px" data-a="p3" data-p="gang/room/repair" data-j='{"room":"${r.key}"}' ${boss ? '' : 'disabled'}>🔧 Ремонт · ${money(h.repair_cost)}</button>`
        : `<button class="btn sm ghost" style="margin-top:6px" data-a="gang-sub" data-v="${go[r.key]}">Открыть</button>`}</div>`).join('');
  const yard = h.online.map((o) => `<span class="muted" style="margin-right:10px">${o.online ? '🟢' : '⚪'} ${esc(o.name)}</span>`).join('');
  const troph = h.trophies.length ? h.trophies.map((t) => `<div class="feed"><span>${t.kind === 'season' ? '👑' : '🏆'}</span><div class="grow">${esc(t.text)}</div><span class="muted">${ago(t.ts)}</span></div>`).join('') : '<div class="muted">Трофеев пока нет: выигрывайте штурмы и сезоны.</div>';
  return charterHtml(m) + `<div class="card" style="background:linear-gradient(135deg, ${h.color}33, transparent)"><div class="row"><div style="font-size:44px">${h.emblem}</div><div class="grow"><b style="font-size:18px">Штаб «${esc(m.name)}»</b><div class="muted">${h.street ? esc(h.street) : 'Здесь решаются дела банды'}</div></div></div>
      <div style="margin-top:8px"><b>🌳 Двор:</b> ${yard}</div></div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-top:10px">${rooms}</div>
    <h2>🏆 Зал славы и трофеи</h2><div class="card">${troph}</div>`;
}


function brigadeHtml(m) {
  const p = m.p8;
  if (!p) return '';
  const can = m.is_boss || m.my_role === 'deputy';
  const rows = p.brigade.map((b) => `<div class="list-item" style="flex-wrap:wrap"><div class="av">${b.icon}</div><div class="grow"><div class="name">${esc(b.name)} «${esc(b.nick)}» · ур. ${b.level}</div>
      <div class="muted">${esc(b.role_name)} · ${esc(b.trait)} · верность ${b.loyalty}${b.status === 'busy' ? ' · на деле: ' + esc(b.task) + ' (' + Math.ceil(b.left / 60) + ' мин)' : b.status === 'injured' ? ' · 🤕 травма ещё ' + gangDur(b.left) : ' · свободен'}</div></div>
      ${can ? `<div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap">${b.status === 'free' ? p.tasks.map((t) => `<button class="btn sm" data-a="p3" data-p="gang/brig/task" data-j='{"bid":${b.id},"task":"${t.key}"}' title="${esc(t.desc)}">${t.icon} ${esc(t.title)} (${t.hours} ч)</button>`).join('') : ''}
        ${b.train_cost ? `<button class="btn sm ghost" data-a="p3" data-p="gang/brig/train" data-j='{"bid":${b.id}}'>💪 Тренировка · ${money(b.train_cost)}</button>` : ''}<button class="btn sm ghost" data-a="p3" data-p="gang/brig/fire" data-j='{"bid":${b.id}}' data-c="Уволить ${esc(b.nick)}?">Уволить</button></div>` : ''}</div>`).join('');
  const hire = p.roles_free.map((r) => `<button class="btn sm" style="margin:4px 6px 0 0" data-a="p3" data-p="gang/brig/hire" data-j='{"role":"${r.key}"}' ${can ? '' : 'disabled'}>${r.icon} Нанять: ${esc(r.title)} · ${money(p.hire_cost)}</button>`).join('');
  return `<div class="card"><b>🧢 Бригада шестёрок</b><div class="muted">Три слота: водила, боец, шнырь. Работают без онлайна: крышуют, сторожат, возят налёты, ищут улики. Зарплата каждый день из общака; недоплатишь, упадёт верность. Свободные бойцы добавляют очки в войне и шанс отбиться от нападения.</div>${rows || '<div class="muted" style="margin-top:6px">Бригады пока нет.</div>'}<div>${hire}</div></div>`;
}

function racketsHtml(m) {
  const p = m.p8;
  if (!p) return '';
  const can = m.is_boss || m.my_role === 'deputy';
  const rows = p.rackets.map((r) => {
    let acts = '';
    if (can) {
      if (!r.owner) acts = `<button class="btn sm" data-a="p3" data-p="gang/racket/claim" data-j='{"key":"${r.key}"}'>Взять под крышу · ${money(p.claim_cost)}</button>`;
      else if (r.mine) acts = [1, 2, 3].map((l) => `<button class="btn sm ${r.greed === l ? '' : 'ghost'}" data-a="p3" data-p="gang/racket/greed" data-j='{"key":"${r.key}","level":${l}}'>${['', 'скромно', 'обычно', 'жадно'][l]}</button>`).join('');
      else acts = `<button class="btn sm bad" data-a="p3" data-p="gang/racket/challenge" data-j='{"key":"${r.key}"}' data-c="Наехать на «${esc(r.owner_name)}» за ${p.challenge_cost} ₽?" ${p.challenge_wait ? 'disabled' : ''}>👊 Наехать · ${money(p.challenge_cost)}</button>`;
    }
    return `<div class="list-item" style="flex-wrap:wrap"><div class="av">${r.icon}</div><div class="grow"><div class="name">${esc(r.title)}${r.mine ? ' (наша)' : ''}</div><div class="muted">${r.owner ? 'Крыша: «' + esc(r.owner_name) + '»' : 'Свободна'} · дань ${money(r.tribute)}/нед.${r.mine ? ' · накоплено ' + money(r.pending) : ''}${r.rebel ? ' · 🔥 бунт ещё ' + gangDur(r.rebel) : ''}</div></div>${acts ? `<div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap">${acts}</div>` : ''}</div>`;
  }).join('');
  const total = p.rackets.filter((r) => r.mine).reduce((s, r) => s + r.pending, 0);
  return `<div class="card"><b>🏪 Крыша над бизнесом</b><div class="muted">Берите точки под крышу и собирайте дань. Жадность увеличивает доход, но злит хозяев: полиция или бунт. Владелец точки может смениться: конкуренты и ИИ-банды наезжают. Крыша над баром даёт участникам −10% в баре.</div>${rows}
    <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="gang/racket/collect" ${can && total > 0 ? '' : 'disabled'}>💰 Собрать дань (${money(total)})</button></div>`;
}

function lendHtml(m) {
  const p = m.p8;
  if (!p) return '';
  const can = m.is_boss || m.my_role === 'deputy';
  if (!can) return `<div class="card"><b>📒 Долги</b><div class="muted">Займы выдают босс и замы. Выдано в долг: ${money(p.lent || 0)}.</div></div>`;
  return `<div class="card"><b>📒 Займы из общака</b><div class="muted">Ты предлагаешь заём, игрок сам решает, брать ли. Деньги уходят из общака при согласии. Просрочка: коллекторы забирают его наличные и тайник в общак. В долгах сейчас: ${money(p.lent || 0)}.</div>
    <select id="lendTo" style="${SEL}">${p.borrowers.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select>
    <div class="row" style="margin-top:6px;gap:6px"><input id="lendAmt" type="number" min="100" max="2000" value="500" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><button class="btn sm" data-a="lend-go">Предложить</button></div>
    <select id="lendHours" style="${SEL}"><option value="24">на 24 ч · +15%</option><option value="48" selected>на 48 ч · +25%</option><option value="72">на 72 ч · +40%</option></select>
    <button class="btn sm ghost" style="margin-top:8px" data-a="p3" data-p="gang/debt/audit">🔍 Ревизия общака</button></div>`;
}

function dynastyHtml(m) {
  const d = m.p8 && m.p8.dyn;
  if (!d) return '';
  const hall = d.hall.map((h) => `<div class="feed"><span>${h.kind === 'boss' ? '👑' : h.kind === 'season' ? '🏆' : '🎖'}</span><div class="grow">${esc(h.name)}: ${esc(h.text)}</div><span class="muted">${ago(h.ts)}</span></div>`).join('') || '<div class="muted">Зал славы пока пуст.</div>';
  return `<div class="card"><b>🧬 Династия</b><div class="muted">Преемник: ${d.heir ? esc(d.heir_name) : 'не назначен'} · смен поколений: ${d.legacy}${d.street ? ' · ' + esc(d.street) : ''}. Если босс пропадёт на 5 дней, власть перейдёт к преемнику.</div>
    ${m.is_boss ? `<select id="heirSel" style="${SEL}">${d.candidates.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select><div class="row" style="margin-top:6px;gap:6px"><button class="btn sm" data-a="heir-go">Назначить</button><button class="btn sm ghost" data-a="p3" data-p="gang/abdicate" data-c="Передать власть преемнику?" ${d.heir ? '' : 'disabled'}>Отречься</button></div>` : ''}
    <h2 style="margin-top:10px">Зал славы</h2>${hall}</div>`;
}

function emblemHtml(m) {
  const e = m.p8 && m.p8.emblem;
  if (!e) return '';
  const dr = S.embDraft || { emoji: e.emoji || '🐺', c1: e.c1 || e.colors[0], c2: e.c2 || e.colors[7], motto: e.motto || '' };
  S.embDraft = dr;
  const emo = e.emojis.map((x) => `<button class="btn sm ${dr.emoji === x ? '' : 'ghost'}" data-a="emb-pick" data-k="emoji" data-v="${x}" style="font-size:18px">${x}</button>`).join('');
  const col = (k) => e.colors.map((c) => `<button data-a="emb-pick" data-k="${k}" data-v="${c}" style="width:28px;height:28px;border-radius:14px;border:${dr[k] === c ? '3px solid var(--text)' : '1px solid #0003'};background:${c};margin:2px"></button>`).join('');
  return `<div class="card"><b>🎨 Эмблема и граффити</b><div class="muted">Эмодзи, два цвета и девиз. Эмблема видна на карте и в списках банд.</div>
    <div style="font-size:42px;margin-top:6px;text-align:center;border-radius:12px;padding:8px;background:linear-gradient(135deg, ${dr.c1}, ${dr.c2})">${dr.emoji}</div>
    <div class="row" style="flex-wrap:wrap;gap:4px;margin-top:8px">${emo}</div><div style="margin-top:6px">Цвет 1: ${col('c1')}</div><div>Цвет 2: ${col('c2')}</div>
    <input id="embMotto" maxlength="40" value="${esc(dr.motto)}" placeholder="Девиз банды" style="${SEL}">
    <div class="row" style="margin-top:8px;gap:6px"><button class="btn sm" data-a="emb-save" ${m.is_boss ? '' : 'disabled'}>Сохранить${e.cost ? ' · ' + money(e.cost) : ''}</button><button class="btn sm ghost" data-a="p3" data-p="gang/graffiti" ${e.emoji && !e.graffiti_wait ? '' : 'disabled'}>🖌 Граффити</button></div></div>`;
}

function codexHtml(m) {
  const c = m.p8 && m.p8.codex;
  if (!c) return '';
  const rules = c.rules.map((r) => `<div class="feed"><span>${r.idx + 1}.</span><div class="grow">${esc(r.text)}</div></div>`).join('') || '<div class="muted">Правил пока нет.</div>';
  const t = c.trial;
  const trial = t ? `<div class="banner lose" style="margin:8px 0">⚖️ Разбор: ${esc(t.accuser)} обвиняет ${esc(t.defendant)} (правило: ${esc(t.rule)}). Улики: ${esc(t.evidence)}<br>Виновен: ${t.guilty} · Невиновен: ${t.innocent} · ещё ${gangDur(t.left)}</div>
    <div class="row" style="gap:6px"><button class="btn sm bad" data-a="p3" data-p="gang/trial/vote" data-j='{"tid":${t.id},"verdict":"guilty"}' ${t.can_vote ? '' : 'disabled'}>Виновен</button><button class="btn sm" data-a="p3" data-p="gang/trial/vote" data-j='{"tid":${t.id},"verdict":"innocent"}' ${t.can_vote ? '' : 'disabled'}>Невиновен</button></div>` : `
    ${c.rules.length ? `<select id="trDef" style="${SEL}">${c.targets.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select><select id="trRule" style="${SEL}">${c.rules.map((r) => `<option value="${r.idx}">${r.idx + 1}. ${esc(r.text)}</option>`).join('')}</select>
    <input id="trEv" maxlength="100" placeholder="Улики: что случилось" style="${SEL}"><button class="btn sm" style="margin-top:6px" data-a="trial-go">⚖️ Обвинить</button>` : ''}`;
  return `<div class="card"><b>📜 Кодекс банды</b>${rules}
    ${m.is_boss ? `<textarea id="rulesTxt" rows="3" placeholder="До 5 правил, каждое с новой строки" style="${SEL}">${esc(c.rules.map((r) => r.text).join('\n'))}</textarea><button class="btn sm" style="margin-top:6px" data-a="rules-go">Сохранить кодекс</button>` : ''}${trial}</div>`;
}

function defectHtml(m) {
  const p = m.p8 && m.p8.defect;
  if (!p) return '';
  const bounties = p.bounties.map((b) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">🕵 ${esc(b.name)} → «${esc(b.gang)}»</div><div class="muted">награда ${money(b.bounty)}</div></div>
      <div class="row" style="flex-basis:100%;gap:6px;margin-top:6px"><button class="btn sm bad" data-a="p3" data-p="gang/defect/hunt" data-j='{"did":${b.id}}' ${b.bounty ? '' : 'disabled'}>🎯 Выследить (⚡15)</button>${m.is_boss ? `<button class="btn sm ghost" data-a="p3" data-p="gang/defect/bounty" data-j='{"did":${b.id},"amount":500}'>+500 ₽ награды</button>` : ''}</div></div>`).join('');
  const offer = m.is_boss && p.targets.length ? `<select id="defTo" style="${SEL}">${p.targets.map((x) => `<option value="${x.id}">${esc(x.name)} («${esc(x.gang)}»)</option>`).join('')}</select>
    <div class="row" style="margin-top:6px;gap:6px"><input id="defAmt" type="number" min="500" max="3000" value="1000" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><select id="defRole" style="border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><option value="grunt">холоп</option><option value="deputy">зам</option></select></div>
    <button class="btn sm" style="margin-top:6px" data-a="defect-go" ${p.wait ? 'disabled' : ''}>🕵 Переманить</button>` : '<div class="muted" style="margin-top:6px">Переманивать может босс; нужны участники других банд.</div>';
  return `<div class="card"><b>🕵 Вербовка и перебежчики</b><div class="muted">Предложи участнику чужой банды деньги и должность. У перебежчика скрытая лояльность: он может ограбить, слить планы или остаться верным. Старая банда назначает награду за его голову.</div>${offer}</div>${bounties ? '<h2>Награды за предателей</h2>' + bounties : ''}`;
}

function showdownHtml(g, m, jailed) {
  const w = g.war || (m.p8 || {}).war;
  const wOps = (m.p8 || {}).war || {};
  const boss = !!m.is_boss;
  let out = '<h2>⚔️ Разборки</h2>';
  if (w) {
    out += `<div class="card" style="border:2px solid #e5484d"><div class="row"><div style="font-size:32px">⚔️</div><div class="grow"><b>Война с «${esc(w.enemy)}»</b><div class="muted">Очки ${w.mine}:${w.theirs} · до конца ${gangDur(w.left)}</div></div></div>
      <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap">
        <button class="btn bad" data-a="rp" data-p="gang/raid" ${jailed || w.raid_left ? 'disabled' : ''}>⚔️ Рейд (⚡−${w.energy})</button>
        ${wOps.can_assault ? `<button class="btn bad" data-a="p3" data-p="gang/assault" data-c="Штурмовать штаб врага? При провале неделя отдыха." ${boss ? '' : 'disabled'}>🏰 Штурм</button>` : ''}
      </div>
      <div class="muted" style="margin-top:8px">Наезд — потребовать контрибуцию. Откуп — заплатить и выйти из войны. Решает босс.</div>
      <div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap">
        <button class="btn sm" data-a="p3" data-p="gang/peace" data-j='{"kind":"demand","amount":1000}' ${boss ? '' : 'disabled'}>💵 Наезд · 1 000 ₽</button>
        <button class="btn sm ghost" data-a="p3" data-p="gang/peace" data-j='{"kind":"tribute","amount":1000}' ${boss ? '' : 'disabled'}>🕊 Откуп · 1 000 ₽</button>
      </div></div>`;
  } else {
    const targets = (g.gangs || []).filter((x) => !x.mine).map((x) => `<div class="list-item"><div class="av">${x.icon || '🕴'}</div><div class="grow"><div class="name">${esc(x.name)}</div><div class="muted">${x.n} человек · ${x.terr || 'без районов'}</div></div>
      ${boss && !g.war_cd ? `<button class="btn sm bad" data-a="gang-act" data-p="gang/war" data-g="${x.id}" data-c="Объявить войну «${esc(x.name)}»? Она длится 12 часов.">Война</button>` : ''}</div>`).join('') || '<div class="muted">Других банд нет.</div>';
    out += `<div class="card"><b>Войны нет</b><div class="muted">Выбери банду и объяви войну. После начала появятся наезд, откуп и рейд.</div>${targets}</div>`;
  }
  out += pactsHtml(g, m);
  return out;
}

function warOpsHtml(m) {
  const w = m.p8 && m.p8.war;
  if (!w) return '';
  const boss = m.is_boss;
  const peaces = w.peaces.map((x) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">🕊 ${x.incoming ? esc(x.other) + ' предлагает мир' : 'Предложено ' + esc(x.other)}</div><div class="muted">${esc(x.kind)} ${x.amount ? money(x.amount) : ''}</div></div>${x.incoming && boss ? `<div class="row" style="flex-basis:100%;gap:6px;margin-top:6px"><button class="btn sm" data-a="p3" data-p="gang/peace/answer" data-j='{"pid":${x.id},"accept":true}'>Принять</button><button class="btn sm ghost" data-a="p3" data-p="gang/peace/answer" data-j='{"pid":${x.id},"accept":false}'>Отказать</button></div>` : ''}</div>`).join('');
  const rooms = w.rooms.map((r) => `<option value="${r.key}">${r.icon} ${esc(r.title)}</option>`).join('');
  const mt = w.my_terr.map((t) => `<option value="${t.key}">${esc(t.name)}</option>`).join('');
  const tt = w.their_terr.map((t) => `<option value="${t.key}">${esc(t.name)}</option>`).join('');
  return `<div class="card" style="border:2px solid #e5484d"><b>🏰 Штурм штаба: ${esc(w.enemy)}</b><div class="muted">Перевес в очках: ${w.lead} (нужно ${w.assault_need}+). Успех: часть общака, уровень проекта, эмблема-трофей, пленный и +30 очков. Провал: −15 авторитета, двое побиты, бойцы ранены, неделя отдыха.</div>
    <button class="btn bad" style="margin-top:8px" data-a="p3" data-p="gang/assault" data-c="Штурмовать штаб врага? При провале неделя отдыха." ${boss && w.can_assault ? '' : 'disabled'}>🏰 Штурмовать (⚡20)</button>${w.assault_cd ? `<div class="muted" style="margin-top:4px">Банда отдыхает ${gangDur(w.assault_cd)}</div>` : ''}
    <h2 style="margin-top:10px">Погром комнаты врага</h2><select id="ranRoom" style="${SEL}">${rooms}</select><button class="btn sm bad" style="margin-top:6px" data-a="ransack-go" ${w.ransack_wait ? 'disabled' : ''}>💥 Разгромить (⚡20)</button></div>
    <div class="card"><b>🕊 Переговоры</b><div class="muted">Вместо штурма: контрибуция, откуп, вассалитет на 3 дня или обмен районами.</div>
      <select id="peKind" style="${SEL}"><option value="demand">Контрибуция: враг платит</option><option value="tribute">Откуп: мы платим</option><option value="vassal">Вассалитет (150 ₽ в сутки)</option><option value="swap">Обмен районами</option></select>
      <input id="peAmt" type="number" min="100" max="5000" value="1000" style="${SEL}"><div class="row" style="gap:6px"><select id="peKa" style="${SEL}">${mt}</select><select id="peKb" style="${SEL}">${tt}</select></div>
      <button class="btn sm" style="margin-top:6px" data-a="peace-go" ${boss ? '' : 'disabled'}>Предложить мир</button></div>${peaces}`;
}

const MAP_XY = { market: [150, 34], port: [226, 60], embank: [272, 124], bath: [252, 200], club: [196, 248], dacha: [104, 248], elev: [48, 200], tire: [28, 124], garages: [74, 60], station: [150, 144] };
const MAP_EDGES = [['market', 'port'], ['port', 'embank'], ['embank', 'bath'], ['bath', 'club'], ['club', 'dacha'], ['dacha', 'elev'], ['elev', 'tire'], ['tire', 'garages'], ['garages', 'station'], ['station', 'market'], ['station', 'club'], ['port', 'elev']];
const MAP_SHORT = { market: 'Рынок', port: 'Порт', embank: 'Наб.', bath: 'Баня', club: 'Клуб', dacha: 'Дачи', elev: 'Элеватор', tire: 'Шином.', garages: 'Гаражи', station: 'Вокзал' };

function mapSvgHtml(g, m, opts = {}) {
  const policeMode = !!opts.police;
  const p = policeMode ? { map: opts.map || [] } : (m.p5 || {});
  if (!p.map || !p.map.length) return '';
  const col = {};
  (g.gangs || p.gangs || []).forEach((x) => { col[x.id] = x.color1 || x.owner_color || '#7f8c8d'; });
  const by = {};
  p.map.forEach((t) => { by[t.key] = t; });
  const preferred = policeMode ? (p.map.find((x) => x.can_patrol) || p.map[0]) : (p.map.find((x) => x.mine) || p.map.find((x) => x.can_claim) || p.map[0]);
  const sel = S.mapSel && by[S.mapSel] ? S.mapSel : (preferred ? preferred.key : null);
  const lines = MAP_EDGES.map(([a, b]) => {
    const hot = sel && (a === sel || b === sel);
    return `<line x1="${MAP_XY[a][0]}" y1="${MAP_XY[a][1]}" x2="${MAP_XY[b][0]}" y2="${MAP_XY[b][1]}" stroke="${hot ? '#6d5bd0' : 'rgba(120,115,105,.35)'}" stroke-width="${hot ? 3 : 2}" ${hot ? 'stroke-dasharray="6 4"' : ''}/>`;
  }).join('');
  const nodes = Object.keys(MAP_XY).map((k) => {
    const t = by[k];
    if (!t) return '';
    const [x, y] = MAP_XY[k];
    const own = t.owner ? (t.owner_color || col[t.owner] || '#7f8c8d') : '#d7d2c7';
    const isSel = sel === k;
    const isNeigh = sel && (by[sel].neigh_keys || []).includes(k);
    const stroke = t.mine ? '#ffffff' : isSel ? '#f5a524' : isNeigh ? '#6d5bd0' : 'rgba(0,0,0,.22)';
    const sw = t.mine || isSel ? 4 : isNeigh ? 3 : 2;
    const ctrl = Math.max(0, Math.min(100, Number(t.control || 0)));
    const pol = Math.max(0, Math.min(100, Number(t.police || 0)));
    const badge = t.mine ? `<g><rect x="${x - 13}" y="${y - 34}" width="26" height="12" rx="6" fill="#6d5bd0"/><text x="${x}" y="${y - 25}" text-anchor="middle" font-size="8" font-weight="800" fill="#fff">ВЫ</text></g>` : '';
    return `<g data-a="map-pick" data-k="${k}" style="cursor:pointer">
      <circle cx="${x}" cy="${y}" r="${isSel ? 24 : 21}" fill="${own}" stroke="${stroke}" stroke-width="${sw}"/>
      <text x="${x}" y="${y + 6}" text-anchor="middle" font-size="17">${t.icon}</text>
      ${badge}
      ${t.fire ? `<text x="${x + 15}" y="${y - 15}" font-size="12">🔥</text>` : ''}
      ${t.block ? `<text x="${x - 23}" y="${y - 15}" font-size="12">🚧</text>` : ''}
      <rect x="${x - 21}" y="${y + 23}" width="42" height="4" rx="2" fill="rgba(0,0,0,.14)"/>
      <rect x="${x - 21}" y="${y + 23}" width="${42 * ctrl / 100}" height="4" rx="2" fill="#2fa66a"/>
      <rect x="${x - 21}" y="${y + 29}" width="42" height="3" rx="1.5" fill="rgba(0,0,0,.10)"/>
      <rect x="${x - 21}" y="${y + 29}" width="${42 * pol / 100}" height="3" rx="1.5" fill="#4c8dff"/>
      <text x="${x}" y="${y + 45}" text-anchor="middle" font-size="9.5" font-weight="800" fill="currentColor">${MAP_SHORT[k] || esc(t.name)}</text>
      ${t.owner ? `<text x="${x}" y="${y + 56}" text-anchor="middle" font-size="8.5" fill="currentColor" opacity=".62">${t.owner_icon || '🏴'} ${esc((t.owner_name || '').slice(0, 8))}</text>` : ''}
    </g>`;
  }).join('');
  const mine = policeMode ? p.map.filter((t) => Number(t.police || 0) > 0) : p.map.filter((t) => t.mine);
  const mineWord = mine.length === 1 ? 'район' : [2, 3, 4].includes(mine.length % 10) && ![12, 13, 14].includes(mine.length % 100) ? 'района' : 'районов';
  const mineLabel = policeMode ? 'с патрулями' : 'под контролем';
  const avg = (key) => p.map.length ? Math.round(p.map.reduce((s, t) => s + Number(t[key] || 0), 0) / p.map.length) : 0;
  const t = sel ? by[sel] : null;
  let info = '';
  if (t) {
    const actions = [];
    if (policeMode) {
      if (t.can_patrol) {
        actions.push(`<button class="btn sm" data-a="p3" data-p="police/patrol/district" data-j='{"key":"${t.key}"}' ${opts.patrol_wait ? 'disabled' : ''}>🚓 Патруль · ${money(opts.cost || 300)}</button>`);
      } else {
        actions.push('<span class="muted" style="font-size:11px">Район без банды или свой: патрулировать нечего.</span>');
      }
    } else {
      const p5 = m.p5 || {};
      const tre = Number(m.treasury || 0);
      const lock = (label, why) => `<button class="btn sm ghost" disabled>${label}</button><span class="muted lock-why">🔒 ${why}</span>`;
      const gate = (wait, cost) => !m.is_boss ? 'решает только босс банды' : wait ? 'банда занята ещё ' + gangDur(wait) : tre < cost ? `в общаке ${money(tre)}, нужно ${money(cost)}` : '';
      if (t.mine) {
        const why = gate(p5.fort_wait, p5.fort_cost);
        actions.push(why ? lock('🧱 Укрепить', why) : `<button class="btn sm ghost" data-a="p3" data-p="gang/fortify" data-j='{"key":"${t.key}"}' data-c="Укрепить район за ${money(p5.fort_cost)}?">🧱 Укрепить · ${money(p5.fort_cost)}</button>`);
      } else if (!t.owner) {
        const why = !t.can_claim ? 'занять можно только район рядом с вашим' : gate(p5.claim_wait, p5.claim_cost);
        actions.push(why ? lock('🏴 Занять', why) : `<button class="btn sm" data-a="p3" data-p="gang/claim" data-j='{"key":"${t.key}"}'>🏴 Занять · ${money(p5.claim_cost)}</button>`);
      } else {
        const why = !t.can_block ? 'нет общей границы: сначала займите район рядом' : p5.block_wait ? 'следующая блокада через ' + gangDur(p5.block_wait) : tre < p5.block_cost ? `в общаке ${money(tre)}, нужно ${money(p5.block_cost)}` : '';
        actions.push(why ? lock('🚧 Блокада', why) : `<button class="btn sm bad" data-a="p3" data-p="gang/blockade" data-j='{"key":"${t.key}"}'>🚧 Блокада · ${money(p5.block_cost)}</button>`);
      }
      if (Number(t.police || 0) > 0 && (m.is_boss || m.my_role === 'deputy')) {
        actions.push(`<button class="btn sm ghost" data-a="p3" data-p="gang/sabotage" data-j='{"key":"${t.key}","mode":"quiet"}' data-c="Подкупить патруль в районе «${esc(t.name)}» за 500 ₽ из общака?">💵 Подкупить патруль</button>`);
        actions.push(`<button class="btn sm bad" data-a="p3" data-p="gang/sabotage" data-j='{"key":"${t.key}","mode":"force"}' data-c="Напасть на патруль в районе «${esc(t.name)}»? Есть риск розыска и тюрьмы.">🥊 Напасть на патруль</button>`);
      }
      if (!actions.length) actions.push(`<span class="muted" style="font-size:11px">${t.owner && !t.mine ? 'Нет общей границы: сначала займите район рядом.' : 'Действий пока нет.'}</span>`);
    }
    const nbs = (t.neigh_keys || []).map((k) => {
      const n = by[k];
      if (!n) return '';
      return `<button class="map-neigh" data-a="map-pick" data-k="${k}">${n.icon} ${esc(MAP_SHORT[k] || n.name)}${n.mine ? ' · наш' : n.owner ? ' · ' + esc((n.owner_name || '').slice(0, 9)) : ''}</button>`;
    }).join('');
    info = `<div class="map-info">
      <div class="row"><div style="font-size:24px">${t.icon}</div><div class="grow"><b>${esc(t.name)}</b>
        <div class="muted">${t.owner ? 'Держит «' + esc(t.owner_name) + '»' : 'Свободен'} · ${t.res ? ({ goods: '🛍 товары', cargo: '📦 груз', parts: '🔩 запчасти', grain: '🌾 зерно', spirits: '🥃 спирт', arms: '🔫 оружие', intel: '🧠 информация' })[t.res] : 'без сырья'}</div></div>
        ${t.mine ? '<span class="badge brand">ваш</span>' : ''}</div>
      <div class="map-meter"><span>Контроль</span><div class="map-bar"><i style="width:${t.control || 0}%;background:#2fa66a"></i></div><b>${t.control || 0}%</b></div>
      <div class="map-meter"><span>Полиция</span><div class="map-bar"><i style="width:${t.police || 0}%;background:#4c8dff"></i></div><b>${t.police || 0}%</b></div>
      ${t.perk ? `<div class="muted" style="margin-top:6px;font-size:12px">⭐ Особенность: ${esc(t.perk)}${t.mine ? '' : t.owner ? ' (работает на хозяина)' : ' (достанется тому, кто займёт)'}</div>` : ''}
      <div class="map-nb">${nbs || '<span class="muted">Соседей нет</span>'}</div>
      <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px">${actions.join('')}</div>
    </div>`;
  }
  const legend = (g.gangs || p.gangs || []).map((x) => `<span class="map-leg"><i style="background:${x.color1 || '#7f8c8d'}"></i>${x.emblem || x.icon || '🏴'} ${esc((x.name || '').slice(0, 14))}</span>`).join('');
  const head = policeMode ? '🚓 Полицейская карта' : '🗺 Оперативная карта';
  return `<div class="card map-card">
    <div class="row"><div class="grow"><b>${head}</b><div class="muted">${mine.length} ${mineWord} ${mineLabel} · цель 6 · средний контроль ${avg('control')}% · полиция ${avg('police')}%</div></div></div>
    <div class="map-legend">${legend}<span class="map-leg"><i style="background:#d7d2c7"></i>свободно</span><span class="map-leg"><i style="background:#4c8dff"></i>полиция</span></div>
    <svg viewBox="0 0 320 330" class="strategy-map" role="img" aria-label="Карта районов">${lines}${nodes}</svg>
    ${info || '<div class="muted" style="text-align:center;margin-top:6px">Нажми на район: увидишь владельца, контроль, полицию и соседей.</div>'}
  </div>`;
}

function bossHtml(d) {
  const b = d && d.boss && d.boss.boss;
  if (!b || b.status !== 'alive') return '';
  const pct = Math.max(0, Math.min(100, Math.round(100 * b.hp / b.max_hp)));
  return `<div class="card" style="border-color:color-mix(in srgb,var(--bad) 35%,var(--line))"><div class="row"><div style="font-size:30px">${b.emoji || '👹'}</div><div class="grow"><b>${esc(b.name)}</b><div class="muted">${b.hp} / ${b.max_hp} HP · осталось ${gangDur(b.ends_in)} · ударов ${d.boss.hits_left}</div></div></div>
    <div class="bar" style="height:10px;margin-top:8px"><i style="width:${pct}%;background:#e5484d"></i></div>
    <button class="btn sm bad" style="margin-top:8px" data-a="hit" ${d.boss.hits_left ? '' : 'disabled'}>⚔️ Ударить босса</button></div>`;
}

function policeMapHtml(p) {
  if (!p || !p.map || !p.map.length) return '';
  return mapSvgHtml({ gangs: p.gangs || [] }, {}, { police: true, map: p.map, patrol_wait: p.patrol_district_wait || 0, cost: p.patrol_district_cost || 300 });
}

function policeBribeHtml(m) {
  const p = m.police;
  if (!p) return '';
  const lvl = { none: 'Вас пока не замечают', low: 'На вас завели дело: вы на радаре', high: 'Дело разрастается: ждите ордер', critical: 'Дело готово к суду: громкий арест близко!' }[p.level];
  const color = { none: '#3fb950', low: '#f5a623', high: '#e67e22', critical: '#e5484d' }[p.level];
  const bribe = p.bribe ? `<div class="muted" style="margin-top:6px">💵 Конверт ждёт ответа начальника ещё ${gangDur(p.bribe.left)} (${money(p.bribe.amount)}).</div>`
    : (m.is_boss ? `<div class="row" style="margin-top:8px;gap:6px"><input id="bribeAmt" type="number" min="300" max="2000" value="1000" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><button class="btn sm bad" data-a="bribe-go">💵 Дать на лапу</button></div>` : '');
  return `<div class="card" style="border-left:4px solid ${color}"><b>🚓 Полиция следит за вами</b><div class="muted">${lvl}. Капитан: ${esc(p.chief || '—')}. Улики копятся от налётов, дел и тюрьмы. Взятка ослабляет дело, но капитан может отказаться и пришить попытку к делу.</div>${bribe}</div>`;
}

function policeHtml(d) {
  const p = (d.gang || {}).pol;
  if (!p) return '';
  const role = p.role;
  const off = p.officers.map((o) => `<div class="feed"><span>👮</span><div class="grow">${esc(o.name)}${o.id < 0 ? ' 🤖' : ''}</div>${role === 'chief' && !o.ai && o.id !== p.captain_id ? `<button class="btn sm ghost" data-a="p3" data-p="police/dismiss" data-j='{"target":${o.id}}'>Уволить</button>` : o.id === p.captain_id ? '<span class="badge brand">капитан</span>' : ''}</div>`).join('') || '<div class="muted">Офицеров пока нет.</div>';
  const shiftCard = role ? `<div class="card"><b>💼 Смена</b><div class="muted">Гарантированная зарплата ${p.shift_pay || 80} ₽, +1 очко полиции, шанс найти улику. Обычные работы полицейскому недоступны.</div>
      <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="police/shift" ${p.shift_wait ? 'disabled' : ''}>Выйти на смену</button>${p.shift_wait ? `<div class="muted" style="margin-top:4px">Следующая смена через ${Math.ceil(p.shift_wait / 60)} мин</div>` : ''}</div>` : '';
  let out = `<div class="card"><div class="row"><div style="font-size:36px">🚓</div><div class="grow"><b>Полиция района</b><div class="muted">Капитан: ${p.chief ? esc(p.chief.name) : 'нет'} · фонд ${money(p.fund)} · порядок ${p.order || 0}% · патрулей: ${p.patrols}</div></div></div>
    <div class="muted" style="margin-top:6px">Полиция мешает ОПГ: патрули снижают шанс бандитских дел, дела на банды ведут к облавам и громким арестам. Фонд пополняется штрафами, изъятым и бюджетом района.</div>
    <div class="map-meter" style="margin-top:8px"><span>Порядок</span><div class="map-bar"><i style="width:${p.order || 0}%;background:#4c8dff"></i></div><b>${p.order || 0}%</b></div></div>
    ${shiftCard}
    ${policeMapHtml(p)}
    <h2>Офицеры</h2><div class="card">${off}</div>`;
  if (!role) {
    out += `<div class="card"><b>Вступить в полицию</b><div class="muted">Бандитам нельзя. Зарплата 100 ₽ в сутки из фонда, патрули и аресты, очки полицейского.</div>
      <button class="btn sm" style="margin-top:8px" data-a="p3" data-p="police/join" ${p.can_join ? '' : 'disabled'}>🚓 Вступить</button></div>`;
  } else {
    out += `<div class="card"><b>👮 Служба</b><div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px"><button class="btn sm" data-a="p3" data-p="police/patrol" ${p.patrol_wait ? 'disabled' : ''}>🚓 Патруль (⚡15)</button>
      ${role !== 'chief' ? '<button class="btn sm ghost" data-a="p3" data-p="police/leave" data-c="Сдать значок?">Уволиться</button>' : ''}</div>${p.patrol_wait ? `<div class="muted" style="margin-top:4px">Следующий обход через ${Math.ceil(p.patrol_wait / 60)} мин</div>` : ''}
      <div class="muted" style="margin-top:6px">Патруль сам находит подозреваемых в розыске. Арест нужен для тяжёлых случаев (розыск 3⭐, у членов банды с делом 60+ достаточно 1⭐).</div></div>`;
  }
  out += policeExtraHtml(p, role);
  if (role === 'chief') {
    const w = p.waits || {};
    const cases = (p.cases || []).map((c) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">${c.ai ? '🧓' : '🕴'} ${esc(c.name)}${c.own ? ' (твоя, не трогать)' : ''}</div>
        <div class="bar" style="height:8px;margin:4px 0"><i style="width:${Math.min(100, Math.round(c.pts / p.case_big * 100))}%;background:${c.pts >= p.case_big ? '#e5484d' : c.pts >= p.case_raid ? '#e67e22' : '#3fb950'}"></i></div>
        <div class="muted">дело ${c.pts}/${p.case_big} · ордер с ${p.case_raid}${c.wiretap ? ' · 🎧 слушаем ещё ' + gangDur(c.wiretap) : ''}</div></div>
        <div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap"><button class="btn sm bad" data-a="p3" data-p="police/raid" data-j='{"gid":${c.gang_id}}' ${c.own || w.raid || c.pts < p.case_raid ? 'disabled' : ''}>🚨 Облава · ${money(p.costs.raid)}</button>
        <button class="btn sm" data-a="p3" data-p="police/wiretap" data-j='{"gid":${c.gang_id}}' ${c.own || w.wire || c.wiretap ? 'disabled' : ''}>🎧 Прослушка · ${money(p.costs.wiretap)}</button>
        <button class="btn sm bad" data-a="p3" data-p="police/bigcase" data-j='{"gid":${c.gang_id}}' data-c="Закрыть громкое дело против «${esc(c.name)}»?" ${c.own || w.big || c.pts < p.case_big ? 'disabled' : ''}>⚖️ Громкое дело</button></div></div>`).join('') || '<div class="muted">Банд пока нет.</div>';
    const posts = (p.territories || []).map((t) => `<button class="btn sm ghost" style="margin:4px 6px 0 0" data-a="p3" data-p="police/checkpoint" data-j='{"key":"${t.key}"}' ${w.post ? 'disabled' : ''}>🚧 ${esc(t.name)}</button>`).join('');
    const bribes = (p.bribes || []).map((b) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">💵 «${esc(b.gang)}» предлагает ${money(b.amount)}</div><div class="muted">ещё ${gangDur(b.left)}</div></div>
        <div class="row" style="flex-basis:100%;gap:6px;margin-top:6px"><button class="btn sm" data-a="p3" data-p="police/bribe" data-j='{"bid":${b.id},"accept":true}'>Взять себе</button><button class="btn sm bad" data-a="p3" data-p="police/bribe" data-j='{"bid":${b.id},"accept":false}'>Отказать (+6 к делу, вернут половину)</button></div></div>`).join('');
    out += `<h2>Дела на банды</h2><div class="card">${cases}</div>${bribes ? '<h2>Конверты</h2><div class="card">' + bribes + '</div>' : ''}`;
  }
  return out;
}


function policeExtraHtml0(p, role) {
  if (!role) return '';
  const SELX = 'width:100%;margin-top:6px;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit';
  const w = p.waits || {};
  const alerts = (p.alerts || []).map((a) => `<div class="feed"><span>📡</span><div class="grow"><b>«${esc(a.gang)}»</b>: ${esc(a.text)}</div><span class="muted">${ago(a.ts)}</span></div>`).join('') || '<div class="muted">Пока тихо: донесений нет.</div>';
  const people = (p.people || []).map((x) => `<option value="${x.id}">${esc(x.name)}${x.wanted ? ' · ' + x.wanted + '⭐' : ''}${x.jailed ? ' · в камере' : ''}${x.gang ? ' · банда' : ''}</option>`).join('');
  const jailedGang = (p.people || []).filter((x) => x.jailed && x.gang).map((x) => `<button class="btn sm ghost" style="margin:4px 6px 0 0" data-a="p3" data-p="police/lawyer" data-j='{"target":${x.id}}'>🎭 Адвокат для ${esc(x.name)}</button>`).join('');
  let out = `${digestHtml(p)}
    <div class="card"><b>⚖️ Допросы</b><div class="muted">Вызов на допрос: 20 минут на явку. Показания дают деньги и улики, молчание рискованно, адвокат спасает. Не явился: розыск +1⭐ и камера.</div>
      <select id="polTarget" style="${SELX}">${people}</select>
      <div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap"><button class="btn sm" data-a="pol-summon" ${w.summon ? 'disabled' : ''}>⚖️ Вызвать на допрос</button>${role === 'chief' ? `<button class="btn sm bad" data-a="pol-search" ${w.search ? 'disabled' : ''}>🔎 Ордер на обыск (${money((p.costs || {}).warrant || 300)})</button>` : ''}</div>
      ${role === 'chief' ? '<div class="muted" style="margin-top:6px">Обыск: розыск 3⭐ или дело на банду от 40. Находят игровые автоматы, вещи, тайники; адвокат может сорвать ордер.</div>' : ''}
      ${jailedGang ? `<div class="muted" style="margin-top:8px">🎭 Подставной адвокат для задержанных бандитов (выйдет на свободу, но станет источником):</div><div>${jailedGang}</div>` : ''}</div>`;
  if (role === 'chief') {
    const pts = (p.points || []).map((x) => `<button class="btn sm ghost" style="margin:4px 6px 0 0" data-a="p3" data-p="police/inspect" data-j='{"key":"${x.key}"}' ${x.can ? '' : 'disabled'} title="${esc(x.why || '')}">${x.icon} ${esc(x.name)}${x.why ? ' · ' + esc(x.why) : ''}</button>`).join('');
    const mode = (p.ai || {}).mode;
    out += `<div class="card"><b>🏪 Проверка точек</b><div class="muted">Штраф банде, точка закрыта на 12 часов, дань уполовинена.</div><div>${pts || '<span class="muted">Нет точек под крышей.</span>'}</div></div>
      <div class="card"><b>🤖 ИИ-офицеры</b><div class="muted">Сержант Петров, лейтенант Громова и капитан Сидоренко патрулируют, задерживают розыскников, ставят прослушки, организуют облавы и закрывают громкие дела сами, чтобы начальнику не работать в одиночку.</div>
      <div class="row" style="margin-top:8px;gap:6px">${['off', 'normal', 'strict'].map((k) => `<button class="btn sm ${mode === k ? '' : 'ghost'}" data-a="p3" data-p="police/ai" data-j='{"mode":"${k}"}'>${{ off: 'Выкл', normal: 'Обычный', strict: 'Строгий' }[k]}</button>`).join('')}</div></div>`;
  }
  return out;
}

function balHtml(d) {
  const b = ((d.patrons || {}).world || {}).bal;
  if (!b) return '';
  const sp = b.spa || {}, bo = b.buyout || {};
  const ch = b.charity || {}, wf = b.welfare || {};
  const inGang = !!(d.gang && d.gang.mine);
  return `<div class="card"><b>🛁 Санаторий</b><div class="muted">⚡+${sp.gain} за ${money(sp.cost)}: энергия — главный ограничитель, и её можно купить. Не чаще раза в 3 часа и ${sp.limit} раз в сутки (сегодня ${sp.used}).</div>
    <div style="margin-top:8px"><button class="btn sm" data-a="p3" data-p="spa" data-j='{}' ${sp.wait > 0 || sp.used >= sp.limit ? 'disabled' : ''}>🛁 Лечь на процедуры${sp.wait > 0 ? ' · через ' + gangDur(sp.wait) : ''}</button></div></div>
    ${ch.amounts ? `<div class="card"><b>🎗 Благотворительность</b><div class="muted">Пожертвования идут району: 30% в фонд пособий, остальное тратится на праздники. За суммы дают титул: 1 000 ₽ Спонсор, 10 000 Меценат, 50 000 Почётный житель, 150 000 Благодетель. Ты пожертвовал ${money(ch.total)}${ch.title ? ' · ' + esc(ch.title) : ''}${ch.next ? ' · до «' + esc(ch.next[1]) + '» ещё ' + money(ch.next[0] - ch.total) : ''}.</div>
      <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap">${ch.amounts.map((a) => `<button class="btn sm ghost" data-a="p3" data-p="charity" data-j='{"amount":${a}}'>${money(a)}</button>`).join('')}</div>
      ${(ch.board || []).length ? `<div class="muted" style="margin-top:8px">Доска почёта: ${ch.board.map((x) => esc(x.name) + ' ' + money(x.total) + (x.title ? ' ' + esc(x.title) : '')).join(' · ')}</div>` : ''}</div>` : ''}
    ${wf.amount ? `<div class="card"><b>🤝 Пособие</b><div class="muted">Если на руках меньше ${money(wf.limit)} (и с тайником меньше 3 000 ₽), город выдаёт ${money(wf.amount)} раз в сутки. Фонд: ${money(wf.fund)}.</div>
      <div style="margin-top:8px"><button class="btn sm" data-a="p3" data-p="welfare/claim" data-j='{}' ${!wf.poor || wf.wait > 0 || wf.fund < wf.amount ? 'disabled' : ''}>Получить пособие${wf.wait > 0 ? ' · через ' + gangDur(wf.wait) : ''}</button></div></div>` : ''}
    ${inGang ? '' : `<div class="card"><b>🧾 Откуп от налога банд</b><div class="muted">${bo.left > 0 ? 'Налог банд не берётся ещё ' + gangDur(bo.left) : 'Заплати один раз ' + money(bo.cost) + ' и банды не берут налог с твоей работы ' + bo.days + ' дней. Адвокат или защита банды режут налог вдвое.'}</div>
    <div style="margin-top:8px"><button class="btn sm ghost" data-a="p3" data-p="tax/buyout" data-j='{}' ${bo.left > 0 ? 'disabled' : ''}>Откупиться · ${money(bo.cost)}</button></div></div>`}`;
}

function econHtml(d) {
  const e = ((d.gang || {}).pc || {}).econ;
  if (!e) return '';
  return `<h2>Расходы и тайник</h2><div class="card"><b>🧾 Расходы в сутки</b><div class="muted">Жильё, машина и ларёк стоят денег каждый день: ${money(e.upkeep)}. Налог на богатство прогрессивный: 1.5% с суммы наличных выше ${money(e.wealth_from)}, 3% выше 60 000 ₽, 5% выше 150 000 ₽ (сейчас ${money(e.wealth)} в сутки). Лишние деньги выгоднее вкладывать в дело, вещи или тайник.</div></div>
    <div class="card"><b>🗄 Тайник</b><div class="muted">${money(e.stash)} из ${money(e.stash_cap)}. Деньги в тайнике не достанут грабители и шантажисты (но обыск по ордеру найдёт).</div>
      <div class="row" style="margin-top:8px;gap:6px"><input id="stashAmt" type="number" min="50" value="500" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><button class="btn sm" data-a="stash-put">Положить</button><button class="btn sm ghost" data-a="stash-take">Забрать</button></div></div>`;
}

function hitHtml(d) {
  const pc = (d.gang || {}).pc || {};
  const t = pc.order_targets || [];
  const kinds = pc.order_kinds || [];
  const tiers = pc.order_tiers || [];
  if (!t.length || !kinds.length || !tiers.length) return '';
  const kind = kinds.find((x) => x.key === S.ordKind) || kinds[0];
  const tier = tiers.find((x) => x.price === S.ordPrice) || tiers[1] || tiers[0];
  const mine = d.gang && d.gang.mine;
  const gangs = pc.order_gangs || [];
  const gs = gangs.map((x) => `<option value="${x.id}" ${String(x.id) === String(S.ordG) ? 'selected' : ''}>${x.ai ? '🧓' : '🕴'} ${esc(x.name)}${x.ai ? ' · берётся сразу' : ' · спросят босса'}</option>`).join('');
  const why = pc.order_wait ? 'следующий заказ через ' + gangDur(pc.order_wait) : mine && mine.my_role === 'grunt' ? 'заказы от банды делает босс или зам' : !gangs.length ? 'нет банд-исполнителей' : '';
  const chance = Math.max(20, Math.min(90, kind.chance + tier.bonus));
  return `<h2>🥊 Заказ</h2><div class="card">
    <div class="muted">Нанять чужую банду против игрока. Платит ${mine ? 'общак банды' : 'твой кошелёк'}.</div>
    <div class="earn-toggle" style="margin:8px 0 0">${kinds.map((k) => `<button data-a="ord-kind" data-v="${k.key}" class="${k.key === kind.key ? 'on' : ''}">${k.icon} ${esc(k.title)}</button>`).join('')}</div>
    <div class="muted" style="margin-top:6px">${kind.icon} ${esc(kind.desc)}.</div>
    <select id="ordTarget" style="${SEL}">${t.map((x) => `<option value="${x.id}" ${String(x.id) === String(S.ordT) ? 'selected' : ''}>${esc(x.name)}</option>`).join('')}</select><select id="ordGang" style="${SEL}">${gs}</select>
    <div class="earn-toggle" style="margin:8px 0 0">${tiers.map((x) => `<button data-a="ord-price" data-v="${x.price}" class="${x.price === tier.price ? 'on' : ''}">${esc(x.title)} · ${money(x.price)}</button>`).join('')}</div>
    <div class="muted" style="margin-top:6px">Шанс успеха около ${chance}%: оружие и охрана цели его снижают. Полиция выйдет на заказчика с шансом ${tier.notice}%. При провале деньги не вернут, а исполнитель может назвать твоё имя (30%).</div>
    <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap"><button class="btn sm bad" data-a="order-go" ${why ? 'disabled' : ''}>Заказать · ${money(tier.price)}</button>${why ? `<span class="muted lock-why">🔒 ${why}</span>` : ''}</div></div>`;
}

function tasksBannerHtml(d) {
  const t = ((d.patrons || {}).world || {}).tasks;
  if (!t || !(t.incoming || []).length) return '';
  return t.incoming.map((a) => `<div class="card" style="border:2px solid #3b82f6"><b>📋 Задание от ${esc(a.by)}: ${a.icon} ${esc(a.title)}</b><div class="muted">${esc(a.desc)}. Осталось ${gangDur(a.left)}. Проигнорируешь: его отправят автоматом, но шанс успеха ×${t.auto_chance / 100}, добыча ×0.7, а авторитет −1.</div>
    <div style="margin-top:8px">${wBtn('task/do', { tid: a.id }, '▶ Выполнить · ⚡' + t.energy)}</div></div>`).join('');
}

function tasksGiveHtml(org) {
  const d = S.rp;
  const t = (((d || {}).patrons || {}).world || {}).tasks;
  const o = t && (t.orgs || []).find((x) => x.org === org);
  if (!o) return '';
  const who = org === 'gang' ? 'участнику банды' : 'офицеру';
  const hist = (o.history || []).map((h) => `<div class="muted" style="margin-top:4px">${h.icon} ${esc(h.title)} → ${esc(h.to)}: ${h.status === 'open' ? 'ждёт ещё ' + gangDur(h.left) : esc(h.result || h.status)}</div>`).join('');
  if (!o.targets.length) return `<div class="card"><b>📋 Задания</b><div class="muted">Некому давать задания: в ${org === 'gang' ? 'банде' : 'полиции'} нет других живых игроков.</div></div>`;
  return `<div class="card"><b>📋 Задания ${who}</b><div class="muted">Дай поручение: у исполнителя 30 минут. Не успел или проигнорировал: задание уйдёт автоматом, но шанс ×${t.auto_chance / 100}, добыча ×0.7, исполнитель теряет очко ${org === 'gang' ? 'авторитета' : 'репутации'}. Выполнил сам: +2 очка.</div>
    <select id="asgTarget_${org}" style="${SEL}">${o.targets.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('')}</select>
    <select id="asgKind_${org}" style="${SEL}">${o.kinds.map((k) => `<option value="${k.key}">${k.icon} ${esc(k.title)}: ${esc(k.desc)}</option>`).join('')}</select>
    <div style="margin-top:8px"><button class="btn sm" data-a="task-give-${org}" ${o.wait > 0 ? 'disabled' : ''}>📋 Дать задание${o.wait > 0 ? ' · через ' + gangDur(o.wait) : ''}</button></div>${hist}</div>`;
}

function gangMgmtHtml0(m) {
  const dep = m.is_boss || m.my_role === 'deputy';
  if (!dep) return '';
  const mem = (m.members || []).filter((x) => x.id > 0 && !x.is_me).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
  const lon = ((m.p13 || {}).loners || []).map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
  return `<div class="card"><b>🎁 Премии и штрафы</b><div class="muted">Премия из общака (+2 авторитета) или штраф в общак (−3 авторитета, раз в сутки на человека).</div>
      <select id="mgTarget" style="${SEL}">${mem}</select><div class="row" style="margin-top:6px;gap:6px"><input id="mgAmt" type="number" min="50" max="1000" value="200" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><button class="btn sm" data-a="bonus-go">Премия</button><button class="btn sm bad" data-a="fine-go">Штраф</button></div></div>
    ${m.is_boss ? `<div class="card"><b>🤝 Крыша для одиночки</b><div class="muted">Предложи жителю без банды покровительство на неделю: он платит в общак, ИИ-банды его не трогают.</div>
      <select id="prTarget" style="${SEL}">${lon}</select><div class="row" style="margin-top:6px;gap:6px"><input id="prPrice" type="number" min="300" max="1500" value="500" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><button class="btn sm" data-a="protect-go">Предложить</button></div></div>` : ''}`;
}

function gangMgmtHtml(m) { return gangMgmtHtml0(m) + tasksGiveHtml('gang'); }
function policeExtraHtml(p, role) { return policeExtraHtml0(p, role) + ctlPoliceHtml(p, role) + tasksGiveHtml('police'); }

function justiceHtml(d) {
  const t = d && d.justice && d.justice.trial;
  if (!t) return '';
  const roles = { mayor: 'мэр', godfather: 'Крёстный отец', police: 'полицейский', gang: 'ОПГ' };
  const mins = (x) => Math.max(1, Math.ceil(x / 60)) + ' мин';
  const head = `<b>⚖️ Суд ${t.is_me ? 'над тобой' : 'над ' + esc(t.name || 'игроком')} · до приговора ${mins(t.left)}</b>
    <div class="muted">Обвинение (${roles[t.role] || esc(t.role)}): ${t.charges.map(esc).join(', ')}.</div>
    <div class="muted" style="margin-top:4px">Приговор сейчас: штраф ${money(t.fine)} · ${t.jail ? 'тюрьма ' + mins(t.jail) : 'без тюрьмы'}${t.lose ? ' · потеря должности' : ''}${t.prior ? ` · судимостей ${t.prior} (+25% к штрафу за каждую)` : ''}.</div>
    <div class="muted" style="margin-top:4px">Свидетели: против ${t.wit_against}, алиби ${t.wit_for}.</div>`;
  let acts = '';
  if (t.is_me) {
    const b = [];
    b.push(t.can_defend ? `<button class="btn sm" data-a="p3" data-p="justice/defend" data-c="Выпустить адвоката? Контракт с ним на этом закончится.">⚖️ Адвокат · штраф −30%, срок −40%</button>`
      : `<button class="btn sm ghost" disabled>⚖️ Адвокат</button><span class="muted lock-why">🔒 ${t.defended ? 'адвокат уже выступил' : 'адвоката нет: наймёшь в «Дом → Услуги»'}</span>`);
    if (t.can_plea) b.push(`<button class="btn sm ghost" data-a="p3" data-p="justice/plea" data-c="Признать вину? Приговор мягче, но конверт судье уже не занести, а дело у детектива останется открытым.">🙏 Признать вину · штраф −25%, срок вдвое меньше</button>`);
    if (t.can_bribe) b.push(`<button class="btn sm ghost" data-a="justice-bribe">💵 Конверт судье · ${money(t.bribe)}</button>`);
    if (t.can_snitch) b.push(`<button class="btn sm bad" data-a="justice-snitch">🗣 Сдать своего: ${esc(t.snitch_name)}${t.snitch_ai ? ' (ИИ)' : ''}</button>`);
    acts = `<div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap">${b.join('')}</div>
      <div class="muted" style="margin-top:6px;font-size:11.5px">Конверт: 75% — штраф втрое меньше и без тюрьмы, 25% — приговор жёстче; купленный приговор оставляет хвост у детектива. Сдать своего: штраф вдвое меньше, должность цела, но человек потерян. Приговор без конверта идёт в судимости.${t.bribed ? ' Конверт уже у судьи.' : ''}${t.plea ? ' Вина признана.' : ''}</div>`;
  } else if (t.can_witness) {
    acts = `<div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap"><button class="btn sm bad" data-a="p3" data-p="justice/witness" data-j='{"side":"against"}'>🗣 Показания против · +150 ₽</button>
      <button class="btn sm ghost" data-a="p3" data-p="justice/witness" data-j='{"side":"for"}' data-c="Дать алиби? С шансом 30% суд поймает на лжи: +1⭐ розыска.">🤝 Дать алиби</button></div>
      <div class="muted" style="margin-top:6px;font-size:11.5px">Показания против: штраф +10%. Алиби: штраф и срок −10%, но суд может поймать на лжи.</div>`;
  } else if (t.my_wit) {
    acts = `<div class="muted" style="margin-top:6px">Ты уже дал ${t.my_wit === 'for' ? 'алиби' : 'показания против'}.</div>`;
  }
  return `<div class="card" style="border:2px solid #e5484d">${head}${acts}</div>`;
}

function p12Html(d) {
  const pc = (d.gang && d.gang.pc) || {};
  let out = justiceHtml(d);
  if (pc.summon) out += `<div class="card" style="border:2px solid #e5484d"><b>⚖️ Тебя вызвали на допрос</b><div class="muted">На явку ещё ${Math.max(1, Math.ceil(pc.summon.left / 60))} мин. Не явишься: розыск +1⭐ и камера.</div>
    <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap"><button class="btn sm" data-a="p3" data-p="police/summon/answer" data-j='{"sid":${pc.summon.id},"choice":"testify"}'>🗣 Дать показания</button><button class="btn sm ghost" data-a="p3" data-p="police/summon/answer" data-j='{"sid":${pc.summon.id},"choice":"silent"}'>🤐 Молчать</button><button class="btn sm ghost" data-a="p3" data-p="police/summon/answer" data-j='{"sid":${pc.summon.id},"choice":"lawyer"}'>⚖️ С адвокатом</button></div></div>`;
  if (pc.pol_lawyer) out += `<div class="card" style="border:2px solid #9b59b6"><b>🎭 «Бесплатный адвокат»</b><div class="muted">Тебе предлагают адвоката и свободу прямо сейчас. Ещё ${Math.max(1, Math.ceil(pc.pol_lawyer.left / 60))} мин. Но у полиции будут рычаги, а банда может заподозрить неладное.</div>
    <div class="row" style="margin-top:8px;gap:6px"><button class="btn sm" data-a="p3" data-p="police/lawyer/answer" data-j='{"oid":${pc.pol_lawyer.id},"accept":true}'>Согласиться</button><button class="btn sm ghost" data-a="p3" data-p="police/lawyer/answer" data-j='{"oid":${pc.pol_lawyer.id},"accept":false}'>Отказаться</button></div></div>`;
  if (pc.prot_offer) out += `<div class="card" style="border:2px solid #3fb950"><b>🤝 Покровительство от «${esc(pc.prot_offer.gang)}»</b><div class="muted">Неделя защиты от ИИ-банд за ${money(pc.prot_offer.price)}.</div>
    <div class="row" style="margin-top:8px;gap:6px"><button class="btn sm" data-a="p3" data-p="protect/answer" data-j='{"pid":${pc.prot_offer.id},"accept":true}'>Принять</button><button class="btn sm ghost" data-a="p3" data-p="protect/answer" data-j='{"pid":${pc.prot_offer.id},"accept":false}'>Отказаться</button></div></div>`;
  (pc.orders_in || []).forEach((o) => { out += `<div class="card" style="border:2px solid #e67e22"><b>🥊 Заказ: ${esc(o.kind || 'избить')}</b><div class="muted">Цель: ${esc(o.target)} · ${money(o.price)} в общак. Ещё ${gangDur(o.left)}.</div>
    <div class="row" style="margin-top:8px;gap:6px"><button class="btn sm bad" data-a="p3" data-p="gang/order/answer" data-j='{"oid":${o.id},"accept":true}'>Взяться</button><button class="btn sm ghost" data-a="p3" data-p="gang/order/answer" data-j='{"oid":${o.id},"accept":false}'>Отказать</button></div></div>`; });
  out += tasksBannerHtml(d) + worldBannerHtml(d);
  return out;
}

function gangPlusHtml(g, m, jailed, tab) {
  const c = m.cuts || {};
  const cuts = `<div class="card"><b>💰 Общак: ${money(m.treasury || 0)}</b>
    <div class="muted">Делёж налёта: босс +${c.boss}%, замы +${c.deputy}% каждый, в общак ${c.treasury}%, остальное поровну. Районы платят боссу ×1.5, замам ×1.2, боссу ещё +5% к шансу дел.</div>
    <div class="muted" style="margin-top:4px">${c.active ? '✅ Банда в деле: привилегии босса действуют.' : '⚠️ Банда простаивает больше ' + c.idle_hours + ' ч: босс остался без доли. Нужен налёт, рейд или дело.'}</div>
    <div class="muted" style="margin-top:4px">Из общака платят за улики и выкуп из тюрьмы (500 ₽). Холопам взамен: своих не грабят, штраф за тюрьму меньше на 30%.</div>
    <div class="row" style="margin-top:8px;flex-wrap:wrap"><button class="btn sm" data-a="gang-donate" data-v="100">+100 ₽</button><button class="btn sm" data-a="gang-donate" data-v="500">+500 ₽</button><button class="btn sm" data-a="gang-donate" data-v="1000">+1000 ₽</button></div></div>`;
  let coup = '';
  const r = m.coup_rules || {};
  if (m.coup) {
    const k = m.coup;
    coup = `<div class="card"><b>⚔️ Разборки: ${esc(k.challenger_name)} против босса</b>
      <div class="muted">Осталось ${gangDur(k.left)} · за ${k.yes}, против ${k.no} · нужно ${k.need} из ${k.voters}</div>
      ${k.can_vote ? `<div class="row" style="margin-top:8px"><button class="btn sm ${k.my_vote === 'for' ? '' : 'ghost'}" data-a="gang-vote" data-v="for">👑 За переворот</button><button class="btn sm ${k.my_vote === 'against' ? '' : 'ghost'}" data-a="gang-vote" data-v="against">🛡 За босса</button></div>` : '<div class="muted" style="margin-top:6px">Босс и претендент не голосуют.</div>'}</div>`;
  } else if (m.my_role !== 'boss') {
    const why = { merit: `нужно авторитета ${r.merit}, у тебя ${m.my_merit}`, days: `нужно ${r.days} дня в банде, у тебя ${m.my_days}`, week: 'ты уже бросал вызов на этой неделе', fresh: 'новый босс ещё не освоился: 3 дня тишины', voters: 'мало голосующих в банде' }[m.coup_block] || '';
    coup = `<div class="card"><b>👑 Путь наверх</b><div class="muted">Авторитет ${m.my_merit}/${r.merit} (растёт за дела, налёты, рейды и взносы в общак) · стаж ${m.my_days}/${r.days} дн.</div>
      <div class="muted" style="margin-top:4px">Вызов боссу: залог ${money(r.deposit)}, банда голосует ${r.hours} ч. Победил: ты босс, он зам. Проиграл: залог уходит боссу.</div>
      <button class="btn bad" style="margin-top:8px" data-a="rp" data-p="gang/coup" ${m.coup_can && !jailed ? '' : 'disabled'}>⚔️ Бросить вызов боссу</button>
      ${why ? `<div class="muted" style="margin-top:6px">${why}</div>` : ''}</div>`;
  }
  const T = { main: cuts + duesHtml(m) + coup + meetHtml(m), ops: warOpsHtml(m) + prisonHelpHtml(m) + rheistHtml(m, jailed) + opsHtml(m, jailed), eco: stockHtml(m, jailed) + racketsHtml(m) + dealsHtml(m) + lendHtml(m) + projectsHtml(m),
    map: mapSvgHtml(g, m) + mapHtml(g, m) + pactsHtml(g, m) + repHtml(m), team: brigadeHtml(m) + specsHtml(m, jailed), spy: policeBribeHtml(m) + defectHtml(m) + plotHtml(m, jailed) + ciHtml(m) + copHtml(g, m, jailed) + amnestyHtml(m) };
  return tab ? (T[tab] || '') : Object.values(T).join('');
}

function ratHtml(g, m, jailed) {
  if (m.is_boss) {
    const r = m.rat;
    if (!r) return '';
    const clues = r.clues.length ? r.clues.map((c) => `<div class="feed"><span>🔎</span><div class="grow">${esc(c.text)}</div></div>`).join('') : '<div class="muted">Улик пока нет: первая придёт через несколько минут.</div>';
    const rows = r.dossier.map((d) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">${esc(d.name)}</div>
        <div class="muted">${d.role === 'deputy' ? '🥈 зам' : '🪓 холоп'} · ${d.days} дн. · ${d.car ? '🚗 на тачке' : '🚶 пешком'} · ${d.drunk ? '🍻 пьёт' : '🚰 трезв'} · ${d.rich ? '💰 богаче среднего' : '👛 беднее среднего'} · ${d.hot ? '⭐ 3+' : '⭐ меньше 3'} · имя на «${esc(d.letter)}»</div></div>
        <button class="btn sm ghost" data-a="gang-act" data-p="gang/interrogate" data-u="${d.id}" ${r.can_interrogate ? '' : 'disabled'}>Допросить</button>
        <button class="btn sm bad" data-a="gang-act" data-p="gang/accuse" data-u="${d.id}" data-c="Обвинить ${esc(d.name)}? Ошибка стоит 800 ₽, авторитета и одной из двух попыток." ${r.can_accuse ? '' : 'disabled'}>Обвинить</button></div>`).join('');
    return `<div class="card"><b>🕵 Дело о крысе</b>
      <div class="muted">Банду кто-то сдал. До закрытия дела ${gangDur(r.left)}. Попыток обвинить: ${r.attempts_left}. Чтобы обвинять, нужно открыть ${r.need} улики.</div>
      <h2 style="margin-top:12px">Улики</h2>${clues}
      <div class="row" style="margin-top:8px"><button class="btn sm" data-a="rp" data-p="gang/clue" ${r.can_buy ? '' : 'disabled'}>🔎 Купить улику · ${money(r.buy_cost)}</button>${r.buy_wait ? `<span class="muted">следующую через ${gangDur(r.buy_wait)}</span>` : ''}</div>${!r.can_buy && !r.buy_wait ? '<div class="muted" style="margin-top:6px">Покупать пока нечего: скрытые улики появляются, когда крыса сливает информацию (она может это раз в 2 часа). Вот придёт слив, и кнопка станет активной.</div>' : ''}
      <h2>Подозреваемые и досье</h2>${rows}
      <div class="muted" style="margin-top:6px">Допрос: ⚡ ${r.int_energy || 15}, осталось ${(r.int_left || 0)} из 3, раз в час.${r.int_wait ? ' Следующий через ' + gangDur(r.int_wait) + '.' : ''} Невиновный всегда говорит правду, а крыса иногда врёт или проговаривается.</div>
      ${(r.answers || []).length ? '<h2>Ответы на допросах</h2>' + r.answers.map((t) => `<div class="feed"><span>🗣</span><div class="grow">${esc(t)}</div></div>`).join('') : ''}
      ${r.accuse_wait ? `<div class="muted" style="margin-top:6px">После ошибки остывать ещё ${gangDur(r.accuse_wait)}.</div>` : ''}
      <div class="muted" style="margin-top:6px">Каждая загадка описывает настоящего крысу, сверяй её с досье. Но крыса умеет подбрасывать ложный след.</div></div>`;
  }
  const k = m.my_rat;
  if (k) {
    const opts = k.targets.map((x) => `<option value="${x.id}">${esc(x.name)}</option>`).join('');
    return `<div class="card"><b>🐀 Тайный квест: остаться незамеченным</b>
      <div class="muted">Дело открыто ещё ${gangDur(k.left)}. Не вычислит босс, получишь ${money(k.escape_pay)} (+200 ₽ за каждый слив). Улик у босса сейчас: ${k.clues_out}. Сливов было: ${k.leaks}.</div>
      <div class="row" style="margin-top:8px"><button class="btn sm" data-a="rp" data-p="gang/leak" ${k.leak_wait ? 'disabled' : ''}>🐀 Слив · +${k.leak_pay[0]}–${k.leak_pay[1]} ₽</button>${k.leak_wait ? `<span class="muted">через ${gangDur(k.leak_wait)}</span>` : ''}</div>
      <div class="muted" style="margin-top:6px">Слить можно раз в 2 часа. Каждая утечка с шансом 25% даёт боссу новую улику.</div>
      ${k.can_frame ? `<div class="row" style="margin-top:10px"><select id="ratTarget" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit">${opts}</select><button class="btn sm" data-a="gang-frame">🎭 Подставить · ${money(k.frame_cost)}</button></div>
        <div class="muted" style="margin-top:4px">Через 15 минут у босса появится улика на выбранного. Если босс его обвинит, ты получишь +700 ₽.</div>` : '<div class="muted" style="margin-top:6px">Ты уже подставил одного человека в этом деле.</div>'}</div>`;
  }
  return `<button class="btn ghost" style="margin-top:10px" data-a="gang-act" data-p="gang/betray" data-c="Сдать банду полиции? Заплатят 800–1500 ₽, у остальных вырастет розыск, а босс начнёт расследование. Если тебя вычислят, вылетишь со штрафом.">🐀 Сдать банду полиции</button>`;
}

const GANG_ROLE = { boss: ['👑', 'Главный босс'], deputy: ['🥈', 'Зам'], grunt: ['🪓', 'Холоп'] };

function gangHtml(d, jailed) {
  const g = d.gang;
  if (!g) return '';
  if (g.mine) {
    const m = g.mine;
    const members = m.members.map((x) => {
      const [ic, rn] = GANG_ROLE[x.role] || GANG_ROLE.grunt;
      let acts = '';
      if (x.can_rank) {
        acts += x.role !== 'deputy' ? `<button class="btn sm ghost" data-a="gang-act" data-p="gang/role" data-u="${x.id}" data-r="deputy">🥈 Зам</button>` : '';
        acts += x.role !== 'grunt' ? `<button class="btn sm ghost" data-a="gang-act" data-p="gang/role" data-u="${x.id}" data-r="grunt">🪓 Холоп</button>` : '';
        acts += `<button class="btn sm ghost" data-a="gang-act" data-p="gang/role" data-u="${x.id}" data-r="boss" data-c="Передать тебе звание босса ${esc(x.name)}? Ты станешь замом.">👑 Босс</button>`;
      }
      if (m.can_bail && x.jailed) acts += `<button class="btn sm ghost" data-a="gang-act" data-p="gang/bail" data-u="${x.id}">🔓 Выкупить · 500 ₽ из общака</button>`;
      if (x.can_kick) acts += `<button class="btn sm bad" data-a="gang-act" data-p="gang/kick" data-u="${x.id}" data-c="Выгнать ${esc(x.name)} из банды?">Выгнать</button>`;
      return `<div class="list-item" style="flex-wrap:wrap"><div class="av">${ic}</div><div class="grow"><div class="name">${esc(x.name)}${x.id < 0 ? ' 🤖' : ''}</div><div class="muted">${rn} · авторитет ${x.merit || 0}${x.jailed ? ' · 🚔 в тюрьме' : ''}${x.dues_late ? ' · ⚠️ взнос просрочен' : ''}${x.probation ? ' · 🧪 испытательный срок' : ''}</div></div>
        ${acts ? `<div class="row" style="flex-basis:100%;gap:6px;margin-top:6px;flex-wrap:wrap">${acts}</div>` : ''}</div>`;
    }).join('');
    const can = !jailed && (m.player_count || 0) >= 2 && !m.heist_left;
    const [myIc, myRn] = GANG_ROLE[m.my_role] || GANG_ROLE.grunt;
    let invite = '';
    if (m.can_invite) {
      const opts = m.candidates.map((x) => `<option value="${x.id}">${esc(x.name)} · ${x.cred || 0} авт.</option>`).join('');
      invite = `<div class="card"><b>📨 Пригласить в банду</b>
        ${m.candidates.length ? `<div class="row" style="margin-top:8px"><select id="gangInv" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit">${opts}</select>
        <button class="btn sm" data-a="gang-invite">Позвать</button></div><div class="muted" style="margin-top:4px">Для вступления нужно ${m.join_cred || 10} уличного авторитета. Кандидаты с меньшим — сначала пусть сделают чёрные дела.</div>` : '<div class="muted" style="margin-top:6px">Некого звать: все уже в бандах или приглашены.</div>'}
        ${m.invited.length ? `<div class="muted" style="margin-top:8px">Ждут ответа:</div>${m.invited.map((x) => `<div class="row" style="margin-top:4px"><div class="grow">${esc(x.name)}</div><button class="btn sm ghost" data-a="gang-act" data-p="gang/uninvite" data-u="${x.id}">Отозвать</button></div>`).join('')}` : ''}</div>`;
    }
    const head = `<div class="card"><b>🕴 ОПГ «${esc(m.name)}»</b><div class="muted">Игроков ${m.player_count || 0}/${g.max} · ИИ-помощников ${m.ai_helpers != null ? m.ai_helpers : (m.ai_count || 0)}/${m.max_ai || 3}${m.ai_citizens ? ' · ИИ-жителей ' + m.ai_citizens : ''}. Бонус к шансу дел: +${m.bonus}%. Твой ранг: ${myIc} ${myRn}</div></div>
      <div class="card"><b>Налёт на всю банду</b><div class="muted">Шанс ~${m.heist_chance}% · ⚡ −${m.heist_energy} · добыча делится по ролям: босс и замы получают больше, часть уходит в общак</div>
      ${m.heist_left ? `<div class="muted" style="margin-top:6px">Банда отдыхает ещё ${Math.ceil(m.heist_left / 60)} мин.</div>` : ''}
      ${(m.player_count || 0) < 2 ? '<div class="muted" style="margin-top:6px">Нужен хотя бы ещё один игрок.</div>' : ''}
      <button class="btn bad" style="margin-top:8px" data-a="rp" data-p="gang/heist" ${can ? '' : 'disabled'}>Устроить налёт</button></div>`;
    const roster = `<h2>Состав</h2>${members}
      <div class="muted" style="margin-top:8px">Босс: зовёт, выгоняет, меняет ранги (замов до ${m.max_deputies}). Зам: зовёт и выгоняет холопов.</div>
      <button class="btn ghost" style="margin-top:10px" data-a="rp" data-p="gang/leave">Уйти из банды</button>`;
    return gangTabs(g, m, jailed, head, invite, roster);
  }
  const inv = (g.invites || []).map((x) => `<div class="list-item"><div class="av">📨</div><div class="grow"><div class="name">${esc(x.name)}</div><div class="muted">${x.n}/${g.max} · зовёт ${esc(x.from)}</div></div>
      <button class="btn sm" data-a="rp" data-p="gang/accept" data-g="${x.id}" ${jailed ? 'disabled' : ''}>Вступить</button>
      <button class="btn sm ghost" data-a="rp" data-p="gang/decline" data-g="${x.id}">Нет</button></div>`).join('');
  return `${inv ? `<h2>Тебя зовут в банду</h2>${inv}` : ''}
    <div class="card"><b>🕴 Основать ОПГ</b><div class="muted">Стоит ${money(g.cost)}. В банде до ${g.max} игроков: боссом будешь ты, замов можно назначить двоих, остальные — холопы. В банду попадают только по приглашению.</div>
      <div class="row" style="margin-top:8px"><input id="gangName" maxlength="20" placeholder="Название банды" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit">
      <button class="btn sm" data-a="gang-create">Создать</button></div></div>
    ${repentHtml(g)}${copHtml(g, null, jailed)}
    ${gangExtraHtml(g, null, jailed)}`;
}

S.charDraft = null;
function heroHtml(d) {
  const c = d.config.char;
  if (!S.charDraft) S.charDraft = { icon: d.me.char.icon || '🧑', hat: d.me.char.hat || '', acc: d.me.char.acc || '', bio: d.me.char.bio || '' };
  const dr = S.charDraft;
  const grid = (kind, list) => `<div class="emojis">${list.map((v) => `<button data-a="char-pick" data-k="${kind}" data-v="${v}" class="${dr[kind] === v ? 'on' : ''}">${v || '—'}</button>`).join('')}</div>`;
  return `<div class="card" style="text-align:center"><div style="font-size:56px;line-height:1.1">${esc(dr.hat + dr.icon + dr.acc)}</div><div class="muted">${esc(dr.bio) || 'Твой персонаж в районе'}</div></div>
    <h2>Образ</h2>${grid('icon', c.icons)}<h2>Головной убор</h2>${grid('hat', c.hats)}<h2>Аксессуар</h2>${grid('acc', c.accs)}
    <h2>О себе</h2><input id="charBio" maxlength="40" value="${esc(dr.bio)}" placeholder="Например: бывший чемпион по шахматам" style="width:100%;border:0;border-radius:12px;padding:12px;background:var(--card);color:var(--text);font:inherit">
    <button class="btn" style="margin-top:10px" data-a="char-save">Сохранить персонажа</button>`;
}


function p7Html(d) {
  const p7 = (d.gang && d.gang.p7) || {};
  let out = '';
  (p7.events || []).forEach((e) => {
    const pct = e.goal ? Math.min(100, Math.round(e.progress / e.goal * 100)) : 0;
    const unit = e.kind === 'boss' ? ' ₽' : '';
    out += `<div class="card" style="border:2px solid #f5a623"><div class="row"><div style="font-size:32px">${e.icon}</div><div class="grow"><b>${esc(e.title)}</b>
      <div class="muted">осталось ${Math.max(1, Math.ceil(e.left / 60))} мин${e.goal ? ' · участников ' + e.people : ''}</div></div></div>
      <div class="muted" style="margin-top:6px">${esc(e.text)}</div>
      ${e.goal ? `<div class="bar" style="height:10px;margin-top:8px"><i style="width:${pct}%"></i></div><div class="muted">${e.progress} / ${e.goal}${unit}</div>` : ''}
      ${e.buttons.length ? `<div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px">${e.buttons.map((b) => `<button class="btn sm" data-a="p3" data-p="event" data-j='{"eid":${e.id},"action":"${b.key}"}' ${b.left <= 0 ? 'disabled' : ''}>${esc(b.title)}</button>`).join('')}</div>` : ''}</div>`;
  });
  const v = p7.vote;
  if (v) {
    out += `<div class="card" style="border:2px solid #6c8cff"><b>🏛 Народный сход</b><div class="muted">Чат голосует за закон района на неделю. Нужно минимум ${v.need} голоса, сейчас ${v.total}. До конца ${Math.max(1, Math.ceil(v.left / 3600))} ч.</div>
      ${v.laws.map((l) => `<div class="list-item"><div class="grow"><div class="name">${l.icon} ${esc(l.title)} · ${l.votes}</div><div class="muted">${esc(l.desc)}</div></div>
        <button class="btn sm ${v.mine === l.key ? '' : 'ghost'}" data-a="p3" data-p="law" data-j='{"vid":${v.id},"law":"${l.key}"}'>${v.mine === l.key ? '✅ Мой' : 'Голос'}</button></div>`).join('')}</div>`;
  }
  if (p7.law) out += `<div class="muted" style="margin:0 0 10px">${p7.law.icon} Действует закон «${esc(p7.law.title)}»: ${esc(p7.law.desc)}. Ещё ${Math.ceil(p7.law.left / 86400)} дн.</div>`;
  return out;
}

async function loadFlat(owner) {
  try { S.flat = await api(`/api/chat/${S.chat}/flat${owner ? '?owner=' + owner : ''}`); S.flatOwner = owner || 0; } catch (e) { toast(e.message); }
  if (S.tab === 'rp' && S.rpSub === 'own' && S.homeTab === 'flat') renderRp();
}

async function flatDo(path, data) {
  if (S.busy) return;
  S.busy = true;
  try {
    const d = await api(`/api/chat/${S.chat}/rp/flat/${path}`, 'POST', data);
    S.flat = d.flat;
    haptic(d.good ? 'ok' : 'bad'); toast(d.text);
    if (S.flat && S.flat.self) S.flatSel = null;
    keepScroll(renderRp);
    api(`/api/chat/${S.chat}/rp`).then((r) => { S.rp = r; }).catch(() => {});
  } catch (e) { toast(e.message); haptic('bad'); }
  S.busy = false;
}

function flatHtml() {
  const f = S.flat;
  if (!f) { loadFlat(S.flatOwner || 0); return '<div class="empty">Загрузка…</div>'; }
  const sel = S.flatSel;
  const grid = f.slots.map((s) => `<button class="flat-slot ${f.self && sel === s.slot ? 'sel' : ''}" data-a="flat-slot" data-s="${s.slot}" ${f.self ? '' : 'disabled'} title="${esc(s.name)}">${s.emoji || (f.self ? '＋' : '')}</button>`).join('');
  const head = `<div class="card"><div class="row"><div class="grow"><b>🏠 Квартира: ${esc(f.owner_name)}</b><div class="muted">уют ${f.cozy}${f.self ? ' · ⚡ восстановление +' + f.regen_bonus + '%' : ''} · гостей сегодня ${f.visits_today}${f.allow_steal ? ' · 🔓 дверь открыта' : ''}</div></div>
      ${f.self ? '' : '<button class="btn sm ghost" data-a="flat-go" data-o="0">← К себе</button>'}</div>
      <div class="flat-room" style="background:${f.theme_color}"><div class="flat-grid">${grid}</div></div></div>`;
  let mine = '';
  if (f.self) {
    const selItem = sel != null ? f.slots[sel] : null;
    const shop = f.shop.map((x) => `<div class="list-item"><div class="av">${x.emoji}</div><div class="grow"><div class="name">${esc(x.name)}</div><div class="muted">уют +${x.cozy} · ⚡ быстрее на ${x.regen}%</div></div>
      <button class="btn sm" data-a="flat-buy" data-i="${x.key}" ${f.money < x.price ? 'disabled' : ''}>${money(x.price)}</button></div>`).join('');
    const themes = f.themes.map((t) => `<button class="btn sm ${t.on ? '' : 'ghost'}" data-a="flat-theme" data-k="${t.key}" ${t.on || f.money < t.price ? 'disabled' : ''}><span style="display:inline-block;width:12px;height:12px;border-radius:6px;background:${t.color};margin-right:4px"></span>${esc(t.name)}${t.price ? ' · ' + money(t.price) : ''}</button>`).join('');
    mine = `<div class="card"><div class="muted">${selItem ? (selItem.item ? `Выбрано: ${selItem.emoji} ${esc(selItem.name)}` : 'Выбрано пустое место: новая вещь встанет сюда.') : 'Нажми на клетку, чтобы выбрать место (иначе вещь встанет в первое свободное).'}</div>
      <div class="row" style="margin-top:8px;flex-wrap:wrap;gap:6px">
        ${selItem && selItem.item ? `<button class="btn sm bad" data-a="flat-sell" data-s="${sel}">Продать за половину</button>` : ''}
        <button class="btn sm" data-a="flat-rest" ${f.rest_wait ? 'disabled' : ''}>🛋 Отдохнуть (⚡+${f.rest_energy})</button>
        </div>
      ${f.rest_wait ? `<div class="muted" style="margin-top:4px">Отдых снова через ${gangDur(f.rest_wait)}</div>` : ''}
      <div class="muted" style="margin-top:6px">Открытая дверь: гость может с 60% шансом вынести случайную вещь. Всё по-честному, но осторожнее!</div></div>
      <h2>Отделка</h2><div class="row" style="flex-wrap:wrap;gap:6px">${themes}</div><h2>Мебель и декор</h2>${shop}`;
  } else {
    mine = `<div class="card"><div class="row" style="flex-wrap:wrap;gap:6px"><button class="btn sm" data-a="flat-visit" data-o="${f.owner}" ${f.visited ? 'disabled' : ''}>🚪 ${f.visited ? 'Ты уже заходил' : 'Зайти в гости'}</button>
      </div>
      <div class="muted" style="margin-top:6px">Дверь заперта: брать вещи нельзя. Но ИИ-грабитель иногда залезает сам.</div></div>`;
  }
  const notes = f.notes.map((n) => `<div class="feed"><div class="grow"><b>${esc(n.name)}</b>: ${esc(n.text)}</div><span class="muted">${ago(n.ts)}</span>${n.can_del ? `<button class="btn sm ghost" data-a="flat-notedel" data-n="${n.id}" data-o="${f.owner}">✕</button>` : ''}</div>`).join('') || '<div class="muted">Стена пуста.</div>';
  const nb = f.neighbors.filter((x) => x.id !== f.me).map((x) => `<div class="list-item"><div class="grow"><div class="name">${esc(x.name)}${x.open ? ' 🔓' : ''}</div><div class="muted">вещей ${x.items} · уют ${x.cozy}</div></div><button class="btn sm ghost" data-a="flat-go" data-o="${x.id}">В гости</button></div>`).join('');
  return `${head}${mine}<h2>Записки на стене</h2><div class="card">${notes}
      <div class="row" style="margin-top:8px"><input id="flatNote" maxlength="80" placeholder="Оставить записку" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><button class="btn sm" data-a="flat-note" data-o="${f.owner}">📌</button></div></div>
    <h2>Соседи</h2>${nb || '<div class="muted">Соседей пока нет.</div>'}`;
}


function godCabinetHtml(d, p) {
  const g = p.godfather || {};
  const c = (p.ctl && p.ctl.god) || {};
  const mg = p.mg || {};
  const w = c.waits || {}, k = c.costs || {};
  const tg = c.targets || [];
  const lbl = (txt, wait) => txt + (wait ? ' · ' + gangDur(wait) : '');
  const opts = tg.map((x) => `<option value="${x.id}">${x.ai ? '🧓' : '🕴'} ${esc(x.name)} · общак ${money(x.treasury)} · районов ${x.terr}${x.levy_wait ? ' · дань через ' + gangDur(x.levy_wait) : ''}</option>`).join('');
  const opts2 = tg.map((x) => `<option value="${x.id}">${x.ai ? '🧓' : '🕴'} ${esc(x.name)}</option>`).join('');
  const war = mg.war_until > 0;
  const warOff = war || mg.war_cd ? 'disabled' : '';
  const warWhy = war ? ' · уже идёт' : mg.war_cd ? ' · перемирие ' + gangDur(mg.war_cd) : '';
  const sec = (t) => `<div class="muted" style="margin-top:12px;font-weight:700">${t}</div>`;
  return `<div class="card" style="border-color:color-mix(in srgb,var(--gold) 45%,var(--line))"><div class="row"><div style="font-size:30px">👑</div><div class="grow"><b>Кабинет Крёстного отца</b>
      <div class="muted">Тёмная касса ${money(g.treasury || 0)} · влияние ${g.influence || 0}/100. Влияние множит дань (×0.6…1.4); при нуле пост теряется.</div></div></div>
    ${tg.length ? `${sec('💰 Дань с банд')}
      <select id="gcTarget" style="${SEL}">${opts}</select>
      <select id="gcMode" style="${SEL}"><option value="soft">Мягкая · 6%, без шума: детектив не узнает</option><option value="normal" selected>Обычная · 12%, уважение банды −1</option><option value="hard">Жёсткая · 20%, но банда может взбунтоваться (30%)</option></select>
      <div class="row" style="gap:6px;margin-top:6px;flex-wrap:wrap"><button class="btn sm" data-a="god-levy">💰 Взять дань</button>
        <button class="btn sm bad" data-a="god-sab" ${w.sab ? 'disabled' : ''} title="Контроль −15 на 2 районах, доход вдвое меньше на 2 ч; провал 20%">${lbl('💣 Саботаж · ' + money(k.sab), w.sab)}</button>
        <button class="btn sm" data-a="god-bribe" ${w.bribe ? 'disabled' : ''} title="Подкупленный сливает ~10% общака; провал 35%">${lbl('💼 Подкуп · ' + money(k.bribe), w.bribe)}</button></div>
      <div class="muted" style="margin-top:4px;font-size:11.5px">Саботаж: контроль −15 на двух районах банды и доход вдвое меньше на 2 ч (провал 20%). Подкуп: участник сливает около 10% общака (провал 35%).</div>
      ${sec('🎭 Стравить банды')}
      <div class="row" style="gap:6px;margin-top:2px"><select id="gcA" style="${SEL}">${opts2}</select><select id="gcB" style="${SEL}">${opts2}</select></div>
      <button class="btn sm bad" style="margin-top:6px" data-a="god-incite" ${w.incite ? 'disabled' : ''}>${lbl('🎭 Война на 12 часов · ' + money(k.incite), w.incite)}</button>` : '<div class="muted" style="margin-top:8px">Чужих банд сейчас нет: влиять не на кого.</div>'}
    ${sec('💸 Финансирование · 500–5 000 ₽ из кассы')}
    <div class="row" style="gap:6px;margin-top:2px;flex-wrap:wrap"><select id="gSide" style="${SEL}"><option value="gang">🕴 Банде: общак и уважение +2</option><option value="police">🚓 Полиции: в фонд</option></select>
      <select id="gFundGang" style="${SEL}"><option value="0">Самой бедной банде игроков</option>${opts2}</select>
      <input id="gAmt" type="number" min="500" max="5000" value="1000" style="${SEL};max-width:130px"><button class="btn sm" data-a="god-fund" ${g.fund_wait ? 'disabled' : ''}>${lbl('💸 Выдать', g.fund_wait)}</button></div>
    ${sec('🎩 Мэр и полиция')}
    <select id="gPress" style="${SEL}"><option value="mayor">Давить на мэра: налог −0.1, полиция −0.3</option><option value="police">Перекрыть деньги полиции: фонд −1 000 ₽</option></select>
    <div class="row" style="gap:6px;margin-top:6px;flex-wrap:wrap"><button class="btn sm ghost" data-a="god-pressure" ${g.pressure_wait ? 'disabled' : ''}>${lbl('🎩 Надавить', g.pressure_wait)}</button>
      <button class="btn sm bad" data-a="p3" data-p="power/god_war" data-c="Объявить войну мэру? 24 часа." ${warOff}>⚔️ Война мэру${warWhy}</button>
      <button class="btn sm" data-a="p3" data-p="power/god_riot" data-c="Подкупить граждан на бунт? 2000 ₽ из кассы.">🔥 Бунт · 2 000 ₽</button>
      <button class="btn sm ghost" data-a="p3" data-p="power/roof" data-c="Заплатить мэрии 3000 ₽ за крышу на 24 часа?" ${g.has_gang ? '' : 'disabled'}>🤝 Крыша · 3 000 ₽${g.has_gang ? '' : ' · нужна банда'}</button></div>
    ${mgStatusHtml(mg)}
    ${tasksGiveHtml('gang')}</div>`;
}

function mgStatusHtml(mg) {
  const war = mg.war_until > 0;
  const lastWar = mg.last ? ` Прошлая война: ${mg.last.winner === 'mayor' ? 'победил мэр' : mg.last.winner === 'god' ? 'победил Крёстный' : 'ничья'} (${mg.last.mayor}:${mg.last.god}).` : '';
  return `<div class="muted" style="margin-top:6px;font-size:11.5px">${war ? `⚔️ Война идёт ещё ${gangDur(mg.war_until)}, счёт мэр ${mg.mayor_score}:${mg.god_score} Крёстный. ` : ''}${mg.roof_until > 0 ? `🤝 Крыша оплачена ещё ${gangDur(mg.roof_until)}. ` : ''}${mg.riot_until > 0 ? `🔥 Бунт ещё ${gangDur(mg.riot_until)}. ` : ''}Война длится 24 часа: мэр давит районы семьи, семья тянет деньги из фонда полиции; победитель по очкам забирает до 3 000 ₽.${lastWar}</div>`;
}

function mayorCabinetHtml(d, p, c) {
  const m = p.mayor || {};
  const mg = p.mg || {};
  const g = p.godfather || {};
  const lv = [0.5, 0.75, 1, 1.25, 1.5];
  const war = mg.war_until > 0;
  const warOff = war || mg.war_cd ? 'disabled' : '';
  const warWhy = war ? ' · уже идёт' : mg.war_cd ? ' · перемирие ' + gangDur(mg.war_cd) : '';
  const sec = (t) => `<div class="muted" style="margin-top:12px;font-weight:700">${t}</div>`;
  const inmates = c.inmates || [], wars = c.wars || [];
  return `<div class="card" style="border-color:color-mix(in srgb,var(--brand) 35%,var(--line))"><div class="row"><div style="font-size:30px">🎩</div><div class="grow"><b>Кабинет мэра</b>
      <div class="muted">Налог ×${c.tax} · полиция ×${c.police} · курс: ${m.align === 'police' ? 'за полицию' : m.align === 'corrupt' ? 'подкупной' : 'нейтральный'} · комендантский час: ${c.curfew ? 'да' : 'нет'}</div></div></div>
    ${sec('📜 Указ · раз в 8 часов')}
    <div class="row" style="gap:6px;margin-top:2px"><select id="mTax" style="${SEL}">${lv.map((v) => `<option value="${v}" ${c.tax === v ? 'selected' : ''}>Налог ×${v}</option>`).join('')}</select>
      <select id="mPol" style="${SEL}">${lv.map((v) => `<option value="${v}" ${c.police === v ? 'selected' : ''}>Полиция ×${v}</option>`).join('')}</select></div>
    <select id="mAlign" style="${SEL}"><option value="neutral" ${m.align === 'neutral' ? 'selected' : ''}>Курс нейтральный</option><option value="police" ${m.align === 'police' ? 'selected' : ''}>За полицию: полиция +20%, налог +10%</option><option value="corrupt" ${m.align === 'corrupt' ? 'selected' : ''}>Подкупной: полиция −20%, налог −10%, детектив заметит</option></select>
    <label class="muted" style="display:block;margin-top:6px"><input type="checkbox" id="mCur" ${c.curfew ? 'checked' : ''}> Комендантский час (23–7): ночью дела закрыты</label>
    <button class="btn sm" style="margin-top:6px" data-a="mayor-set" ${c.set_wait ? 'disabled' : ''}>📜 Подписать указ${c.set_wait ? ' · через ' + gangDur(c.set_wait) : ''}</button>
    <div class="muted" style="margin-top:4px;font-size:11.5px">Налог множит сборы с общаков и богатых. Полиция множит фонд и частоту ОМОНа.</div>
    ${sec('💰 Бюджет · раз в 12 часов')}
    <div class="row" style="gap:6px;margin-top:2px"><select id="mBud" style="${SEL}">${[500, 1000, 2000, 3000].map((v) => `<option value="${v}">${money(v)} из фонда полиции в пособия</option>`).join('')}</select><button class="btn sm" data-a="mayor-budget" ${m.budget_wait ? 'disabled' : ''}>Перевести${m.budget_wait ? ' · ' + gangDur(m.budget_wait) : ''}</button></div>
    <div class="muted" style="margin-top:4px;font-size:11.5px">Порядок в районе +1 за каждые 500 ₽, бедные получают пособие, но у полиции меньше денег на операции.</div>
    ${sec('⚖️ Правосудие')}
    <div class="row" style="margin-top:2px;gap:6px;flex-wrap:wrap">${inmates.map((x) => `<button class="btn sm ghost" data-a="p3" data-p="mayor/pardon" data-j='{"target":${x.id}}' ${c.pardons ? '' : 'disabled'}>🕊 Помиловать ${esc(x.name)}</button>`).join('') || '<span class="muted">В тюрьме никого: миловать некого.</span>'}
      <button class="btn sm bad" data-a="p3" data-p="mayor/crackdown" data-c="Объявить зачистку? У всех с розыском 3⭐+ станет +1⭐, полиция в 1.5 раза активнее 12 часов." ${c.crackdown_used ? 'disabled' : ''}>🚔 Зачистка${c.crackdown_wait ? ' · через ' + gangDur(c.crackdown_wait) : ''}</button></div>
    ${wars.length ? `<select id="mWar" style="${SEL}">${wars.map((x) => `<option value="${x.gid}">${esc(x.name)}</option>`).join('')}</select><button class="btn sm ghost" style="margin-top:6px" data-a="mayor-peace" ${c.peace ? '' : 'disabled'}>🏛 Отменить войну банды</button>` : ''}
    <div class="muted" style="margin-top:4px;font-size:11.5px">Сегодня осталось: помилований ${c.pardons || 0} из ${(c.limits || {}).pardons || 2}, отмен войны ${c.peace || 0} из ${(c.limits || {}).peace || 1}. Отмена останавливает войну банды без победителя: район никто не теряет.</div>
    ${sec('🚓 Полиция')}
    ${captainHtml(p.ctl && p.ctl.captain)}
    ${sec('👑 Против Крёстного')}
    <div class="row" style="gap:6px;margin-top:2px;flex-wrap:wrap"><button class="btn sm bad" data-a="mayor-anti" ${m.anti_wait ? 'disabled' : ''}>🚔 Удар по кассе · 2 500 ₽${m.anti_wait ? ' · ' + gangDur(m.anti_wait) : ''}</button>
      <button class="btn sm bad" data-a="p3" data-p="power/mayor_war" data-c="Объявить войну Крёстному? 24 часа." ${warOff || (g.player ? '' : 'disabled')}>⚔️ Война${warWhy}</button>
      <button class="btn sm ghost" data-a="p3" data-p="power/mayor_raid" data-c="Рейд на семью Крёстного? 1000 ₽ из фонда." ${g.player ? '' : 'disabled'}>🚔 Рейд · 1 000 ₽</button></div>
    ${g.player ? '' : '<div class="muted lock-why" style="margin-top:4px">🔒 Война и рейд доступны, когда Крёстный — игрок. Удар по кассе глушит ИИ-Крёстного на 6 часов.</div>'}
    ${mgStatusHtml(mg)}
    ${tasksGiveHtml('police')}</div>`;
}

function powerHtml(d) {
  const p = d && d.power;
  if (!p) return '';
  const g = p.godfather || {};
  const m = p.mayor || {};
  const c = (((d.gang || {}).pc || {}).council || {}).mayor || {};
  const boss = !!(d.gang && d.gang.mine && d.gang.mine.is_boss);
  const who = `<div class="muted" style="margin:0 0 8px">🎩 Мэр: ${m.exists ? esc(c.gname || 'игрок') : 'Архип (ИИ)'} · 👑 Крёстный: ${g.player ? esc(g.name) : 'ИИ'}${g.candidate && !g.player ? ', кандидат ' + esc(g.candidate.name) : ''}</div>`;
  let out = '';
  if (g.player && g.is_me) {
    out += godCabinetHtml(d, p);
  } else if (!g.player && ((g.candidate && g.candidate.is_me) || g.can_become || (boss && !g.candidate))) {
    out += `<div class="card"><div class="row"><div style="font-size:30px">👑</div><div class="grow"><b>${g.candidate && g.candidate.is_me ? 'Ты кандидат в Крёстные отцы' : 'Пост Крёстного свободен'}</b><div class="muted">Сейчас тёмную кассу держит ИИ. Крёстный берёт дань с банд, стравливает их и воюет с мэром.</div></div></div>
      ${g.can_become ? `<button class="btn sm" style="margin-top:8px" data-a="p3" data-p="power/god" data-c="Стать Крёстным отцом за ${money(g.cost || 5000)}${g.solo ? ' наличными' : ' из общака'}?">Стать Крёстным отцом · ${money(g.cost || 5000)}${g.solo ? ' наличными' : ''}</button>`
        : `<div class="muted lock-why" style="margin-top:8px">🔒 ${esc(g.why || 'Нужно ' + money(g.cost || 5000) + ' в общаке и ' + (g.min_terr || 2) + ' района')}</div>`}
      ${!g.candidate && boss ? '<button class="btn sm ghost" style="margin-top:8px" data-a="p3" data-p="power/nominate">Выдвинуться кандидатом</button>' : ''}</div>`;
  }
  if (m.exists && m.is_me) {
    out += mayorCabinetHtml(d, p, c);
  } else if (!m.exists && m.can_campaign) {
    out += `<div class="card"><b>🎩 Мэра нет</b><div class="muted">Пока районом правит Архип (ИИ). Мэром может стать только сотрудник полиции.</div><button class="btn sm" style="margin-top:8px" data-a="mayor-campaign">🗳 Начать выборы</button></div>`;
  }
  return `<div class="sec-head"><h2>Власть района</h2></div>${who}${out}`;
}

function captainHtml(c) {
  if (!c || !c.is_mayor) return '';
  const cands = (c.candidates || []).map((x) => `<option value="${x.id}">${x.ai ? '🤖 ' : ''}${esc(x.name)}</option>`).join('');
  return `<div class="card" style="margin-top:8px"><b>🚓 Капитан полиции: ${c.id ? esc(c.name) : 'должность свободна'}</b>
    <div class="muted">Капитана назначает мэр из офицеров, в том числе ИИ (смена раз в 6 часов); ИИ-капитан сам ведёт дела, облавы и давление на районы. Пост не пустует: если снять капитана, обязанности примет ИИ-капитан Волков. Приказы капитану — в блоке «Задания» ниже.</div>
    ${c.proposal ? `<div class="banner" style="margin:8px 0 0;padding:10px"><b>📋 ${esc(c.proposal.by)} просит добро на ${esc(c.proposal.title)} против «${esc(c.proposal.gname)}»</b>
      <div class="muted" style="margin-top:4px">Добро: изъятие +${c.proposal.bonus}%. Вето: операции не будет и фонд цел, но детектив это запомнит. Через ${gangDur(c.proposal.left)} капитан решит сам.</div>
      <div class="row" style="margin-top:8px;gap:6px"><button class="btn sm" data-a="p3" data-p="power/prop" data-j='{"accept":true}'>✅ Дать добро</button><button class="btn sm bad" data-a="p3" data-p="power/prop" data-j='{"accept":false}' data-c="Запретить операцию? Детектив возьмёт это на заметку.">🛑 Вето</button></div></div>` : ''}
    ${cands ? `<select id="capTarget" style="${SEL}">${cands}</select><div class="row" style="margin-top:6px;gap:6px"><button class="btn sm" data-a="cap-appoint" ${c.wait ? 'disabled' : ''}>Назначить капитаном${c.wait ? ' · ' + gangDur(c.wait) : ''}</button>${c.id ? '<button class="btn sm ghost" data-a="cap-clear">Снять</button>' : ''}</div>` : '<div class="muted" style="margin-top:6px">Других офицеров нет.</div>'}</div>`;
}

function godInfluenceHtml() { return ''; }

function ctlPoliceHtml(p, role) {
  const c = (((S.rp || {}).power || {}).ctl || {}).police;
  if (!role || !c) return '';
  const chief = role === 'chief';
  const k = c.costs || {}, w = c.waits || {};
  const dist = (p.map || []).filter((x) => x.can_patrol);
  const opts = dist.map((x) => `<option value="${x.key}">${x.icon} ${esc(x.name)} · ${esc(x.owner_name || '')} · контроль ${x.control} · давление ${x.police}</option>`).join('');
  const gopts = (p.gangs || []).map((x) => `<option value="${x.id}">${x.ai ? '🧓' : '🕴'} ${esc(x.name)}</option>`).join('');
  const cf = (c.curfews || []).map((x) => `<div class="muted">🌙 ${esc(x.name)} · ещё ${gangDur(x.left)}</div>`).join('');
  const op = (c.outposts || []).map((x) => `<div class="row" style="margin-top:4px"><div class="grow muted">🏢 ${esc(x.name)}</div>${chief ? `<button class="btn sm ghost" data-a="ctl-outpost-close" data-k="${x.key}">Закрыть</button>` : ''}</div>`).join('');
  const kp = (c.kpps || []).map((x) => `<div class="muted">🚧 КПП ${esc(x.name)} · ещё ${gangDur(x.left)}</div>`).join('');
  return `<h2>🛡 Контроль районов</h2><div class="card"><div class="muted">Давят на любую банду, включая ИИ. Цель — район, который держит банда.</div>
    ${opts ? `<select id="ctlKey" style="${SEL}">${opts}</select>
    <div class="row" style="gap:6px;margin-top:6px;flex-wrap:wrap">
      ${chief ? `<button class="btn sm bad" data-a="ctl-curfew" ${w.curfew ? 'disabled' : ''} title="2 часа: давление +3 и контроль −2 каждые 5 минут, доход вдвое меньше">${'🌙 Комендантский час · ' + money(k.curfew)}${w.curfew ? ' · ' + gangDur(w.curfew) : ''}</button>
      <button class="btn sm" data-a="ctl-outpost" title="Постоянное давление +3 / контроль −2 каждые 10 минут; содержание ${k.upkeep} ₽ в сутки">🏢 Опорный пункт · ${money(k.outpost)}</button>` : ''}
      <button class="btn sm" data-a="ctl-kpp" ${w.kpp ? 'disabled' : ''} title="3 часа: изымает груз и контрабанду в фонд, доход района вдвое меньше">🚧 КПП · ${money(k.kpp)}${w.kpp ? ' · ' + gangDur(w.kpp) : ''}</button></div>` : '<div class="muted" style="margin-top:6px">Нет чужих районов для операции.</div>'}
    ${!chief ? '<div class="muted" style="margin-top:6px">Комендантский час и опорные пункты вводит капитан.</div>' : ''}
    ${cf}${op}${kp}
    <div class="muted" style="margin-top:10px">🕵 Агент в банде: на 24 часа, раз в 30 минут добавляет улики, в конце бьёт по контролю 2 районов банды.</div>
    ${gopts ? `<select id="ctlGang" style="${SEL}">${gopts}</select><button class="btn sm" style="margin-top:6px" data-a="ctl-agent">🕵 Внедриться (⚡)</button>` : ''}</div>`;
}

function patronsHtml(d) {
  const p = d && d.patrons;
  const mine = d.gang && d.gang.mine;
  if (!p || !mine || !mine.is_boss) return '';
  const g = p.godfather || {};
  const m = p.mayor || {};
  const nowTs = Date.now() / 1000;
  let out = '';
  if (!g.in_shadow) {
    const debt = g.debt;
    out += `<div class="card"><div class="row"><div style="font-size:28px">${g.icon || '👑'}</div><div class="grow"><b>${esc(g.name || 'Крёстный отец')} (ИИ)</b><div class="muted">Пока Крёстного-игрока нет, тёмную кассу держит он: требует дань и сдаёт бригаду.</div></div></div>
      ${debt ? `<div class="muted" style="margin-top:8px">Старый долг: <b>${money(debt.owed || 0)}</b> · осталось ${gangDur(Math.max(0, (debt.due || 0) - nowTs))}. Не вернёшь — заберёт 15% общака и объявит войну.</div><button class="btn sm" style="margin-top:6px" data-a="p3" data-p="patron/repay">Вернуть долг</button>` : ''}
      ${g.tribute ? `<div class="muted" style="margin-top:8px">Требует дань: <b>${money(g.tribute.amount || 0)}</b> · осталось ${gangDur(Math.max(0, (g.tribute.expires || 0) - nowTs))}. Отказ или просрочка — семья объявит войну.</div>
        <div class="row" style="gap:6px;margin-top:6px"><button class="btn sm" data-a="p3" data-p="patron/tribute">Заплатить</button><button class="btn sm ghost" data-a="p3" data-p="patron/refuse" data-c="Отказать Крёстному? Его семья объявит войну.">Отказать</button></div>` : ''}
      <div class="muted" style="margin-top:8px">Бригада наёмников на 24 часа: +5 контроля району.</div>
      <button class="btn sm ghost" style="margin-top:6px" data-a="p3" data-p="patron/merc" ${(g.merc_until || 0) > 0 ? 'disabled' : ''}>🥊 Нанять · ${money(g.merc_cost || 2000)}${(g.merc_until || 0) > 0 ? ' · ещё ' + gangDur(g.merc_until) : ''}</button></div>`;
  }
  if (!m.in_shadow) {
    out += `<div class="card"><div class="row"><div style="font-size:28px">${m.icon || '🎩'}</div><div class="grow"><b>${esc(m.name || 'Мэр Архип')} (ИИ)</b><div class="muted">Налог ×${m.tax || 1} · полиция ×${m.police || 1}. Конверт: полиция ×0.5 на час, но есть риск дела.</div></div></div>
      <button class="btn sm bad" style="margin-top:6px" data-a="p3" data-p="patron/bribe" ${(m.bribe_until || 0) > 0 ? 'disabled' : ''}>💵 Подкупить · ${money(m.bribe_cost || 2000)}${(m.bribe_until || 0) > 0 ? ' · действует ' + gangDur(m.bribe_until) : ''}</button></div>`;
  }
  return out ? `<div class="sec-head"><h2>Теневые связи</h2><span class="badge">только босс</span></div>${out}` : '';
}

function wBtn(p, j, label, cls, dis) {
  return `<button class="btn sm ${cls || ''}" data-a="p3" data-p="${p}" data-j='${JSON.stringify(j || {})}' ${dis ? 'disabled' : ''}>${label}</button>`;
}

function worldBannerHtml(d) {
  const w = (d.patrons || {}).world;
  if (!w) return '';
  const me = d.me || {};
  const costs = w.costs || {};
  let out = '';
  const f = w.feud;
  if (f) out += `<div class="card" style="border:2px solid #e5484d"><b>👑 Крёстный отец мстит банде «${esc(f.gang)}»</b><div class="muted">«${esc(f.family)}» воюет с ней: ${esc(f.why)}. Вражда ещё ${gangDur(f.left)}.</div>${f.mine ? `<div style="margin-top:8px">${wBtn('patron/atone', {}, 'Откупиться · ' + money(f.cost), 'bad')}</div>` : ''}</div>`;
  const c = w.city;
  if (c && c.kind === 'raid') out += `<div class="card" style="border:2px solid #e67e22"><b>🚔 Городская облава через ${gangDur(c.left)}</b><div class="muted">Тех, кто в розыске (2⭐ и больше), задержат на 8 минут и оштрафуют на 300 ₽.</div><div style="margin-top:8px">${c.hidden ? '✅ Ты залёг на дно' : wBtn('city/hide', {}, '🕳 Залечь на дно · ' + money(costs.hide || 150), '', !(me.wanted > 0))}</div></div>`;
  if (c && c.kind === 'strike') out += `<div class="card" style="border:2px solid #e67e22"><b>✊ Забастовка</b><div class="muted">Зарплаты ×0.6, ещё ${gangDur(c.left)}. Голосов профсоюза: ${c.support} из ${c.need}. Победа: зарплаты +15% на 6 часов.</div>
    <div class="row" style="margin-top:8px;gap:6px">${wBtn('city/support', {}, '✊ Поддержать', '', !!c.mine)}${wBtn('city/scab', {}, '🧱 Штрейкбрехер +200 ₽', 'ghost', !!c.mine)}</div></div>`;
  if (c && c.kind === 'market') out += `<div class="card" style="border:2px solid #e67e22"><b>🛒 Передел рынка</b><div class="muted">«${esc(c.to)}» нацелилась на район ${esc(c.target)}. Через ${gangDur(c.left)} она его заберёт.</div>${c.can_defend ? `<div style="margin-top:8px">${c.defended ? '✅ Защита оплачена' : wBtn('city/defend', {}, '🛡 Защитить · ' + money(costs.defend || 500), 'bad')}</div>` : ''}</div>`;
  return out;
}

function worldHtml(d) {
  const w = (d.patrons || {}).world;
  if (!w) return '';
  const role = w.role || '';
  const dep = role === 'boss' || role === 'deputy';
  let out = '';
  const du = w.duel || {};
  const S2 = { m: ['🎩', 'Мэр Архип'], g: ['👑', 'Крёстный отец'] };
  if (du.active) {
    const mp = (du.pts || {}).m || 0, gp = (du.pts || {}).g || 0, tot = Math.max(1, mp + gp);
    out += `<div class="card"><b>⚔️ Борьба за район: 🎩 Архип против 👑 Крёстного</b><div class="muted">Ещё ${gangDur(du.left)}. Победившая сторона на сутки меняет налоги и полицию, ставки её сторонников возвращаются с прибавкой (80% общего банка).</div>
      <div style="display:flex;height:10px;border-radius:6px;overflow:hidden;margin:8px 0"><div style="width:${Math.round(100 * mp / tot)}%;background:#3b82f6"></div><div style="flex:1;background:#a855f7"></div></div>
      <div class="row" style="justify-content:space-between"><span>🎩 ${mp} очк. · ${du.sup.m} чел.</span><span>👑 ${gp} очк. · ${du.sup.g} чел.</span></div>
      ${du.mine ? `<div class="muted" style="margin-top:6px">Ты за ${S2[du.mine][0]} ${S2[du.mine][1]}${du.my_don ? ' · вложено ' + money(du.my_don) : ''}. Банк: ${money(du.pot || 0)}.</div>
        <div class="row" style="margin-top:6px;gap:6px;flex-wrap:wrap">${[100, 500, 1000].map((a) => wBtn('duel/fund', { amount: a }, '+' + money(a), 'ghost')).join('')}</div>`
        : `<div class="row" style="margin-top:8px;gap:6px">${wBtn('duel/side', { side: 'm' }, '🎩 За Архипа')}${wBtn('duel/side', { side: 'g' }, '👑 За Крёстного', 'bad')}</div>`}</div>`;
  } else if (du.effect) {
    out += `<div class="card"><b>⚔️ Итог борьбы за район</b><div class="muted">Победил ${du.effect.winner === 'm' ? '🎩 Архип: налог ×1.5, полиция ×1.5' : '👑 Крёстный отец: налог ×0.5, полиция ×0.5'} ещё ${gangDur(du.effect.left)}.</div></div>`;
  }
  const im = w.impeach || {};
  if (im.active) {
    out += `<div class="card"><b>🗳 Мэр Архип${im.tax !== 1 ? ' · налог ×' + im.tax : ''}</b><div class="muted">Недовольны налогом и рейдами? Если ${im.need} жителей проголосуют за отставку за сутки, Архип слетает, а совет боссов выберет нового мэра. Сейчас голосов: ${im.votes} из ${im.need}.</div>
      <div style="margin-top:8px">${im.cd > 0 ? `<span class="muted">Архипа смещали недавно: ещё ${gangDur(im.cd)}</span>` : wBtn('world/impeach', {}, im.mine ? '✅ Ты проголосовал' : '😡 Сместить Архипа', 'bad', !!im.mine)}</div></div>`;
  } else if (im.suspended > 0) {
    out += `<div class="card"><b>🗳 Архип в отставке</b><div class="muted">Мэр сметён жителями. Налог 1.0, полиция без произвола ещё ${gangDur(im.suspended)}; нового мэра выбирают боссы банд.</div></div>`;
  }
  const hr = w.hero || {};
  if (hr.day && hr.name) out += `<div class="card"><b>🏅 Герой района (${esc(hr.day)})</b><div class="muted">${hr.icon ? hr.icon + ' ' : ''}${esc(hr.name)}${hr.ai ? ' (ИИ)' : ''}: ${esc(hr.why || '')}.</div></div>`;
  if (role) {
    const my = w.my || {};
    const biz = (w.citizens || []).filter((x) => x.biz);
    const lures = (w.citizens || []).filter((x) => !x.mine && x.state !== 'police');
    out += `<h2>🏪 Бизнесы и информаторы</h2><div class="card"><div class="muted">Бизнесы жителей: можно собрать выручку в общак (250–700 ₽, ⚡8, раз в 20 минут на банду, у бизнеса раз в 6 часов). Риск: хозяин вызовет своих или побежит в полицию.</div>${(w.bal || {}).upkeep ? `<div class="muted" style="margin-top:4px">🏛 Расходы банды в сутки: штаб ${money(w.bal.upkeep.hq)}${w.bal.upkeep.tax ? ', налог на общак ' + money(w.bal.upkeep.tax) : ''}${w.bal.upkeep.guard ? ', охрана точек ' + money(w.bal.upkeep.guard) : ''}. Общак свыше 10 000 ₽ облагается налогом, жадные точки бунтуют.</div>` : ''}${my.rtax ? `<div class="muted" style="margin-top:4px">🧾 Налог с рабочих под вашей крышей за всё время: ${money(my.rtax)}</div>` : ''}
      ${biz.map((x) => `<div class="row" style="margin-top:8px;gap:6px"><div class="grow"><b>${esc(x.icon + ' ' + x.name)}</b><div class="muted">${esc(x.biz)}${x.biz_wait ? ' · выручка копится ещё ' + gangDur(x.biz_wait) : ''}</div></div>${wBtn('world/shake', { cid: x.id }, 'Собрать', '', x.biz_wait > 0 || my.shake_wait > 0)}</div>`).join('') || '<div class="muted" style="margin-top:6px">Бизнесов пока нет: жители копят деньги.</div>'}
      ${my.shake_wait > 0 ? `<div class="muted" style="margin-top:6px">Банда сможет собирать снова через ${gangDur(my.shake_wait)}.</div>` : ''}</div>
      <div class="card"><b>📞 Доносы</b><div class="muted">Жители стучат в полицию на банды. Дело сейчас: ${my.case || 0}. Конверты информаторам снимают 8 очков дела (${money(my.hush_cost || 300)} из общака, раз в 2 часа).</div>
      <div style="margin-top:8px">${wBtn('world/hush', {}, '🤫 Заткнуть информаторов', '', !dep || my.hush_wait > 0 || !(my.case > 0))}</div></div>
      ${role === 'boss' ? `<div class="card"><b>🔀 Переманить жителя</b><div class="muted">${money(my.lure_cost || 800)} из общака. Из чужой банды шанс 45%, свободного 60%. Раз в час.</div>
        ${lures.map((x) => `<div class="row" style="margin-top:6px;gap:6px"><div class="grow">${x.icon} ${esc(x.name)} <span class="muted">${x.group ? '· ' + esc(x.group) : '· свободен'}</span></div>${wBtn('world/lure', { cid: x.id }, 'Переманить', 'ghost', my.lure_wait > 0)}</div>`).join('')}</div>` : ''}`;
  }
  return out;
}

function aiCitizensHtml() { return ''; }

function dirtyHtml(me, jailed) {
  const list = (S.rp && S.rp.config && S.rp.config.dirty) || [];
  if (!list.length) return '';
  return `<div class="sec-head"><h2>Чёрные дела <small>для авторитета</small></h2><span class="badge">${me.street_cred || 0}/10</span></div>
    ${list.map((d) => `<div class="list-item" style="flex-wrap:wrap"><div class="av">${d.icon}</div><div class="grow"><div class="name">${esc(d.title)}</div>
      <div class="job-meta"><span>${d.pay[0]}–${d.pay[1]} ₽</span><span>+${d.cred} авторитета</span><span>шанс ~${d.chance}%</span><span>🚔 +${d.wanted}</span><span>⚡ −${d.energy}</span></div></div>
      <button class="btn sm bad" data-a="rp" data-p="dirty" data-k="${d.key}" ${jailed || me.energy < d.energy ? 'disabled' : ''}>Взяться</button></div>`).join('')}`;
}

function residentsHtml(d) {
  const r = d.res;
  if (!r) return '';
  const SELR = 'width:100%;margin-top:6px;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit';
  const opt = (x, sel) => `<option value="${x.key}" ${sel ? 'selected' : ''}>${x.icon} ${esc(x.name)} · ${x.owner_name ? '«' + esc(x.owner_name) + '»' : 'свободен'} · жителей ${x.residents}</option>`;
  let body = '';
  if (r.eligible) {
    const h = r.districts.find((x) => x.key === r.home);
    if (h) {
      const why = !h.owner ? 'район свободен: поддерживать некого' : r.wait ? 'снова через ' + gangDur(r.wait) : '';
      const boost = h.gift ? `<div class="banner win" style="margin:8px 0 0;padding:9px;font-size:12.5px">🎁 ${h.gift.side === 'gang' ? 'Банда' : 'Мэрия'} задабривает жителей ещё ${gangDur(h.gift.left)}: поддержка ${h.gift.side === 'gang' ? 'банды' : 'полиции'} даёт +6 и ${money(r.reward * 2)}.</div>` : '';
      body = `<div class="muted">Ты живёшь в районе <b>${h.icon} ${esc(h.name)}</b>. ${h.owner ? 'Хозяин — «' + esc(h.owner_name) + '»' : 'Хозяина нет'} · контроль ${h.control}% · полиция ${h.police}%.</div>${boost}
        <div class="row" style="margin-top:8px;gap:6px;flex-wrap:wrap">
          <button class="btn sm" data-a="p3" data-p="res/support" data-j='{"side":"gang"}' ${why ? 'disabled' : ''}>🕴 За банду · контроль +4</button>
          <button class="btn sm ghost" data-a="p3" data-p="res/support" data-j='{"side":"police"}' ${why ? 'disabled' : ''}>🚓 За полицию · давление +4</button>
          ${why ? `<span class="muted lock-why">🔒 ${why}</span>` : ''}</div>
        <div class="muted" style="margin-top:6px;font-size:11.5px">Раз в 4 часа. Сторона платит ${money(r.reward)} благодарности, а твой выбор ещё сутки тянет район в ту же сторону.${r.last_side ? ' В прошлый раз ты был ' + (r.last_side === 'gang' ? 'за банду.' : 'за полицию.') : ''}</div>`;
    } else {
      body = '<div class="muted">Ты вне банд и полиции. Выбери район, где живёшь: жители решают, кто там сильнее.</div>';
    }
    body += `<select id="resKey" style="${SELR}">${r.districts.map((x) => opt(x, x.key === r.home)).join('')}</select>
      <button class="btn sm ghost" style="margin-top:6px" data-a="res-settle" ${r.move_wait ? 'disabled' : ''}>${h ? '🚚 Переехать' : '🏘 Поселиться'}${r.move_wait ? ' · через ' + gangDur(r.move_wait) : ''}</button>`;
  } else {
    body = `<div class="muted">${esc(r.why)} Сейчас жителей в районе: ${r.total}.</div>`;
  }
  let giftBlock = '';
  if (r.gift_side) {
    const keys = r.districts.filter((x) => r.gift_keys.includes(x.key));
    giftBlock = `<div class="muted" style="margin-top:10px">🎁 Задобрить жителей: ${money(r.gift_cost)} из ${r.gift_side === 'gang' ? 'общака' : 'фонда полиции'} делятся между жителями района, и ${r.gift_hours} ч их поддержка твоей стороны сильнее.</div>
      ${keys.length ? `<select id="resGift" style="${SELR}">${keys.map((x) => opt(x, false)).join('')}</select><button class="btn sm" style="margin-top:6px" data-a="res-gift">🎁 Раздать · ${money(r.gift_cost)}</button>`
        : `<div class="muted lock-why" style="margin-top:4px">🔒 ${r.gift_side === 'gang' ? 'В твоих районах пока никто не живёт или раздача уже идёт.' : 'Нет районов с жителями под бандой или раздача уже идёт.'}</div>`}`;
  }
  return `<div class="sec-head"><h2>Жители районов</h2><span class="badge">🏘 ${r.total}</span></div><div class="card">${body}${giftBlock}</div>`;
}

function homeHtml(d, shop, exch) {
  const tab = S.homeTab || 'prop';
  const tabs = [['prop', '🏠 Имущество'], ['flat', '🛋 Квартира'], ['serv', '🧰 Услуги']];
  const nav = `<div class="earn-toggle">${tabs.map(([k, t]) => `<button data-a="home-tab" data-v="${k}" class="${k === tab ? 'on' : ''}">${t}</button>`).join('')}</div>`;
  const body = tab === 'flat' ? flatHtml()
    : tab === 'serv' ? `${loanHtml(d)}${lawyerHtml(d)}${defenseHtml(d)}${balHtml(d)}<h2>Обмен</h2>${exch}`
    : `<h2>Имущество</h2>${shop}${econHtml(d)}`;
  return nav + body;
}

const NEWS_FILTERS = [['all', 'Все'], ['gang', 'Банды'], ['police', 'Полиция'], ['power', 'Власть'], ['people', 'Жители']];
const NEWS_MARKS = {
  gang: ['🕴', '🏴', '🧱', '⚔', '💰', '🥊', '🚧', '🏳', '🐀', '🛡', '📒', '🗣', '💣', '🎭', '🔀', '🏪'],
  police: ['🚓', '🚨', '🎧', '⚖', '🚔', '🌙', '🏢', '📋', '🛑', '🕵', '🔎'],
  power: ['🎩', '👑', '🏛', '🔥', '🤝'],
  people: ['🏘', '🎁', '🏅', '🗂', '🧭', '💍', '🎗'],
  doom: ['☠'],
};

function newsHtml(d, board) {
  const f = S.newsF || 'all';
  const marks = NEWS_MARKS[f] || [];
  const all = d.news || [];
  const list = f === 'all' ? all : all.filter((n) => marks.some((mk) => (n.text || '').startsWith(mk)));
  const chips = `<div class="subnav" style="margin-bottom:10px">${NEWS_FILTERS.concat(all.some((n) => (n.text || '').startsWith('☠')) ? [['doom', '☠ Судный день']] : []).map(([k, t]) => `<button data-a="news-f" data-v="${k}" class="${k === f ? 'on' : ''}">${t}</button>`).join('')}</div>`;
  const feed = list.length ? list.map((n) => `<div class="feed"><div class="grow">${esc(n.text)}</div><span class="when">${ago(n.ts)}</span></div>`).join('') : '<div class="muted">По этому фильтру пока пусто.</div>';
  const pc = (d.gang || {}).pc || {};
  const s2 = pc.season || { top: [] };
  const top = (s2.top || []).map((x, i) => `<div class="feed"><span>${['🥇', '🥈', '🥉', '4', '5'][i]}</span><div class="grow">${esc(x.name)}</div><b>${x.pts}</b></div>`).join('') || '<div class="muted">Очков пока нет.</div>';
  const shame = (pc.debts || []).map((x) => `<div class="feed"><span>⚠️</span><div class="grow">${esc(x.debtor)} просрочил долг «${esc(x.gang)}»: ${money(x.owed)}</div></div>`).join('');
  const pol = (d.gang || {}).pol || {};
  const journal = (pol.alerts || []).map((a) => `<div class="feed"><span>📡</span><div class="grow"><b>«${esc(a.gang)}»</b>: ${esc(a.text)}</div><span class="when">${ago(a.ts)}</span></div>`).join('');
  return `<div class="sec-head"><h2>Новости района</h2><span class="badge">${list.length}</span></div>${chips}<div class="card">${feed}</div>
    ${journal ? `<h2>📡 Журнал полиции</h2><div class="card"><div class="muted">Полная лента донесений. Короткая сводка по бандам — в разделе «Полиция».</div>${journal}</div>` : ''}
    ${shame ? `<h2>📒 Должники района</h2><div class="card">${shame}</div>` : ''}
    <h2>👑 Сезон ${esc(s2.key || '')}: контроль района</h2><div class="card"><div class="muted">Каждый район даёт очки каждый час (связанные вдвое). Победитель месяца получает 3000 ₽ и запись в зал славы.</div>${top}</div>
    <h2>Самые богатые</h2>${board}`;
}

function loanHtml(d) {
  const pc = (d.gang || {}).pc || {};
  const L = pc.loan;
  if (!L) return '';
  const mine = (pc.my_debts || []).map((x) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">${x.late ? '⚠️' : '📒'} Долг «${esc(x.gang)}»: ${money(x.owed)}</div>
      <div class="muted">${x.late ? 'просрочен: коллекторы забирают наличные и тайник каждые 12 часов' : 'вернуть через ' + gangDur(Math.max(0, x.left))}</div></div>
      <div class="row" style="flex-basis:100%;gap:6px;margin-top:6px"><button class="btn sm" data-a="p3" data-p="debt/repay" data-j='{"did":${x.id}}' ${d.me.money < 1 ? 'disabled' : ''}>${d.me.money < x.owed ? 'Внести сколько есть' : 'Вернуть всё'}</button></div></div>`).join('');
  const offers = (pc.debt_offers || []).map((x) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">🤝 «${esc(x.gang)}» предлагает ${money(x.amount)}</div>
      <div class="muted">вернуть ${money(x.owed)} за ${x.hours} ч · предложение ещё ${gangDur(x.left)}</div></div>
      <div class="row" style="flex-basis:100%;gap:6px;margin-top:6px"><button class="btn sm" data-a="p3" data-p="debt/answer" data-j='{"did":${x.id},"accept":true}'>Взять</button><button class="btn sm ghost" data-a="p3" data-p="debt/answer" data-j='{"did":${x.id},"accept":false}'>Отказаться</button></div></div>`).join('');
  const why = L.blocked ? 'после списанного долга тебе не дают взаймы ещё ' + gangDur(L.blocked) : L.busy ? 'сначала верни текущий долг' : !L.lenders.length ? 'у банд сейчас нет свободных денег' : '';
  const form = why ? `<div class="muted lock-why" style="margin-top:8px">🔒 Новый заём: ${why}.</div>` : `<select id="loanG" style="${SEL}">${L.lenders.map((x) => `<option value="${x.id}">🧓 ${esc(x.name)} · даст до ${money(x.max)}</option>`).join('')}</select>
      <select id="loanH" style="${SEL}">${L.terms.map((x) => `<option value="${x.hours}" ${x.hours === 48 ? 'selected' : ''}>на ${x.hours} ч · вернуть +${x.pct}%</option>`).join('')}</select>
      <div class="row" style="margin-top:6px;gap:6px"><input id="loanA" type="number" min="${L.min}" max="${L.max}" value="500" style="flex:1;min-width:0;border:0;border-radius:12px;padding:12px;background:var(--bg);color:var(--text);font:inherit"><button class="btn sm" data-a="loan-take">Занять</button></div>`;
  return `<h2>📒 Долги и займы</h2><div class="card">${mine}${offers}
    <div class="muted" ${mine || offers ? 'style="margin-top:8px"' : ''}>Занять можно у ИИ-банды сразу, а банда игроков присылает предложение. Чем длиннее срок, тем дороже. Просрочка: коллекторы забирают наличные, долг растёт на 15%, потом лезут в тайник. Через трое суток долг списывают, а тебе неделю не дают взаймы.</div>
    ${form}</div>`;
}

function digestHtml(p) {
  const rows = (p.digest || []).map((x) => `<div class="list-item" style="flex-wrap:wrap"><div class="grow"><div class="name">${x.ai ? '🧓' : '🕴'} ${esc(x.gang)} · дело ${x.pts}/${x.big}</div>
      <div class="bar" style="height:6px;margin:4px 0"><i style="width:${Math.min(100, Math.round(x.pts / x.big * 100))}%"></i></div>
      <div class="muted">${x.items.map((i) => `${i.icon} ${i.label} ×${i.n}`).join(' · ') || 'за сутки донесений нет'}</div>
      <div class="muted">➜ ${esc(x.hint)}${x.wire ? ' · 🎧 прослушка ещё ' + gangDur(x.wire) : ''}${x.last ? ' · последнее ' + ago(x.last) : ''}</div></div></div>`).join('') || '<div class="muted">За сутки тихо: донесений нет.</div>';
  return `<h2>📡 Оперативная сводка</h2><div class="card"><div class="muted">За сутки по каждой банде: что происходило и на что уже хватает дела. Полная лента — в разделе «Новости».</div>${rows}</div>`;
}

function doomHtml(d) {
  const z = d.doom;
  if (!z) return '';
  const dk = 'border:2px solid #2b2b31;background:linear-gradient(135deg,#1c1c21,#2a1d1d);color:#eee';
  let out = '';
  if (z.dead_me) {
    out += `<div class="card" style="${dk}"><div class="row"><div style="font-size:36px">☠️</div><div class="grow"><b>Твой персонаж погиб в Судный день</b>
      <div style="color:#bbb;font-size:13px;margin-top:2px">${z.perm_me ? 'Твой персонаж погиб навсегда.' : 'Твой персонаж мёртв до рассвета: ещё ' + gangDur(z.dead_left) + '.'} Действия недоступны, но ты видишь всё, что творится в районе.</div></div></div></div>`;
  }
  if (z.active) {
    const p = z.phase;
    out += `<div class="card" style="${dk}"><div class="row"><div style="font-size:34px">${p.icon}</div><div class="grow"><b style="font-size:16px">СУДНЫЙ ДЕНЬ · «${esc(p.title)}»</b>
      <div style="color:#bbb;font-size:12px">Фаза ${p.n} из ${p.of} · до рассвета ${gangDur(z.left)} · следующая волна через ${gangDur(z.next_wave)}</div></div></div>
      <div style="margin-top:8px;font-size:13px">${esc(p.desc)}</div>
      <div style="margin-top:8px">${z.effects.map((x) => `<div style="font-size:12.5px;color:#d9c9c9">▪ ${esc(x)}</div>`).join('')}</div>
      <div style="margin-top:8px;font-size:12px;color:#bbb">Волн уже было: ${z.waves}. Деньги в тайнике уцелеют при пожарах; бедным приходит гуманитарная помощь. Герои суток получат награду.</div>
      ${(z.dead || []).length ? `<div style="margin-top:8px;font-size:12.5px">🕯 Погибли: ${z.dead.map((x) => esc(x.name) + (x.perm ? ' (навсегда)' : '')).join(', ')}</div>` : ''}</div>`;
  } else if (z.last) {
    const l = z.last;
    out += `<div class="card" style="${dk}"><div class="row"><div style="font-size:30px">🌅</div><div class="grow"><b>Судный день окончен</b><div style="color:#bbb;font-size:12px">Выжили: ${l.survivors}</div></div></div>
      ${(l.heroes || []).map((h) => `<div style="font-size:13px;margin-top:4px">🏅 ${esc(h.name)}: +${money(h.delta)} за сутки, награда ${money(h.reward)}</div>`).join('')}
      ${(l.back || []).length ? `<div style="font-size:12.5px;margin-top:6px">С рассветом вернулись: ${l.back.map(esc).join(', ')}</div>` : ''}
      ${(l.gone || []).length ? `<div style="font-size:12.5px;margin-top:4px">🕯 Навсегда: ${l.gone.map(esc).join(', ')}</div>` : ''}</div>`;
  }
  return out;
}

function todoHtml(d) {
  const list = d.todo || [];
  const on = !d.notify || d.notify.on;
  const bell = `<button class="tag-btn" data-a="notify-toggle" title="Личные уведомления о срочном">${on ? '🔔' : '🔕'}</button>`;
  const headRow = `<div class="row"><b class="grow">🧭 Сейчас</b><span class="muted" style="font-size:11px;margin-right:6px">${on ? 'срочное придёт в личку' : 'уведомления выключены'}</span>${bell}</div>`;
  if (!list.length) return `<div class="card todo-card">${headRow}<div class="muted" style="margin-top:4px">Срочного нет. Загляни в «Город»: там новости и власть района.</div></div>`;
  const rows = list.map((x) => `<div class="todo-item ${x.urgent ? 'urgent' : ''}" data-a="${x.rp ? 'rp' : 'todo-go'}" ${x.rp ? `data-p="${x.rp}"` : `data-v="${x.go}" data-s="${x.gsub || ''}"`}>
      <div class="todo-ic">${x.icon}</div><div class="grow"><div class="name">${esc(x.title)}</div><div class="muted">${esc(x.sub)}</div></div><div class="todo-go">${x.rp ? 'Забрать' : '›'}</div></div>`).join('');
  return `<div class="card todo-card">${headRow}${rows}</div>`;
}

function onboardHtml(d) {
  const o = d && d.onboard;
  if (!o || o.done) return '';
  const pct = Math.round((o.step / o.total) * 100);
  return `<div class="card" style="border-color:color-mix(in srgb,var(--brand) 35%,var(--line));background:linear-gradient(135deg,color-mix(in srgb,var(--brand) 10%,var(--card)),var(--card))">
    <div class="row"><div style="font-size:26px">🧭</div><div class="grow"><b>Первый день · шаг ${o.step}/${o.total}</b><div class="muted">${esc(o.title)}</div></div></div>
    <div class="muted" style="margin-top:6px">${esc(o.text)}</div>
    <div class="bar" style="height:8px;margin-top:8px"><i style="width:${pct}%"></i></div>
    <button class="btn sm" style="margin-top:9px" data-a="onboard-go" data-v="${o.action}">${esc(o.button)}</button>
  </div>`;
}

function renderRp() {
  const d = S.rp, me = d.me, c = d.config;
  const jailed = me.jail_left > 0;
  const inGang = !!(d.gang && d.gang.mine);
  const policeRole = (d.gang && d.gang.pol && d.gang.pol.role) || '';
  const isCop = !!policeRole;
  const cred = me.street_cred || 0;
  const stars = '⭐'.repeat(me.wanted) + '☆'.repeat(5 - me.wanted);
  const pct = Math.round((me.energy / me.max_energy) * 100);
  const head = `<div class="rp-hero">
      <div class="row">
        <div class="rp-char ${[...(me.char.text || '🧑')].length > 1 ? 'multi' : ''}">${esc(me.char.text || '🧑')}</div>
        <div class="grow"><div class="rp-label">Наличные</div><div class="rp-money">${money(me.money)}</div>
          <div class="muted" style="font-size:11px;opacity:.7">${esc(me.char.bio || 'Житель района')}</div></div>
        <div class="rp-wanted"><div class="rp-label">Розыск</div><b>${me.wanted ? stars : '—'}</b></div>
      </div>
      <div class="rp-energy"><div class="lab"><span>⚡ Энергия</span><span>${Math.round(me.energy)} / ${me.max_energy}</span></div>
        <div class="bar" style="height:9px"><i style="width:${pct}%"></i></div></div>
      <div class="tags"><span>🚗 ${esc(c.cars[me.car].title)}</span><span>🏠 ${esc(c.homes[me.home].title)}</span><span>🌯 ${esc(c.stalls[me.stall].title)}</span><button class="tag-btn ${(S.rpSub === 'map' || (S.rpSub === 'gang' && S.gangSub === 'map')) ? 'on' : ''}" data-a="rp-sub" data-v="map">🗺 Карта</button><button class="tag-btn" data-a="rp-sub" data-v="hero">🎭 Образ</button>${isCop ? `<span>🚓 ${policeRole === 'chief' ? 'капитан' : 'офицер'} · смена</span>` : inGang ? `<span>🪓 авторитет ${d.gang.mine.merit || 0}</span>` : `<span>🥷 уличный авторитет ${cred}/10</span>`}</div>
      ${me.beaten_left ? `<div class="banner lose" style="margin:11px 0 0">🤕 Побит за долги в банде: ещё ${Math.ceil(me.beaten_left / 60)} мин. Дела недоступны.</div>` : ''}
      ${me.cop && (me.cop.rep || me.cop.agent) ? `<div class="hero-chip" style="margin-top:10px;display:inline-flex">🚓 ${esc(me.cop.rank)} · ${me.cop.rep} очк.${me.cop.agent ? ' · двойной агент' : ''}</div>` : ''}
    </div>`;
  const last = S.rpLast ? `<div class="banner ${S.rpGood ? 'win' : 'lose'}" style="font-size:13.5px;font-weight:650;margin:0 0 10px;padding:12px">${esc(S.rpLast)}</div>` : '';
  const jail = jailed ? `<div class="card" style="border-color:color-mix(in srgb,var(--bad) 30%,var(--line))">
      <div class="row"><span style="font-size:26px">🚔</span><div class="grow"><b>Тюрьма</b><div class="muted">Выйти можно взяткой или побегом — или подождать.</div></div></div>
      <div class="btn-row jail-actions" style="margin-top:11px">
        <button class="btn sm" data-a="rp" data-p="bribe">💸 Взятка · ${money(150 + 100 * me.wanted)}</button>
        <button class="btn sm ghost" data-a="rp" data-p="escape" ${me.esc_used ? 'disabled' : ''}>🏃 Побег · 25%</button></div></div>` : '';
  const jtax = ((d.patrons || {}).world || {}).jobtax || {};
  const jobs = c.jobs.map((j) => `<div class="list-item"><div class="av">${j.icon}</div><div class="grow"><div class="name">${esc(j.title)}</div>
      <div class="job-meta"><span>${j.pay[0]}–${j.pay[1]} ₽</span><span>⚡ −${j.energy}</span>${jtax[j.key] ? `<span title="Точка под крышей банды">🕴 налог ${jtax[j.key].pct}% · ${esc(jtax[j.key].gang)}</span>` : ''}</div></div>
      <button class="btn sm" data-a="rp" data-p="work" data-k="${j.key}" ${jailed || me.energy < j.energy ? 'disabled' : ''}>Работать</button></div>`).join('');
  const crcd = ((d.patrons || {}).world || {}).crcd || {};
  const crimes = c.crimes.map((j) => `<div class="list-item"><div class="av">${j.icon}</div><div class="grow"><div class="name">${esc(j.title)}</div>
      <div class="job-meta"><span>шанс ~${j.chance}%</span><span>${j.pay[1] ? j.pay[0] + '–' + j.pay[1] + ' ₽' : 'часть наличных'}</span><span>🚔 +${j.wanted}</span><span>⚡ −${j.energy}</span>${crcd[j.key] > 0 ? `<span>⏳ ${gangDur(crcd[j.key])}</span>` : ''}</div></div>
      <button class="btn sm bad" data-a="rp" data-p="crime" data-k="${j.key}" ${jailed || me.energy < j.energy || crcd[j.key] > 0 ? 'disabled' : ''}>На дело</button></div>`).join('');
  const nextOf = (kind, list, cur) => cur + 1 < list.length ? `<button class="btn sm" data-a="rp-buy" data-k="${kind}" ${me.money < list[cur + 1].price ? 'disabled' : ''}>${esc(list[cur + 1].title)} · ${money(list[cur + 1].price)}</button>` : '<span class="muted">максимум 👑</span>';
  const stallInfo = me.stall ? `<div class="list-item" style="border-color:color-mix(in srgb,var(--gold) 30%,var(--line))"><div class="av">🌯</div><div class="grow"><div class="name">Выручка ларька</div><div class="muted">${c.stalls[me.stall].income} ₽/час · накоплено ${money(me.stall_ready)}</div></div><button class="btn sm gold" data-a="rp" data-p="collect" ${me.stall_ready < 1 ? 'disabled' : ''}>Забрать</button></div>` : '';
  const shop = `${stallInfo}
    <div class="list-item"><div class="av">🚗</div><div class="grow"><div class="name">Машина</div><div class="muted">+${c.car_bonus}% к заработку за уровень</div></div>${nextOf('car', c.cars, me.car)}</div>
    <div class="list-item"><div class="av">🏠</div><div class="grow"><div class="name">Жильё</div><div class="muted">больше энергии и быстрее отдых</div></div>${nextOf('home', c.homes, me.home)}</div>
    <div class="list-item"><div class="av">🌯</div><div class="grow"><div class="name">Ларёк с шаурмой</div><div class="muted">пассивный доход, копится до ${c.stall_hours} часов</div></div>${nextOf('stall', c.stalls, me.stall)}</div>`;
  const exch = `<div class="card row"><div class="grow"><b>Обмен на сантиметры</b><div class="muted">${c.rate} ₽ = 1 см · сегодня ещё ${me.exch_left} см</div></div>
      <button class="btn sm" data-a="rp-exch" ${me.exch_left < 1 || me.money < c.rate ? 'disabled' : ''}>1 см</button></div>`;
  const news = d.news.length ? d.news.map((n) => `<div class="feed"><div class="grow">${esc(n.text)}</div><span class="when">${ago(n.ts)}</span></div>`).join('') : '<div class="muted">В районе пока тихо.</div>';
  const medals = ['🥇', '🥈', '🥉'];
  const board = d.board.map((p, i) => `<div class="list-item"><div class="rank ${i < 3 ? 'top' : ''}">${medals[i] || i + 1}</div><div class="grow"><div class="name">${esc(p.char || '')} ${esc(p.name)}${p.jail ? ' 🚔' : ''}</div><div class="muted">${esc(p.car)}</div></div><div class="val">${money(p.net)}</div></div>`).join('');
  if (S.rpSub === 'flat') { S.rpSub = 'own'; S.homeTab = 'flat'; }
  if (S.rpSub === 'map' && inGang) { S.rpSub = 'gang'; S.gangSub = 'map'; }
  const sub = S.rpSub || 'earn';
  const subs = [['earn', '💼', 'Работа'], ['own', '🏠', 'Дом'], ['gang', '🕴', 'ОПГ'], ['police', '🚓', 'Полиция'], ['town', '🏙', 'Город'], ['news', '📰', 'Новости']];
  const nav = `<div class="subnav">${subs.map(([k, ic, t]) => `<button data-a="rp-sub" data-v="${k}" class="${k === sub ? 'on' : ''}">${ic} ${t}</button>`).join('')}</div>`;
  const earnToggle = `<div class="earn-toggle"><button data-a="rp-earn" data-v="jobs" class="${S.rpEarn !== 'dirty' ? 'on' : ''}">💼 Работа</button><button data-a="rp-earn" data-v="dirty" class="${S.rpEarn === 'dirty' ? 'on' : ''}">🥷 Чернуха</button></div>`;
  const copHint = `<div class="card"><div class="row"><div style="font-size:26px">🚓</div><div class="grow"><b>Ты на службе</b><div class="muted">Обычные работы и чёрные дела закрыты. Зарплата — на смене, доход — с дел и конфиската.</div></div></div><button class="btn sm" style="margin-top:8px" data-a="rp-sub" data-v="police">Открыть полицию</button></div>`;
  const civilianEarn = `${earnToggle}${S.rpEarn === 'dirty' ? dirtyHtml(me, jailed) : jobs}
    ${cred < 10 ? `<div class="card muted">🥷 Уличный авторитет ${cred}/10. Сделай чёрные дела, чтобы получить приглашение в ОПГ.</div>` : `<div class="card" style="border-color:color-mix(in srgb,var(--good) 35%,var(--line))"><b>Тебя могут позвать в ОПГ</b><div class="muted">Авторитет ${cred}. Босс увидит тебя в списке кандидатов.</div></div>`}`;
  const gangEarn = `${contractsHtml(d)}<div class="sec-head"><h2>Дела банды</h2><span class="badge">только для ОПГ</span></div>${crimes}`;
  const body = sub === 'earn' ? (isCop ? copHint : inGang ? gangEarn : civilianEarn)
    : sub === 'own' ? homeHtml(d, shop, exch)
    : sub === 'news' ? newsHtml(d, board)
    : sub === 'gang' ? gangHtml(d, jailed)
    : sub === 'police' ? policeHtml(d)
    : sub === 'map' ? (d.gang && d.gang.mine ? mapSvgHtml(d.gang, d.gang.mine) + mapHtml(d.gang, d.gang.mine) : '<div class="card muted">Ты пока не в ОПГ: карта района появится после создания банды.</div>')
    : sub === 'hero' ? heroHtml(d)
    : `${powerHtml(d)}${bossHtml(d)}${councilHtml(d)}${residentsHtml(d)}${patronsHtml(d)}${worldHtml(d)}${hitHtml(d)}`;
  view.innerHTML = `<div class="screen">${head}${sub === 'earn' ? todoHtml(d) + onboardHtml(d) : ''}${last}${doomHtml(d)}${nav}${p3Html(d)}${p4Html(d)}${body}${p7Html(d)}${p8Html(d)}${p12Html(d)}${jail}${prisonHtml(d)}</div>`;
  const act = view.querySelector('.subnav button.on');
  if (act) { const bar = act.closest('.subnav'); if (bar) bar.scrollLeft = act.offsetLeft - bar.clientWidth / 2 + act.clientWidth / 2; }
}

async function viewRp() {
  if (!S.rp) view.innerHTML = `<div class="screen"><div class="sk hero"></div><div class="sk row"></div><div class="sk row"></div></div>`;
  S.rp = await api(`/api/chat/${S.chat}/rp`);
  renderRp();
  clearInterval(S.rpTimer);
  S.rpTimer = setInterval(async () => {
    if (S.tab !== 'rp' || S.busy) return;
    const ae = document.activeElement;
    if (ae && ['INPUT', 'TEXTAREA', 'SELECT'].includes(ae.tagName)) return;
    try {
      const y = window.scrollY;
      S.rp = await api(`/api/chat/${S.chat}/rp`);
      renderRp();
      window.scrollTo(0, y);
    } catch (e) { /* повторим */ }
  }, 15000);
}

async function rpDo(path, data) {
  if (S.busy) return;
  S.busy = true;
  try {
    const d = await api(`/api/chat/${S.chat}/rp/${path}`, 'POST', data);
    S.rpLast = d.text; S.rpGood = d.good; S.rp = d.state; toast(d.text);
    if (d.size != null && S.board) { S.board.me.size = d.size; updateChip(); }
    haptic(d.good ? 'ok' : 'bad'); keepScroll(renderRp);
  } catch (e) { toast(e.message); haptic('bad'); }
  S.busy = false;
}

setInterval(() => {
  const el = document.getElementById('jailT');
  if (el) el.textContent = fmtLeft(Math.max(0, Math.round((Number(el.dataset.end) - Date.now()) / 1000)));
  const gv = document.getElementById('giveT');
  if (gv) {
    const left = Math.max(0, Math.round((S.giveAt - Date.now()) / 1000));
    gv.textContent = left ? 'доступно через ' + fmtLeft(left) : 'можно передавать';
    document.querySelectorAll('.give-go').forEach((b) => { b.disabled = left > 0; });
  }
}, 1000);

// ───────── вкладка «Задания» ─────────
function daysWord(n) { return n % 10 === 1 && n % 100 !== 11 ? 'день' : [2, 3, 4].includes(n % 10) && ![12, 13, 14].includes(n % 100) ? 'дня' : 'дней'; }

function hoursLeft(sec) { const h = Math.floor(sec / 3600); return h >= 1 ? h + ' ч' : Math.max(1, Math.floor(sec / 60)) + ' мин'; }

async function viewTasks() {
  view.innerHTML = `<div class="screen"><div class="sk hero" style="height:110px"></div><div class="sk row"></div><div class="sk row"></div><div class="sk row"></div></div>`;
  let d;
  try { d = await api(`/api/chat/${S.chat}/tasks`); }
  catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  S.tasks = d;
  const q = d.quests;
  const done = q.items.filter((i) => i.claimed).length;
  const readyCount = q.items.filter((i) => i.ready).length + (q.bonus_ready ? 1 : 0);
  const pct = Math.round(done / Math.max(1, q.items.length) * 100);
  const items = q.items.map((i) => `<div class="quest-card ${i.ready ? 'ready' : ''} ${i.claimed ? 'done' : ''}">
      <div class="q-ic">${i.icon}</div>
      <div class="q-main"><div class="q-title">${esc(i.title)}</div>
        <div class="q-bar"><i style="width:${Math.round((i.have / i.need) * 100)}%"></i></div>
        <div class="q-reward" style="margin-top:4px">${i.have} / ${i.need} · награда +${i.reward} см</div></div>
      ${i.claimed ? '<span class="badge good">✓</span>' : i.ready ? `<button class="btn sm" data-a="quest" data-v="${i.id}">Забрать</button>` : `<span class="badge">+${i.reward}</span>`}
    </div>`).join('');
  const bonus = `<div class="quest-card ${q.bonus_ready ? 'ready' : ''} ${q.bonus_claimed ? 'done' : ''}" style="border-style:dashed">
      <div class="q-ic">🎁</div>
      <div class="q-main"><div class="q-title">Бонус за все три задания</div>
        <div class="q-reward" style="margin-top:3px">+${q.bonus} см сверху</div></div>
      ${q.bonus_claimed ? '<span class="badge good">✓</span>' : q.bonus_ready ? '<button class="btn sm gold" data-a="quest" data-v="bonus">Забрать</button>' : `<span class="badge">+${q.bonus}</span>`}
    </div>`;

  const b = d.boss.boss;
  let boss;
  if (!b) boss = '<div class="card muted">Босс пока не появился. Отдохни — он придёт.</div>';
  else if (b.status === 'alive') {
    boss = `<div class="boss-card">
      <div class="row"><div class="boss-face">${b.emoji}</div><div class="grow"><div class="boss-name">${esc(b.name)}</div>
        <div class="muted">осталось ${hoursLeft(b.ends_in)} · ${b.hp} / ${b.max_hp} HP</div></div>
        <span class="badge" style="background:rgba(255,255,255,.12);color:#fff">${Math.round(b.hp / b.max_hp * 100)}%</span></div>
      <div class="hp boss-hp"><i style="width:${Math.round((b.hp / b.max_hp) * 100)}%"></i></div>
      <button class="btn bad" data-a="hit" ${d.boss.hits_left ? '' : 'disabled'}>${d.boss.hits_left ? `⚔️ Ударить · осталось ${d.boss.hits_left} из ${d.boss.hits_per_day}` : 'Удары на сегодня закончились'}</button>
      ${bossTop(d.boss.top)}</div>`;
  } else {
    const dead = b.status === 'dead';
    boss = `<div class="boss-card"><div class="row"><div class="boss-face">${dead ? '🏆' : '💨'}</div><div class="grow"><div class="boss-name">${esc(b.name)} ${dead ? 'повержен!' : 'сбежал'}</div>
      <div class="muted">Следующий босс появится через ${hoursLeft(b.rest_left || 60)}</div></div></div>${bossTop(d.boss.top)}</div>`;
  }

  const season = d.season;
  const seasonHtml = season ? `<div class="sec-head"><h2>${esc(season.name)} <small>${season.days_left} ${daysWord(season.days_left)} до финала</small></h2></div>
    <div class="card season-card">
      ${(season.board || []).slice(0, 3).map((p, i) => `<div class="podium-row">
        <span class="rank top">${['🥇', '🥈', '🥉'][i]}</span><span class="av">${esc(p.emoji || '🙂')}</span>
        <span class="grow"><span class="name" style="display:block">${esc(p.username)}</span><span class="muted" style="font-size:11px">${p.score} очков</span></span>
        <span class="val">${(season.prizes || [])[i] || ''} см</span></div>`).join('') || '<div class="muted">Сезон только начался — стань первым.</div>'}
      <div class="muted" style="margin-top:8px">Очки дают за активность в чате: игры, дела, дуэли и помощь району.</div>
    </div>` : '';
  view.innerHTML = `<div class="screen">
    <div class="screen-head"><span class="eyebrow">🎯 челлендж дня</span><h1>Задания</h1><p>Маленькие цели, которые делают район живее.</p></div>
    <div class="task-hero"><div class="ring" style="--p:${pct}"><b>${done}/3</b><small>готово</small></div>
      <div class="grow"><h2>${readyCount ? `${readyCount} ${rewardWord(readyCount)} ждёт` : 'План на сегодня'}</h2>
        <p>${readyCount ? 'Забери награду — она уже твоя.' : 'Выполняй задания в чате и возвращайся за наградой.'}</p></div></div>
    ${items}${bonus}
    <div class="sec-head"><h2>Босс чата</h2><span class="badge ${b && b.status === 'alive' ? 'bad' : 'good'}">${b ? (b.status === 'alive' ? 'жив' : b.status === 'dead' ? 'повержен' : 'убежал') : 'скоро'}</span></div>
    ${boss}${seasonHtml}</div>`;
}

function bossTop(top) {
  if (!top || !top.length) return '';
  return `<h2 style="margin-top:16px">Лучшие бойцы</h2>${top.map((t, i) => `<div class="row" style="padding:3px 0"><span>${['🥇', '🥈', '🥉', '4', '5'][i]}</span><span class="grow">${esc(t.username || '?')}</span><b>${t.dmg}</b></div>`).join('')}`;
}


// ───────── покер ─────────
S.pk = null; S.pkOpen = []; S.pkLevels = []; S.pkSig = ''; S.pkRaise = 0; S.pkWasTurn = false;

function pcard(c) {
  if (c === '?') return '<span class="pc back">🂠</span>';
  return `<span class="pc ${c.includes('♥') || c.includes('♦') ? 'red' : ''}">${esc(c)}</span>`;
}

async function refreshPoker() {
  try {
    const d = await api(`/api/chat/${S.chat}/poker`);
    S.pk = d.mine; S.pkOpen = d.open; S.pkLevels = d.levels;
  } catch (e) { S.pk = null; }
}

function renderPokerTable() {
  const t = S.pk;
  if (!t) return;
  const inPlay = ['pre', 'flop', 'turn', 'river'].includes(t.phase);
  const board = Array.from({ length: 5 }, (_, i) => t.board[i] ? pcard(t.board[i]) : '<span class="pc empty"></span>').join('');
  const seats = t.seats.map((p) => {
    const me = p.id === t.you;
    const state = p.sitting ? 'ждёт раздачи' : p.folded ? 'сбросил' : p.allin ? 'ALL-IN' : '';
    return `<div class="list-item pk-seat ${p.turn ? 'turn' : ''} ${p.folded ? 'dead' : ''}">
      <div class="av">${p.dealer ? '🅳' : '🙂'}</div>
      <div class="grow"><div class="name">${esc(p.name)}${me ? ' (ты)' : ''}</div>
        <div class="muted">${p.chips} см${p.bet ? ` · ставка ${p.bet}` : ''}${state ? ` · ${state}` : ''}</div></div>
      <div class="pcs">${p.cards.map(pcard).join('')}</div></div>`;
  }).join('');
  let last = '';
  if (t.last && t.phase === 'break') {
    const w = t.last.winners.map((x) => `${esc(x.name)} +${x.amount} см${x.hand ? ' (' + esc(x.hand) + ')' : ''}`).join('<br>');
    last = `<div class="banner win" style="font-size:16px;font-weight:700">🏆 ${w}</div>`;
  }
  const me = t.seats.find((p) => p.id === t.you);
  let panel = '';
  if (t.your_turn) {
    const call = t.to_call > 0 ? `Уравнять ${t.to_call}` : 'Чек';
    if (!S.pkRaise || S.pkRaise < t.min_raise_to || S.pkRaise > t.max_raise_to) S.pkRaise = t.min_raise_to;
    const raise = t.can_raise ? `<div class="card" style="margin:10px 0 0">
        <div class="row"><span class="muted">Рейз до</span><b id="pkRv" class="grow" style="text-align:right">${S.pkRaise}</b></div>
        <input id="pkRange" type="range" min="${t.min_raise_to}" max="${t.max_raise_to}" value="${S.pkRaise}" style="width:100%" ${t.min_raise_to >= t.max_raise_to ? 'disabled' : ''}>
        <div class="row" style="margin-top:6px">
          <button class="btn sm ghost" data-a="pk-preset" data-v="${t.min_raise_to}">Мин</button>
          <button class="btn sm ghost" data-a="pk-preset" data-v="${t.pot_raise_to}">Банк</button>
          <button class="btn sm ghost" data-a="pk-preset" data-v="${t.max_raise_to}">Олл-ин</button>
          <button class="btn sm grow" data-a="pk-raise">Рейз</button></div></div>` : '';
    panel = `<div class="banner" style="font-size:16px;margin:12px 0 6px">Твой ход · <span id="pkT">${t.left}</span> с</div>
      <div class="row"><button class="btn bad" data-a="pk-act" data-v="fold">Сбросить</button>
      <button class="btn" data-a="pk-act" data-v="${t.to_call > 0 ? 'call' : 'check'}">${call}</button></div>${raise}`;
  } else if (inPlay) {
    const who = t.seats.find((p) => p.turn);
    panel = `<div class="muted" style="text-align:center;margin:12px 0">Ходит ${who ? esc(who.name) : '…'} · <span id="pkT">${t.left}</span> с</div>`;
  } else {
    panel = `<div class="muted" style="text-align:center;margin:12px 0">${t.seats.length < 2 ? 'Ждём второго игрока…' : 'Следующая раздача через <span id="pkT">' + t.left + '</span> с'}</div>`;
  }
  view.innerHTML = `<div class="screen"><div class="screen-head"><span class="eyebrow">🃏 покер</span><h1>За столом</h1><p>Техасский холдем. Не тяни с ходом.</p></div><div class="card pk-table">
      <div class="row"><b class="grow">🃏 ${esc(t.title)}</b><span class="muted">блайнды ${t.sb}/${t.bb}</span></div>
      <div class="pk-board">${board}</div>
      <div style="text-align:center"><span class="muted">Банк</span> <b style="font-size:22px">${t.pot} см</b></div></div>
    ${last}${seats}${panel}
    <button class="btn ghost" style="margin-top:12px" data-a="pk-leave">Встать из-за стола${me ? ` (вернут ${me.chips} см)` : ''}</button></div>`;
  const rg = $('#pkRange');
  if (rg) rg.addEventListener('input', () => { S.pkRaise = Number(rg.value); $('#pkRv').textContent = S.pkRaise; });
}

function pokerLobbyHtml() {
  const open = S.pkOpen.map((t) => `<div class="list-item"><div class="av">🃏</div><div class="grow"><div class="name">${esc(t.title)} · вход ${t.buyin} см</div>
      <div class="muted">${t.count}/${t.max} · ${esc(t.names.join(', '))}</div></div>
      <button class="btn sm" data-a="pk-join" data-v="${t.tid}">Сесть</button></div>`).join('')
    || `<div class="empty-live"><div class="e-ic">♠️</div><b>Открытых столов нет</b><p>Открой свой — за столом до 6 игроков.</p></div>`;
  const levels = S.pkLevels.map((l) => `<div class="list-item"><div class="av">♠️</div><div class="grow"><div class="name">${esc(l.title)}</div>
      <div class="muted">вход ${l.buyin} см · блайнды ${l.sb}/${l.bb}</div></div>
      <button class="btn sm" data-a="pk-create" data-v="${l.key}">Открыть</button></div>`).join('');
  return `<div class="card" style="background:radial-gradient(100% 100% at 100% 0%, var(--good-soft), transparent 60%), var(--surface)">
      <div class="row"><div style="font-size:40px;line-height:1">🃏</div><div class="grow"><b style="font-size:17px">Техасский холдем</b>
        <div class="muted">2–6 игроков. Фишка = 1 см: вход и выход по-честному, банк забирает победитель.</div></div></div>
      <div class="row" style="margin-top:11px"><span class="pill">⏱ 30 секунд на ход</span><span class="pill">♻️ без комиссии</span></div>
    </div>
    <div class="sec-head"><h2>Открытые столы</h2><span class="badge">${S.pkOpen.length}</span></div>${open}
    <div class="sec-head"><h2>Новый стол</h2></div>${levels}
    <div class="card muted" style="margin-top:10px">Два пропуска подряд — вылет из-за стола. При выходе остаток фишек возвращается в сантиметры.</div>`;
}

function viewPoker() { S.gameSeg = 'poker'; S.sig = ''; viewArena(); }

// ───────── действия ─────────
const actions = {
  profile: (el) => openProfile(el.dataset.v),
  goto: (el) => setTab(el.dataset.v),
  'open-act': (el) => openActivity(el.dataset.v),
  'game-seg': (el) => setGameSeg(el.dataset.v),
  'casino-game': (el) => { S.casinoGame = el.dataset.v === 'slots' ? 'slots' : 'wheel'; renderCasino(); haptic(); },
  'home-refresh': () => viewHome(),
  chats: () => openChats(),
  'pick-chat': (el) => selectChat(el.dataset.v),
  'me-seg': (el) => { S.meSeg = el.dataset.v; haptic(); viewMe(); },
  'rp-earn': (el) => { S.rpEarn = el.dataset.v; haptic(); renderRp(); },
  bet: (el) => {
    S.bet = Math.min(S.me.max_bet, Math.max(1, S.bet + Number(el.dataset.v)));
    const v = $('#betVal');
    if (v) v.textContent = S.bet + ' см';
    haptic();
  },
  spin: spinWheel,
  slots: spinSlots,
  daily: async () => {
    try {
      const d = await api(`/api/chat/${S.chat}/daily`, 'POST');
      if (d.claimed) {
        toast(`🎁 +${d.bonus} см! Серия: ${d.streak} дн.`);
        haptic('ok');
        if (S.board) { S.board.me.size = d.size; S.board.me.last_daily = dailyKey(); S.board.me.streak = d.streak; }
      } else {
        toast('Бонус за сегодня уже забран. Приходи завтра!');
      }
      updateChip();
      await refreshCurrent();
    } catch (e) { toast(e.message); }
  },
  emoji: async (el) => {
    try {
      const cur = el.classList.contains('on');
      await api(`/api/chat/${S.chat}/emoji`, 'POST', { emoji: cur ? '' : el.dataset.v });
      haptic();
      await viewMe();
    } catch (e) { toast(e.message); }
  },
  create: (el) => createRoom(el.dataset.v, false),
  'create-bot': (el) => createRoom(el.dataset.v, true),
  join: async (el) => {
    try {
      S.room = (await api(`/api/chat/${S.chat}/rooms/${el.dataset.v}/join`, 'POST')).room;
      S.sig = ''; haptic(); viewArena();
    } catch (e) { toast(e.message); refreshArena(); }
  },
  act: async (el) => {
    try {
      S.room = (await api(`/api/room/${S.room.rid}/act`, 'POST', { action: el.dataset.v })).room;
      haptic(); S.sig = ''; viewArena();
    } catch (e) { toast(e.message); haptic('bad'); }
  },
  'cos-buy': (el) => cosAction('buy', el),
  'cos-equip': (el) => cosAction('equip', el),
  'onboard-go': (el) => {
    const v = el.dataset.v;
    S.rpSub = v === 'gang' || v === 'police' ? v : 'earn';
    if (v === 'dirty') S.rpEarn = 'dirty';
    if (v === 'earn') S.rpEarn = 'jobs';
    renderRp(); haptic(); window.scrollTo({ top: 0, behavior: 'smooth' });
  },
  'todo-go': (el) => {
    S.rpSub = el.dataset.v || 'earn';
    if (el.dataset.s) S.gangSub = el.dataset.s;
    if (S.rpSub === 'flat') S.flat = null;
    renderRp(); haptic();
    const n = document.querySelector('.subnav');
    if (n) n.scrollIntoView({ behavior: 'smooth', block: 'start' });
  },
  'notify-toggle': () => rpDo('notify/toggle', {}),
  'res-settle': () => rpDo('res/settle', { key: ($('#resKey') || {}).value }),
  'res-gift': () => rpDo('res/gift', { key: ($('#resGift') || {}).value }),
  'rp-sub': (el) => {
    S.rpSub = el.dataset.v;
    if (el.dataset.v !== 'hero') S.charDraft = null;
    if (el.dataset.v === 'flat') S.flat = null;
    renderRp(); haptic();
    window.scrollTo({ top: 0, behavior: 'smooth' });
  },
  'char-pick': (el) => { S.charDraft[el.dataset.k] = el.dataset.v; const b = $('#charBio'); if (b) S.charDraft.bio = b.value; renderRp(); haptic(); },
  'char-save': () => { const b = $('#charBio'); if (b) S.charDraft.bio = b.value; const dr = S.charDraft; S.charDraft = null; rpDo('char', dr); },
  'gang-invite': () => { const v = Number(($('#gangInv') || {}).value); if (v) rpDo('gang/invite', { target: v }); },
  'gang-act': (el) => { if (el.dataset.c && !confirm(el.dataset.c)) return; rpDo(el.dataset.p, { target: Number(el.dataset.u), role: el.dataset.r, gid: Number(el.dataset.g) || undefined }); },
  p3: (el) => { if (el.dataset.c && !confirm(el.dataset.c)) return; let j = {}; try { j = JSON.parse(el.dataset.j || '{}'); } catch (e) { /* пусто */ } rpDo(el.dataset.p, j); },
  'map-pick': (el) => { S.mapSel = el.dataset.k === S.mapSel ? null : el.dataset.k; keepScroll(renderRp); haptic(); },
  'emb-pick': (el) => { const b = $('#embMotto'); if (S.embDraft && b) S.embDraft.motto = b.value; S.embDraft[el.dataset.k] = el.dataset.v; keepScroll(renderRp); haptic(); },
  'emb-save': () => { const dr = S.embDraft || {}; dr.motto = ($('#embMotto') || {}).value || ''; rpDo('gang/emblem', { emoji: dr.emoji, c1: dr.c1, c2: dr.c2, motto: dr.motto }); },
  'rules-go': () => rpDo('gang/rules', { text: ($('#rulesTxt') || {}).value || '' }),
  'trial-go': () => rpDo('gang/trial', { defendant: Number(($('#trDef') || {}).value), rule: Number(($('#trRule') || {}).value), evidence: ($('#trEv') || {}).value || '' }),
  'lend-go': () => rpDo('gang/debt/lend', { target: Number(($('#lendTo') || {}).value), amount: Number(($('#lendAmt') || {}).value), hours: Number(($('#lendHours') || {}).value) || 48 }),
  'heir-go': () => rpDo('gang/heir', { target: Number(($('#heirSel') || {}).value) }),
  'defect-go': () => rpDo('gang/defect', { target: Number(($('#defTo') || {}).value), offer: Number(($('#defAmt') || {}).value), role: ($('#defRole') || {}).value }),
  'ransack-go': () => rpDo('gang/room/ransack', { room: ($('#ranRoom') || {}).value }),
  'peace-go': () => rpDo('gang/peace', { kind: ($('#peKind') || {}).value, amount: Number(($('#peAmt') || {}).value), key_a: ($('#peKa') || {}).value, key_b: ($('#peKb') || {}).value }),
  'mayor-set': () => rpDo('mayor/set', { tax: Number(($('#mTax') || {}).value), police: Number(($('#mPol') || {}).value), curfew: !!($('#mCur') || {}).checked, align: ($('#mAlign') || {}).value || '' }),
  'mayor-align': () => rpDo('mayor/set', { align: ($('#mAlign') || {}).value || 'neutral' }),
  'mayor-campaign': () => rpDo('mayor/campaign'),
  'mayor-anti': () => rpDo('power/mayor_anti'),
  'justice-bribe': () => rpDo('justice/bribe', { amount: 0 }),
  'justice-snitch': () => rpDo('justice/snitch'),
  'cap-appoint': () => rpDo('power/captain', { target: Number(($('#capTarget') || {}).value) }),
  'cap-clear': () => rpDo('power/captain', { target: 0 }),
  'god-levy': () => rpDo('power/levy', { gid: Number(($('#gcTarget') || {}).value), mode: ($('#gcMode') || {}).value || 'normal' }),
  'god-sab': () => rpDo('power/sabotage', { gid: Number(($('#gcTarget') || {}).value) }),
  'god-bribe': () => rpDo('power/bribe_member', { gid: Number(($('#gcTarget') || {}).value) }),
  'god-incite': () => rpDo('power/incite', { a: Number(($('#gcA') || {}).value), b: Number(($('#gcB') || {}).value) }),
  'ctl-curfew': () => rpDo('police/curfew', { key: ($('#ctlKey') || {}).value }),
  'ctl-outpost': () => rpDo('police/outpost', { key: ($('#ctlKey') || {}).value }),
  'ctl-outpost-close': (el) => rpDo('police/outpost_close', { key: el.dataset.k }),
  'ctl-kpp': () => rpDo('police/kpp', { key: ($('#ctlKey') || {}).value }),
  'ctl-agent': () => rpDo('police/infiltrate', { gid: Number(($('#ctlGang') || {}).value) }),
  'god-collect': () => rpDo('power/collect'),
  'god-pressure': () => rpDo('power/pressure', { target: ($('#gPress') || {}).value || 'mayor' }),
  'god-fund': () => rpDo('power/fund', { side: ($('#gSide') || {}).value || 'gang', amount: Number(($('#gAmt') || {}).value) || 1000, gid: Number(($('#gFundGang') || {}).value) || 0 }),
  'mayor-peace': () => rpDo('mayor/peace', { gid: Number(($('#mWar') || {}).value) }),
  'contract-npc': (el) => rpDo('contract/accept', { cid: Number(el.dataset.c), npc: Number(($('#npc' + el.dataset.c) || {}).value || 0) }),
  'bribe-go': () => rpDo('gang/bribe', { amount: Number(($('#bribeAmt') || {}).value) }),
  'task-give-gang': () => rpDo('task/give', { target: Number(($('#asgTarget_gang') || {}).value), kind: ($('#asgKind_gang') || {}).value }),
  'task-give-police': () => rpDo('task/give', { target: Number(($('#asgTarget_police') || {}).value), kind: ($('#asgKind_police') || {}).value }),
  'pol-summon': () => rpDo('police/summon', { target: Number(($('#polTarget') || {}).value) }),
  'pol-search': () => rpDo('police/search', { target: Number(($('#polTarget') || {}).value) }),
  'stash-put': () => rpDo('stash/put', { amount: Number(($('#stashAmt') || {}).value) }),
  'stash-take': () => rpDo('stash/take', { amount: Number(($('#stashAmt') || {}).value) }),
  'order-go': () => rpDo('order', { target: Number(($('#ordTarget') || {}).value), hired: Number(($('#ordGang') || {}).value), price: S.ordPrice || 2500, kind: S.ordKind || 'beat' }),
  'ord-kind': (el) => { S.ordT = ($('#ordTarget') || {}).value; S.ordG = ($('#ordGang') || {}).value; S.ordKind = el.dataset.v; keepScroll(renderRp); haptic(); },
  'ord-price': (el) => { S.ordT = ($('#ordTarget') || {}).value; S.ordG = ($('#ordGang') || {}).value; S.ordPrice = Number(el.dataset.v); keepScroll(renderRp); haptic(); },
  'home-tab': (el) => { S.homeTab = el.dataset.v; if (el.dataset.v === 'flat') S.flat = null; keepScroll(renderRp); haptic(); },
  'news-f': (el) => { S.newsF = el.dataset.v; keepScroll(renderRp); haptic(); },
  'loan-take': () => rpDo('debt/take', { gid: Number(($('#loanG') || {}).value), amount: Number(($('#loanA') || {}).value), hours: Number(($('#loanH') || {}).value) }),
  'mayor-budget': () => rpDo('power/budget', { amount: Number(($('#mBud') || {}).value) }),
  'bonus-go': () => rpDo('gang/bonus', { target: Number(($('#mgTarget') || {}).value), amount: Number(($('#mgAmt') || {}).value) }),
  'fine-go': () => rpDo('gang/fine', { target: Number(($('#mgTarget') || {}).value), amount: Number(($('#mgAmt') || {}).value) }),
  'protect-go': () => rpDo('gang/protect', { target: Number(($('#prTarget') || {}).value), price: Number(($('#prPrice') || {}).value) }),
  'gang-sub': (el) => { S.gangSub = el.dataset.v; keepScroll(renderRp); haptic(); },
  sell: (el) => rpDo('gang/sell', { item: el.dataset.i, qty: Number(el.dataset.q), buyer: Number(($('#sellBuyer') || {}).value || 0) }),
  'deal-send': () => rpDo('gang/deal', { gid: Number(($('#dealGang') || {}).value), kind: ($('#dealKind') || {}).value, item: ($('#dealItem') || {}).value, qty: Number(($('#dealQty') || {}).value), price: Number(($('#dealPrice') || {}).value) }),
  'flat-slot': (el) => { S.flatSel = Number(el.dataset.s) === S.flatSel ? null : Number(el.dataset.s); keepScroll(renderRp); haptic(); },
  'flat-buy': (el) => flatDo('buy', { item: el.dataset.i, slot: S.flatSel != null && S.flat && S.flat.slots[S.flatSel] && !S.flat.slots[S.flatSel].item ? S.flatSel : null }),
  'flat-sell': (el) => { if (confirm('Продать за половину цены?')) flatDo('sell', { slot: Number(el.dataset.s) }); },
  'flat-theme': (el) => flatDo('theme', { key: el.dataset.k }),
  'flat-rest': () => flatDo('rest', {}),
  'flat-visit': (el) => flatDo('visit', { owner: Number(el.dataset.o) }),
  'flat-note': (el) => { const t = ($('#flatNote') || {}).value || ''; if (t.trim()) flatDo('note', { owner: Number(el.dataset.o), text: t }); },
  'flat-notedel': (el) => flatDo('notedel', { nid: Number(el.dataset.n), owner: Number(el.dataset.o) }),
  'flat-go': (el) => { S.flat = null; S.flatSel = null; loadFlat(Number(el.dataset.o)); renderRp(); },
  'gang-plot': (el) => rpDo('gang/plot', { method: el.dataset.v }),
  'gang-repent': (el) => rpDo('gang/repent/decide', { target: Number(el.dataset.u), accept: el.dataset.v === '1' }),
  'gang-vote': (el) => rpDo('gang/vote', { side: el.dataset.v }),
  'gang-donate': (el) => rpDo('gang/donate', { amount: Number(el.dataset.v) }),
  'gang-frame': () => rpDo('gang/frame', { target: Number(($('#ratTarget') || {}).value) }),
  'gang-create': () => rpDo('gang/create', { name: ($('#gangName') || {}).value || '' }),
  'pk-create': async (el) => { try { S.pk = (await api(`/api/chat/${S.chat}/poker`, 'POST', { level: el.dataset.v })).table; S.pkSig = ''; haptic('ok'); await refreshPoker(); viewPoker(); } catch (e) { toast(e.message); haptic('bad'); } },
  'pk-join': async (el) => { try { S.pk = (await api(`/api/chat/${S.chat}/poker/${el.dataset.v}/join`, 'POST')).table; S.pkSig = ''; haptic(); await refreshPoker(); viewPoker(); } catch (e) { toast(e.message); haptic('bad'); refreshPoker(); } },
  'pk-act': async (el) => { try { S.pk = (await api(`/api/poker/${S.pk.tid}/act`, 'POST', { action: el.dataset.v })).table; S.pkSig = ''; haptic(); viewPoker(); } catch (e) { toast(e.message); haptic('bad'); refreshPoker(); } },
  'pk-raise': async () => { try { S.pk = (await api(`/api/poker/${S.pk.tid}/act`, 'POST', { action: 'raise', amount: S.pkRaise })).table; S.pkSig = ''; haptic(); viewPoker(); } catch (e) { toast(e.message); haptic('bad'); refreshPoker(); } },
  'pk-preset': (el) => { S.pkRaise = Number(el.dataset.v); const r = $('#pkRange'); if (r) r.value = S.pkRaise; const v = $('#pkRv'); if (v) v.textContent = S.pkRaise; haptic(); },
  'pk-leave': async () => {
    const tid = S.pk && S.pk.tid;
    S.pk = null; S.pkSig = '';
    if (tid) { try { await api(`/api/poker/${tid}/leave`, 'POST'); } catch (e) { /* стол уже закрыт */ } }
    await refreshPoker(); viewPoker(); loadBoard().catch(() => {});
  },
  rp: (el) => rpDo(el.dataset.p, el.dataset.k ? { key: el.dataset.k } : el.dataset.g ? { gid: Number(el.dataset.g) } : {}),
  'rp-buy': (el) => rpDo('buy', { kind: el.dataset.k }),
  'rp-exch': () => rpDo('exchange', { cm: 1 }),
  'bar-buy': async (el) => {
    if (S.busy) return;
    S.busy = true;
    try {
      const d = await api(`/api/chat/${S.chat}/bar/buy`, 'POST', { id: el.dataset.id });
      S.rpLast = d.text; S.rpGood = true; S.rp.bar = { ...S.rp.bar, ...d.bar };
      S.board.me.size = d.size; updateChip(); haptic('ok'); keepScroll(renderRp);
    } catch (e) { toast(e.message); haptic('bad'); }
    S.busy = false;
  },
  give: async () => {
    const to = Number($('#giveTo').value), amount = Number($('#giveAmt').value);
    try {
      const d = await api(`/api/chat/${S.chat}/give`, 'POST', { to, amount });
      S.giveAt = Date.now() + d.give_in * 1000; S.board.me.size = d.size; updateChip();
      toast(`Передано ${amount} см`); haptic('ok');
      closeModal(); loadBoard().catch(() => {});
    } catch (e) { toast(e.message); haptic('bad'); }
  },
  'bet-amount': (el) => { S.betArena = Number(el.dataset.v); S.sig = ''; viewArena(); },
  'bet-room': async (el) => {
    try {
      const d = await api(`/api/chat/${S.chat}/rooms/${el.dataset.r}/bet`, 'POST', { on: Number(el.dataset.v), amount: S.betArena });
      toast(`Ставка ${S.betArena} см принята`); haptic('ok');
      S.board.me.size = d.size; updateChip(); S.sig = ''; refreshArena();
    } catch (e) { toast(e.message); haptic('bad'); }
  },
  'share-card': async () => {
    try { await api(`/api/chat/${S.chat}/card`, 'POST'); toast('Карточка отправлена в чат'); haptic('ok'); }
    catch (e) { toast(e.message); haptic('bad'); }
  },
  quest: async (el) => {
    try {
      const d = await api(`/api/chat/${S.chat}/quests/claim`, 'POST', { id: el.dataset.v });
      toast(`+${d.reward} см за задание!`); haptic('ok');
      if (S.board) S.board.me.size = d.size;
      updateChip(); await refreshCurrent();
    } catch (e) { toast(e.message); haptic('bad'); }
  },
  hit: async () => {
    try {
      const d = await api(`/api/chat/${S.chat}/boss/hit`, 'POST');
      toast(d.killed ? '💥 Босс повержен!' : (d.crit ? `🔥 Крит! −${d.dmg} HP` : `⚔️ −${d.dmg} HP`));
      haptic(d.killed ? 'ok' : undefined);
      if (S.board) S.board.me.size = d.size;
      updateChip(); await refreshCurrent();
    } catch (e) { toast(e.message); haptic('bad'); }
  },
  'mafia-create': async () => { try { S.mafia = (await api(`/api/chat/${S.chat}/mafia`, 'POST')).mafia; S.gameSeg = 'mafia'; S.sig = ''; haptic('ok'); viewArena(); } catch (e) { toast(e.message); } },
  'mafia-join': async (el) => { try { S.mafia = (await api(`/api/chat/${S.chat}/mafia/${el.dataset.v}/join`, 'POST')).mafia; S.sig = ''; haptic(); viewArena(); } catch (e) { toast(e.message); refreshArena(); } },
  'mafia-start': async () => { try { S.mafia = (await api(`/api/mafia/${S.mafia.rid}/start`, 'POST')).mafia; S.sig = ''; haptic('ok'); viewArena(); } catch (e) { toast(e.message); } },
  'mafia-act': async (el) => { try { S.mafia = (await api(`/api/mafia/${S.mafia.rid}/act`, 'POST', { action: el.dataset.v })).mafia; haptic(); S.sig = ''; viewArena(); } catch (e) { toast(e.message); haptic('bad'); } },
  'mafia-leave': async () => {
    const rid = S.mafia && S.mafia.rid;
    S.mafia = null; S.sig = '';
    if (rid) { try { await api(`/api/mafia/${rid}/leave`, 'POST'); } catch (e) { /* уже закрыта */ } }
    await loadBoard().catch(() => {});
    await refreshArena(); viewArena();
  },
  leave: async () => {
    const rid = S.room && S.room.rid;
    S.room = null; S.sig = '';
    if (rid) { try { await api(`/api/room/${rid}/leave`, 'POST'); } catch (e) { /* уже закрыта */ } }
    await loadBoard().catch(() => {});
    await refreshArena(); viewArena();
  },
};

async function refreshCurrent() {
  if (S.tab === 'home') return viewHome();
  if (S.tab === 'tasks') return viewTasks();
  if (S.tab === 'me') return viewMe();
  if (S.tab === 'rp') return viewRp();
  if (S.tab === 'games') {
    await refreshArena();
    viewGames();
  }
}

function openActivity(id) {
  haptic();
  if (['casino', 'arena', 'mafia'].includes(id)) { setGameSeg(id); return; }
  if (id === 'work') { S.rpSub = 'earn'; S.rpEarn = 'jobs'; setTab('rp'); return; }
  if (id === 'gang') { S.rpSub = 'gang'; setTab('rp'); return; }
  setTab(id);
}

async function createRoom(game, vsBot) {
  try {
    S.room = (await api(`/api/chat/${S.chat}/rooms`, 'POST', { game, vs_bot: vsBot })).room;
    S.gameSeg = 'arena'; S.sig = ''; haptic(); viewArena();
  } catch (e) { toast(e.message); }
}

document.addEventListener('click', (e) => {
  const el = e.target.closest('[data-a]');
  if (el && !el.disabled && actions[el.dataset.a]) {
    e.preventDefault();
    actions[el.dataset.a](el);
  }
});

window.addEventListener('scroll', () => {
  document.body.classList.toggle('scrolled', window.scrollY > 8);
}, { passive: true });

// ───────── навигация ─────────
async function setTab(tab) {
  let seg = null;
  if (['casino', 'arena', 'mafia'].includes(tab)) { seg = tab; tab = 'games'; }
  if (tab === 'chat') tab = 'rp';
  if (!['home', 'games', 'rp'].includes(tab)) tab = 'home';
  if (seg) S.gameSeg = seg;
  S.tab = tab; S.sig = '';
  document.querySelectorAll('#tabs button').forEach((b) => b.classList.toggle('on', b.dataset.tab === tab));
  stopPoll(); clearInterval(S.rpTimer);
  try {
    if (tab === 'home') await viewHome();
    else if (tab === 'rp') await viewRp();
    else { await refreshArena(false); viewGames(); manageGamesPoll(); }
  } catch (e) {
    view.innerHTML = `<div class="empty">${esc(e.message)}</div>`;
  }
  window.scrollTo(0, 0);
}

async function init() {
  applyTheme();
  if (!tg || !tg.initData) {
    view.innerHTML = '<div class="empty"><span class="e-ic">📱</span>Открой мини-апп из Telegram: напиши боту /app</div>';
    return;
  }
  try { S.me = await api('/api/me'); }
  catch (e) { view.innerHTML = `<div class="empty">${esc(e.message)}</div>`; return; }
  applyTheme();
  if (!S.me.chats.length) {
    view.innerHTML = '<div class="empty"><span class="e-ic">🏙</span>Тебя пока нет ни в одном чате с ботом.<br>Напиши что-нибудь боту в групповом чате и открой мини-апп снова.</div>';
    return;
  }
  const saved = recall('chat');
  S.chat = S.me.chats.some((c) => String(c.chat_id) === saved) ? Number(saved) : S.me.chats[0].chat_id;
  updateHeader();

  document.querySelectorAll('#tabs button').forEach((b) => b.addEventListener('click', () => { haptic(); setTab(b.dataset.tab); }));
  $('#chatBtn').addEventListener('click', () => { haptic(); openChats(); });
  $('#balanceBtn').addEventListener('click', () => { haptic(); setTab('rp'); });
  if (tg.onEvent) { try { tg.onEvent('themeChanged', applyTheme); } catch (e) { /* старые клиенты */ } }
  $('#modalClose').addEventListener('click', closeModal);
  $('#modal').addEventListener('click', (e) => {
    if (e.target.id === 'modal') { closeModal(); return; }
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

  // открыть нужную вкладку по ссылке: ?tab=casino или startapp=casino
  // параметр startapp: «вкладка» или «вкладка_c<id чата без минуса>»
  const params = new URLSearchParams(location.search);
  const raw = params.get('tab') || params.get('tgWebAppStartParam') || (tg.initDataUnsafe || {}).start_param || '';
  const [want, chatPart] = raw.split('_c');
  let target = want;
  if (['casino', 'arena', 'mafia'].includes(want)) { S.gameSeg = want; target = 'games'; }
  if (want === 'chat') target = 'rp';
  if (want === 'bar') { S.rpSub = 'bar'; target = 'rp'; }
  if (want === 'work') { S.rpSub = 'earn'; S.rpEarn = 'jobs'; target = 'rp'; }
  if (want === 'gang') { S.rpSub = 'gang'; target = 'rp'; }
  if (want === 'police') { S.rpSub = 'police'; target = 'rp'; }
  if (want === 'map') { S.rpSub = 'map'; target = 'rp'; }
  if (!['home', 'games', 'rp'].includes(target)) target = 'home';
  const fromLink = chatPart ? -Number(chatPart) : null;
  if (fromLink && S.me.chats.some((c) => c.chat_id === fromLink)) {
    S.chat = fromLink;
    remember('chat', String(fromLink));
  }
  updateHeader();
  if (target !== 'home') await loadBoard().catch(() => {});
  await setTab(target);
}

init();
