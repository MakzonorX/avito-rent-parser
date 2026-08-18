'use strict';

const $ = (id) => document.getElementById(id);
const api = async (url, options = {}) => {
  const res = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  });
  const data = await res.json().catch(() => ({}));
  return { ok: res.ok, data };
};

function toast(message, kind = '') {
  const el = $('toast');
  el.textContent = message;
  el.className = `toast show ${kind}`;
  clearTimeout(el._timer);
  el._timer = setTimeout(() => { el.className = 'toast'; }, 3600);
}

/* ------------------------------------------------ вкладки */
document.querySelectorAll('.tab').forEach((tab) => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.tab').forEach((t) => t.classList.remove('active'));
    document.querySelectorAll('.tab-panel').forEach((p) => p.classList.remove('active'));
    tab.classList.add('active');
    $(`tab-${tab.dataset.tab}`).classList.add('active');
    if (tab.dataset.tab === 'ads') loadAds();
  });
});

/* ------------------------------------------------ формат */
const fmtMoney = (value) =>
  value || value === 0 ? `${Number(value).toLocaleString('ru-RU')} ₽` : '—';

function timeAgo(iso) {
  if (!iso) return '—';
  const seconds = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 60) return 'только что';
  if (seconds < 3600) return `${Math.floor(seconds / 60)} мин. назад`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)} ч. назад`;
  return `${Math.floor(seconds / 86400)} дн. назад`;
}

function inFuture(iso) {
  if (!iso) return '';
  const seconds = Math.floor((new Date(iso).getTime() - Date.now()) / 1000);
  if (seconds <= 0) return 'сейчас';
  if (seconds < 60) return `через ${seconds} сек.`;
  return `через ${Math.floor(seconds / 60)} мин.`;
}

const escapeHtml = (text) =>
  String(text ?? '').replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

/* ------------------------------------------------ статус */
async function refreshStatus() {
  const { ok, data } = await api('/api/status');
  if (!ok) return;

  const { runner, stats, cookies } = data;
  const running = runner.state === 'running';

  const badge = $('status-badge');
  badge.textContent = running ? 'работает' : runner.state === 'error' ? 'ошибка' : 'остановлен';
  badge.className = `badge ${running ? 'badge-running' : runner.state === 'error' ? 'badge-error' : 'badge-stopped'}`;

  $('btn-start').classList.toggle('hidden', running);
  $('btn-stop').classList.toggle('hidden', !running);

  $('st-state').textContent = running ? 'Работает' : runner.state === 'error' ? 'Ошибка' : 'Остановлен';
  $('st-uptime').textContent = runner.started_at ? `запущен ${timeAgo(runner.started_at)}` : '';
  $('st-session').textContent = runner.found_in_session;
  $('st-today').textContent = stats.today;
  $('st-total').textContent = `всего в базе: ${stats.total}`;
  $('st-cycles').textContent = runner.cycles;
  $('st-next').textContent = runner.next_cycle_at
    ? `следующая ${inFuture(runner.next_cycle_at)}`
    : runner.last_cycle_at ? `последняя ${timeAgo(runner.last_cycle_at)}` : '';
  $('st-requests').textContent = `${runner.good_requests} / ${runner.bad_requests}`;
  $('st-requests').nextElementSibling.textContent = runner.used_browser ? 'через браузер' : 'успешные / неудачные';
  $('st-avg').textContent = fmtMoney(stats.avg_price);

  $('alert-box').innerHTML = runner.last_error
    ? `<div class="alert alert-error"><b>Проблема:</b> ${escapeHtml(runner.last_error)}</div>`
    : '';

  let cookieText = cookies.exists
    ? `Сохранено ${cookies.count} cookies${cookies.has_ft ? ' (есть ft ✓)' : ' (нет ключевой ft ✗)'}` +
      (cookies.age_hours !== null ? `, обновлены ${cookies.age_hours} ч. назад` : '')
    : 'Cookies не сохранены';
  if (cookies.running) cookieText = 'Получаю cookies через браузер...';
  if (cookies.error) cookieText += ` · Ошибка: ${cookies.error}`;
  if (cookies.result) cookieText += ` · ${cookies.result}`;
  $('cookies-status').textContent = cookieText;
}

/* ------------------------------------------------ управление */
$('btn-start').onclick = async () => {
  const { data } = await api('/api/start', { method: 'POST' });
  toast(data.message, data.ok ? 'success' : '');
  refreshStatus();
};
$('btn-stop').onclick = async () => {
  const { data } = await api('/api/stop', { method: 'POST' });
  toast(data.message);
  refreshStatus();
};
$('btn-check').onclick = async () => {
  const { data } = await api('/api/check-now', { method: 'POST' });
  toast(data.message, data.ok ? 'success' : '');
  refreshStatus();
};

/* ------------------------------------------------ настройки */
const linesToList = (text) => text.split('\n').map((s) => s.trim()).filter(Boolean);
const listToLines = (list) => (list || []).join('\n');

async function loadSettings() {
  const { ok, data } = await api('/api/settings');
  if (!ok) return;
  const { avito, rent, presets } = data;

  const select = $('preset-select');
  select.innerHTML = '<option value="">— выбрать шаблон —</option>';
  Object.entries(presets).forEach(([name, url]) => {
    const option = document.createElement('option');
    option.value = url;
    option.textContent = name;
    select.appendChild(option);
  });

  $('urls').value = listToLines(avito.urls);
  $('count').value = avito.count;
  $('pause_general').value = avito.pause_general;
  $('max_age_hours').value = Math.round((avito.max_age || 0) / 3600);
  $('min_price').value = avito.min_price;
  $('max_price').value = avito.max_price;
  $('keys_word_white_list').value = listToLines(avito.keys_word_white_list);
  $('keys_word_black_list').value = listToLines(avito.keys_word_black_list);
  $('tg_token').value = avito.tg_token || '';
  $('tg_chat_id').value = listToLines(avito.tg_chat_id);
  $('tg_only_text').checked = !!avito.tg_only_text;
  $('proxy_notifier').value = avito.proxy_notifier || '';
  $('proxy_string').value = avito.proxy_string || '';
  $('proxy_change_url').value = avito.proxy_change_url || '';
  $('timeout').value = avito.timeout;
  $('max_count_of_retry').value = avito.max_count_of_retry;
  $('pause_between_links').value = avito.pause_between_links;
  $('block_threshold').value = avito.block_threshold;
  $('use_own_cookies').checked = !!avito.use_own_cookies;

  $('min_area').value = rent.min_area;
  $('max_area').value = rent.max_area;
  $('min_floor').value = rent.min_floor;
  $('max_floor').value = rent.max_floor;
  $('exclude_first_floor').checked = !!rent.exclude_first_floor;
  $('exclude_last_floor').checked = !!rent.exclude_last_floor;
  $('exclude_daily').checked = !!rent.exclude_daily;
  $('require_photo').checked = !!rent.require_photo;
  $('address_include').value = listToLines(rent.address_include);
  $('address_exclude').value = listToLines(rent.address_exclude);
  $('send_photos').checked = !!rent.send_photos;
  $('first_run_silent').checked = !!rent.first_run_silent;
  $('fetch_mode').value = rent.fetch_mode || 'auto';
  $('browser_headless').checked = rent.browser_headless !== false;
  $('max_photos').value = rent.max_photos;

  const rooms = (rent.rooms || []).map(Number);
  document.querySelectorAll('#rooms-checks input').forEach((box) => {
    box.checked = rooms.includes(Number(box.value));
  });
}

$('preset-select').onchange = (event) => {
  if (!event.target.value) return;
  const current = linesToList($('urls').value);
  if (!current.includes(event.target.value)) current.push(event.target.value);
  $('urls').value = current.join('\n');
  event.target.value = '';
};

$('btn-save').onclick = async () => {
  const rooms = [...document.querySelectorAll('#rooms-checks input:checked')].map((b) => Number(b.value));

  const payload = {
    avito: {
      urls: linesToList($('urls').value),
      count: $('count').value,
      pause_general: $('pause_general').value,
      max_age: Number($('max_age_hours').value || 0) * 3600,
      min_price: $('min_price').value,
      max_price: $('max_price').value || 99999999,
      keys_word_white_list: linesToList($('keys_word_white_list').value),
      keys_word_black_list: linesToList($('keys_word_black_list').value),
      tg_token: $('tg_token').value.trim(),
      tg_chat_id: linesToList($('tg_chat_id').value),
      tg_only_text: $('tg_only_text').checked,
      proxy_notifier: $('proxy_notifier').value.trim(),
      proxy_string: $('proxy_string').value.trim(),
      proxy_change_url: $('proxy_change_url').value.trim(),
      timeout: $('timeout').value,
      max_count_of_retry: $('max_count_of_retry').value,
      pause_between_links: $('pause_between_links').value,
      block_threshold: $('block_threshold').value,
      use_own_cookies: $('use_own_cookies').checked,
    },
    rent: {
      rooms,
      min_area: $('min_area').value,
      max_area: $('max_area').value,
      min_floor: $('min_floor').value,
      max_floor: $('max_floor').value,
      exclude_first_floor: $('exclude_first_floor').checked,
      exclude_last_floor: $('exclude_last_floor').checked,
      exclude_daily: $('exclude_daily').checked,
      require_photo: $('require_photo').checked,
      address_include: linesToList($('address_include').value),
      address_exclude: linesToList($('address_exclude').value),
      send_photos: $('send_photos').checked,
      first_run_silent: $('first_run_silent').checked,
      fetch_mode: $('fetch_mode').value,
      browser_headless: $('browser_headless').checked,
      max_photos: $('max_photos').value,
    },
  };

  const { ok, data } = await api('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
  toast(data.message || (ok ? 'Сохранено' : 'Ошибка'), ok ? 'success' : 'error');
  if (ok) $('save-hint').textContent = 'изменения применятся со следующей проверки';
};

$('btn-tg-test').onclick = async () => {
  toast('Отправляю...');
  const { ok, data } = await api('/api/telegram/test', { method: 'POST' });
  toast(data.message, ok ? 'success' : 'error');
};

/* ------------------------------------------------ cookies */
$('btn-cookies-refresh').onclick = async () => {
  const { data } = await api('/api/cookies/refresh', { method: 'POST' });
  toast(data.message);
};

$('btn-cookies-import').onclick = async () => {
  const raw = $('cookies-raw').value.trim();
  if (!raw) return toast('Вставь строку cookies', 'error');
  const { ok, data } = await api('/api/cookies/import', {
    method: 'POST',
    body: JSON.stringify({ cookies: raw }),
  });
  toast(data.message, ok ? 'success' : 'error');
  if (ok) { $('cookies-raw').value = ''; refreshStatus(); }
};

/* ------------------------------------------------ объявления */
function adCard(ad) {
  const params = [
    ad.rooms === 0 ? 'Студия' : ad.rooms === -1 ? 'Комната' : ad.rooms ? `${ad.rooms}-комн.` : null,
    ad.area ? `${ad.area} м²` : null,
    ad.floor && ad.total_floors ? `${ad.floor}/${ad.total_floors} эт.` : null,
  ].filter(Boolean).join(' · ');

  const photo = (ad.images && ad.images[0])
    ? `<img class="ad-photo" src="${escapeHtml(ad.images[0])}" loading="lazy" alt="">`
    : '<div class="ad-photo-empty">🏠</div>';

  return `
    <div class="ad">
      ${photo}
      <div class="ad-body">
        <div class="ad-price">${fmtMoney(ad.price)}<span class="hint"> /мес</span></div>
        <div class="ad-title">${escapeHtml(params || ad.title)}</div>
        <div class="ad-address">📍 ${escapeHtml(ad.address || '—')}</div>
        <div class="ad-meta">
          <span>найдено ${timeAgo(ad.found_at)}</span>
          <span>${ad.images ? ad.images.length : 0} фото</span>
        </div>
      </div>
      <a class="ad-link" href="${escapeHtml(ad.url)}" target="_blank" rel="noopener">Открыть на Avito →</a>
    </div>`;
}

async function loadAds() {
  const search = $('ads-search').value.trim();
  const { ok, data } = await api(`/api/ads?limit=60&search=${encodeURIComponent(search)}`);
  if (!ok) return;
  $('ads-list').innerHTML = data.items.length
    ? data.items.map(adCard).join('')
    : '<div class="empty">Пока ничего не найдено</div>';
}

let searchTimer;
$('ads-search').oninput = () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(loadAds, 300);
};

$('btn-ads-clear').onclick = async () => {
  if (!confirm('Удалить историю найденных объявлений?')) return;
  const { data } = await api('/api/ads/clear', { method: 'POST' });
  toast(data.message, 'success');
  loadAds();
};

async function loadRecent() {
  const { ok, data } = await api('/api/ads?limit=6');
  if (!ok) return;
  $('recent-ads').innerHTML = data.items.length
    ? data.items.map(adCard).join('')
    : '<div class="empty">Пока ничего не найдено. Запусти парсер — новые объявления появятся здесь.</div>';
}

/* ------------------------------------------------ логи */
let lastLogSeq = 0;

async function pollLogs() {
  const { ok, data } = await api(`/api/logs?after=${lastLogSeq}`);
  if (!ok || !data.items.length) return;
  lastLogSeq = data.last_seq;

  const box = $('logs-box');
  const html = data.items.map((record) =>
    `<span class="log-time">${record.time}</span> <span class="log-${record.level}">${escapeHtml(record.message)}</span>`
  ).join('\n');
  box.insertAdjacentHTML('beforeend', (box.innerHTML ? '\n' : '') + html);

  while (box.childNodes.length > 1200) box.removeChild(box.firstChild);
  if ($('logs-autoscroll').checked) box.scrollTop = box.scrollHeight;
}

$('btn-logs-clear').onclick = async () => {
  await api('/api/logs/clear', { method: 'POST' });
  $('logs-box').innerHTML = '';
  lastLogSeq = 0;
};

/* ------------------------------------------------ старт */
loadSettings();
refreshStatus();
loadRecent();
pollLogs();

setInterval(refreshStatus, 3000);
setInterval(pollLogs, 2000);
setInterval(loadRecent, 15000);

/* ================================================ мастер настройки */
const wizard = {
  el: $('wizard'),
  step: 1,
  total: 5,

  open(step = 1) {
    this.el.classList.remove('hidden');
    this.goto(step);
  },

  close() {
    this.el.classList.add('hidden');
  },

  goto(step) {
    this.step = Math.min(Math.max(step, 1), this.total);
    document.querySelectorAll('.wizard-step').forEach((el) => {
      el.classList.toggle('active', Number(el.dataset.step) === this.step);
    });
    document.querySelectorAll('.wizard-dot').forEach((dot) => {
      const n = Number(dot.dataset.step);
      dot.classList.toggle('current', n === this.step);
      dot.classList.toggle('done', n < this.step);
    });
    if (this.step === 5) this.renderSummary();
    this.el.scrollTop = 0;
  },

  next() { this.goto(this.step + 1); },
  back() { this.goto(this.step - 1); },

  result(id, kind, title, hint = '') {
    const el = $(id);
    el.className = `wizard-result filled ${kind}`;
    el.innerHTML = `<b>${title}</b>${hint ? `<span class="hint">${hint}</span>` : ''}`;
  },

  async prefill() {
    const { ok, data } = await api('/api/settings');
    if (!ok) return;
    const { avito, rent, presets } = data;

    $('wz_tg_token').value = avito.tg_token || '';
    $('wz_tg_chat_id').value = listToLines(avito.tg_chat_id);
    $('wz_proxy_string').value = avito.proxy_string || '';
    $('wz_url').value = listToLines(avito.urls);
    $('wz_min_price').value = avito.min_price;
    $('wz_max_price').value = avito.max_price;
    $('wz_pause').value = avito.pause_general;
    $('wz_max_age').value = Math.round((avito.max_age || 0) / 3600);

    const select = $('wz_preset');
    select.innerHTML = '';
    Object.entries(presets).forEach(([name, url]) => {
      const option = document.createElement('option');
      option.value = url;
      option.textContent = name;
      select.appendChild(option);
    });
    const own = document.createElement('option');
    own.value = '';
    own.textContent = 'Своя ссылка (вставить ниже)';
    select.appendChild(own);

    const rooms = (rent.rooms || []).map(Number);
    document.querySelectorAll('#wz-rooms input').forEach((box) => {
      box.checked = rooms.includes(Number(box.value));
    });
  },

  collectSearch() {
    return {
      avito: {
        urls: linesToList($('wz_url').value),
        min_price: $('wz_min_price').value || 0,
        max_price: $('wz_max_price').value || 99999999,
        pause_general: $('wz_pause').value || 300,
        max_age: Number($('wz_max_age').value || 0) * 3600,
      },
      rent: {
        rooms: [...document.querySelectorAll('#wz-rooms input:checked')].map((b) => Number(b.value)),
      },
    };
  },

  renderSummary() {
    const search = this.collectSearch();
    const token = $('wz_tg_token').value.trim();
    const chats = linesToList($('wz_tg_chat_id').value);
    const roomsLabel = search.rent.rooms.length
      ? search.rent.rooms.map((r) => (r === 0 ? 'студия' : `${r}-комн.`)).join(', ')
      : 'любая';
    const proxy = $('wz_proxy_string').value.trim();

    const rows = [
      ['Telegram', token && chats.length ? `настроен, получателей: ${chats.length}` : 'не настроен — уведомлений не будет'],
      ['Прокси', proxy || 'без прокси'],
      ['Цена', `${Number(search.avito.min_price).toLocaleString('ru-RU')} — ${Number(search.avito.max_price).toLocaleString('ru-RU')} ₽/мес`],
      ['Комнатность', roomsLabel],
      ['Проверка', `раз в ${search.avito.pause_general} сек.`],
      ['Свежесть', `не старше ${Math.round(search.avito.max_age / 3600)} ч.`],
    ];
    $('wz-summary').innerHTML = rows
      .map(([key, value]) => `<div class="wizard-summary-row"><span>${key}</span><span>${escapeHtml(value)}</span></div>`)
      .join('');
  },
};

/* --- навигация --- */
document.querySelectorAll('[data-wizard]').forEach((btn) => {
  btn.addEventListener('click', async () => {
    const action = btn.dataset.wizard;
    if (action === 'next') {
      if (wizard.step === 4) {
        const payload = wizard.collectSearch();
        const { ok } = await api('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
        if (!ok) return toast('Не удалось сохранить настройки', 'error');
      }
      wizard.next();
    } else if (action === 'back') {
      wizard.back();
    } else if (action === 'skip-step') {
      wizard.next();
    } else if (action === 'skip') {
      await api('/api/onboarding/complete', { method: 'POST' });
      wizard.close();
      toast('Мастер можно открыть кнопкой 🧭 в шапке');
    }
  });
});

$('btn-wizard').onclick = async () => {
  await wizard.prefill();
  wizard.open(1);
};

/* --- шаг Telegram --- */
$('wz-tg-check').onclick = async () => {
  const token = $('wz_tg_token').value.trim();
  const chats = linesToList($('wz_tg_chat_id').value);
  if (!token || !chats.length) {
    return wizard.result('wz-tg-result', 'fail', 'Заполни оба поля',
      'Нужен и токен бота, и chat_id получателя.');
  }

  wizard.result('wz-tg-result', '', 'Отправляю тестовое сообщение...');
  await api('/api/settings', {
    method: 'POST',
    body: JSON.stringify({ avito: { tg_token: token, tg_chat_id: chats } }),
  });

  const { ok, data } = await api('/api/telegram/test', { method: 'POST' });
  if (ok) {
    wizard.result('wz-tg-result', 'ok', '✅ Сообщение отправлено',
      'Проверь чат — там должно быть подтверждение от бота.');
    setTimeout(() => wizard.next(), 1400);
  } else {
    wizard.result('wz-tg-result', 'fail', '❌ Не получилось',
      `${escapeHtml(data.message || '')}<br>Частая причина: боту ещё не написали первым, или chat_id указан неверно.`);
  }
};

/* --- шаг доступа к Avito --- */
$('wz-avito-check').onclick = async () => {
  const proxy = $('wz_proxy_string').value.trim();
  wizard.result('wz-avito-result', '', 'Проверяю доступ, это займёт несколько секунд...');

  const { data } = await api('/api/check-avito', {
    method: 'POST',
    body: JSON.stringify({ proxy_string: proxy }),
  });

  const where = [data.ip, data.location, data.org].filter(Boolean).join(' · ');
  if (data.ok) {
    wizard.result('wz-avito-result', 'ok', '✅ Avito отвечает, доступ есть', escapeHtml(where));
    await api('/api/settings', { method: 'POST', body: JSON.stringify({ avito: { proxy_string: proxy } }) });
    setTimeout(() => wizard.next(), 1400);
  } else {
    wizard.result('wz-avito-result', 'fail', `❌ ${escapeHtml(data.message)}`,
      `${escapeHtml(where)}<br><br>${escapeHtml(data.hint)}`);
    $('wz-proxy-field').classList.remove('hidden');
  }
};

/* --- финиш --- */
$('wz-finish').onclick = async () => {
  const payload = wizard.collectSearch();
  payload.avito.tg_token = $('wz_tg_token').value.trim();
  payload.avito.tg_chat_id = linesToList($('wz_tg_chat_id').value);
  payload.avito.proxy_string = $('wz_proxy_string').value.trim();

  const saved = await api('/api/settings', { method: 'POST', body: JSON.stringify(payload) });
  if (!saved.ok) return toast('Не удалось сохранить настройки', 'error');

  await api('/api/onboarding/complete', { method: 'POST' });
  const { data } = await api('/api/start', { method: 'POST' });

  wizard.close();
  loadSettings();
  refreshStatus();
  toast(data.ok ? 'Парсер запущен — следи за дашбордом' : data.message, 'success');
};

/* --- показать мастер при первом входе --- */
(async () => {
  const { ok, data } = await api('/api/settings');
  if (ok && data.app && !data.app.onboarding_done) {
    await wizard.prefill();
    wizard.open(1);
  }
})();
