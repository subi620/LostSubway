/* 로스트인지하철 화면 로직 — 해시 라우팅: #/ · #/results?… · #/item/<id> */
(() => {
  'use strict';

  // ---------- 기본 도구 ----------
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const icon = (id, cls = 'ic') => `<svg class="${cls}" aria-hidden="true"><use href="#${id}"></use></svg>`;
  const pad = (n) => String(n).padStart(2, '0');

  const CATS = [
    ['wallet', '지갑·카드', 'i-wallet'],
    ['phone', '휴대폰', 'i-phone'],
    ['bag', '가방', 'i-bag'],
    ['umbrella', '우산', 'i-umbrella'],
    ['device', '이어폰·기기', 'i-earbuds'],
    ['etc', '기타', 'i-dots'],
  ];
  const CAT = Object.fromEntries(CATS.map(([key, label, ic]) => [key, { label, icon: ic }]));
  const PERIODS = [3, 7, 14, 30];
  const PAGE = 30;
  const DETAIL_BATCH = 8;

  function photoSrc(original, width, quality) {
    if (!original || ['localhost', '127.0.0.1'].includes(location.hostname)) return original;
    try {
      const url = new URL(original);
      if (url.protocol !== 'https:' || url.hostname !== 'minwon24.police.go.kr') return original;
      return `/api/image?${new URLSearchParams({ url: original, w: width, q: quality })}`;
    } catch { return original; }
  }

  // localStorage 는 막혀 있을 수 있으니 항상 try/catch
  const local = {
    get(key, fallback) {
      try { const v = localStorage.getItem(key); return v ? JSON.parse(v) : fallback; } catch { return fallback; }
    },
    set(key, value) {
      try { localStorage.setItem(key, JSON.stringify(value)); } catch { /* 저장 안 돼도 화면은 동작 */ }
    },
  };
  const session = {
    get(key) { try { return sessionStorage.getItem(key); } catch { return null; } },
    set(key, value) { try { sessionStorage.setItem(key, value); } catch { /* 무시 */ } },
  };

  async function api(path, params = {}, signal) {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v !== null && v !== undefined && v !== ''));
    let res;
    try {
      res = await fetch(`/api/${path}${qs.toString() ? `?${qs}` : ''}`, { signal, headers: { Accept: 'application/json' } });
    } catch (e) {
      if (e.name === 'AbortError') throw e;
      throw new Error('서버에 연결하지 못했어요. python server.py 가 켜져 있는지 확인해 주세요.');
    }
    let data;
    try { data = await res.json(); } catch { throw new Error('서버 응답을 읽지 못했어요.'); }
    if (!res.ok) throw new Error(data.error || `오류가 났어요 (${res.status})`);
    return data;
  }

  const lineBadge = (m, small = false) =>
    `<span class="lb${small ? ' sm' : ''}" style="background:${esc(m.color)};color:${esc(m.fg)}" role="img" aria-label="${esc(m.name)}">${esc(m.label)}</span>`;
  const lineBadges = (lines, small = false) => (lines || []).map((m) => lineBadge(m, small)).join('');

  const today0 = () => { const d = new Date(); d.setHours(0, 0, 0, 0); return d; };
  const fmtYmd = (ymd) => { const [, m, d] = (ymd || '').split('-'); return m ? `${m}.${d}` : ''; };
  function ago(ymd) {
    const n = Math.round((today0() - new Date(`${ymd}T00:00:00`)) / 86400000);
    return n <= 0 ? '오늘' : n === 1 ? '어제' : `${n}일 전`;
  }
  function fmtStamp(iso) {
    if (!iso) return '';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '' : `${pad(d.getMonth() + 1)}.${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
  }

  let toastTimer;
  function toast(msg) {
    const el = $('#toast');
    el.textContent = msg;
    el.classList.add('show');
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => el.classList.remove('show'), 2600);
  }

  // ---------- 라우팅 ----------
  function parseHash() {
    const raw = location.hash.slice(1) || '/';
    const [path, qs = ''] = raw.split('?');
    return { path, q: Object.fromEntries(new URLSearchParams(qs)) };
  }
  const queryString = (q) => new URLSearchParams(Object.entries(q).filter(([, v]) => v !== null && v !== undefined && v !== '')).toString();
  const resultsHref = (q) => `#/results?${queryString(q)}`;

  function showView(id, title) {
    for (const v of $$('.view')) v.hidden = v.id !== id;
    document.title = title ? `${title} · 로스트인지하철` : '로스트인지하철 · 지하철 분실물 찾기';
    window.scrollTo(0, 0);
  }
  function focusHeading(id) {
    const h = $(`#${id} h1`);
    if (h && document.activeElement !== $('#station-input')) h.focus({ preventScroll: true });
  }

  let routeCtrl;
  function route() {
    routeCtrl?.abort();
    routeCtrl = new AbortController();
    const { path, q } = parseHash();
    if (path.startsWith('/item/')) return showItem(decodeURIComponent(path.slice(6)), routeCtrl.signal);
    if (path === '/results') return showResults(q, routeCtrl.signal);
    return showSearch();
  }

  // ---------- 상태 ----------
  const form = {
    place: null, // {type:'station', name, lines:[meta]} | {type:'line', line: meta}
    cat: null,
    days: 14,
  };
  let status = null;
  let linesCache = null;

  // ---------- ① 검색 ----------
  const input = $('#station-input');
  const list = $('#station-list');
  let suggestTimer;
  let suggestCtrl;
  let activeIndex = -1;
  let suggestions = [];

  function buildSearchForm() {
    $('#cat-grid').innerHTML = CATS.map(([key, label, ic]) =>
      `<button type="button" class="cat cat-${key}" data-cat="${key}" aria-pressed="false">${icon(ic)}<span>${label}</span></button>`).join('');
    $('#period').innerHTML = PERIODS.map((d) =>
      `<span class="seg"><input type="radio" name="days" id="days-${d}" value="${d}"><label for="days-${d}">${d}일</label></span>`).join('');

    $('#cat-grid').addEventListener('click', (e) => {
      const btn = e.target.closest('.cat');
      if (!btn) return;
      form.cat = form.cat === btn.dataset.cat ? null : btn.dataset.cat;
      syncFormUI();
    });
    $('#watch-alerts').addEventListener('click', onWatchSummaryClick);
    $('#period').addEventListener('change', (e) => { form.days = Number(e.target.value); });

    input.addEventListener('input', () => {
      form.place = null;
      renderStationBox();
      setError('');
      clearTimeout(suggestTimer);
      suggestTimer = setTimeout(loadSuggestions, 140);
    });
    input.addEventListener('keydown', onSuggestKey);
    input.addEventListener('focus', () => { if (input.value.trim() && !form.place) loadSuggestions(); });
    input.addEventListener('blur', () => setTimeout(closeSuggest, 120));
    list.addEventListener('mousedown', (e) => e.preventDefault()); // 클릭 전에 blur 로 닫히지 않게
    list.addEventListener('click', (e) => {
      const li = e.target.closest('[data-index]');
      if (li) chooseStation(suggestions[Number(li.dataset.index)]);
    });
    $('#station-clear').addEventListener('click', () => {
      form.place = null;
      input.value = '';
      renderStationBox();
      closeSuggest();
      input.focus();
    });

    $('#recent-chips').addEventListener('click', (e) => {
      const chip = e.target.closest('.chip');
      if (!chip) return;
      if (chip.dataset.action === 'lines') return toggleLinePicker();
      const recent = local.get('lis.recent', [])[Number(chip.dataset.index)];
      if (recent) {
        form.place = recent;
        input.value = recent.type === 'line' ? `${recent.line.name} 열차 안` : recent.name;
        renderStationBox();
        setError('');
      }
    });
    $('#line-grid').addEventListener('click', (e) => {
      const btn = e.target.closest('.line-btn');
      if (!btn) return;
      const meta = (linesCache || []).find((l) => l.line === btn.dataset.line);
      if (!meta) return;
      form.place = { type: 'line', line: meta };
      input.value = `${meta.name} 열차 안`;
      renderStationBox();
      setError('');
      toggleLinePicker(false);
    });

    $('#search-form').addEventListener('submit', (e) => {
      e.preventDefault();
      closeSuggest();
      const q = { days: form.days, cat: form.cat };
      if (form.place?.type === 'line') {
        q.line = form.place.line.line;
        q.scope = 'line';
      } else {
        const name = form.place?.name || input.value.trim().replace(/\s+/g, '');
        if (!name) {
          setError('역 이름을 입력하거나, 열차 안이었다면 호선을 골라 주세요.');
          input.focus();
          return;
        }
        q.station = name;
        if (!form.place) form.place = { type: 'station', name, lines: [] };
      }
      addRecent(form.place);
      location.hash = resultsHref(q);
    });
  }

  function setError(msg) {
    $('#station-error').textContent = msg;
    $('#station-box').classList.toggle('invalid', Boolean(msg));
    input.setAttribute('aria-invalid', msg ? 'true' : 'false');
  }

  function renderStationBox() {
    const p = form.place;
    $('#station-badges').innerHTML = p?.type === 'line' ? lineBadge(p.line) : p?.type === 'station' ? lineBadges(p.lines) : '';
    $('#station-clear').hidden = !input.value;
  }

  function syncFormUI() {
    for (const btn of $$('.cat')) btn.setAttribute('aria-pressed', String(btn.dataset.cat === form.cat));
    const radio = $(`#days-${form.days}`);
    if (radio) radio.checked = true;
    if (form.place) input.value = form.place.type === 'line' ? `${form.place.line.name} 열차 안` : form.place.name;
    renderStationBox();
  }

  async function loadSuggestions() {
    const text = input.value.trim();
    if (!text) return closeSuggest();
    suggestCtrl?.abort();
    suggestCtrl = new AbortController();
    try {
      const data = await api('stations', { q: text }, suggestCtrl.signal);
      if (input.value.trim() !== text) return;
      suggestions = data.stations;
      activeIndex = suggestions.length ? 0 : -1;
      renderSuggest(text);
    } catch (e) {
      if (e.name !== 'AbortError') closeSuggest();
    }
  }

  function renderSuggest(text) {
    if (!suggestions.length) {
      list.innerHTML = `<li class="s-empty" role="option" aria-disabled="true">'${esc(text)}' 이름의 역에서 최근 습득물이 없어요. 그대로 검색하면 이름이 비슷한 곳을 찾아봐요.</li>`;
    } else {
      list.innerHTML = suggestions.map((s, i) => `
        <li id="opt-${i}" role="option" data-index="${i}" aria-selected="${i === activeIndex}">
          <span class="badges">${lineBadges(s.lines, true)}</span>
          <span class="s-name">${esc(s.name)}</span>
          <span class="s-count">30일 ${s.count.toLocaleString()}건</span>
        </li>`).join('');
    }
    list.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    input.setAttribute('aria-activedescendant', activeIndex >= 0 ? `opt-${activeIndex}` : '');
  }

  function closeSuggest() {
    list.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function onSuggestKey(e) {
    if (list.hidden || !suggestions.length) return;
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault();
      activeIndex = (activeIndex + (e.key === 'ArrowDown' ? 1 : -1) + suggestions.length) % suggestions.length;
      renderSuggest(input.value.trim());
      $(`#opt-${activeIndex}`)?.scrollIntoView({ block: 'nearest' });
    } else if (e.key === 'Enter' && activeIndex >= 0) {
      e.preventDefault();
      chooseStation(suggestions[activeIndex]);
    } else if (e.key === 'Escape') {
      closeSuggest();
    }
  }

  function chooseStation(s) {
    if (!s) return;
    form.place = { type: 'station', name: s.name, lines: s.lines };
    input.value = s.name;
    renderStationBox();
    closeSuggest();
    setError('');
  }

  function placeKey(p) { return p.type === 'line' ? `line:${p.line.line}` : `station:${p.name}`; }
  function addRecent(place) {
    if (!place) return;
    const recent = local.get('lis.recent', []).filter((r) => placeKey(r) !== placeKey(place));
    recent.unshift(place);
    local.set('lis.recent', recent.slice(0, 5));
  }

  function renderRecent() {
    const recent = local.get('lis.recent', []);
    const chips = recent.map((r, i) => r.type === 'line'
      ? `<button type="button" class="chip" data-index="${i}">${lineBadge(r.line, true)}${esc(r.line.name)} 열차 안</button>`
      : `<button type="button" class="chip" data-index="${i}">${esc(r.name)}</button>`);
    const open = !$('#line-picker').hidden;
    $('#recent-chips').innerHTML =
      (recent.length ? '<span class="chips-label">최근</span>' : '') + chips.join('') +
      `<button type="button" class="chip" data-action="lines" aria-expanded="${open}" aria-controls="line-picker">${icon('i-train')}열차 안 (호선만 기억)</button>`;
  }

  async function toggleLinePicker(force) {
    const picker = $('#line-picker');
    const open = force ?? picker.hidden;
    picker.hidden = !open;
    renderRecent();
    if (!open) return;
    if (!linesCache) {
      $('#line-grid').innerHTML = '<span class="field-hint">노선 불러오는 중…</span>';
      try {
        linesCache = (await api('lines')).lines;
      } catch (e) {
        $('#line-grid').innerHTML = `<span class="field-error">${esc(e.message)}</span>`;
        return;
      }
    }
    const btn = (l) => `<button type="button" class="line-btn" data-line="${esc(l.line)}">${lineBadge(l, true)}<span>${esc(l.name)}</span></button>`;
    const numbered = linesCache.filter((l) => /^\d+호선$/.test(l.line));
    const others = linesCache.filter((l) => !/^\d+호선$/.test(l.line));
    $('#line-grid').innerHTML = linesCache.length
      ? `${numbered.length ? `<div class="line-grid">${numbered.map(btn).join('')}</div>` : ''}
         ${others.length ? `<div class="line-grid two">${others.map(btn).join('')}</div>` : ''}`
      : '<span class="field-hint">아직 받은 데이터가 없어요.</span>';
  }

  let statusTimer;
  async function refreshStatus() {
    clearTimeout(statusTimer);
    const note = $('#sync-note');
    try {
      status = await api('status');
    } catch (e) {
      note.className = 'sync-note warn';
      note.textContent = e.message;
      return;
    }
    const { sync, stats, hasKey } = status;
    note.className = 'sync-note';
    if (!hasKey) {
      note.classList.add('warn');
      note.textContent = '.env 에 API 키가 없어 새 습득물을 받지 못해요. 저장된 데이터로만 찾아요.';
    } else if (sync?.running) {
      note.innerHTML = `<span class="dot busy" aria-hidden="true"></span>LOST112 최신 습득물 받는 중 (${sync.done}/${sync.total}일)`;
      statusTimer = setTimeout(() => { if (!$('#view-search').hidden) refreshStatus(); }, 4000);
    } else if (sync?.error) {
      note.classList.add('warn');
      note.textContent = `${sync.error} · 저장된 ${stats.transit.toLocaleString()}건으로 찾아요.`;
    } else {
      note.innerHTML = `<span class="dot" aria-hidden="true"></span>최근 30일 지하철 습득물 ${stats.transit.toLocaleString()}건 · ${fmtStamp(sync?.lastSync)} 갱신`;
    }
  }

  function showSearch() {
    showView('view-search');
    $('#line-picker').hidden = true;
    renderRecent();
    syncFormUI();
    refreshStatus();
    checkWatches();
    focusHeading('view-search');
  }

  // ---------- 저장한 검색 (다시 확인) ----------
  const watchKey = (q) => JSON.stringify([q.station || '', q.line || '', q.scope || '', q.cat || '', String(q.days || 14)]);
  const getWatches = () => local.get('lis.watch', []);
  const findWatch = (q) => getWatches().find((w) => w.key === watchKey(q));

  function describeQuery(q, data) {
    const parts = [];
    if (q.station) parts.push(q.scope === 'keyword' ? `'${q.station}' 포함` : q.scope === 'line' && q.line ? `${q.station} + ${data?.lineInfo?.name || q.line} 전체` : q.station);
    else if (q.line) parts.push(`${data?.lineInfo?.name || q.line} 열차 안`);
    parts.push(q.cat ? CAT[q.cat].label : '전체 물품');
    parts.push(`최근 ${q.days || 14}일`);
    return parts.join(' · ');
  }

  const WATCH_MAX = 5;
  // 저장 결과: 'saved' | 'saved-dropped'(가장 오래된 조건을 지움) | 'removed'
  function setWatch(q, label, ids, on) {
    const others = getWatches().filter((w) => w.key !== watchKey(q));
    if (!on) { local.set('lis.watch', others); return 'removed'; }
    others.unshift({ key: watchKey(q), q, label, seen: ids.slice(0, 500), at: Date.now() });
    local.set('lis.watch', others.slice(0, WATCH_MAX));
    return others.length > WATCH_MAX ? 'saved-dropped' : 'saved';
  }

  function markSeen(q, ids) {
    const watches = getWatches();
    const w = watches.find((x) => x.key === watchKey(q));
    if (!w) return [];
    const fresh = ids.filter((id) => !w.seen.includes(id));
    w.seen = [...new Set([...ids, ...w.seen])].slice(0, 500);
    local.set('lis.watch', watches);
    return fresh;
  }

  // 첫 화면 '저장한 조건' — 저장한 게 있으면 새 습득물이 없어도 항상 보여줌
  const watchView = { rows: [], open: null, asOf: '', token: 0 };

  async function checkWatches() {
    const token = ++watchView.token;
    const watches = getWatches();
    watchView.rows = watches.map((w) => ({ w, fresh: null, failed: false }));
    renderWatchSummary();
    if (!watches.length) return;
    const rows = await Promise.all(watches.map(async (w) => {
      try {
        const data = await api('search', w.q);
        return { w, fresh: data.items.filter((it) => !w.seen.includes(it.id)).length, failed: false, asOf: data.asOf };
      } catch { return { w, fresh: null, failed: true }; }
    }));
    if (token !== watchView.token) return; // 그사이 다시 확인을 시작했으면 버림
    watchView.rows = rows;
    watchView.asOf = rows.find((r) => r.asOf)?.asOf || '';
    if (watchView.open === null) watchView.open = rows.some((r) => r.fresh > 0);
    renderWatchSummary();
  }

  function renderWatchSummary() {
    const box = $('#watch-alerts');
    const rows = watchView.rows;
    if (!rows.length) { box.hidden = true; box.innerHTML = ''; return; }
    const checking = rows.some((r) => r.fresh === null && !r.failed);
    const total = rows.reduce((n, r) => n + (r.fresh || 0), 0);
    const state = checking ? '새 습득물 확인 중…' : total ? `새 습득물 ${total.toLocaleString()}건` : '새 습득물 없음';
    const pill = (r) => r.failed ? '<span class="ws-pill">확인 실패</span>'
      : r.fresh === null ? '<span class="ws-pill">확인 중</span>'
      : r.fresh ? `<span class="ws-pill has-new">새 ${r.fresh.toLocaleString()}건</span>` : '<span class="ws-pill">새 습득물 없음</span>';
    const open = Boolean(watchView.open);
    box.hidden = false;
    box.innerHTML = `
      <button type="button" class="ws-head" data-action="toggle" aria-expanded="${open}" aria-controls="ws-body">
        <span class="ws-icon${total ? ' on' : ''}">${icon('i-bell')}</span>
        <span class="ws-text"><strong>저장한 조건 ${rows.length}개</strong><span class="ws-state${total ? ' has-new' : ''}">${state}</span></span>
        ${icon('i-chevron', 'ic sm ws-chev')}
      </button>
      <div class="ws-body" id="ws-body"${open ? '' : ' hidden'}>
        <ul class="ws-list">${rows.map((r) => `
          <li class="ws-row">
            <a class="ws-link" href="${resultsHref(r.w.q)}"><span class="ws-label">${esc(r.w.label)}</span>${pill(r)}</a>
            <button type="button" class="icon-btn" data-action="delete" data-key="${esc(r.w.key)}" aria-label="${esc(r.w.label)} 저장 지우기">${icon('i-x', 'ic sm')}</button>
          </li>`).join('')}
        </ul>
        <p class="ws-foot">이 기기의 이 브라우저에만 저장돼요${watchView.asOf ? ` · ${fmtStamp(watchView.asOf)} 기준` : ''}</p>
      </div>`;
  }

  function onWatchSummaryClick(e) {
    const btn = e.target.closest('[data-action]');
    if (!btn) return;
    if (btn.dataset.action === 'toggle') {
      watchView.open = !watchView.open;
    } else if (btn.dataset.action === 'delete') {
      const key = btn.dataset.key;
      local.set('lis.watch', getWatches().filter((w) => w.key !== key));
      watchView.rows = watchView.rows.filter((r) => r.w.key !== key);
      toast('저장한 조건을 지웠어요.');
    }
    renderWatchSummary();
    $('#watch-alerts .ws-head')?.focus();
  }

  const saveBtnInner = (on) => on ? `${icon('i-check')}<span>저장됨</span>` : `${icon('i-bookmark')}<span>이 조건 저장</span>`;
  function setSaveBtn(btn, on) {
    btn.classList.toggle('on', on);
    btn.innerHTML = saveBtnInner(on);
    if (on) btn.setAttribute('aria-label', '저장됨, 누르면 저장 해제');
    else btn.removeAttribute('aria-label');
  }

  function watchCardHtml(q, label, idPrefix) {
    const on = Boolean(findWatch(q));
    return `
      <section class="watch-card" aria-labelledby="${idPrefix}-watch-title">
        <div class="watch-text">
          <strong id="${idPrefix}-watch-title">못 찾으면 다시 확인</strong>
          <span id="${idPrefix}-watch-desc">이 조건을 이 기기에 저장해 두면, 첫 화면 '저장한 조건'에서 새로 올라온 습득물을 바로 볼 수 있어요.</span>
        </div>
        <button type="button" class="save-btn${on ? ' on' : ''}" aria-describedby="${idPrefix}-watch-desc"${on ? ' aria-label="저장됨, 누르면 저장 해제"' : ''}
                data-watch='${esc(JSON.stringify({ q, label }))}'>${saveBtnInner(on)}</button>
      </section>`;
  }

  document.addEventListener('click', (e) => {
    const btn = e.target.closest('.save-btn[data-watch]');
    if (!btn) return;
    const { q, label } = JSON.parse(btn.dataset.watch);
    const on = !findWatch(q);
    const ids = (lastResults && watchKey(lastResults.query) === watchKey(q)) ? lastResults.items.map((i) => i.id) : [];
    const result = setWatch(q, label, ids, on);
    if (on && !findWatch(q)) { toast('이 브라우저에 저장할 수 없어요. 개인정보 보호 모드인지 확인해 주세요.'); return; }
    setSaveBtn(btn, on);
    toast(result === 'removed' ? '저장한 조건을 지웠어요.'
      : result === 'saved-dropped' ? `저장했어요. 최대 ${WATCH_MAX}개라 가장 오래된 조건은 지웠어요.`
      : "저장했어요. 첫 화면 '저장한 조건'에서 새 습득물을 볼 수 있어요.");
  });

  // ---------- ② 결과 ----------
  let lastResults = null;

  function applyQueryToForm(data) {
    const q = data.query;
    form.days = q.days;
    form.cat = q.cat;
    if (q.station) form.place = { type: 'station', name: q.station, lines: data.station?.lines || [] };
    else if (data.lineInfo) form.place = { type: 'line', line: data.lineInfo };
  }

  function chipsHtml(data) {
    const q = data.query;
    const chips = [];
    const stLines = lineBadges(data.station?.lines, true);
    if (q.station && q.scope === 'keyword') chips.push(`<span class="fchip">'${esc(q.station)}' 들어간 역</span>`);
    else if (q.station) chips.push(`<span class="fchip">${stLines}${esc(q.station)}</span>`);
    if (q.scope === 'line' && data.lineInfo) {
      chips.push(`<span class="fchip">${lineBadge(data.lineInfo, true)}${esc(data.lineInfo.name)} ${q.station ? '전체' : '열차 안'}</span>`);
    }
    chips.push(`<span class="fchip">${q.cat ? esc(CAT[q.cat].label) : '전체 물품'}</span>`);
    chips.push(`<span class="fchip">최근 ${q.days}일</span>`);
    return chips.join('');
  }

  function cardHtml(it, { pending, isNew }) {
    const thumbSrc = photoSrc(it.img, 128, 65);
    const thumb = it.img
      ? `<img src="${esc(thumbSrc)}" ${thumbSrc !== it.img ? `data-original="${esc(it.img)}"` : ''} alt="" width="60" height="60" loading="lazy" decoding="async" fetchpriority="low" referrerpolicy="no-referrer" data-fallback="${esc(CAT[it.cat]?.icon || 'i-dots')}">`
      : icon(CAT[it.cat]?.icon || 'i-dots', 'ic lg');
    const origin = it.movedFrom
      ? `<span class="card-origin">${icon('i-train')}<span class="truncate">${esc(it.movedFrom)}역에서 습득 → 센터로 옮겨짐</span></span>`
      : '';
    return `
      <li>
        <a class="card" href="#/item/${encodeURIComponent(it.id)}" data-id="${esc(it.id)}">
          <span class="thumb">${thumb}</span>
          <span class="card-main">
            <span class="card-top">
              <span class="card-name">${isNew ? '<span class="new">NEW</span>' : ''}${esc(it.name)}</span>
              ${pending ? '<span class="badge pending" data-badge aria-hidden="true"></span>' : ''}
            </span>
            <span class="card-place">${icon('i-pin')}<span class="truncate">${esc(it.place)} 보관</span></span>
            ${origin}
            <span class="card-meta">습득 ${fmtYmd(it.ymd)} · ${ago(it.ymd)}${it.color ? ` · ${esc(it.color)}` : ''}</span>
          </span>
        </a>
      </li>`;
  }

  function bindPhotoFallbacks(root) {
    for (const img of $$('img[data-fallback], img[data-original]', root)) {
      img.addEventListener('error', () => {
        if (img.dataset.original) {
          img.src = img.dataset.original;
          delete img.dataset.original;
        } else if (img.dataset.fallback) {
          img.parentElement.innerHTML = icon(img.dataset.fallback, 'ic lg');
        } else {
          img.parentElement.remove();
        }
      });
    }
  }

  async function fillStatuses(ids, signal) {
    if (!ids.length) return;
    try {
      const { details } = await api('details', { ids: ids.join(',') }, signal);
      for (const id of ids) {
        const card = $(`.card[data-id="${CSS.escape(id)}"]`);
        const badge = card && $('[data-badge]', card);
        if (!badge) continue;
        const st = details[id]?.status;
        if (!st) { badge.remove(); continue; }
        badge.className = `badge ${st.tone}`;
        badge.textContent = st.text;
        badge.removeAttribute('aria-hidden');
      }
    } catch (e) {
      if (e.name !== 'AbortError') for (const b of $$('[data-badge]')) b.remove();
    }
  }

  function widenHtml(data) {
    const q = data.query;
    if (!data.widen.length) {
      return data.total ? '' : `
        <section class="widen">
          <div class="widen-head"><strong>넓혀 봐도 더 나오는 물건이 없어요</strong>
          <span>다른 역 이름으로 찾아보거나, 아래 공식 사이트에서 직접 확인해 보세요.</span></div>
        </section>`;
    }
    const buttons = data.widen.map((w) => {
      const next = { ...q, ...w.params };
      const title = w.type === 'line' ? `${lineBadge(w.line, true)}${esc(w.title)}` : esc(w.title);
      return `
        <a class="widen-btn" href="${resultsHref(next)}">
          <span class="w-text"><span class="w-title">${title}</span>${w.hint ? `<span class="w-hint">${esc(w.hint)}</span>` : ''}</span>
          <span class="w-add">+${w.add.toLocaleString()}</span>
        </a>`;
    }).join('');
    return `
      <section class="widen" aria-labelledby="widen-title">
        <div class="widen-head">
          <strong id="widen-title">${data.total ? '못 찾으셨나요? 범위를 넓혀 보세요' : '범위를 넓히면 이만큼 더 있어요'}</strong>
          <span>역에서 맡긴 물건은 노선별 유실물센터로 옮겨지고, 보관장소 표기가 역 이름과 다를 수 있어요.</span>
        </div>
        <div class="widen-list">${buttons}</div>
      </section>`;
  }

  const officialHtml = `
    <section class="official" aria-labelledby="official-title">
      <span class="section-label" id="official-title">공식 사이트에서 직접 확인</span>
      <div class="official-grid">
        <a href="https://www.lost112.go.kr/" target="_blank" rel="noopener noreferrer">
          <strong>LOST112 ${icon('i-external')}</strong><span>경찰청 유실물 통합포털</span></a>
        <a href="https://www.seoulmetro.co.kr/kr/page.do?menuIdx=541" target="_blank" rel="noopener noreferrer">
          <strong>유실물센터 ${icon('i-external')}</strong><span>서울교통공사 안내</span></a>
      </div>
    </section>`;

  async function showResults(q, signal) {
    showView('view-results', '검색 결과');
    const body = $('#results-body');
    $('#result-chips').innerHTML = '';
    body.innerHTML = '<div class="skeleton"></div><div class="skeleton"></div><div class="skeleton"></div>';
    focusHeading('view-results');

    let data;
    try {
      data = await api('search', q, signal);
    } catch (e) {
      if (e.name === 'AbortError') return;
      body.innerHTML = `
        <div class="error-box"><p>${esc(e.message)}</p>
        <button type="button" class="btn-ghost" id="retry">${icon('i-refresh')}다시 시도</button></div>`;
      $('#retry').addEventListener('click', route);
      return;
    }

    lastResults = data;
    session.set('lis.lastResults', location.hash);
    applyQueryToForm(data);
    const fresh = new Set(markSeen(data.query, data.items.map((i) => i.id)));
    const label = describeQuery(data.query, data);

    $('#result-chips').innerHTML = chipsHtml(data);
    const head = `
      <div class="results-head">
        <span class="count">습득물 <strong>${data.total.toLocaleString()}건</strong></span>
        <span class="asof">LOST112 · ${fmtStamp(data.asOf) || '—'} 기준${data.syncing ? ' (갱신 중)' : ''}</span>
      </div>
      ${data.notice ? `<p class="notice">${esc(data.notice)}</p>` : ''}`;

    const empty = data.total ? '' : `
      <div class="empty">${icon('i-search')}
        <strong>조건에 맞는 습득물이 아직 없어요</strong>
        <p>등록까지 하루 이틀 걸리기도 해요. 범위를 넓히거나, 아래에서 이 조건을 저장해 두세요.</p>
      </div>`;

    body.innerHTML = `
      ${head}
      ${empty}
      <ul class="cards" id="cards"></ul>
      <button type="button" class="btn-more" id="more" hidden></button>
      ${data.total > data.items.length ? `<p class="cap-note">최근 ${data.items.length}건까지 보여드려요. 조건을 좁히면 더 정확해요.</p>` : ''}
      ${widenHtml(data)}
      ${watchCardHtml(data.query, label, 'results')}
      ${officialHtml}`;

    let shown = 0;
    const more = $('#more');
    const renderMore = () => {
      const chunk = data.items.slice(shown, shown + PAGE);
      const ul = $('#cards');
      ul.insertAdjacentHTML('beforeend', chunk.map((it, i) =>
        cardHtml(it, { pending: shown + i < DETAIL_BATCH, isNew: fresh.has(it.id) && findWatch(data.query) })).join(''));
      bindPhotoFallbacks(ul);
      shown += chunk.length;
      const left = data.items.length - shown;
      more.hidden = left <= 0;
      more.textContent = `더 보기 (${left.toLocaleString()}건 남음)`;
    };
    more.addEventListener('click', renderMore);
    renderMore();
    fillStatuses(data.items.slice(0, DETAIL_BATCH).map((i) => i.id), signal);
  }

  // ---------- ③ 찾으러 가기 ----------
  function mapQuery(place) {
    return (place || '').replace(/[()]/g, ' ').replace(/\s+/g, ' ').trim();
  }

  async function showItem(id, signal) {
    showView('view-item', '찾으러 가기');
    const back = session.get('lis.lastResults');
    $('#item-back').href = back || '#/';
    $('#item-back').setAttribute('aria-label', back ? '검색 결과로 돌아가기' : '처음으로');
    const body = $('#item-body');
    body.innerHTML = '<div class="item-section"><div class="skeleton"></div><div class="skeleton"></div></div>';
    focusHeading('view-item');

    let data;
    try {
      data = await api('item', { id }, signal);
    } catch (e) {
      if (e.name === 'AbortError') return;
      body.innerHTML = `<div class="item-section"><div class="error-box"><p>${esc(e.message)}</p>
        <button type="button" class="btn-ghost" id="retry">${icon('i-refresh')}다시 시도</button></div></div>`;
      $('#retry').addEventListener('click', route);
      return;
    }
    if (!data.item) {
      body.innerHTML = `<div class="item-section"><div class="error-box"><p>${esc(data.error)}</p>
        <a class="btn-ghost" href="https://www.lost112.go.kr/" target="_blank" rel="noopener noreferrer">LOST112에서 확인 ${icon('i-external')}</a></div></div>`;
      return;
    }

    const it = data.item;
    const d = data.detail || {};
    const st = d.status;
    const place = d.depPlace || it.place;
    const tel = d.tel;
    const img = d.img || it.img;
    const detailSrc = photoSrc(img, 640, 75);
    document.title = `${it.name} · 로스트인지하철`;

    const facts = [
      ['습득일', `${it.ymd ? `${it.ymd.replaceAll('-', '.')} (${ago(it.ymd)})` : '—'}${d.hour ? ` ${esc(d.hour)}` : ''}`],
      it.origin ? ['습득한 역', `${esc(it.origin)}역`] : null,
      ['보관 장소', esc(place)],
      ['연락처', tel ? `<a href="tel:${esc(tel)}">${esc(tel)}</a>` : '등록된 번호 없음'],
      ['분류', esc(it.category || '—')],
      it.color ? ['색상', esc(it.color)] : null,
    ].filter(Boolean);

    const lastQ = lastResults?.query;
    body.innerHTML = `
      <div class="item-head">
        ${st ? `<span class="badge ${st.tone}">${esc(st.text)}</span>` : ''}
        <h2 class="item-name">${esc(it.name)}</h2>
        <div class="item-place"><span class="badges">${lineBadges(it.lines, true)}</span>${esc(place)} · ${fmtYmd(it.ymd)} 습득</div>
        ${it.movedFrom ? `<div class="item-moved">${icon('i-train')}${esc(it.movedFrom)}역에서 습득한 뒤 유실물센터로 옮겨졌어요</div>` : ''}
      </div>
      ${img ? `<div class="photo"><img src="${esc(detailSrc)}" ${detailSrc !== img ? `data-original="${esc(img)}"` : ''} alt="${esc(it.name)} 사진" decoding="async" fetchpriority="high" referrerpolicy="no-referrer"></div>` : ''}
      ${data.error ? `<div class="item-section"><p class="notice">상세 정보를 불러오지 못해 목록 정보만 보여드려요. (${esc(data.error)})</p></div>` : ''}

      <div class="item-section">
        <dl class="facts">${facts.map(([k, v]) => `<div class="fact"><dt>${k}</dt><dd>${v}</dd></div>`).join('')}</dl>
        ${d.note ? `<p class="note">${esc(d.note)}</p>` : ''}
      </div>

      <div class="item-section">
        <span class="section-label">이렇게 찾으면 돼요</span>
        <ol class="steps">
          <li class="step">
            <span class="step-rail"><span class="step-num">1</span><span class="step-line"></span></span>
            <span class="step-text"><strong>보관 장소에 먼저 전화</strong><span>내 물건이 맞는지 특징(색, 안에 든 카드 등)으로 확인해요.${tel ? ` ${esc(tel)}` : ''}</span></span>
          </li>
          <li class="step">
            <span class="step-rail"><span class="step-num">2</span><span class="step-line"></span></span>
            <span class="step-text"><strong>신분증 챙겨서 방문</strong><span>운영 시간은 방문 전에 전화로 확인하세요. 대리 수령은 위임 서류가 필요할 수 있어요.</span></span>
          </li>
          <li class="step">
            <span class="step-rail"><span class="step-num outline">3</span></span>
            <span class="step-text"><strong>늦어지면 이관될 수 있어요</strong><span>보관 기간이 지나면 경찰서로 넘어가요. 그때는 LOST112에서 새 보관 장소를 확인하세요.</span></span>
          </li>
        </ol>
      </div>

      ${lastQ ? `<div class="item-section">${watchCardHtml(lastQ, describeQuery(lastQ, lastResults), 'item')}</div>` : ''}
      <div class="spacer"></div>

      <div class="bottom-bar">
        ${tel
          ? `<a class="btn-call" href="tel:${esc(tel)}">${icon('i-call')}전화하기</a>`
          : `<a class="btn-call" role="link" aria-disabled="true">${icon('i-call')}번호 없음</a>`}
        <a class="btn-map" href="https://map.kakao.com/link/search/${encodeURIComponent(mapQuery(place))}" target="_blank" rel="noopener noreferrer">${icon('i-pin')}지도에서 보기</a>
      </div>`;

    bindPhotoFallbacks(body);
  }

  // ---------- 시작 ----------
  buildSearchForm();
  window.addEventListener('hashchange', route);
  route();
})();
