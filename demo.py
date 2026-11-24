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
        ('HM-301', 'Органайзер для ящика', 'Для дома', 24, 8, 510, 12),
        ('HM-302', 'Корзина для хранения', 'Для дома', 260, 2, 470, 10),
        ('HM-303', 'Вешалка из бука', 'Для дома', 70, 0, 390, 8),
        ('HM-304', 'Настольная лампа', 'Для дома', 95, 3, 640, 6),
        ('ST-401', 'Блокнот в точку', 'Канцтовары', 18, 6, 420, 9),
        ('ST-402', 'Набор маркеров', 'Канцтовары', 125, 1, 490, 8),
        ('ST-403', 'Папка для документов', 'Канцтовары', 80, 4, 655, 7),
        ('SP-501', 'Бутылка для спорта', 'Спорт', 36, 9, 530, 14),
        ('SP-502', 'Эспандер-лента', 'Спорт', 110, 1, 480, 11),
        ('SP-503', 'Коврик для йоги', 'Спорт', 150, 5, 600, 10),
    ]
    colors = ('белый', 'чёрный', 'серый', 'синий', 'зелёный', 'бордовый', 'песочный')
    scenarios = ((25, 5, 12), (210, 2, 8), (80, 0, 10), (110, 5, 7),
                 (18, 6, 11), (95, 4, 6), (120, 1, 9))
    for prefix, name, category, sizes, cost in (
        ('HM', 'Контейнер', 'Для дома', ('1 л', '2 л'), 450),
        ('ST', 'Ежедневник', 'Канцтовары', ('А5', 'А6'), 380),
        ('SP', 'Резинка для фитнеса', 'Спорт', ('лёгкая', 'средняя'), 410),
    ):
        for size_index, size in enumerate(sizes):
            for color_index, color in enumerate(colors):
                stock, units, delta = scenarios[color_index]
                sku = f'{prefix}-{601 + size_index * len(colors) + color_index}'
                rows.append((sku, f'{name} {size}, цвет {color}', category, stock, units, cost, delta))
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
