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
const metric = (label, value) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
let state, tab = 'products', category, period = 0, busy = false;
const filter = {search:'', category:'', kind:''};
const options = (values, selected) => values.map(([value, label]) =>
  `<option value="${escape(value)}" ${value === selected ? 'selected' : ''}>${escape(label)}</option>`).join('');
const categoryOptions = () => Object.keys(state.rules).map(value => [value, value]);

function row(p) {
  const r = p.rec;
  const allowed = ['up','down'].includes(r.kind) && !p.pending && !p.dismissed;
  const status = p.latest;
  return `<tr>
    <td><strong>${escape(p.name)}</strong><small>${escape(p.sku)} · ${escape(p.category)}</small></td>
    <td class="money">${money(r.price)}<small>Опорная ${money(p.anchor)}</small></td>
    <td class="money"><strong>${money(r.proposed)}</strong>${r.step ? `<small>${r.step > 0 ? '+' : ''}${num(r.step)}%</small>` : ''}</td>
    <td>${num(p.stock)} шт.<small>${r.cover == null ? 'Нет темпа продаж' : num(r.cover) + ' дн.'}</small></td>
    <td>${r.supply ? `${date(r.supply.date)}<small>${num(r.supply.quantity)} шт.</small>` : 'Поставка неизвестна'}</td>
    <td>${badge(r.kind, kinds[r.kind])}<div>${escape(r.reason)}</div>
      <details><summary>Исходные данные</summary><small>Продажи ${num(r.sold)} шт. за ${num(r.available)} дн. наличия<br>
       Темп ${num(r.rate)} шт./день<br>Себестоимость ${money(p.cost)}<br>Учётная цена ${money(p.accounting)}<br>
       Минимальная цена ${money(r.minimum)}</small></details></td>
    <td>${status ? badge(status.status, statuses[status.status]) : '<span class="muted">Не отправлялась</span>'}
      ${status?.message ? `<small>${escape(status.message)}</small>` : ''}
      ${allowed ? `<div class="actions"><button class="primary" data-action="approve"
       data-sku="${escape(p.sku)}" data-id="${r.id}">Подтвердить</button><button data-action="reject"
       data-sku="${escape(p.sku)}" data-id="${r.id}">Отклонить</button></div>` : ''}</td></tr>`;
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
  return `<div class="metrics">
    ${metric('Товаров', p.length)}${metric('Рекомендаций', p.filter(x => ['up','down'].includes(x.rec.kind) && !x.pending && !x.dismissed).length)}
    ${metric('Ожидают применения', p.filter(x => x.pending).length)}${metric('С ограничениями', p.filter(x => x.rec.kind === 'blocked').length)}
    </div><div class="panel catalog"><div class="toolbar">
    <input type="search" id="search" aria-label="Поиск по названию или артикулу" placeholder="Название или артикул" value="${escape(filter.search)}">
    <select id="filter-category" aria-label="Категория">${options([['','Все категории'],...categoryOptions()],filter.category)}</select>
    <select id="filter-kind" aria-label="Тип рекомендации">${options([['','Все рекомендации'],...Object.entries(kinds)],filter.kind)}</select>
    </div><div class="table-scroll"><table><thead><tr><th>Товар</th><th>Цена WB</th><th>Предложение</th>
    <th>Остаток / запас</th><th>Поставка</th><th>Причина</th><th>Отправка</th></tr></thead><tbody id="rows"></tbody></table></div></div>`;
}

function rules() {
  category ??= Object.keys(state.rules)[0];
  return `<div class="panel"><h2>Настройки категории</h2><form data-action="rules">
    <label>Категория<select id="rule-category" name="category">${options(categoryOptions(),category)}</select></label>
    <div class="fields">${Object.entries(fields).map(([name,label]) =>
      `<label>${label}<input name="${name}" type="number" step="any" value="${escape(state.rules[category]?.[name])}"></label>`).join('')}</div>
    <button class="primary">Сохранить и пересчитать</button></form></div>
    <div class="panel"><h2>Режим товаров</h2><div class="table-scroll"><table><thead><tr><th>Товар</th><th>Режим</th></tr></thead><tbody>
    ${state.products.map(p => `<tr><td>${escape(p.name)}<small>${escape(p.sku)}</small></td><td><select aria-label="Режим ${escape(p.sku)}"
      data-mode="${escape(p.sku)}">${options([['manual','Только рекомендации'],['auto','Автоматически']],p.auto ? 'auto':'manual')}</select></td></tr>`).join('')}
    </tbody></table></div></div>`;
}

function integrations() {
  return `<div class="panel"><h2>Подключения</h2><form data-action="settings">
    <label>URL JSON-выгрузки 1С<input name="url" type="url" value="${escape(state.url)}" placeholder="http://127.0.0.1:8766/snapshot"></label>
    <p class="muted">${state.url ? 'Настроен внешний HTTP-источник.' : 'Используется встроенный демонстрационный набор.'}</p>
    <div class="fields"><label>Задержка имитатора WB, с<input name="delay" type="number" min="1" max="60" value="${state.wb.delay}" required></label>
    <label>Максимальное снижение в WB, %<input name="max_drop" type="number" min="0" max="100" step="any" value="${state.wb.max_drop}" required></label></div>
    <div class="actions"><button class="primary">Сохранить</button><button type="button" data-action="check">Проверить соединения</button></div>
    <div>Последняя успешная синхронизация · ${timestamp(state.synced)}</div>
    ${state.error ? `<div role="alert">${escape(state.error)}</div>` : ''}</form></div>
    <div class="panel"><h2>Ручная цена в имитаторе WB</h2><form data-action="manual"><div class="fields">
    <label>Товар<select name="sku">${options(state.products.map(p => [p.sku,p.sku+' · '+p.name]))}</select></label>
    <label>Цена, ₽<input name="price" type="number" min="0.01" step="0.01" required></label>
    </div><button class="primary">Изменить цену в WB</button></form></div>`;
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

async function action(body) {
  if (busy) return;
  busy = true;
  try {
    const response = await fetch('/api/action', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const result = await response.json();
    if (result.state) {
      state = result.state;
      if (response.ok || !['rules','settings','manual'].includes(body.action)) render();
    }
    notice(result.message || result.error, !response.ok);
  } catch (error) {
    notice(error.message, true);
  } finally {
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
  } else if (button.id === 'sync') {
    action({action:'sync'});
  } else if (button.dataset.action) {
    action({action:button.dataset.action,sku:button.dataset.sku,id:button.dataset.id});
  }
});
document.addEventListener('submit', event => {
  event.preventDefault();
  action({action:event.target.dataset.action,...Object.fromEntries(new FormData(event.target))});
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
  } else if (el.dataset.mode) {
    action({action:'mode',sku:el.dataset.mode,auto:el.value === 'auto'});
  }
});

async function poll() {
  if (busy || (state && !state.products.some(p => p.pending) && !state.history.some(e => e.status === 'pending'))) return;
  try {
    const response = await fetch('/api/state');
    const next = await response.json();
    if (busy) return;
    const changed = !state || JSON.stringify(state.history) !== JSON.stringify(next.history);
    state = next;
    if (changed && !['rules','integrations'].includes(tab)) {
      if ($('#rows')) refreshRows();
      else render();
    } else if (!$('#content').querySelector('div')) {
      render();
    }
  } catch {
    if (!state) notice('Панель недоступна. Повторяем подключение.', true);
  }
}
poll();
setInterval(poll, 2000);
