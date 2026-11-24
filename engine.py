from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

DEFAULT = dict(up=2, down=2, margin=15, commission=15, logistics=50,
               deviation=10, interval=48, stale=90, idle=30)
D = lambda value: Decimal(str(value))


def recommend(product, market, rule, now):
    today = now.date()
    window = [(date.fromisoformat(row['date']), row) for row in product['sales']
              if today - timedelta(days=30) <= date.fromisoformat(row['date']) < today]
    days = [(when, row) for when, row in window if row['available']]
    sold = sum(row['units'] for _, row in days)
    rate = sold / len(days) if days else 0
    stock = product.get('sellable', product['stock'])
    cover = stock / rate if rate else None
    middle = today - timedelta(days=15)
    early = [row['units'] for when, row in days if when < middle]
    recent = [row['units'] for when, row in days if when >= middle]
    previous_rate = sum(early) / len(early) if len(early) >= 7 else None
    recent_rate = sum(recent) / len(recent) if len(recent) >= 7 else None
    has_trend = previous_rate is not None and recent_rate is not None
    faster = has_trend and sum(recent) >= 3 and recent_rate > previous_rate * 1.5
    slower = has_trend and sum(early) >= 3 and recent_rate < previous_rate * 0.65
    supply = min((s for s in product['supplies'] if s.get('date')
                  and date.fromisoformat(s['date']) >= today and s['quantity'] > 0),
                 key=lambda s: s['date'], default=None)
    price = D(market['price'])
    result = dict(price=float(price), proposed=float(price), rate=rate, sold=sold,
                  available=len(days), unavailable=len(window) - len(days), cover=cover, supply=supply,
                  recent_rate=recent_rate, previous_rate=previous_rate, minimum=None)
    if stock <= 0:
        return dict(result, kind='same', reason='Нет доступного остатка для продажи')
    fields = ('margin', 'commission', 'logistics')
    if product.get('cost') is None or any(rule.get(k) is None for k in fields):
        return dict(result, kind='blocked', reason='Недостаточно данных для расчёта минимальной цены')

    minimum = (D(product['cost']) + D(rule['logistics'])) / (
        1 - (D(rule['margin']) + D(rule['commission'])) / 100)
    result['minimum'] = float(minimum)
    if price < minimum:
        kind, reason, step = 'up', 'Цена ниже минимальной маржи', rule['up']
    elif cover is not None and supply and cover < (date.fromisoformat(supply['date']) - today).days:
        kind, reason, step = 'up', 'Доступного запаса не хватит до поставки', rule['up']
    elif faster and recent_rate and stock / recent_rate <= 30:
        kind, reason, step = 'up', 'Темп продаж вырос, доступный запас ограничен', rule['up']
    elif slower and (cover is None or cover >= 45):
        kind, reason, step = 'down', 'Темп продаж снизился при достаточном остатке', -rule['down']
    elif cover is not None and cover > rule['stale']:
        kind, reason, step = 'down', 'Текущего запаса хватит дольше порога залежалости', -rule['down']
    elif stock >= 10 and not sold and sum(when >= today - timedelta(days=rule['idle'])
                                         for when, _ in days) >= rule['idle']:
        kind, reason, step = 'down', 'Нет продаж при устойчивом наличии товара', -rule['down']
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
        return dict(result, kind='blocked', reason=f'{reason}. {limit}',
                    cooldown=limit == 'Минимальный интервал ещё не прошёл')
    return dict(result, kind=kind, reason=reason, proposed=float(target), step=float(step))
