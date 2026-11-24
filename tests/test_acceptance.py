from datetime import timedelta
from http.server import HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from unittest import TestCase, main
from unittest.mock import patch

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
                self.action('settings', url=f'http://127.0.0.1:{source.server_port}/snapshot',
                            delay=2, max_drop=3)
                self.assertIn('8 товаров', self.action('check'))
                self.action('sync')
            finally:
                source.shutdown()
                thread.join()
        items = self.app.view(self.now)['products']
        self.assertEqual(len(items), 8)
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
        self.assertEqual(len(self.app.data['products']), 8)

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
        self.action('mode', sku='KM-102', auto=True)
        self.action('sync')
        self.assertFalse(self.app.data['history'])
        self.rule('Посуда', down=4)
        event = self.app.data['history'][-1]
        self.assertEqual((event['sku'], event['method'], event['status']),
                         ('KM-102', 'Автоматически', 'pending'))
        stale = self.rec('KM-101')['id']
        self.action('manual', sku='KM-101', price=1040)
        self.app.tick(self.now + timedelta(seconds=9))
        self.assertEqual(event['status'], 'rejected')
        self.assertEqual(self.app.data['market']['KM-102']['price'], 1000)
        self.assertEqual(self.rec('KM-101')['price'], 1000)
        with self.assertRaises(ValueError):
            self.action('approve', sku='KM-101', id=stale)
        self.action('sync')
        self.assertEqual(self.rec('KM-101')['price'], 1040)
        self.assertEqual(self.app.data['observed']['KM-101']['anchor'], 1040)
        self.assertEqual(sum(e['sku'] == 'KM-102' for e in self.app.data['history']), 1)

    def test_manager_rejection(self):
        sku = 'TX-204'
        recommendation = self.rec(sku)['id']
        self.action('reject', sku=sku, id=recommendation)
        with self.assertRaises(ValueError):
            self.action('approve', sku=sku, id=recommendation)
        self.assertEqual(self.app.data['history'][-1]['status'], 'dismissed')


if __name__ == '__main__':
    main()
