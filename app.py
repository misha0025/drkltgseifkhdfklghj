from copy import deepcopy
from datetime import date, datetime, time, timedelta
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import NamedTemporaryFile
from uuid import uuid4
from zoneinfo import ZoneInfo
import json
import math
import mimetypes
import os
import re

from adapters import Marketplace, accounting_snapshot, accounting_url, odata_endpoint
from engine import DEFAULT, recommend

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('REPRICER_STATE', ROOT / 'state.json'))


def clock():
    return datetime.now(ZoneInfo('Europe/Moscow'))


class App:
    def __init__(self):
        self.data = json.loads(STATE.read_text(encoding='utf-8')) if STATE.exists() else dict(
            products=[], market={}, observed={}, rules={}, recommendations={}, category_modes={},
            history=[], dismissed=[], url='', wb=dict(delay=8, max_drop=3),
            synced=None, error=None)
        self.data.pop('auto', None)
        self.data.setdefault('category_modes', {})
        self.data.setdefault('batch_tasks', {})
        connection = self.data.setdefault('connection', {})
        for key, default in dict(service='', login='', password='', mode='json',
                                 warehouse='', organization='', price_type='').items():
            connection.setdefault(key, default)
        self.data.setdefault('schedule', dict(enabled=False, time='09:00', slot=None, attempted=None))
        if not self.data['synced']:
            self.sync(clock())
            self.save()

    def save(self):
        with NamedTemporaryFile(mode='w', encoding='utf-8', dir=STATE.parent, delete=False) as output:
            temporary = Path(output.name)
            try:
                json.dump(self.data, output, ensure_ascii=False)
                output.close()
                temporary.replace(STATE)
            finally:
                temporary.unlink(missing_ok=True)

    def adapter(self):
        return Marketplace(self.data['market'], self.data['wb'])

    def tick(self, now):
        data = self.data
        pending = {sku for sku, row in data['market'].items() if row.get('pending')}
        self.adapter().settle(data['history'], now)
        settled = [sku for sku in pending if not data['market'][sku].get('pending')]
        if settled:
            for sku in settled:
                data['observed'][sku] = data['market'][sku].copy()
            self.calculate(now)
            self.save()

    def run_schedule(self, now):
        schedule = self.data['schedule']
        if not schedule['enabled']:
            return
        slot = datetime.combine(now.date(), time.fromisoformat(schedule['time']), tzinfo=now.tzinfo)
        if now < slot or schedule['slot'] == slot.isoformat():
            return
        schedule.update(slot=slot.isoformat(), attempted=now.isoformat())
        try:
            self.sync(now)
        except ValueError:
            return
        self.save()

    def next_sync(self, now):
        schedule = self.data['schedule']
        if not schedule['enabled']:
            return None
        slot = datetime.combine(now.date(), time.fromisoformat(schedule['time']), tzinfo=now.tzinfo)
        if schedule['slot'] == slot.isoformat():
            slot += timedelta(days=1)
        return slot.isoformat()

    def observe(self):
        self.data['observed'] = {sku: market.copy() for sku, market in self.data['market'].items()}

    def calculate(self, now, auto=False):
        data = self.data
        previous = data['recommendations']
        data['recommendations'] = {}
        for product in data['products']:
            sku = product['sku']
            market = data['observed'][sku]
            rule = data['rules'][product['category']]
            result = recommend(product, market, rule, now)
            identity = [sku, market['revision'], product['stock'], product.get('cost'), result]
            result['id'] = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            data['recommendations'][sku] = result
            if (auto and data['category_modes'].get(product['category']) == 'auto' and result['kind'] in ('up', 'down')
                    and result['id'] not in data['dismissed']
                    and previous.get(sku, {}).get('id') != result['id']
                    and data['market'][sku]['revision'] == market['revision']
                    and not any(e.get('recommendation') == result['id'] for e in data['history'])):
                self.submit(sku, result, 'Автоматически', now)

    def sync(self, now):
        data = self.data
        try:
            products = accounting_snapshot(data['url'], **data['connection'])
        except Exception as error:
            data['error'] = str(error)
            self.save()
            raise ValueError(f'Источник учёта недоступен. {error}') from error
        data['products'] = products
        for product in products:
            sku, price = product['sku'], product['accounting']
            data['rules'].setdefault(product['category'], DEFAULT.copy())
            data['market'].setdefault(sku, dict(price=price, anchor=price, revision=0))
        self.observe()
        self.calculate(now, auto=True)
        data.update(synced=now.isoformat(), error=None)

    def submit(self, sku, result, method, now, *, task=None, manual=False):
        market = self.data['market'][sku]
        if not manual and market['revision'] != self.data['observed'][sku]['revision']:
            raise ValueError('Цена на площадке изменилась. Синхронизируйте данные')
        event = dict(id=uuid4().hex, sku=sku, old=result['price'], new=result['proposed'],
                     reason=result['reason'], method=method, status='pending', at=now.isoformat(),
                     recommendation=result.get('id'))
        if task:
            event.update(task=task, manual=manual)
        self.adapter().submit(sku, event, now)
        self.data['history'].append(event)
        self.data['observed'][sku]['pending'] = market['pending']

    def send_batch(self, body, now):
        task = body['task_id']
        items = body['items']
        if not isinstance(task, str) or not re.fullmatch(r'[0-9a-f-]{36}', task):
            raise ValueError('Некорректный номер задания')
        if not isinstance(items, list) or not items:
            raise ValueError('Добавьте товары в задание')
        digest = sha256(json.dumps(items, sort_keys=True).encode()).hexdigest()
        message = f'Задание отправлено в имитатор WB · позиций: {len(items)}'
        data = self.data
        if task in data['batch_tasks']:
            if data['batch_tasks'][task] != digest:
                raise ValueError('Задание с этим номером уже отправлено')
            return message

        self.calculate(now)
        seen = set()
        validated = []
        for item in items:
            sku = item['sku']
            if sku in seen or sku not in data['recommendations']:
                raise ValueError(f'{sku} · повторяющийся или неизвестный артикул')
            seen.add(sku)
            market = data['market'][sku]
            if market.get('pending') or item['revision'] != market['revision']:
                raise ValueError(f'{sku} · цена изменилась или ожидает применения. Обновите позицию в задании')
            price = number(item['price'], 0.01, 100000000)
            if price != round(price, 2) or price == market['price']:
                raise ValueError(f'{sku} · укажите новую цену с точностью до копеек')
            source = item['source']
            if source == 'recommendation':
                rec = data['recommendations'][sku]
                if (rec['id'] != item.get('recommendation_id') or rec['kind'] not in ('up', 'down')
                        or rec['id'] in data['dismissed'] or rec['proposed'] != price
                        or market['revision'] != data['observed'][sku]['revision']):
                    raise ValueError(f'{sku} · рекомендация устарела. Обновите позицию в задании')
            elif source == 'manual':
                rec = dict(price=market['price'], proposed=price, reason='Ручная цена из задания')
            else:
                raise ValueError(f'{sku} · неизвестный источник цены')
            validated.append((sku, rec, source == 'manual'))

        self.data = deepcopy(data)
        try:
            for sku, rec, manual in validated:
                self.submit(sku, rec, 'Менеджер', now, task=task, manual=manual)
            self.data['batch_tasks'][task] = digest
            self.save()
        except Exception:
            self.data = data
            raise
        return message

    def action(self, body, now):
        data = self.data
        action = body['action']
        sku = body.get('sku')
        if action == 'send_batch':
            return self.send_batch(body, now)
        if action in ('approve', 'reject'):
            old = data['recommendations'].get(sku, {})
            if old.get('id') != body.get('id') or sku not in data['observed']:
                raise ValueError('Рекомендация устарела')
            if data['market'][sku]['revision'] != data['observed'][sku]['revision']:
                raise ValueError('Цена на площадке изменилась. Синхронизируйте данные')
            self.calculate(now)
            result = data['recommendations'][sku]
            if (result['id'] != body['id'] or result['kind'] not in ('up', 'down')
                    or result['id'] in data['dismissed'] or data['market'][sku].get('pending')):
                raise ValueError('Рекомендация уже обработана или устарела')
            if action == 'approve':
                self.submit(sku, result, 'Менеджер', now)
            else:
                data['dismissed'].append(result['id'])
                data['history'].append(dict(id=uuid4().hex, sku=sku, old=result['price'],
                    new=result['proposed'], reason=result['reason'], method='Менеджер',
                    status='dismissed', at=now.isoformat()))
        elif action == 'sync':
            self.sync(now)
        elif action == 'settings':
            delay = number(body['delay'], 1, 60)
            max_drop = number(body['max_drop'], 0, 100)
            url = body['url'].strip()
            connection = data['connection']
            service = body.get('service', connection['service']).strip()
            login = body.get('login', connection['login']).strip()
            mode = body.get('mode', 'ut' if url else 'json')
            if mode not in ('json', 'ut'):
                raise ValueError('Неизвестный режим подключения 1С')
            scope = {key: body.get(key, connection[key]).strip()
                     for key in ('warehouse', 'organization', 'price_type')}
            if mode == 'ut':
                if not url or not all(scope.values()):
                    raise ValueError('Для УТ укажите адрес, склад, организацию и вид цены')
                odata_endpoint(url)
                service = ''
            new_password = body.get('password', '')
            same_connection = (url, service, login) == (data['url'], connection['service'], connection['login'])
            password = new_password or (connection['password'] if same_connection else '')
            if ':' in login:
                raise ValueError('Логин 1С не должен содержать двоеточие')
            if login and not url:
                raise ValueError('Укажите адрес публикации 1С')
            if not login and new_password:
                raise ValueError('Укажите логин пользователя 1С')
            if url:
                accounting_url(url, service)
            data.update(url=url, connection=dict(service=service, login=login, password=password, mode=mode, **scope),
                        wb=dict(delay=delay, max_drop=max_drop))
        elif action == 'schedule':
            enabled, at = body['enabled'], body['time']
            if type(enabled) is not bool or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]', at):
                raise ValueError('Укажите время синхронизации в формате ЧЧ:ММ')
            data['schedule'].update(enabled=enabled, time=at)
        elif action == 'check':
            try:
                rows = accounting_snapshot(data['url'], **data['connection'])
            except Exception as error:
                data['error'] = str(error)
                self.save()
                raise ValueError(f'Источник учёта недоступен. {error}') from error
            return f'Источник учёта доступен · {len(rows)} товаров. Имитатор WB доступен'
        elif action == 'rules':
            category = body['category']
            if category not in data['rules']:
                raise ValueError('Неизвестная категория')
            rules = {}
            for field in DEFAULT:
                raw = body[field]
                if raw == '' and field in ('margin', 'commission', 'logistics'):
                    rules[field] = None
                else:
                    lower = 0.01 if field in ('up', 'down') else 1 if field in ('idle', 'stale') else 0
                    upper = dict(down=99.99, idle=30, logistics=1000000, interval=8760, stale=3650).get(field, 100)
                    rules[field] = number(raw, lower, upper)
                    if field == 'idle' and not rules[field].is_integer():
                        raise ValueError('Порог дней без продаж должен быть целым')
            if (rules['margin'] or 0) + (rules['commission'] or 0) >= 100:
                raise ValueError('Сумма маржи и комиссии должна быть меньше 100%')
            mode = body.get('mode', data['category_modes'].get(category, 'manual'))
            if mode not in ('manual', 'auto'):
                raise ValueError('Некорректный режим категории')
            data['category_modes'][category] = mode
            if data['rules'][category] != rules:
                data['rules'][category] = rules
                self.calculate(now, auto=True)
        elif action == 'manual':
            market = data['market'][sku]
            old_price = market['price']
            price = round(number(body['price'], 0.01, 100000000), 2)
            self.adapter().manual(sku, price, data['history'], now)
            data['observed'][sku].pop('pending', None)
            data['history'].append(dict(id=uuid4().hex, sku=sku, old=old_price, new=price,
                reason='Ручная смена цены на площадке', method='Имитатор WB',
                status='manual', at=now.isoformat(), finished=now.isoformat()))
        else:
            raise ValueError('Неизвестное действие')
        self.save()
        return {'sync': 'Данные синхронизированы', 'manual': 'Цена записана в имитаторе. Запустите синхронизацию',
                'approve': 'Цена отправлена в имитатор WB', 'reject': 'Рекомендация отклонена'}.get(action, 'Настройки сохранены')

    def view(self, now):
        data = self.data
        products = []
        for p in data['products']:
            sku = p['sku']
            observed = data['observed'][sku]
            market = data['market'][sku]
            rec = data['recommendations'][sku]
            latest = next((e for e in reversed(data['history']) if e['sku'] == sku), None)
            products.append(dict(p, sales=None, supplies=None, rec=rec, latest=latest,
                                 price=market['price'], revision=market['revision'], anchor=market['anchor'], pending=bool(market.get('pending')),
                                 needs_sync=market['revision'] != observed['revision'],
                                 auto=data['category_modes'].get(p['category']) == 'auto', dismissed=rec['id'] in data['dismissed']))
        reports = []
        for start, end in ((7, 0), (14, 7), (30, 0), (60, 30)):
            lower, upper = now.date() - timedelta(days=start), now.date() - timedelta(days=end)
            days = [d for p in data['products'] for d in p['sales']
                    if lower <= date.fromisoformat(d['date']) < upper]
            revenues = costs = 0
            stale = 0
            for p in data['products']:
                rule = data['rules'][p['category']]
                sold = sum(d['units'] for d in p['sales'] if lower <= date.fromisoformat(d['date']) < upper)
                available = sum(d['available'] for d in p['sales'] if lower <= date.fromisoformat(d['date']) < upper)
                if available and p['stock'] > 0 and (
                        (sold and p['stock'] * available / sold > rule['stale'])
                        or (not sold and available >= rule['idle'])):
                    stale += 1
                if p.get('cost') is None or rule['commission'] is None or rule['logistics'] is None:
                    continue
                price = p['accounting']
                revenues += sold * price
                costs += sold * (p['cost'] + rule['logistics'] + price * rule['commission'] / 100)
            applied = sum(e['status'] == 'applied' and lower <= datetime.fromisoformat(e['finished']).date()
                          < (upper + timedelta(days=1) if end == 0 else upper) for e in data['history'])
            reports.append(dict(days=start-end, previous=bool(end), from_date=str(lower), to_date=str(upper),
                                outages=sum(not d['available'] for d in days) if days else None,
                                stale=stale if days else None, margin=(revenues-costs)/revenues*100 if revenues else None,
                                applied=applied))
        return dict(products=products, rules=data['rules'], history=data['history'][::-1],
                    url=data['url'], service=data['connection']['service'], login=data['connection']['login'],
                    source={key: data['connection'][key] for key in ('mode', 'warehouse', 'organization', 'price_type')},
                    has_password=bool(data['connection']['password']), category_modes=data['category_modes'],
                    schedule={key: data['schedule'][key] for key in ('enabled', 'time', 'attempted')},
                    next_sync=self.next_sync(now), wb=data['wb'], synced=data['synced'],
                    error=data['error'], reports=reports)


