"""Границы учётной системы и площадки."""
from datetime import date, datetime, timedelta
import json
import os
import math
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from demo import snapshot


def accounting_snapshot(url):
    if not url:
        return snapshot()
    address = urlsplit(url)
    if address.scheme not in ('http', 'https') or not address.hostname or address.username:
        raise ValueError('Укажите HTTP(S)-адрес выгрузки без учётных данных')
    headers = {'Accept': 'application/json'}
    if token := os.getenv('REPRICER_1C_AUTH'):
        headers['Authorization'] = token
    with urlopen(Request(url, headers=headers), timeout=5) as response:
        rows = json.load(response)
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

    @staticmethod
    def finish(history, pending, status, message, now):
        event = next(item for item in history if item['id'] == pending['event'])
        event.update(status=status, message=message, finished=now.isoformat())
        return event
