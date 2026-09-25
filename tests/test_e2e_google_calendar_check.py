"""全件適用の開始条件を外部書込みなしで診断する。"""
import json
from copy import deepcopy

import pytest

import e2e_google_sync_probe as probe
from tests.test_e2e_google_sync_probe import Scenario


@pytest.mark.parametrize('statuses,expected', [([], 'empty'), (['confirmed'], 'active'),
    (['tentative'], 'active'), (['cancelled'], 'deleted'), (['confirmed', 'cancelled'], 'mixed')])
def test_calendar_check_is_read_only(monkeypatch, statuses, expected):
    test = Scenario(monkeypatch)
    before = deepcopy(test.env.STATE_KV.data)
    calls = []

    async def google(method, url, token, payload=None):
        assert method == 'GET' and payload is None
        assert 'showDeleted=true' in url and 'singleEvents=true' in url
        assert 'fields=items(status),nextPageToken' in url
        calls.append(url)
        if len(statuses) > 1 and 'pageToken=' not in url:
            return 200, {'items': [{'status': statuses[0]}], 'nextPageToken': 'next'}
        return 200, {'items': [{'status': value} for value in (statuses[1:] if 'pageToken=' in url else statuses)]}

    monkeypatch.setattr(probe, '_google_request', google)
    status, result = test.call('inspect')
    assert status == 200 and result['status'] == 'calendar_' + expected
    assert result['dirty'] is False
    assert len(calls) == (2 if len(statuses) > 1 else 1)
    assert test.env.STATE_KV.data == before
    assert not test.pages and not test.discord and not test.google
    assert not test.calls


@pytest.mark.parametrize('http,data,error', [(403, {'secret': 'do-not-emit'}, 'http_403'),
    (503, {}, 'http_503'), (200, {}, 'response_invalid'),
    (200, {'items': [{'summary': 'private'}]}, 'response_invalid')])
def test_calendar_check_rejects_failure_without_data(monkeypatch, http, data, error):
    test = Scenario(monkeypatch)
    async def google(*args):
        return http, data
    monkeypatch.setattr(probe, '_google_request', google)
    status, result = test.call('inspect')
    assert status == 503
    assert result['error'] == 'google_sync_calendar_' + error
    assert result['dirty'] is False
    assert 'private' not in json.dumps(result) and 'do-not-emit' not in json.dumps(result)
