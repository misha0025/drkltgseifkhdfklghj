from base64 import b64encode
from datetime import timedelta
from http.server import HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase, main
from unittest.mock import patch
from uuid import uuid4

from app import App, clock
from demo import Handler


class Acceptance(TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        state_patch = patch('app.STATE', Path(folder.name) / 'state.json')
        state_patch.start()
        self.addCleanup(state_patch.stop)
        self.app = App()
        self.now = clock()

    def action(self, action, **fields):
        return self.app.action(dict(action=action, **fields), self.now)

    def rec(self, sku):
        return self.app.data['recommendations'][sku]

    def rule(self, category, **changes):
        fields = self.app.data['rules'][category] | changes
        self.action('rules', category=category, **fields)

    def test_catalog_and_http_integration(self):
        with HTTPServer(('127.0.0.1', 0), Handler) as source:
            thread = Thread(target=source.serve_forever)
            thread.start()
            try:
                self.action('settings', mode='json', url=f'http://127.0.0.1:{source.server_port}/snapshot',
                            delay=2, max_drop=3)
                self.assertIn('60 товаров', self.action('check'))
                self.action('sync')
            finally:
                source.shutdown()
                thread.join()
        items = self.app.view(self.now)['products']
        self.assertEqual(len(items), 60)
        self.assertEqual(sum(p['rec']['kind'] in ('up', 'down') for p in items), 42)
        self.assertEqual(self.rec('KM-101')['cover'], 3)
        self.assertEqual(self.rec('KM-101')['supply']['quantity'], 80)
        self.assertEqual(self.rec('TX-201')['available'], 15)
        self.assertIsNone(self.rec('TX-201')['supply'])
        self.assertEqual(self.rec('TX-202')['kind'], 'same')
        self.assertEqual(self.rec('TX-203')['kind'], 'blocked')
        self.assertEqual(self.rec('KM-104')['kind'], 'blocked')
        self.assertEqual(self.rec('TX-204')['kind'], 'down')
        self.assertIn('маржи', self.rec('KM-103')['reason'])
        previous_sync = self.app.data['synced']
        with self.assertRaises(ValueError):
            self.action('sync')
        self.assertEqual(self.app.data['synced'], previous_sync)
        self.assertEqual(len(self.app.data['products']), 60)

    def test_approval_delay_and_restart(self):
        sku = 'KM-101'
        self.rule('Посуда', up=3)
        self.assertEqual(self.rec(sku)['proposed'], 1030)
        recommendation = self.rec(sku)['id']
        self.action('approve', sku=sku, id=recommendation)
        with self.assertRaises(ValueError):
            self.action('approve', sku=sku, id=recommendation)
        self.app = App()
        self.assertEqual(self.app.data['market'][sku]['price'], 1000)
        self.app.tick(self.now + timedelta(seconds=9))
        self.assertEqual(self.app.data['market'][sku]['price'], 1030)
        self.assertEqual(self.app.data['history'][-1]['status'], 'applied')
        self.assertIn('интервал', self.rec(sku)['reason'])
        self.assertEqual(self.app.view(self.now)['reports'][0]['applied'], 1)

    def test_automatic_mode_rejection_and_manual_revision(self):
        self.rule('Посуда', mode='auto')
        self.action('sync')
        self.assertFalse(self.app.data['history'])
        self.rule('Посуда', down=4)
        self.assertEqual([e['sku'] for e in self.app.data['history']], ['KM-102'])
        event = self.app.data['history'][-1]
        self.assertEqual((event['sku'], event['method'], event['status']),
                         ('KM-102', 'Автоматически', 'pending'))
        stale = self.rec('KM-101')['id']
        self.action('manual', sku='KM-101', price=1040)
        self.app.tick(self.now + timedelta(seconds=9))
        self.assertEqual(event['status'], 'rejected')
        self.assertEqual(self.app.data['market']['KM-102']['price'], 1000)
        self.assertEqual(self.rec('KM-101')['price'], 1000)
        product = next(p for p in self.app.view(self.now)['products'] if p['sku'] == 'KM-101')
        self.assertEqual(product['price'], 1040)
        self.assertTrue(product['needs_sync'])
        with self.assertRaises(ValueError):
            self.action('approve', sku='KM-101', id=stale)
        self.action('sync')
        self.assertEqual(self.rec('KM-101')['price'], 1040)
        self.assertEqual(self.app.data['observed']['KM-101']['anchor'], 1040)
        product = next(p for p in self.app.view(self.now)['products'] if p['sku'] == 'KM-101')
        self.assertFalse(product['needs_sync'])
        self.assertEqual(sum(e['sku'] == 'KM-102' for e in self.app.data['history']), 1)

    def test_authenticated_publication(self):
        expected = 'Basic ' + b64encode('operator:secret'.encode()).decode()

        class Published(Handler):
            def do_GET(self):
                if self.path != '/trade/hs/repricer/snapshot':
                    self.send_error(404)
                elif self.headers.get('Authorization') != expected:
                    self.send_error(401)
                else:
                    self.path = '/snapshot'
                    super().do_GET()

        with HTTPServer(('127.0.0.1', 0), Published) as source:
            thread = Thread(target=source.serve_forever)
            thread.start()
            try:
                fields = dict(mode='json', url=f'http://127.0.0.1:{source.server_port}/trade',
                              service='hs/repricer/snapshot', login='operator', delay=2, max_drop=3)
                self.action('settings', **fields, password='wrong')
                with self.assertRaisesRegex(ValueError, '401'):
                    self.action('check')
                self.action('settings', **fields, password='secret')
                self.app = App()
                self.action('settings', **fields, password='')
                self.action('sync')
                view = self.app.view(self.now)
                self.assertTrue(view['has_password'])
                self.assertNotIn('password', view)
                self.assertEqual(len(view['products']), 60)
            finally:
                source.shutdown()
                thread.join()

    def test_connection_form_defaults_to_standard_ut(self):
        self.action('settings', url='https://example.test/ut/ru/', login='operator',
                    password='', warehouse='Склад', organization='Организация',
                    price_type='Розничная', delay=2, max_drop=3)
        with patch('app.accounting_snapshot', return_value=[]) as source:
            self.action('check')
        source.assert_called_once_with('https://example.test/ut/ru/', service='', login='operator',
                                       password='', mode='ut', warehouse='Склад',
                                       organization='Организация', price_type='Розничная')

    def test_schedule_runs_once_per_day_and_survives_restart(self):
        start = self.now.replace(hour=9, minute=0, second=0, microsecond=0)
        with patch.object(self.app, 'sync', wraps=self.app.sync) as sync:
            self.action('schedule', enabled=True, time='09:00')
            self.app.run_schedule(start - timedelta(seconds=1))
            sync.assert_not_called()
            self.app.run_schedule(start)
            self.app.run_schedule(start + timedelta(minutes=1))
            sync.assert_called_once_with(start)
        self.app = App()
        self.assertEqual(self.app.next_sync(start), (start + timedelta(days=1)).isoformat())
        with patch('app.accounting_snapshot', side_effect=ValueError('Ошибка источника')) as source:
            tomorrow = start + timedelta(days=1)
            self.app.run_schedule(tomorrow)
            self.app.run_schedule(tomorrow + timedelta(minutes=1))
            source.assert_called_once()
            self.assertEqual(self.app.data['synced'], start.isoformat())
            self.assertEqual(self.app.data['error'], 'Ошибка источника')
            self.assertEqual(self.app.view(tomorrow)['schedule']['attempted'], tomorrow.isoformat())

    def test_batch_is_atomic_replay_safe_and_applies_through_wb(self):
        recommendation = self.rec('KM-101')
        item = dict(sku='KM-101', price=recommendation['proposed'], source='recommendation',
                    revision=0, recommendation_id=recommendation['id'])
        manual = dict(sku='TX-202', price=1050, source='manual', revision=1)
        task = uuid4()
        with self.assertRaisesRegex(ValueError, 'TX-202'):
            self.action('send_batch', task_id=str(task), items=[item, manual])
        self.assertEqual(self.app.data['history'], [])

        manual['revision'] = 0
        fields = dict(task_id=str(task), items=[item, manual])
        self.action('send_batch', **fields)
        self.app = App()
        self.action('send_batch', **fields)
        self.assertEqual(len(self.app.data['history']), 2)
        self.assertEqual({e['task'] for e in self.app.data['history']}, {str(task)})
        self.assertTrue(all(e['status'] == 'pending' for e in self.app.data['history']))
        self.assertEqual(self.app.data['market']['TX-202']['price'], 1000)
        self.app.tick(self.now + timedelta(seconds=9))
        self.assertEqual(self.app.data['market']['TX-202']['anchor'], 1050)
        self.assertEqual(self.app.data['market']['KM-101']['price'], 1020)
        self.assertEqual(self.app.data['market']['KM-101']['anchor'], 1000)
        self.assertTrue(all(e['status'] == 'applied' for e in self.app.data['history']))

    def test_manager_rejection(self):
        sku = 'TX-204'
        recommendation = self.rec(sku)['id']
        self.action('reject', sku=sku, id=recommendation)
        with self.assertRaises(ValueError):
            self.action('approve', sku=sku, id=recommendation)
        self.assertEqual(self.app.data['history'][-1]['status'], 'dismissed')


if __name__ == '__main__':
    main()