def number(raw, lower, upper):
    value = float(raw)
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f'Значение должно быть от {lower} до {upper}')
    return value


class Handler(BaseHTTPRequestHandler):
    def send_json(self, data, code=200):
        content = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self):
        if self.path == '/api/state':
            now = clock()
            app.tick(now)
            self.send_json(app.view(now))
            return
        name = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(self.path)
        if not name:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', mimetypes.guess_type(name)[0] + '; charset=utf-8')
        self.end_headers()
        self.wfile.write((ROOT / 'static' / name).read_bytes())

    def do_POST(self):
        if self.path != '/api/action':
            self.send_error(404)
            return
        try:
            if self.headers.get_content_type() != 'application/json':
                raise ValueError('Ожидается JSON')
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 65536:
                raise ValueError('Некорректный размер запроса')
            now = clock()
            app.tick(now)
            message = app.action(json.loads(self.rfile.read(size)), now)
            self.send_json(dict(message=message, state=app.view(now)))
        except (ValueError, KeyError, TypeError, AttributeError) as error:
            self.send_json(dict(error=str(error), state=app.view(clock())), 400)


class Server(HTTPServer):
    def service_actions(self):
        now = clock()
        app.tick(now)
        app.run_schedule(now)


if __name__ == '__main__':
    app = App()
    host = os.environ.get('REPRICER_HOST', '127.0.0.1')
    port = int(os.environ.get('REPRICER_PORT', '8765'))
    with Server((host, port), Handler) as server:
        print(f'http://{host}:{port}', flush=True)
        server.serve_forever()
