import json
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from jobFilter.hiringcafe import HiringCafeClient, HiringCafeError


def html(props):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(
        {'buildId': 'new-build', 'props': {'pageProps': props}}) + '</script>'


def response(props):
    return Mock(headers={'content-type': 'application/json'}, json=lambda: {'pageProps': props})


class HiringCafeTests(unittest.TestCase):
    def setUp(self):
        self.client = HiringCafeClient(delay=0)
        self.client._build_id = 'build'
        self.state = {'searchQuery': 'software engineer', 'dateFetchedPastNDays': 2}
        self.props = {'ssrHits': [{'id': 'a'}], 'ssrIsLastPage': True}

    def test_classic_route_preserves_search_and_page(self):
        with patch.object(self.client, '_get', return_value=response(self.props)) as get:
            self.assertEqual(self.client.search_page(self.state, 2), self.props)
        path, params = get.call_args.args
        self.assertEqual(path, '/_next/data/build/classic.json')
        self.assertEqual(json.loads(params['searchState']), self.state)
        self.assertEqual(params['page'], '2')
        url = urlsplit(self.client.search_url(self.state, 2))
        self.assertEqual(url.path, '/classic')
        self.assertEqual(json.loads(parse_qs(url.query)['searchState'][0]), self.state)

    def test_redirect_json_falls_back_to_classic_html_instead_of_zero_jobs(self):
        redirect = {'__N_REDIRECT': '/classic', '__N_REDIRECT_STATUS': 307}
        def get(path, params=None, **kwargs):
            if path.endswith('.json'):
                return response(redirect)
            return Mock(text=html(self.props))
        with patch.object(self.client, '_get', side_effect=get) as fetch:
            self.assertEqual(list(self.client.search(self.state)), [{'id': 'a'}])
        self.assertEqual(fetch.call_args.args[0], '/classic')
        self.assertEqual(json.loads(fetch.call_args.args[1]['searchState']), self.state)

    def test_missing_results_or_upstream_error_are_not_empty_success(self):
        for props in ({}, {'__N_REDIRECT': '/classic'}, {'ssrHits': []},
                      {'ssrHits': [], 'ssrIsLastPage': False},
                      {'ssrHits': None, 'ssrIsLastPage': True},
                      {'ssrError': 'backend unavailable', **self.props}):
            with self.subTest(props=props), patch.object(self.client, '_get', return_value=Mock(
                    headers={'content-type': 'application/json'}, json=lambda: {'pageProps': props}, text=html(props))):
                with self.assertRaises(HiringCafeError):
                    list(self.client.search(self.state))

    def test_pagination_and_genuine_empty_results(self):
        pages = [{'ssrHits': [{'id': 'a'}], 'ssrIsLastPage': False},
                 {'ssrHits': [{'id': 'b'}], 'ssrIsLastPage': True}]
        with patch.object(self.client, 'search_page', side_effect=pages) as get:
            self.assertEqual(list(self.client.search(self.state)), [{'id': 'a'}, {'id': 'b'}])
            self.assertEqual([c.args[1] for c in get.call_args_list], [0, 1])
        with patch.object(self.client, 'search_page', return_value={'ssrHits': [], 'ssrIsLastPage': True}):
            self.assertEqual(list(self.client.search(self.state)), [])

    def test_stale_build_is_refreshed(self):
        with patch.object(self.client, '_get', side_effect=[HiringCafeError('404'), Mock(text=html({})), response(self.props)]) as get:
            self.assertEqual(self.client.search_page(self.state), self.props)
        self.assertEqual([c.args[0] for c in get.call_args_list],
                         ['/_next/data/build/classic.json', '/classic', '/_next/data/new-build/classic.json'])
