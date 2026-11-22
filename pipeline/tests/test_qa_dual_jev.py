"""The Jev SDK glue the staged audit (spec 063) reuses. No network.

The 061 measured reader prompt and its pinned digests were retired by spec 069 F5."""
import unittest
from types import SimpleNamespace as NS

from pipeline.src import qa_dual_jev as jev


class SdkGlueTest(unittest.TestCase):
    def test_raw_answers_keep_everything_the_api_said(self):
        response = NS(choices={'verdict': NS(choice='ok', confidence=0.87, probabilities={'ok': 0.9, 'defect': 0.1})},
                      nouls={'supported': NS(noul=0.8)})
        self.assertEqual(jev.raw_answers(response), {
            'verdict': {'type': 'choice', 'choice': 'ok', 'confidence': 0.87,
                        'probabilities': {'ok': 0.9, 'defect': 0.1}},
            'supported': {'type': 'noul', 'noul': 0.8}})

    def test_exceptions_map_to_the_failure_classes(self):
        from typesafe_sdk import (TypeSafeAPIConnectionError, TypeSafeAuthenticationError,
                                  TypeSafePermissionDeniedError, TypeSafeRateLimitError)
        self.assertTrue(jev.classify_exception(jev.ConfigError('sin clave')).startswith('config:'))
        self.assertTrue(jev.classify_exception(TypeSafeAuthenticationError(status=401, body=None, headers={}, message='k'))
                        .startswith('config:'))
        self.assertTrue(jev.classify_exception(TypeSafePermissionDeniedError(status=403, body=None, headers={}, message='k'))
                        .startswith('config:'))
        self.assertTrue(jev.classify_exception(TypeSafeRateLimitError(status=429, body=None, headers={}, message='slow'))
                        .startswith('api-error 429:'))
        self.assertTrue(jev.classify_exception(TypeSafeAPIConnectionError('down')).startswith('TypeSafeAPIConnectionError:'))

    def test_client_needs_the_key_before_touching_the_sdk(self):
        from unittest.mock import patch
        with patch.dict('os.environ', {jev.API_KEY_ENV: '   '}):
            with self.assertRaises(jev.ConfigError):
                jev.JevClient()
        with patch.dict('os.environ', {jev.API_KEY_ENV: 'ts-test-key'}):
            client = jev.JevClient()
            self.assertEqual(client.model, jev.JEV_MODEL)
            client.close()


if __name__ == '__main__':
    unittest.main()
