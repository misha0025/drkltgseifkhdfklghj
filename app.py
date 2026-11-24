from datetime import date, datetime, timedelta
from hashlib import sha256
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo
import json
import math
import mimetypes
import os

from adapters import Marketplace, accounting_snapshot
from engine import DEFAULT, recommend

ROOT = Path(__file__).resolve().parent
STATE = Path(os.environ.get('REPRICER_STATE', ROOT / 'state.json'))


def clock():
    return datetime.now(ZoneInfo('Europe/Moscow'))


class App:
    def __init__(self):
        self.data = json.loads(STATE.read_text()) if STATE.exists() else dict(
            products=[], market={}, observed={}, rules={}, recommendations={}, auto={},
            history=[], dismissed=[], url='', wb=dict(delay=8, max_drop=3),
            synced=None, error=None)
        if not self.data['synced']:
            self.sync(clock())
            self.save()

    def save(self):
        temporary = STATE.with_suffix('.tmp')
        temporary.write_text(json.dumps(self.data, ensure_ascii=False))
        temporary.replace(STATE)

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
            identity = [sku, market['revision'], rule, product['stock'], product.get('cost'), result]
            result['id'] = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            data['recommendations'][sku] = result
            if (auto and data['auto'].get(sku) and result['kind'] in ('up', 'down')
                    and result['id'] not in data['dismissed']
                    and previous.get(sku, {}).get('id') != result['id']
                    and data['market'][sku]['revision'] == market['revision']
                    and not any(e.get('recommendation') == result['id'] for e in data['history'])):
                self.submit(sku, result, 'Автоматически', now)

    def sync(self, now):
        data = self.data
        try:
            products = accounting_snapshot(data['url'])
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

    def submit(self, sku, result, method, now):
        market = self.data['market'][sku]
        if market['revision'] != self.data['observed'][sku]['revision']:
            raise ValueError('Цена на площадке изменилась. Синхронизируйте данные')
        event = dict(id=uuid4().hex, sku=sku, old=result['price'], new=result['proposed'],
                     reason=result['reason'], method=method, status='pending', at=now.isoformat(),
                     recommendation=result['id'])
        self.adapter().submit(sku, event, now)
        self.data['history'].append(event)
        self.data['observed'][sku] = market.copy()

    def action(self, body, now):
        data = self.data
        action = body['action']
        sku = body.get('sku')
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
            data.update(url=url, wb=dict(delay=delay, max_drop=max_drop))
        elif action == 'check':
            try:
                rows = accounting_snapshot(data['url'])
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
            data['rules'][category] = rules
            self.calculate(now, auto=True)
        elif action == 'mode':
            if sku not in data['recommendations'] or type(body['auto']) is not bool:
                raise ValueError('Некорректный режим или артикул')
            data['auto'][sku] = body['auto']
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
            market = data['observed'][sku]
            rec = data['recommendations'][sku]
            latest = next((e for e in reversed(data['history']) if e['sku'] == sku), None)
            products.append(dict(p, sales=None, supplies=None, rec=rec, latest=latest,
                                 pending=bool(market.get('pending')), anchor=market['anchor'],
                                 auto=data['auto'].get(sku, False), dismissed=rec['id'] in data['dismissed']))
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
                    url=data['url'], wb=data['wb'], synced=data['synced'], error=data['error'], reports=reports)


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
        app.tick(clock())


if __name__ == '__main__':
    app = App()
    port = int(os.environ.get('REPRICER_PORT', '8765'))
    print(f'http://127.0.0.1:{port}', flush=True)
    Server(('127.0.0.1', port), Handler).serve_forever()
