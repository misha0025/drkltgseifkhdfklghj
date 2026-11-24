from datetime import date
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlsplit

from adapters import odata_endpoint
from ut_odata import COST, EMPTY, FEATURE, GOOD, ITEM_ANALYTICS, ORG, ORDERS
from ut_odata import PARTNER_ANALYTICS, STOCK, UT, WAREHOUSE


class ODataAdapter(TestCase):
    def test_url_and_pagination(self):
        endpoint = odata_endpoint('http://example.test/ut/ru/')
        urls = []

        def read(url):
            urls.append(url)
            if len(urls) == 1:
                return {'value': [1], 'odata.nextLink': '?$skip=1'}
            return {'value': [2]}

        self.assertEqual(UT(endpoint, read).records('Catalog_Склады', filter_by='DeletionMark eq false'), [1, 2])
        self.assertIn('DeletionMark%20eq%20false', urls[0])
        self.assertEqual(urlsplit(urls[1]).path, urlsplit(urls[0]).path)
        with self.assertRaisesRegex(ValueError, 'ссылка'):
            UT(endpoint, lambda _: {'value': [], 'odata.nextLink': 'http://other.test/'}).records('')

    def test_joins_sales_cost_prices_and_partial_delivery_by_characteristic(self):
        product_key = {GOOD: 'good', FEATURE: 'blue'}
        price_resource = 'InformationRegister_ЦеныНоменклатуры25_RecordType'
        datasets = {
            '': [{'name': price_resource}],
            'Catalog_Валюты': [{'Ref_Key': 'rub', 'Code': '643'}],
            'Catalog_Номенклатура': [{'Ref_Key': 'good', 'Code': 'code', 'Артикул': 'article',
                'Description': 'Товар', 'ВидНоменклатуры_Key': 'kind', 'DeletionMark': False, 'IsFolder': False}],
            'Catalog_ХарактеристикиНоменклатуры': [{'Ref_Key': 'blue', 'Description': 'Синий', 'DeletionMark': False}],
            'Catalog_ВидыНоменклатуры': [{'Ref_Key': 'kind', 'Description': 'Категория'}],
            'Catalog_КлючиАналитикиУчетаНоменклатуры': [dict(product_key, Ref_Key='item-key')],
            'Catalog_КлючиАналитикиУчетаПоПартнерам': [{'Ref_Key': 'partner-key', ORG: 'org'}],
            STOCK + '/Balance()': [dict(product_key, **{WAREHOUSE: 'warehouse', 'ВНаличииBalance': 7})],
            STOCK + "/Balance(Period=datetime'2026-09-26T00:00:00')":
                [dict(product_key, **{WAREHOUSE: 'warehouse', 'ВНаличииBalance': 0})],
            STOCK + '_RecordType': [dict(product_key, **{WAREHOUSE: 'warehouse', 'ВНаличии': 7},
                                          Active=True, RecordType='Receipt', Period='2026-11-24T12:00:00')],
            'AccumulationRegister_ВыручкаИСебестоимостьПродаж_RecordType': [
                {ITEM_ANALYTICS: 'item-key', PARTNER_ANALYTICS: 'partner-key', 'Period': '2026-11-24T13:00:00',
                 'Active': True, 'Склад': 'warehouse', 'Склад_Type': 'StandardODATA.Catalog_Склады',
                 'Количество': 2, 'ХозяйственнаяОперация': 'РеализацияВРозницу'}],
            COST + '/Balance()': [{ITEM_ANALYTICS: 'item-key', ORG: 'org', 'РазделУчета': 'ТоварыНаСкладах',
                                   'КоличествоBalance': 7, 'СтоимостьУпрBalance': 630, 'ДопРасходыУпрBalance': 70}],
            ORDERS + '/Balance()': [dict(product_key, **{WAREHOUSE: 'warehouse', 'ЗаказПоставщику_Key': 'order',
                'КодСтроки': 12, 'КОформлениюBalance': 2, 'КПоступлениюBalance': 1})],
            'Document_ЗаказПоставщику': [{'Ref_Key': 'order', ORG: 'org', 'Posted': True, 'DeletionMark': False}],
            'Document_ЗаказПоставщику_Товары': [{'Ref_Key': 'order', 'КодСтроки': 12,
                                                'ДатаПоступления': '2026-11-28', 'Отменено': False}],
            price_resource: [{GOOD: 'good', 'ХарактеристикаЦО_Key': 'blue', 'Active': True,
                'Period': '2026-11-01', 'ВидЦены_Key': 'retail', 'Цена': 200, 'Валюта_Key': 'rub', 'Упаковка_Key': EMPTY}],
        }
        adapter = UT('http://example.test/ut', None)
        with patch.object(adapter, 'resolve', side_effect=['warehouse', 'org', 'retail']), patch.object(
            adapter, 'records', side_effect=lambda resource, *fields, **kwargs: datasets[resource]
        ):
            rows = adapter.snapshot(date(2026, 11, 25), 'warehouse', 'org', 'retail')
        self.assertEqual(len(rows), 1)
        product = rows[0]
        self.assertEqual((product['sku'], product['article'], product['stock'], product['accounting'], product['cost']),
                         ('ut:good:blue', 'article', 7, 200, 100))
        self.assertEqual(product['sales'][-1], {'date': '2026-11-24', 'units': 2, 'available': True})
        self.assertFalse(product['sales'][-2]['available'])
        self.assertEqual(product['supplies'], [{'date': '2026-11-28', 'quantity': 3}])
