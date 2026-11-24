"""Отдельный HTTP-источник с демонстрационными данными учётной системы."""
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, HTTPServer


def snapshot():
    today = datetime.now(ZoneInfo('Europe/Moscow')).date()
    rows = [
        ('KM-101', 'Кофейная кружка', 'Посуда', 30, 10, 550, 10),
        ('KM-102', 'Стеклянный графин', 'Посуда', 140, 1, 550, 7),
        ('KM-103', 'Термокружка', 'Посуда', 100, 5, 660, 7),
        ('TX-201', 'Вафельное полотенце', 'Текстиль', 15, 5, 550, None),
        ('TX-202', 'Хлопковый плед', 'Текстиль', 120, 4, 550, 7),
        ('TX-203', 'Льняная салфетка', 'Текстиль', 30, 8, 710, 12),
        ('TX-204', 'Декоративная наволочка', 'Текстиль', 80, 0, 550, 8),
        ('KM-104', 'Фарфоровая пиала', 'Посуда', 40, 2, None, 15),
    ]
    return [dict(sku=sku, name=name, category=category, stock=stock, cost=cost, accounting=1000,
                 sales=[dict(date=str(today - timedelta(days=i)), available=sku != 'TX-201' or i <= 15,
                             units=units if sku != 'TX-201' or i <= 15 else 0)
                        for i in range(1, 61)],
                 supplies=[dict(date=str(today + timedelta(days=delta)), quantity=80)]
                 if delta is not None else [])
            for sku, name, category, stock, units, cost, delta in rows]


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/snapshot':
            self.send_error(404)
            return
        payload = json.dumps(snapshot()).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(payload)


if __name__ == '__main__':
    HTTPServer(('127.0.0.1', 8766), Handler).serve_forever()
