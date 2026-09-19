"""Offline joint-choice contracts; transport is replaced, policy is real."""
import json
from copy import deepcopy
from unittest.mock import Mock

import httpx
import pytest
from test_agent import choice, page

from jev_ultrafast import agent as loop
from jev_ultrafast import model
from jev_ultrafast.questions import NEXT_ACTION


def observed():
    p = page()
    p['actions'] += [
        {'id': 's1', 'kind': 'select', 'node': 30, 'label': 'Color → Blue', 'value': 'blue'},
        {'id': 's2', 'kind': 'select', 'node': 30, 'label': 'Color → Red', 'value': 'red'},
        {'id': 'scroll_up', 'kind': 'scroll', 'label': 'Scroll up'},
        {'id': 'scroll_down', 'kind': 'scroll', 'label': 'Scroll down'},
    ]
    return p


def transport(monkeypatch, selected, mutate=None):
    def post(url, key, body):
        assert url == 'https://api.typesafe.ai/v1/systemone'
        assert key == 'offline'
        assert set(body['questions']) == {'action'}
        head = body['questions']['action']
        answer = choice(head['criteria'], selected)
        if mutate:
            mutate(answer)
        return {'model': 'offline', 'answers': {'action': answer}, 'usage': {'test': 1}}
    mock = Mock(side_effect=post)
    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline')
    monkeypatch.setattr(model, 'post_json', mock)
    return mock


@pytest.mark.parametrize('selected,operation,target,action', [
    ('CLICK:1', 'CLICK', '1', 'e2'), ('CLICK:2', 'CLICK', '2', 'e3'),
    ('TYPE_TEXT:1', 'TYPE_TEXT', '1', 'e1'),
    ('SELECT:3:1', 'SELECT', '3:1', 's1'), ('SELECT:3:2', 'SELECT', '3:2', 's2'),
    ('SCROLL_UP', 'SCROLL_UP', None, 'scroll_up'),
    ('SCROLL_DOWN', 'SCROLL_DOWN', None, 'scroll_down'),
    ('WAIT', 'WAIT', None, 'wait'), ('DONE', 'DONE', None, 'DONE'),
    ('BLOCKED', 'BLOCKED', None, 'BLOCKED'),
])
def test_complete_observed_pairs_one_request(monkeypatch, selected, operation, target, action):
    post = transport(monkeypatch, selected)
    p = observed()
    original = deepcopy(p)
    d = model.choose_joint(p, 'Entire original goal; all requirements', [])
    assert (d['operation'], d['target'], d['choice']) == (operation, target, action)
    assert post.call_count == 1
    assert p == original
    head = d['request']['questions']['action']
    assert len(head['criteria']) == 10
    assert head['instructions']['goal'] == 'Entire original goal; all requirements'
    assert NEXT_ACTION in head['instructions']['rules']
    assert d['chooser'] == 'joint' and d['joint_choice_count'] == 10
    assert d['target_confidence'] is None
    assert d['target_probabilities'] == {}
    assert d['confidence_scope'] == 'joint_action'
    assert d['operation_probabilities_source'] == 'sum_of_joint_probabilities'
    assert d['probabilities'][action] == 1
    assert d['joint_probabilities'][selected] == 1
    assert loop.choose is model.choose_joint


@pytest.mark.parametrize('mutation', [
    lambda a: a.update(choice='CLICK:999'),
    lambda a: a.update(choice='TYPE_TEXT:2'),
    lambda a: a['probabilities'].update({'invented': 0}),
    lambda a: a['probabilities'].pop('DONE'),
    lambda a: a['probabilities'].update({'DONE': float('nan')}),
    lambda a: a.update(confidence=True),
    lambda a: a.update(choice='DONE'),
])
def test_invalid_joint_fails_closed_without_retry(monkeypatch, mutation):
    post = transport(monkeypatch, 'CLICK:1', mutation)
    with pytest.raises(ValueError, match='Invalid TypeSafe'):
        model.choose_joint(observed(), 'Goal', [])
    assert post.call_count == 1


def test_marginals_are_sums_not_independent_heads(monkeypatch):
    def probabilities(a):
        a['probabilities'] = dict.fromkeys(a['probabilities'], 0)
        a['probabilities'].update({'CLICK:1': .4, 'CLICK:2': .3, 'DONE': .3})
        a['confidence'] = .8
    transport(monkeypatch, 'CLICK:1', probabilities)
    d = model.choose_joint(observed(), 'Goal', [])
    assert d['operation_probabilities']['CLICK'] == pytest.approx(.7)
    assert d['probabilities']['e2'] == .4
    assert d['confidence'] == .8
    assert d['target_confidence'] is None


@pytest.mark.parametrize('wait', [False, True])
def test_empty_targets_offer_only_observed_controls_and_terminals(monkeypatch, wait):
    p = page()
    p['actions'] = p['actions'][-1:] if wait else []
    transport(monkeypatch, 'BLOCKED')
    d = model.choose_joint(p, 'Goal', [])
    assert set(d['joint_probabilities']) == ({'WAIT', 'DONE', 'BLOCKED'} if wait else {'DONE', 'BLOCKED'})


@pytest.mark.parametrize('total', [64, 65])
def test_real_post_json_uses_one_http_dispatch(monkeypatch, total):
    p = page()
    p['actions'] = [dict(id=f'e{i}', kind='click', node=i, label=f'Button {i}') for i in range(total - 3)]
    p['actions'].append(dict(id='wait', kind='wait', label='Wait'))
    requests = []

    def dispatch(request):
        requests.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={'model': 'offline', 'answers': {
            name: choice(head['criteria'], 'BLOCKED' if name in {'action', 'operation'} else '1')
            for name, head in body['questions'].items()
        }})

    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline')
    with httpx.Client(transport=httpx.MockTransport(dispatch)) as client:
        monkeypatch.setattr(model, 'CLIENT', client)
        d = model.choose_joint(p, 'Goal', [])
    assert d['choice'] == 'BLOCKED'
    assert len(requests) == 1


@pytest.mark.parametrize('total', [63, 64, 65, 90])
def test_threshold_and_original_fallback_parity(monkeypatch, total):
    p = page()
    p['actions'] = [dict(id=f'e{i}', kind='click', node=i, label=f'Button {i}') for i in range(total - 3)]
    p['actions'].append(dict(id='wait', kind='wait', label='Wait'))
    calls = []
    def post(url, key, body):
        calls.append(deepcopy(body))
        return {'model': 'offline', 'answers': {
            name: choice(head['criteria'], 'BLOCKED' if name in {'action', 'operation'} else '1')
            for name, head in body['questions'].items()
        }}
    monkeypatch.setenv('TYPESAFE_API_KEY', 'offline')
    monkeypatch.setattr(model, 'post_json', post)
    d = model.choose_joint(p, 'Full goal', [{'action': 'previous'}])
    assert len(calls) == 1
    assert d['joint_choice_count'] == total
    if total <= 64:
        assert set(calls[0]['questions']) == {'action'}
        assert d['chooser'] == 'joint'
    else:
        baseline = model.choose(p, 'Full goal', [{'action': 'previous'}])
        assert calls[0] == calls[1]
        assert d['chooser'] == 'multifactor_fallback'
        for k in baseline:
            if k != 'latency_ms':
                assert d[k] == baseline[k]
