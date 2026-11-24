"""Границы учётной системы и площадки."""
from datetime import date, datetime, timedelta
from base64 import b64encode
import json
import os
import math
from urllib.parse import unquote, urlsplit
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from demo import snapshot
from ut_odata import UT
from zoneinfo import ZoneInfo


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def accounting_url(url, service=''):
    address = urlsplit(url)
    if (address.scheme not in ('http', 'https') or not address.hostname
            or address.username is not None or address.fragment):
        raise ValueError('Укажите HTTP(S)-адрес без учётных данных и фрагмента')
    if service:
        path = urlsplit(service)
        if address.query or path.scheme or path.netloc or path.query or path.fragment or '..' in path.path.split('/'):
            raise ValueError('Укажите путь HTTP-сервиса относительно публикации')
        return url.rstrip('/') + '/' + service.strip('/')
    return url


def odata_endpoint(url):
    root = accounting_url(url).rstrip('/')
    if urlsplit(root).query:
        raise ValueError('Укажите адрес публикации УТ без параметров запроса')
    if root.endswith('/ru'):
        root = root[:-3]
    suffix = '/odata/standard.odata'
    return root if root.endswith(suffix) else root + suffix


def accounting_snapshot(url, service='', login='', password='', mode='json',
                        warehouse='', organization='', price_type=''):
    if not url:
        return snapshot()
    token = os.getenv('REPRICER_1C_AUTH') if not login else None
    endpoint = odata_endpoint(url) if mode == 'ut' else accounting_url(url, service)
    headers = {'Accept': 'application/json'}
    if login:
        credentials = b64encode(f'{login}:{password}'.encode('utf-8')).decode('ascii')
        headers['Authorization'] = f'Basic {credentials}'
    elif token:
        headers['Authorization'] = token
    def read_json(target):
        try:
            with build_opener(NoRedirect()).open(Request(target, headers=headers), timeout=20) as response:
                return json.load(response)
        except HTTPError as error:
            if error.code in (401, 403):
                raise ValueError(f'HTTP {error.code}. Проверьте логин, пароль и права в 1С') from error
            if 300 <= error.code < 400:
                raise ValueError(f'HTTP {error.code}. Укажите конечный адрес публикации или выгрузки') from error
            resource = unquote(urlsplit(target).path.rsplit('/standard.odata/', 1)[-1])
            raise ValueError(f'HTTP {error.code} · {resource}. Проверьте публикацию объекта 1С') from error
        except json.JSONDecodeError as error:
            raise ValueError('Ожидался JSON. Проверьте адрес и режим подключения 1С') from error

    if mode == 'ut':
        today = datetime.now(ZoneInfo('Europe/Moscow')).date()
        return UT(endpoint, read_json).snapshot(today, warehouse, organization, price_type)
    rows = read_json(endpoint)
    if not isinstance(rows, list) or not rows:
        raise ValueError('Выгрузка должна содержать массив товаров')
    seen = set()
    for row in rows:
        for key in ('sku', 'name', 'category'):
            if not isinstance(row[key], str) or not row[key].strip():
                raise ValueError(f'Некорректное поле {key}')
        if row['sku'] in seen:
            raise ValueError('Артикулы в выгрузке должны быть уникальными')
        seen.add(row['sku'])
        for key in ('stock', 'accounting', 'cost'):
            if key == 'cost' and row.get(key) is None:
                continue
            value = row[key]
            if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (key == 'accounting' and value == 0):
                raise ValueError(f'Некорректное значение {key}')
        dates = set()
        for day in row['sales']:
            when = date.fromisoformat(day['date'])
            if when in dates or type(day['available']) is not bool or type(day['units']) is not int or day['units'] < 0:
                raise ValueError('Некорректная история продаж')
            dates.add(when)
        for supply in row['supplies']:
            if supply.get('date'):
                date.fromisoformat(supply['date'])
            if type(supply['quantity']) is not int:
                raise ValueError('Некорректное количество поставки')
    return rows


class Marketplace:
    def __init__(self, prices, settings):
        self.prices = prices
        self.settings = settings

    def submit(self, sku, event, now):
        market = self.prices[sku]
        if market.get('pending'):
            raise ValueError('Цена уже ожидает применения')
        market['pending'] = dict(
            event=event['id'], due=(now + timedelta(seconds=self.settings['delay'])).isoformat(),
            reject=event['new'] < event['old'] * (1 - self.settings['max_drop'] / 100))

    def manual(self, sku, price, history, now):
        market = self.prices[sku]
        if pending := market.pop('pending', None):
            self.finish(history, pending, 'superseded', 'Цена изменена вручную на площадке', now)
        market.update(price=price, anchor=price, changed=now.isoformat(), revision=market['revision'] + 1)

    def settle(self, history, now):
        for market in self.prices.values():
            pending = market.get('pending')
            if not pending or datetime.fromisoformat(pending['due']) > now:
                continue
            del market['pending']
            if pending['reject']:
                self.finish(history, pending, 'rejected', 'Имитатор отклонил слишком резкое снижение', now)
            else:
                event = self.finish(history, pending, 'applied', 'Цена подтверждена площадкой', now)
                market.update(price=event['new'], changed=now.isoformat(), revision=market['revision'] + 1)
                if event.get('manual'):
                    market['anchor'] = event['new']

    @staticmethod
    def finish(history, pending, status, message, now):
        event = next(item for item in history if item['id'] == pending['event'])
        event.update(status=status, message=message, finished=now.isoformat())
        return event
