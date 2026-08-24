'use strict';

const state = {
  targets: [],
  results: {},      // key -> スキャン結果
  checked: {},      // key -> bool（実際の表示。起動中アプリで強制OFFになりうる）
  preferred: {},     // key -> bool（ユーザーの希望。永続化対象。起動中による強制OFFでは変えない）
  open: {},         // key -> 詳細を開いているか
  scanning: false,
  cleaning: false,
  disk: { free: 0, total: 0, drive: 'C:' },
  freedTotal: 0,
  lockedTotal: 0,
  theme: 'dark',
};

const $ = (id) => document.getElementById(id);

// --- 表示ユーティリティ ---------------------------------------------------

function fmtSize(bytes) {
  if (!bytes) return '0 B';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let v = bytes, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  const digits = v >= 100 || i === 0 ? 0 : v >= 10 ? 1 : 2;
  return v.toFixed(digits) + ' ' + units[i];
}

function fmtDate(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

function shortenPath(path, max = 78) {
  if (path.length <= max) return path;
  return path.slice(0, 18) + ' … ' + path.slice(-(max - 21));
}

function appName(exe) {
  const map = {
    'chrome.exe': 'Chrome',
    'discord.exe': 'Discord',
    'discordptb.exe': 'Discord PTB',
    'discordcanary.exe': 'Discord Canary',
    'steam.exe': 'Steam',
  };
  return map[exe.toLowerCase()] || exe.replace(/\.exe$/i, '');
}

function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

// --- 描画 ------------------------------------------------------------------

function renderList() {
  const list = $('list');
  const groups = [
    { risk: 'safe', title: '安全に消せるもの' },
    { risk: 'caution', title: '中身を確認してから消すもの' },
  ];
  let html = '';
  for (const g of groups) {
    const targets = state.targets.filter((t) => t.risk === g.risk);
    if (!targets.length) continue;
    html += `<div class="group-label">${g.title}</div>`;
    for (const t of targets) html += rowHtml(t);
  }
  list.innerHTML = html;
  bindRows();
  updateTotals();
}

function rowHtml(t) {
  const res = state.results[t.key];
  const open = state.open[t.key];
  const checked = state.checked[t.key];

  let sizeCls = 'size pending', sizeText = '…';
  if (res) {
    sizeText = fmtSize(res.size);
    sizeCls = res.size ? 'size' : 'size zero';
  }

  const badges = [];
  if (t.risk === 'caution') badges.push('<span class="badge caution">要確認</span>');
  if (t.needsAdmin) badges.push('<span class="badge admin">管理者</span>');
  if (t.toTrash) badges.push('<span class="badge trash">ごみ箱送り</span>');
  if (t.conflicts && t.conflicts.length) {
    badges.push(`<span class="badge running">${esc(appName(t.conflicts[0]))} 起動中</span>`);
  }

  return `
    <div class="row ${open ? 'open' : ''}" data-key="${t.key}">
      <div class="row-main">
        <input type="checkbox" data-check="${t.key}" ${checked ? 'checked' : ''}>
        <span class="label">${esc(t.label)}</span>
        ${badges.join('')}
        <div class="spacer" style="flex:1"></div>
        <span class="${sizeCls}">${sizeText}</span>
        <span class="caret">${open ? '▾' : '▸'}</span>
      </div>
      <div class="detail">${detailHtml(t, res)}</div>
    </div>`;
}

function detailHtml(t, res) {
  let html = `<div class="note">${esc(t.detail)}</div>`;

  if (t.conflicts && t.conflicts.length) {
    const names = t.conflicts.map(appName).join('・');
    html += `<div class="note warn">${esc(names)} が起動中です。起動したまま消すとほとんどのファイルはロックされていて消せず、`
      + `動作が不安定になることがあります。${esc(names)} を終了してから再スキャンしてください。</div>`;
  }
  if (t.checksLocks) {
    html += '<div class="note">削除の直前にもう一度ロックを調べ、実行中のアプリが使っているフォルダは丸ごと見送ります（中途半端に消してアプリを壊さないため）。</div>';
  }

  if (!res) return html + '<div class="note">スキャン中…</div>';

  const notes = [];
  if (res.skippedRecent) {
    notes.push(`使用中の可能性があるため ${res.skippedRecent} 件（${fmtSize(res.skippedRecentSize)}）を対象外にしました`);
  }
  if (res.denied) {
    notes.push(`${res.denied} 件はアクセスできませんでした${t.needsAdmin ? '（管理者権限で開き直すと消せる場合があります）' : '（使用中のファイル）'}`);
  }
  if (res.error) notes.push(`エラー: ${res.error}`);
  if (notes.length) html += `<div class="note warn">${notes.map(esc).join('<br>')}</div>`;

  if (t.key === 'recycle_bin') {
    html += `<div class="paths"><div class="path-line"><span class="p">全ドライブのごみ箱：${res.files} 項目</span><span class="s">${fmtSize(res.size)}</span></div></div>`;
    return html;
  }

  if (!res.items.length) {
    html += `<div class="paths"><div class="more">消せるものはありません。</div>`;
    html += t.roots.map((r) => `<div class="path-line"><span class="p" data-open="${esc(r)}">${esc(shortenPath(r))}</span></div>`).join('');
    return html + '</div>';
  }

  html += '<div class="paths">';
  for (const item of res.items) {
    html += `<div class="path-line">
      <span class="p" data-open="${esc(item.path)}" title="${esc(item.path)}">${esc(shortenPath(item.path))}</span>
      <span class="d">${fmtDate(item.mtime)}</span>
      <span class="s">${fmtSize(item.size)}</span>
    </div>`;
  }
  if (res.itemsTotal > res.items.length) {
    html += `<div class="more">ほか ${res.itemsTotal - res.items.length} 件</div>`;
  }
  html += '</div>';
  return html;
}

function updateRow(key) {
  const row = document.querySelector(`.row[data-key="${key}"]`);
  const t = state.targets.find((x) => x.key === key);
  if (!row || !t) return;
  const res = state.results[key];
  const sizeEl = row.querySelector('.size');
  if (res) {
    sizeEl.textContent = fmtSize(res.size);
    sizeEl.className = res.size ? 'size' : 'size zero';
  }
  row.querySelector('.detail').innerHTML = detailHtml(t, res);
  bindPaths(row);
}

function updateTotals() {
  let total = 0, count = 0;
  for (const t of state.targets) {
    if (!state.checked[t.key]) continue;
    const res = state.results[t.key];
    if (res && res.size) { total += res.size; count++; }
  }
  $('totalSize').textContent = total ? fmtSize(total) : '0 B';
  $('btnClean').disabled = state.cleaning || state.scanning || total === 0;
  return { total, count };
}

function updateDisk(gainBytes) {
  const d = state.disk;
  if (!d.total) return;
  const usedPct = ((d.total - d.free) / d.total) * 100;
  const gainPct = gainBytes ? Math.min((gainBytes / d.total) * 100, usedPct) : 0;
  $('diskUsed').style.width = (usedPct - gainPct) + '%';
  $('diskGain').style.width = gainPct + '%';
  $('diskText').textContent =
    `${d.drive} ドライブ 空き ${fmtSize(d.free)} / ${fmtSize(d.total)}`;
}

function setStatus(text, cls) {
  const el = $('status');
  el.textContent = text;
  el.className = 'status' + (cls ? ' ' + cls : '');
}

function saveSelection() {
  window.pywebview.api.save_selection(state.preferred);
}

// --- テーマ ------------------------------------------------------------

function applyTheme(theme) {
  state.theme = theme;
  document.documentElement.setAttribute('data-theme', theme);
  $('btnTheme').textContent = theme === 'light' ? 'ダークモードにする' : 'ライトモードにする';
}

function toggleTheme() {
  const next = state.theme === 'light' ? 'dark' : 'light';
  applyTheme(next);
  window.pywebview.api.save_theme(next);
}

// --- イベント --------------------------------------------------------------

function bindRows() {
  document.querySelectorAll('.row').forEach((row) => {
    const key = row.dataset.key;
    row.querySelector('.row-main').addEventListener('click', (e) => {
      if (e.target.matches('input[type=checkbox]')) return;
      state.open[key] = !state.open[key];
      row.classList.toggle('open', state.open[key]);
      row.querySelector('.caret').textContent = state.open[key] ? '▾' : '▸';
    });
    row.querySelector('input[type=checkbox]').addEventListener('change', (e) => {
      state.checked[key] = e.target.checked;
      state.preferred[key] = e.target.checked;
      updateTotals();
      saveSelection();
    });
    bindPaths(row);
  });
}

function bindPaths(row) {
  row.querySelectorAll('[data-open]').forEach((el) => {
    el.addEventListener('click', (e) => {
      e.stopPropagation();
      window.pywebview.api.open_path(el.dataset.open);
    });
  });
}

function startScan() {
  if (state.scanning || state.cleaning) return;
  state.scanning = true;
  state.results = {};
  state.freedTotal = 0;
  $('btnRescan').disabled = true;
  $('diskGain').style.width = '0%';
  setStatus('スキャン中…');
  renderList();
  window.pywebview.api.start_scan();
}

function askConfirm() {
  const rows = state.targets
    .filter((t) => state.checked[t.key] && state.results[t.key] && state.results[t.key].size)
    .map((t) => ({ t, res: state.results[t.key] }));
  if (!rows.length) return;

  $('confirmList').innerHTML = rows
    .map(({ t, res }) => `<li>${esc(t.label)} <span class="s">${fmtSize(res.size)}</span></li>`)
    .join('');

  const notes = [];
  const trash = rows.filter(({ t }) => t.toTrash);
  const hard = rows.filter(({ t }) => !t.toTrash);
  if (trash.length) notes.push('「ごみ箱送り」の項目はごみ箱から復元できます。');
  if (hard.length) {
    notes.push(trash.length
      ? 'それ以外は完全に削除され、元に戻せません。'
      : '削除すると元に戻せません。');
  }
  if (rows.some(({ t }) => t.key === 'steam_temp')) notes.push('Steam のダウンロード中のゲームがある場合は中断してから実行してください。');
  const running = [...new Set(rows.flatMap(({ t }) => (t.conflicts || []).map(appName)))];
  if (running.length) {
    notes.push(`${running.join('・')} が起動中です。終了してから実行することを勧めます。`);
  }
  $('confirmNote').innerHTML = notes.map(esc).join('<br>');

  $('confirm').classList.add('show');
}

function runClean() {
  $('confirm').classList.remove('show');
  const keys = state.targets
    .filter((t) => state.checked[t.key] && state.results[t.key] && state.results[t.key].size)
    .map((t) => t.key);
  if (!keys.length) return;
  state.cleaning = true;
  state.freedTotal = 0;
  state.lockedTotal = 0;
  $('btnClean').disabled = true;
  $('btnRescan').disabled = true;
  setStatus('削除中…');
  window.pywebview.api.clean(keys);
}

// --- Python からのイベント -------------------------------------------------

window.onPyEvent = function (event, data) {
  if (event === 'scanned') {
    state.results[data.key] = data;
    updateRow(data.key);
    updateTotals();
  } else if (event === 'scanDone') {
    state.scanning = false;
    state.disk.free = data.disk.free;
    state.disk.total = data.disk.total;
    // 再スキャン中にアプリが起動／終了していることがあるので取り直す
    if (data.conflicts) {
      for (const t of state.targets) {
        const next = data.conflicts[t.key] || [];
        if (next.length && !(t.conflicts || []).length) state.checked[t.key] = false;
        t.conflicts = next;
      }
      renderList();  // バッジを描き直す（行の開閉とチェック状態は state から復元される）
    }
    updateDisk(0);
    $('btnRescan').disabled = false;
    $('scanTime').textContent = `スキャン ${data.elapsed} 秒`;
    const { total } = updateTotals();
    setStatus(total ? `${fmtSize(total)} 削除できます` : '消せるものはありません', total ? '' : 'done');
  } else if (event === 'cleanStart') {
    setStatus(`削除中: ${data.label}`);
  } else if (event === 'cleanProgress') {
    setStatus(`削除中: ${data.done} / ${data.total}`);
  } else if (event === 'cleaned') {
    state.freedTotal += data.freed;
    if (data.locked) state.lockedTotal += data.locked;
    state.results[data.key] = { key: data.key, size: 0, files: 0, items: [], itemsTotal: 0, denied: data.skipped, skippedRecent: 0, skippedRecentSize: 0, error: null };
    state.checked[data.key] = false;
    state.preferred[data.key] = false;
    updateRow(data.key);
    const box = document.querySelector(`.row[data-key="${data.key}"] input[type=checkbox]`);
    if (box) box.checked = false;
    updateTotals();
  } else if (event === 'cleanDone') {
    state.cleaning = false;
    state.disk.free = data.disk.free;
    state.disk.total = data.disk.total;
    updateDisk(state.freedTotal);
    $('btnRescan').disabled = false;
    updateTotals();
    saveSelection();
    const extra = state.lockedTotal
      ? `（使用中の ${state.lockedTotal} 件は見送りました）` : '';
    setStatus(`${fmtSize(state.freedTotal)} を解放しました${extra}`, 'done');
  }
};

// --- 起動 ------------------------------------------------------------------

window.addEventListener('pywebviewready', async () => {
  const info = await window.pywebview.api.bootstrap();
  state.targets = info.targets;
  state.disk = { ...info.disk };
  applyTheme(info.theme === 'light' ? 'light' : 'dark');
  $('appVersion').textContent = info.version ? `v${info.version}` : '';
  // bootstrap は通常起動時に1度だけ呼ばれるが、念のため古いキーを残さない。
  state.checked = {};
  state.preferred = {};
  // 前回保存した選択（希望）があればそれを使い、無ければ既定値。
  // 対象アプリが起動中のカテゴリは、希望に関わらず表示上はオフにしておく。
  const saved = info.savedChecked || {};
  for (const t of state.targets) {
    state.preferred[t.key] = Object.prototype.hasOwnProperty.call(saved, t.key)
      ? !!saved[t.key]
      : t.defaultOn;
    state.checked[t.key] = state.preferred[t.key] && !(t.conflicts && t.conflicts.length);
  }
  if (info.isAdmin) {
    $('adminTag').style.display = '';
  } else {
    $('btnAdmin').style.display = '';
  }
  updateDisk(0);
  state.scanning = true;
  renderList();
  setStatus('スキャン中…');
  $('btnRescan').disabled = true;
});

$('btnTheme').addEventListener('click', toggleTheme);
$('btnSelectAvailable').addEventListener('click', () => {
  // 起動中でないものをすべて選択する（希望していたかどうかに関わらずON）。
  for (const t of state.targets) {
    if (t.conflicts && t.conflicts.length) continue;
    state.checked[t.key] = true;
    state.preferred[t.key] = true;
  }
  renderList();
  saveSelection();
});
$('btnRescan').addEventListener('click', startScan);
$('btnClean').addEventListener('click', askConfirm);
$('btnCancel').addEventListener('click', () => $('confirm').classList.remove('show'));
$('btnConfirm').addEventListener('click', runClean);
$('btnAdmin').addEventListener('click', () => window.pywebview.api.request_admin());
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') $('confirm').classList.remove('show');
  if (e.key === 'F5') { e.preventDefault(); startScan(); }
});
