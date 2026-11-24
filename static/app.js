const titles = {
  products: ['Товары', 'Рекомендации по ценам и статус их применения'],
  rules: ['Правила', 'Настройки по категориям и режимы по товарам'],
  integrations: ['Интеграции', 'Источник учётных данных и имитатор Wildberries'],
  report: ['Отчёт', 'Наличие, расчётная маржа и история цен'],
};
const kinds = {up:'Повышение', down:'Снижение', same:'Без изменений', blocked:'Ограничение'};
const statuses = {pending:'Ожидает применения', applied:'Применена', rejected:'Отклонена WB',
  dismissed:'Отклонена менеджером', superseded:'Заменена вручную', manual:'Установлена вручную'};
const fields = {up:'Повышение, %', down:'Снижение, %', margin:'Минимальная маржа, %',
  commission:'Комиссия WB, %', logistics:'Логистика, ₽/шт.', deviation:'Отклонение от ручной цены, %',
  interval:'Интервал, ч', stale:'Порог запаса, дней', idle:'Порог без продаж, дней'};
const $ = selector => document.querySelector(selector);
const escape = value => String(value ?? '').replace(/[&<>"']/g, ch =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'})[ch]);
const fmt = new Intl.NumberFormat('ru-RU', {maximumFractionDigits: 2});
const num = value => value == null ? '—' : fmt.format(value);
const money = value => value == null ? '—' : num(value) + ' ₽';
const date = value => value ? value.split('-').reverse().join('.') : '—';
const timestamp = value => value ? new Date(value).toLocaleString('ru-RU', {timeZone:'Europe/Moscow'}) : '—';
const badge = (kind, label) => `<span class="badge ${kind}">${label}</span>`;
let state, tab = 'products', category, period = 0, busy = false;
const filter = {search:'', category:'', kind:''};
const options = (values, selected) => values.map(([value, label]) =>
  `<option value="${escape(value)}" ${value === selected ? 'selected' : ''}>${escape(label)}</option>`).join('');
const categoryOptions = () => Object.keys(state.rules).map(value => [value, value]);

function hasProposal(p) {
  return !p.needs_sync && ['up', 'down'].includes(p.rec.kind);
}

function row(p) {
  const r = p.rec;
  const proposed = hasProposal(p);
  const allowed = proposed && !p.pending && !p.dismissed;
  const status = p.latest;
  const sku = escape(p.sku);
  return `<tr>
    <th scope="row"><strong>${escape(p.name)}</strong><small>${sku} · ${escape(p.category)}</small></th>
    <td class="price-cell"><div class="price-line"><strong>${money(p.price)}</strong>
      <button class="edit-price" type="button" data-edit-price="${sku}" aria-label="Изменить цену WB · ${sku}" title="Изменить цену вручную"><span aria-hidden="true">✎</span></button>
      </div><small>Опорная ${money(p.anchor)}</small></td>
    <td class="proposal-cell">${proposed ? `<div class="price-line"><strong>${money(r.proposed)}</strong>${badge(r.kind,`${r.step > 0 ? '+' : ''}${num(r.step)}%`)}</div>`
      : p.needs_sync ? '<span class="muted">После синхронизации</span>' : '<span class="muted">—</span>'}
      ${allowed ? `<div class="proposal-actions"><button data-action="approve" data-sku="${sku}" data-id="${r.id}"
        aria-label="Принять рекомендацию · ${sku}">Принять</button><button data-action="reject" data-sku="${sku}"
        data-id="${r.id}" aria-label="Отклонить рекомендацию · ${sku}">Отклонить</button></div>` : ''}</td>
    <td class="stock-cell">${num(p.stock)} шт.<small>${r.cover == null ? 'Нет темпа продаж' : num(r.cover) + ' дн. запаса'}</small></td>
    <td class="supply-cell">${r.supply ? `${date(r.supply.date)}<small>${num(r.supply.quantity)} шт.</small>` : '<span class="muted">Неизвестна</span>'}</td>
    <td class="reason-cell">${p.needs_sync ? 'Цена изменена вручную' : escape(r.reason)}
      <details><summary>Расчёт</summary><small>Продажи ${num(r.sold)} шт. за ${num(r.available)} дн. наличия<br>
       Темп ${num(r.rate)} шт./день<br>Себестоимость ${money(p.cost)}<br>Учётная цена ${money(p.accounting)}<br>
       Минимальная цена ${money(r.minimum)}</small></details></td>
    <td>${status ? badge(status.status, statuses[status.status]) : '<span class="muted">—</span>'}
      ${status?.message ? `<small>${escape(status.message)}</small>` : ''}</td></tr>`;
}

function refreshRows() {
  const search = filter.search.trim().toLocaleLowerCase('ru');
  const rows = state.products.filter(p => (!filter.category || p.category === filter.category)
    && (!filter.kind || p.rec.kind === filter.kind)
    && `${p.sku} ${p.name}`.toLocaleLowerCase('ru').includes(search));
  $('#rows').innerHTML = rows.map(row).join('') || '<tr><td colspan="7">Товары по этим фильтрам не найдены</td></tr>';
}

function products() {
  const p = state.products;
  const outstanding = p.filter(x => hasProposal(x) && !x.pending && !x.dismissed).length;
  const pending = p.filter(x => x.pending).length;
  return `<div class="panel catalog"><div class="toolbar">
    <input type="search" id="search" aria-label="Поиск по названию или артикулу" placeholder="Название или артикул" value="${escape(filter.search)}">
    <select id="filter-category" aria-label="Категория">${options([['','Все категории'],...categoryOptions()],filter.category)}</select>
    <select id="filter-kind" aria-label="Тип рекомендации">${options([['','Все рекомендации'],...Object.entries(kinds)],filter.kind)}</select>
    <span class="catalog-counts">Товаров <strong>${p.length}</strong> · Рекомендаций <strong>${outstanding}</strong> · В очереди <strong>${pending}</strong></span>
    </div><div class="table-scroll"><table aria-label="Товары и цены WB"><thead><tr>
    <th scope="col">Товар</th><th scope="col">Цена WB</th><th scope="col">Рекомендация</th>
    <th scope="col">Остаток</th><th scope="col">Поставка</th><th scope="col">Причина</th><th scope="col">Статус WB</th></tr></thead>
    <tbody id="rows"></tbody></table></div></div>`;
}

function rules() {
  category ??= Object.keys(state.rules)[0];
  const groups = [
    ['Изменение цены', ['up', 'down', 'deviation', 'interval']],
    ['Маржа и расходы', ['margin', 'commission', 'logistics']],
    ['Залежалость', ['stale', 'idle']],
  ];
  return `<div class="panel settings-panel"><h2>Настройки категории</h2><form data-action="rules">
    <label>Категория<select id="rule-category" name="category">${options(categoryOptions(),category)}</select></label>
    <label>Режим применения цен<select name="mode">${options(
      [['manual','Только рекомендации'],['auto','Автоматически']], state.category_modes[category] || 'manual')}</select></label>
    <p class="muted">Режим действует для всех товаров категории. Автоматически отправляются новые рекомендации.</p>
    ${groups.map(([title, names]) => `<fieldset><legend>${title}</legend>
      ${names.map(name => `<label>${fields[name]}<input name="${name}" type="number" step="any"
        value="${escape(state.rules[category]?.[name])}"></label>`).join('')}</fieldset>`).join('')}
    <button class="primary">Сохранить и пересчитать</button></form></div>`;
}

function syncStatus() {
  return `<div>Следующий запуск · ${state.schedule.enabled ? timestamp(state.next_sync) : 'Выключен'}</div>
    <div>Последняя попытка по расписанию · ${timestamp(state.schedule.attempted)}</div>
    <div>Последняя успешная синхронизация · ${timestamp(state.synced)}</div>
    ${state.error ? `<div role="alert">${escape(state.error)}</div>` : ''}`;
}

function integrations() {
  return `<div class="panel"><h2>Подключение к 1С и WB</h2><form data-action="settings">
    <div class="fields">
      <label>Адрес публикации 1С<input name="url" type="url" value="${escape(state.url)}" placeholder="https://demo.example.ru/trade"></label>
      <label>Путь HTTP-сервиса<input name="service" value="${escape(state.service)}" placeholder="hs/repricer/snapshot"></label>
      <label>Логин 1С<input name="login" autocomplete="username" value="${escape(state.login)}"></label>
      <label>Пароль 1С<input name="password" type="password" autocomplete="new-password" placeholder="${state.has_password ? 'Пароль сохранён' : ''}"></label>
    </div>
    <p class="muted">${state.url ? 'Пустой пароль сохраняет текущий пароль при том же адресе, пути и логине.' : 'Пока используется встроенный демонстрационный набор.'}</p>
    <div class="fields"><label>Задержка имитатора WB, с<input name="delay" type="number" min="1" max="60" step="any" value="${state.wb.delay}" required></label>
    <label>Максимальное снижение в WB, %<input name="max_drop" type="number" min="0" max="100" step="any" value="${state.wb.max_drop}" required></label></div>
    <div class="actions"><button class="primary">Сохранить подключения</button><button type="button" data-action="check">Проверить соединения</button></div>
    </form></div>
    <div class="panel settings-panel"><h2>Расписание синхронизации</h2><form data-action="schedule">
    <label class="checkbox"><input type="checkbox" name="enabled" ${state.schedule.enabled ? 'checked' : ''}> Синхронизировать ежедневно</label>
    <label>Время запуска, МСК<input type="time" name="time" value="${escape(state.schedule.time)}" required></label>
    <button class="primary">Сохранить расписание</button>
    <div id="sync-status" class="sync-status">${syncStatus()}</div></form></div>`;
}

function report() {
  return `<div class="toolbar"><label>Сравнение периодов<select id="period">${options([['0','7 дней'],['2','30 дней']],String(period))}</select></label></div>
    <div class="panel table-scroll"><table><thead><tr><th>Период</th><th>Дней без остатка, суммарно</th>
    <th>Залежавшиеся товары</th><th>Расчётная маржа</th><th>Применено изменений</th></tr></thead><tbody>
    ${state.reports.slice(period,period+2).map(r => `<tr><td>${r.previous ? 'Предыдущий' : 'Текущий'}<small>
    ${date(r.from_date)} — ${date(r.to_date)} (искл.)</small></td><td>${num(r.outages)}</td><td>${num(r.stale)}</td>
    <td>${r.margin == null ? '—' : num(r.margin)+'%'}</td><td>${r.applied}</td></tr>`).join('')}</tbody></table>
    <small>Продажи и наличие учитываются за завершённые дни. Складской остаток, учётная цена и расходы взяты из последней выгрузки и текущих настроек. Число применений в текущем периоде включает сегодня.</small></div>
    <div class="panel"><h2>История изменений цен</h2><div class="table-scroll"><table><thead><tr><th>Время, МСК</th><th>Артикул</th>
    <th>Старая цена</th><th>Новая цена</th><th>Причина</th><th>Способ</th><th>Результат</th></tr></thead><tbody>
    ${state.history.map(e => `<tr><td>${timestamp(e.at)}</td><td>${escape(e.sku)}</td><td>${money(e.old)}</td><td>${money(e.new)}</td>
    <td>${escape(e.reason)}</td><td>${escape(e.method)}</td><td>${badge(e.status,statuses[e.status])}<small>${escape(e.message)}</small></td></tr>`).join('')
    || '<tr><td colspan="7">История пока пуста</td></tr>'}</tbody></table></div></div>`;
}

function render() {
  $('#title').textContent = titles[tab][0];
  $('#subtitle').textContent = titles[tab][1];
  $('#updated').textContent = 'Данные · ' + timestamp(state.synced);
  $('#content').innerHTML = ({products,rules,integrations,report})[tab]();
  if (tab === 'products') refreshRows();
}

function notice(message, error = false) {
  $('#notice').textContent = message;
  $('#notice').classList.toggle('error', error);
}

function openManualPrice(sku) {
  const product = state.products.find(p => p.sku === sku);
  if (!product) return;
  const dialog = $('#manual-price');
  const form = dialog.querySelector('form');
  form.elements.sku.value = sku;
  form.elements.price.value = product.price;
  $('#manual-context').textContent = `${product.name} · ${sku}`;
  $('#manual-pending').hidden = !product.pending;
  $('#manual-error').textContent = '';
  dialog.showModal();
  form.elements.price.select();
}

async function action(body) {
  if (busy) return;
  busy = true;
  const manual = body.action === 'manual';
  const saveButton = $('#manual-price button.primary');
  if (manual) saveButton.disabled = true;
  try {
    const response = await fetch('/api/action', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (result.state) {
      state = result.state;
      if (response.ok || !['rules','settings','manual'].includes(body.action)) render();
    }
    if (manual && response.ok) {
      $('#manual-price').close();
      [...document.querySelectorAll('[data-edit-price]')].find(button => button.dataset.editPrice === body.sku)?.focus();
    }
    if (manual && !response.ok) $('#manual-error').textContent = result.error;
    else notice(result.message || result.error, !response.ok);
  } catch (error) {
    if (manual) $('#manual-error').textContent = error.message;
    else notice(error.message, true);
  } finally {
    if (manual) saveButton.disabled = false;
    busy = false;
  }
}

document.addEventListener('click', event => {
  const button = event.target.closest('button');
  if (!button || !state) return;
  if (button.dataset.tab) {
    document.querySelector('[aria-current]').removeAttribute('aria-current');
    button.setAttribute('aria-current','page');
    tab = button.dataset.tab;
    notice('');
    render();
  } else if (button.dataset.editPrice) {
    openManualPrice(button.dataset.editPrice);
  } else if (button.id === 'manual-cancel') {
    $('#manual-price').close();
  } else if (button.id === 'sync') {
    action({action:'sync'});
  } else if (button.dataset.action) {
    action({action:button.dataset.action,sku:button.dataset.sku,id:button.dataset.id});
  }
});
document.addEventListener('submit', event => {
  event.preventDefault();
  const values = Object.fromEntries(new FormData(event.target));
  const type = event.target.dataset.action;
  if (type === 'schedule') values.enabled = event.target.elements.enabled.checked;
  action({action:type,...values});
});
document.addEventListener('input', event => {
  if (event.target.id === 'search') {
    filter.search = event.target.value;
    refreshRows();
  }
});
document.addEventListener('change', event => {
  const el = event.target;
  if (el.id === 'filter-category' || el.id === 'filter-kind') {
    filter[el.id.slice(7)] = el.value;
    refreshRows();
  } else if (el.id === 'rule-category') {
    category = el.value;
    render();
  } else if (el.id === 'period') {
    period = Number(el.value);
    render();
  }
});

async function poll() {
  if (busy) return;
  try {
    const response = await fetch('/api/state');
    const next = await response.json();
    if (busy) return;
    const changed = !state || state.synced !== next.synced
      || JSON.stringify(state.history) !== JSON.stringify(next.history);
    const existingStatus = $('#sync-status');
    const previousStatus = existingStatus && syncStatus();
    state = next;
    if (!$('#content').querySelector('div')) {
      render();
    } else if (changed && ['products','report'].includes(tab)) {
      const searchFocused = document.activeElement?.id === 'search';
      render();
      if (searchFocused) {
        $('#search').focus();
        $('#search').setSelectionRange(filter.search.length, filter.search.length);
      }
    }
    if (existingStatus && previousStatus !== syncStatus()) existingStatus.innerHTML = syncStatus();
  } catch {
    if (!state) notice('Панель недоступна. Повторяем подключение.', true);
  }
}
poll();
setInterval(poll, 2000);
