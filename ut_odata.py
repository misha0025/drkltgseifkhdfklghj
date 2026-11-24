"""Read-only adapter for a published standard 1C:UT 11 OData interface."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from urllib.parse import quote, urlencode, urljoin, urlsplit
from uuid import UUID

EMPTY = '00000000-0000-0000-0000-000000000000'
STOCK = 'AccumulationRegister_ТоварыНаСкладах'
ORDERS = 'AccumulationRegister_ЗаказыПоставщикам'
COST = 'AccumulationRegister_СебестоимостьТоваров'
GOOD = 'Номенклатура_Key'
FEATURE = 'Характеристика_Key'
WAREHOUSE = 'Склад_Key'
ORG = 'Организация_Key'
ITEM_ANALYTICS = 'АналитикаУчетаНоменклатуры_Key'
PARTNER_ANALYTICS = 'АналитикаУчетаПоПартнерам_Key'
SALE_OPERATIONS = {'РеализацияКлиенту', 'РеализацияВРозницу', 'РеализацияКомиссионногоТовара'}
RETURN_OPERATIONS = {'ВозвратТоваровОтКлиента', 'ВозвратТоваровОтКлиентаПрошлыхПериодов',
                     'ОприходованиеПоВозврату', 'СторноРеализации'}


def item(row, characteristic=FEATURE):
    return row[GOOD], row.get(characteristic) or EMPTY


def number(value):
    return Decimal(str(value or 0))


def day(row, key='Period'):
    return str(row.get(key) or '')[:10]


class UT:
    def __init__(self, endpoint, read_json):
        self.endpoint = endpoint.rstrip('/') + '/'
        self.read_json = read_json

    def records(self, resource, *fields, filter_by=None):
        query = {'$format': 'json'}
        if fields:
            query['$select'] = ','.join(fields)
        if filter_by:
            query['$filter'] = filter_by
        url = self.endpoint + quote(resource, safe="/()'=:") + '?' + urlencode(query, quote_via=quote)
        seen = set()
        rows = []
        base = urlsplit(self.endpoint)
        while url:
            address = urlsplit(url)
            if (address.scheme, address.netloc) != (base.scheme, base.netloc) or not address.path.startswith(base.path) or url in seen:
                raise ValueError(f'Некорректная ссылка на страницу OData · {resource}')
            seen.add(url)
            page = self.read_json(url)
            if not isinstance(page.get('value'), list):
                raise ValueError(f'Некорректный ответ OData · {resource}')
            rows.extend(page['value'])
            next_page = page.get('odata.nextLink') or page.get('@odata.nextLink')
            url = urljoin(url, next_page) if next_page else None
        return rows

    def resolve(self, catalog, value, label):
        fields = ['Ref_Key', 'Description']
        if catalog != 'Организации':
            fields.append('IsFolder')
        rows = [row for row in self.records('Catalog_' + catalog, *fields,
                                           filter_by='DeletionMark eq false') if not row.get('IsFolder')]
        matches = [row['Ref_Key'] for row in rows if value.casefold() in
                   (row['Ref_Key'].casefold(), row['Description'].strip().casefold())]
        if len(matches) != 1:
            names = ', '.join(row['Description'] for row in rows[:12])
            raise ValueError(f'{label}: укажите точное название или GUID. Доступны: {names}')
        return str(UUID(matches[0]))

    def snapshot(self, today, warehouse, organization, price_type):
        start = today - timedelta(days=60)
        day_keys = [str(start + timedelta(days=i)) for i in range(60)]
        warehouse = self.resolve('Склады', warehouse, 'Склад')
        organization = self.resolve('Организации', organization, 'Организация')
        price_type = self.resolve('ВидыЦен', price_type, 'Вид цены')
        queries = {
            'currencies': ('Catalog_Валюты', 'Ref_Key', 'Code'),
            'goods': ('Catalog_Номенклатура', 'Ref_Key', 'Code', 'Description', 'Артикул', 'ВидНоменклатуры_Key', 'DeletionMark', 'IsFolder'),
            'features': ('Catalog_ХарактеристикиНоменклатуры', 'Ref_Key', 'Description', 'DeletionMark'),
            'categories': ('Catalog_ВидыНоменклатуры', 'Ref_Key', 'Description'),
            'stock': (STOCK + '/Balance()', GOOD, FEATURE, WAREHOUSE, 'ВНаличииBalance'),
            'opening': (STOCK + f"/Balance(Period=datetime'{start}T00:00:00')", GOOD, FEATURE, WAREHOUSE, 'ВНаличииBalance'),
            'movements': (STOCK + '_RecordType', GOOD, FEATURE, WAREHOUSE, 'Active', 'Period', 'RecordType', 'ВНаличии'),
            'sales': ('AccumulationRegister_ВыручкаИСебестоимостьПродаж_RecordType', ITEM_ANALYTICS, PARTNER_ANALYTICS, 'Period', 'Active', 'Склад', 'Склад_Type', 'Количество', 'ХозяйственнаяОперация'),
            'item_keys': ('Catalog_КлючиАналитикиУчетаНоменклатуры', 'Ref_Key', GOOD, FEATURE),
            'partner_keys': ('Catalog_КлючиАналитикиУчетаПоПартнерам', 'Ref_Key', ORG),
            'cost': (COST + '/Balance()', ITEM_ANALYTICS, ORG, 'РазделУчета', 'КоличествоBalance', 'СтоимостьУпрBalance', 'ДопРасходыУпрBalance'),
            'orders': (ORDERS + '/Balance()', GOOD, FEATURE, WAREHOUSE, 'ЗаказПоставщику_Key', 'КодСтроки', 'КПоступлениюBalance'),
            'order_heads': ('Document_ЗаказПоставщику', 'Ref_Key', ORG, 'Posted', 'DeletionMark', 'ДатаПоступления'),
            'order_lines': ('Document_ЗаказПоставщику_Товары', 'Ref_Key', 'КодСтроки', 'ДатаПоступления', 'Отменено'),
        }
        published = {row.get('name') for row in self.records('')}
        for name, characteristic in (('ЦеныНоменклатуры', FEATURE), ('ЦеныНоменклатуры25', 'ХарактеристикаЦО_Key')):
            resource = 'InformationRegister_' + name + '_RecordType'
            if resource in published:
                queries[characteristic] = (resource, GOOD, characteristic, 'Active', 'Period', 'ВидЦены_Key', 'Цена', 'Валюта_Key', 'Упаковка_Key')
        if not any(key in queries for key in (FEATURE, 'ХарактеристикаЦО_Key')):
            raise ValueError('В OData не опубликован регистр цен номенклатуры')

        warehouse_filter = f"{WAREHOUSE} eq guid'{warehouse}'"
        org_filter = f"{ORG} eq guid'{organization}'"
        historical = f"Period ge datetime'{start}T00:00:00' and Period lt datetime'{today}T00:00:00'"
        current_price = f"ВидЦены_Key eq guid'{price_type}' and Period lt datetime'{today + timedelta(days=1)}T00:00:00'"
        filters = {
            'stock': warehouse_filter, 'opening': warehouse_filter,
            'movements': f'{warehouse_filter} and {historical}', 'sales': historical,
            'cost': org_filter, 'orders': warehouse_filter, 'order_heads': org_filter,
            FEATURE: current_price, 'ХарактеристикаЦО_Key': current_price,
        }

        def fetch(entry):
            key, arguments = entry
            return key, self.records(*arguments, filter_by=filters.get(key))

        with ThreadPoolExecutor(max_workers=4) as pool:
            data = dict(pool.map(fetch, queries.items()))

        rubles = {row['Ref_Key'] for row in data['currencies'] if row['Code'] == '643'}
        item_keys = {row['Ref_Key']: item(row) for row in data['item_keys']}
        partner_keys = {row['Ref_Key']: row[ORG] for row in data['partner_keys']}
        categories = {row['Ref_Key']: row['Description'] for row in data['categories']}
        features = {row['Ref_Key']: row['Description'] for row in data['features'] if not row['DeletionMark']}

        stock, opening, movements, sold = (defaultdict(Decimal) for _ in range(4))
        for resource, totals in (('stock', stock), ('opening', opening)):
            for row in data[resource]:
                if row[WAREHOUSE] == warehouse:
                    totals[item(row)] += number(row['ВНаличииBalance'])
        dates = set(day_keys)
        for row in data['movements']:
            period = day(row)
            if row['Active'] and period in dates and row[WAREHOUSE] == warehouse:
                direction = {'Receipt': 1, 'Expense': -1}.get(row['RecordType'])
                if direction is None:
                    raise ValueError('Неизвестный вид складского движения УТ')
                movements[item(row), period] += direction * number(row['ВНаличии'])
        for row in data['sales']:
            period = day(row)
            key = item_keys.get(row[ITEM_ANALYTICS])
            if (key and row['Active'] and period in dates and row['Склад'] == warehouse
                    and row['Склад_Type'] == 'StandardODATA.Catalog_Склады'
                    and partner_keys.get(row[PARTNER_ANALYTICS]) == organization
                    and row['ХозяйственнаяОперация'] in SALE_OPERATIONS | RETURN_OPERATIONS):
                quantity = number(row['Количество'])
                sold[key, period] += -abs(quantity) if row['ХозяйственнаяОперация'] in RETURN_OPERATIONS else quantity

        cost_amount, cost_units = defaultdict(Decimal), defaultdict(Decimal)
        for row in data['cost']:
            key = item_keys.get(row[ITEM_ANALYTICS])
            if key and row[ORG] == organization and row['РазделУчета'] == 'ТоварыНаСкладах':
                cost_units[key] += number(row['КоличествоBalance'])
                cost_amount[key] += number(row['СтоимостьУпрBalance']) + number(row['ДопРасходыУпрBalance'])

        prices = {}
        for priority, characteristic in enumerate((FEATURE, 'ХарактеристикаЦО_Key')):
            for row in data.get(characteristic, []):
                if (row['Active'] and row['ВидЦены_Key'] == price_type and row['Валюта_Key'] in rubles
                        and row['Упаковка_Key'] in (EMPTY, '', None) and day(row) <= str(today)):
                    key = item(row, characteristic)
                    candidate = (row['Period'], priority, number(row['Цена']))
                    if key not in prices or candidate[:2] > prices[key][:2]:
                        prices[key] = candidate

        heads = {row['Ref_Key']: row for row in data['order_heads']
                 if row['Posted'] and not row['DeletionMark'] and row[ORG] == organization}
        lines = {(row['Ref_Key'], row['КодСтроки']): row for row in data['order_lines'] if not row['Отменено']}
        incoming = defaultdict(Decimal)
        for row in data['orders']:
            order = row['ЗаказПоставщику_Key']
            line = lines.get((order, row['КодСтроки']))
            amount = number(row['КПоступлениюBalance'])
            if order in heads and line and row[WAREHOUSE] == warehouse and amount > 0:
                period = day(line, 'ДатаПоступления')
                if not period or period.startswith('0001'):
                    period = day(heads[order], 'ДатаПоступления')
                if period >= str(today):
                    incoming[item(row), period] += amount
        supplies = defaultdict(list)
        for (key, period), amount in sorted(incoming.items()):
            supplies[key].append(dict(date=period, quantity=float(amount)))

        products = []
        goods = {row['Ref_Key']: row for row in data['goods'] if not row['DeletionMark'] and not row['IsFolder']}
        for key in sorted(set(stock) | set(opening) | {key for key, _ in sold} | set(supplies)):
            ref, feature = key
            row = goods.get(ref)
            price = prices.get(key) or prices.get((ref, EMPTY))
            if not row or (feature != EMPTY and feature not in features) or not price or price[2] <= 0:
                continue
            remaining = opening[key]
            sales = []
            for period in day_keys:
                before = remaining
                remaining += movements[key, period]
                units = max(0, sold[key, period])
                sales.append(dict(date=period, units=float(units), available=before > 0 or remaining > 0 or units > 0))
            display = row['Description']
            if feature != EMPTY:
                display += ' · ' + features[feature]
            products.append(dict(
                sku='ut:' + ref + (':' + feature if feature != EMPTY else ''),
                article=row['Артикул'] or row['Code'], name=display,
                category=categories.get(row['ВидНоменклатуры_Key']) or 'Без категории',
                accounting=float(price[2]), stock=float(max(0, stock[key])), sales=sales,
                cost=float(cost_amount[key] / cost_units[key])
                if cost_units[key] > 0 and cost_amount[key] > 0 else None,
                supplies=supplies[key]))
        if not products:
            raise ValueError('Нет товаров с рублёвой ценой за единицу для выбранного склада и вида цены')
        return sorted(products, key=lambda row: (row['name'].casefold(), row['sku']))
