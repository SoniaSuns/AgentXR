import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from store import SessionStore


class PhotoHistoryTests(unittest.TestCase):
    def test_old_images_survive_record_count_and_remain_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=SessionStore(tmp)
            task={'goal':'test','steps':[],'plan':'','domain':'auto'}
            for i in range(30):
                store.save('history-session',task,record={'id':f'record-{i:04}', 'frame_id':f'frame-{i}', 'captured_at':i+1},image=b'photo')
            self.assertEqual(len(store.detail('history-session')['records']),30)
            historical=store.historical_photo('history-session','record-0000',365*86400)
            self.assertEqual(historical['image'],b'photo')
            self.assertTrue(historical['historical_only'])
            self.assertGreater(historical['age_seconds'],300)
            with self.assertRaises(ValueError):
                store.historical_photo('different-session','record-0000',365*86400)
            with self.assertRaises(ValueError):
                store.historical_photo('history-session','record-0029',10)
            store.delete_record('history-session','record-0000')
            with self.assertRaises(ValueError):
                store.historical_photo('history-session','record-0000',365*86400)
            self.assertFalse((Path(tmp)/'history-session/images/frame-0.jpg').exists())

    def test_explicit_retention_limit_is_still_supported(self):
        with tempfile.TemporaryDirectory() as tmp, patch('config.M',return_value=2):
            store=SessionStore(tmp)
            for i in range(3):
                store.save('history-session',{},record={'id':f'record-{i:04}','frame_id':f'frame-{i}','captured_at':i+1},image=b'photo')
            self.assertEqual(len(store.detail('history-session')['records']),2)
            self.assertFalse((Path(tmp)/'history-session/images/frame-0.jpg').exists())
