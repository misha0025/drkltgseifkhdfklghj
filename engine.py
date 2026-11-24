from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

DEFAULT = dict(up=2, down=2, margin=15, commission=15, logistics=50,
               deviation=10, interval=48, stale=90, idle=30)
D = lambda value: Decimal(str(value))


def recommend(product, market, rule, now):
    today = now.date()
    days = [day for day in product['sales']
            if today - timedelta(days=30) <= date.fromisoformat(day['date']) < today
            and day['available']]
    sold = sum(day['units'] for day in days)
    rate = sold / len(days) if days else 0
    stock = product['stock']
    cover = stock / rate if rate else None
    supply = min((s for s in product['supplies'] if s.get('date')
                  and date.fromisoformat(s['date']) >= today and s['quantity'] > 0),
                 key=lambda s: s['date'], default=None)
    price = D(market['price'])
    result = dict(price=float(price), proposed=float(price), rate=rate, sold=sold,
                  available=len(days), cover=cover, supply=supply, minimum=None)
    fields = ('margin', 'commission', 'logistics')
    if product.get('cost') is None or any(rule.get(k) is None for k in fields):
        return dict(result, kind='blocked', reason='Недостаточно данных для расчёта минимальной цены')

    minimum = (D(product['cost']) + D(rule['logistics'])) / (
        1 - (D(rule['margin']) + D(rule['commission'])) / 100)
    result['minimum'] = float(minimum)
    if price < minimum:
        kind, reason, step = 'up', 'Цена ниже минимальной маржи', rule['up']
    elif stock > 0 and cover is not None and supply and cover < (date.fromisoformat(supply['date']) - today).days:
        kind, reason, step = 'up', 'Запас закончится до поставки', rule['up']
    elif stock > 0 and ((cover is not None and cover > rule['stale']) or
                       (len(days) >= rule['idle'] and not any(
                           d['units'] for d in days if date.fromisoformat(d['date']) >= today - timedelta(days=rule['idle']))
                        and sum(date.fromisoformat(d['date']) >= today - timedelta(days=rule['idle']) for d in days)
                        >= rule['idle'])):
        kind, reason, step = 'down', 'Товар залежался', -rule['down']
    else:
        return dict(result, kind='same', reason='Оснований для изменения нет')

    target = (price * (1 + D(step) / 100)).quantize(D('0.01'), rounding=ROUND_HALF_UP)
    anchor = D(market['anchor'])
    last = market.get('changed')
    limit = None
    if market.get('pending'):
        limit = 'Предыдущая цена ожидает применения'
    elif target < minimum:
        limit = 'Полный шаг не обеспечивает минимальную маржу'
    elif abs(target - anchor) > anchor * D(rule['deviation']) / 100:
        limit = 'Полный шаг выходит за отклонение от ручной цены'
    elif last and now - datetime.fromisoformat(last) < timedelta(hours=rule['interval']):
        limit = 'Минимальный интервал ещё не прошёл'
    elif target == price:
        limit = 'После округления цена не меняется'
    if limit:
        return dict(result, kind='blocked', reason=f'{reason}. {limit}')
    return dict(result, kind=kind, reason=reason, proposed=float(target), step=float(step))
