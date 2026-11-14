"""Only the generic H3b refusal, not H3 recovery, belongs in H5."""
from unittest.mock import Mock

import pytest
from test_agent import decision
from test_agent import runner as baseline_runner

from jev_ultrafast import agent as loop
from jev_ultrafast.browser import StalePage


@pytest.fixture
def runner():
    return baseline_runner.__wrapped__()


@pytest.mark.parametrize('raises', [False, True])
@pytest.mark.parametrize('command', ['act', 'tick'])
def test_stale_blocked_stops_with_explicit_uncertainty(runner, monkeypatch, raises, command):
    state = runner.state
    state['decision'] = decision('BLOCKED')
    choose = Mock(return_value=decision('BLOCKED'))
    monkeypatch.setattr(loop, 'choose', choose)
    stale = StalePage('navigation') if raises else False
    state['browser'].fresh.side_effect = [True, stale] if command == 'tick' else [stale]
    result = runner.command(command, {'fingerprint': state['page']['fingerprint']})
    assert result['status'] == 'blocked'
    assert result['plan_index'] == 0
    assert result['decision'] is None
    assert result['stop_reason'] == {
        'code': 'blocked_freshness_unconfirmed',
        'message': 'Stopped safely because the BLOCKED decision could not be freshness-validated; '
                   'current task feasibility is unknown.',
    }
    assert choose.call_count == int(command == 'tick')
    state['browser'].observe.assert_not_called()
    state['browser'].act.assert_not_called()
    state['browser'].fresh.side_effect = StalePage('still navigating')
    for next_command in ['predict', 'tick']:
        with pytest.raises(ValueError, match='stopped'):
            runner.command(next_command)
        assert state['status'] == 'blocked'
    assert choose.call_count == int(command == 'tick')
    state['browser'].observe.assert_not_called()
    assert list(runner.run()) == []


@pytest.mark.parametrize('raises', [False, True])
@pytest.mark.parametrize('command', ['act', 'tick'])
def test_stale_done_never_completes(runner, monkeypatch, raises, command):
    state = runner.state
    state['decision'] = decision('DONE')
    monkeypatch.setattr(loop, 'choose', Mock(return_value=decision('DONE')))
    stale = StalePage('navigation') if raises else False
    state['browser'].fresh.side_effect = [True, stale] if command == 'tick' else [stale]
    if command == 'act':
        with pytest.raises(StalePage):
            runner.command(command, {'fingerprint': state['page']['fingerprint']})
    else:
        runner.command(command)
        state['browser'].observe.assert_called_once()
    assert state['status'] == 'ready'
    assert 'stop_reason' not in state
    state['browser'].act.assert_not_called()


@pytest.mark.parametrize('selected', ['DONE', 'BLOCKED'])
def test_fresh_terminal_unchanged(runner, selected):
    runner.state['decision'] = decision(selected)
    result = runner.command('act', {'fingerprint': runner.state['page']['fingerprint']})
    assert result['status'] == ('done' if selected == 'DONE' else 'blocked')
    assert 'stop_reason' not in result


def test_unrelated_terminal_error_not_converted_to_refusal(runner):
    runner.state['decision'] = decision('BLOCKED')
    runner.state['browser'].fresh.side_effect = RuntimeError('transport')
    with pytest.raises(RuntimeError, match='transport'):
        runner.command('act', {'fingerprint': runner.state['page']['fingerprint']})
    assert 'stop_reason' not in runner.state
