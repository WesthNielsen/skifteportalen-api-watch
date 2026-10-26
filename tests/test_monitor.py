import unittest
from monitor import normalize_page, normalize_document, diff


class MonitorTests(unittest.TestCase):
    def test_page_extracts_documents_ignores_nav(self):
        html = b'''<html><nav><a href="/hidden.pdf">Hidden</a></nav><main>
        <h1>API</h1><a href="/media/spec.pdf">Integration guide</a>
        <a href="/other">Contact</a></main></html>'''
        text, links = normalize_page(html, 'https://www.domstol.dk/page')
        self.assertIn('API', text)
        self.assertNotIn('Hidden', text)
        self.assertEqual(list(links), ['https://www.domstol.dk/media/spec.pdf'])

    def test_json_ignores_key_order(self):
        a, _ = normalize_document('https://x/spec.json', b'{"a":1,"b":2}')
        b, _ = normalize_document('https://x/spec.json', b'{"b":2, "a":1}')
        self.assertEqual(a, b)

    def test_diff(self):
        a = {'url1': {'hash': 'a'}, 'url2': {'hash': 'b'}}
        b = {'url1': {'hash': 'c'}, 'url3': {'hash': 'd'}}
        self.assertEqual(diff(a, b), [('CHANGED', 'url1'), ('REMOVED', 'url2'), ('ADDED', 'url3')])


if __name__ == '__main__':
    unittest.main()
