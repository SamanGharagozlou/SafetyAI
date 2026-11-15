"""State machine tests: pure Python, fake clock. pytest -p no:anyio ros2_ws/src/d1_companion/test"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from d1_companion.statemachine import Companion, Config, S, ev  # noqa: E402

PLACES = {
    'home': {'x': 0, 'y': 0, 'yaw': 0, 'label': 'Start point', 'aliases': ['home', 'start', 'reception']},
    'front_2m': {'x': 2, 'y': 0, 'yaw': 0, 'label': 'Front, 2 m ahead', 'aliases': ['front', 'ahead']},
    'back_2m': {'x': -2, 'y': 0, 'yaw': 3.14, 'label': 'Back, 2 m behind', 'aliases': ['back', 'behind']},
    'right_2m': {'x': 0, 'y': -2, 'yaw': -1.57, 'label': 'Right, 2 m', 'aliases': ['right']},
    'left_2m': {'x': 0, 'y': 2, 'yaw': 1.57, 'label': 'Left, 2 m', 'aliases': ['left']},
}
GOOD = {'connected': True, 'mode': 'standing', 'nav_ready': True, 'battery': 0.8, 'localization': {'state': 'localized', 'ready': True}}


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def make(**cfg):
    clk = Clock()
    sm = Companion(PLACES, Config(**cfg), clock=clk)
    sm.handle(ev('gateway_up'))
    sm.handle(ev('robot_state', state=dict(GOOD)))
    return sm, clk


def kinds(effects):
    return [e.kind for e in effects]


def goal(effects):
    g = [e for e in effects if e.kind == 'send_goal']
    return g[0].data if g else None


def go_and_arrive(sm, dest):
    out = sm.handle(ev('select', dest=dest))
    g = goal(out)
    assert g and g['place'] == dest and sm.state == S.NAVIGATING
    sm.handle(ev('goal_received', id=g['id']))
    sm.handle(ev('goal_accepted', id=g['id']))
    sm.handle(ev('goal_progress', id=g['id'], distance=1.0))
    assert sm.goal_status == 'moving' and sm.distance == 1.0
    out = sm.handle(ev('goal_reached', id=g['id']))
    assert sm.state == S.ARRIVED and sm.at == dest and 'say' in kinds(out)
    return g


def test_join_and_single_guest():
    sm, _ = make()
    assert sm.state == S.IDLE
    out = sm.handle(ev('join', nickname='Ana'))
    assert sm.state == S.AWAITING_INPUT and 'say' in kinds(out) and 'Ana' in out[0].data['text']
    assert 'Front, 2 m ahead' in out[0].data['text']
    assert kinds(sm.handle(ev('join', nickname='Bob'))) == ['join_rejected']          # one escort per robot


def test_join_refused_until_the_robot_is_localized():
    sm, clk = make()
    bad = dict(GOOD, localization={'state': 'need_pose', 'ready': False})
    sm.handle(ev('robot_state', state=bad))
    out = sm.handle(ev('join', nickname='A'))
    assert kinds(out) == ['join_rejected'] and 'not available' in out[0].data['reason'] and sm.session is None
    sm.handle(ev('robot_state', state=dict(GOOD)))                       # localized now: the same QR works
    out = sm.handle(ev('join', nickname='A'))
    assert 'join_rejected' not in kinds(out) and sm.session is not None and sm.state == S.AWAITING_INPUT
    sm2, _ = make()
    sm2.handle(ev('robot_state', state=dict(GOOD, nav_ready=False)))     # Nav2 not running: also not ready
    assert kinds(sm2.handle(ev('join', nickname='B'))) == ['join_rejected']


def test_full_tour_and_return_home_on_end():
    sm, _ = make()
    sm.handle(ev('join'))
    go_and_arrive(sm, 'front_2m')
    go_and_arrive(sm, 'back_2m')
    go_and_arrive(sm, 'right_2m')
    go_and_arrive(sm, 'left_2m')
    out = sm.handle(ev('end'))                                             # ends away from home -> drives back
    assert sm.session is None and sm.state == S.RETURNING_HOME and 'session_closed' in kinds(out)
    g = goal(out)
    assert g['place'] == 'home'
    sm.handle(ev('goal_accepted', id=g['id']))
    sm.handle(ev('goal_reached', id=g['id']))
    assert sm.state == S.IDLE and sm.at == 'home'
    assert kinds(sm.handle(ev('join'))) and sm.state == S.AWAITING_INPUT   # a new guest can join again


def test_end_at_home_goes_straight_to_idle():
    sm, _ = make()
    sm.handle(ev('join'))
    out = sm.handle(ev('end'))
    assert sm.state == S.IDLE and goal(out) is None


def test_already_here_and_unknown_place():
    sm, _ = make()
    sm.handle(ev('join'))
    out = sm.handle(ev('select', dest='home'))
    assert goal(out) is None and 'already' in out[0].data['text']
    out = sm.handle(ev('select', dest='moon'))
    assert goal(out) is None and sm.state == S.AWAITING_INPUT


def test_failure_blocked_then_retry():
    sm, _ = make()
    sm.handle(ev('join'))
    g = goal(sm.handle(ev('select', dest='front_2m')))
    sm.handle(ev('goal_accepted', id=g['id']))
    out = sm.handle(ev('goal_failed', id=g['id'], reason='path blocked'))
    assert sm.state == S.BLOCKED and 'path blocked' in out[0].data['text'] and 'stop_robot' in kinds(out)
    assert any(o['kind'] == 'retry' for o in sm.options())
    g2 = goal(sm.handle(ev('say', text='try again')))
    assert g2 and g2['place'] == 'front_2m' and g2['id'] != g['id'] and sm.state == S.NAVIGATING


def test_goal_rejected_not_localized():
    sm, _ = make()
    sm.handle(ev('join'))
    g = goal(sm.handle(ev('select', dest='left_2m')))
    sm.handle(ev('goal_received', id=g['id']))
    out = sm.handle(ev('goal_rejected', id=g['id'], reason='robot is not localized on the map'))
    assert sm.state == S.BLOCKED and 'not localized' in out[0].data['text']


def test_stop_midway_and_late_cancel_ignored():
    sm, _ = make()
    sm.handle(ev('join'))
    g = goal(sm.handle(ev('select', dest='front_2m')))
    sm.handle(ev('goal_accepted', id=g['id']))
    out = sm.handle(ev('stop'))
    assert sm.state == S.STOPPED and 'stop_robot' in kinds(out)
    sm.handle(ev('goal_canceled', id=g['id'], reason='stop'))              # the gateway confirms the cancel: must not change anything
    assert sm.state == S.STOPPED
    g2 = goal(sm.handle(ev('say', text='take me to the left')))
    assert g2['place'] == 'left_2m' and sm.state == S.NAVIGATING


def test_reroute_ignores_events_of_the_old_goal():
    sm, _ = make()
    sm.handle(ev('join'))
    g1 = goal(sm.handle(ev('select', dest='front_2m')))
    sm.handle(ev('goal_accepted', id=g1['id']))
    g2 = goal(sm.handle(ev('select', dest='back_2m')))
    assert g2['id'] != g1['id'] and sm.dest == 'back_2m'
    sm.handle(ev('goal_canceled', id=g1['id'], reason='preempted by a new goal'))
    sm.handle(ev('goal_reached', id=g1['id']))                             # stale
    assert sm.state == S.NAVIGATING and sm.dest == 'back_2m'
    sm.handle(ev('goal_reached', id=g2['id']))
    assert sm.state == S.ARRIVED and sm.at == 'back_2m'


def test_same_destination_twice_is_not_resent():
    sm, _ = make()
    sm.handle(ev('join'))
    sm.handle(ev('select', dest='front_2m'))
    out = sm.handle(ev('select', dest='front_2m'))
    assert goal(out) is None and 'already heading' in out[0].data['text']


def test_lying_robot_stands_up_first():
    sm, _ = make()
    sm.handle(ev('robot_state', state={**GOOD, 'mode': 'lying'}))
    sm.handle(ev('join'))
    out = sm.handle(ev('select', dest='front_2m'))
    assert sm.state == S.PREPARING and goal(out) is None
    intent = [e for e in out if e.kind == 'intent'][0].data
    assert intent['name'] == 'stand_up'
    out = sm.handle(ev('intent_done', id=intent['id']))
    assert goal(out)['place'] == 'front_2m' and sm.state == S.NAVIGATING


def test_stand_up_failure_and_timeout():
    sm, clk = make()
    sm.handle(ev('robot_state', state={**GOOD, 'mode': 'lying'}))
    sm.handle(ev('join'))
    out = sm.handle(ev('select', dest='front_2m'))
    iid = [e for e in out if e.kind == 'intent'][0].data['id']
    sm.handle(ev('intent_failed', id=iid, reason='SDK refused'))
    assert sm.state == S.BLOCKED
    sm.handle(ev('select', dest='front_2m'))
    clk.t += 25
    sm.tick()
    assert sm.state == S.BLOCKED and 'took too long' in sm.message


def test_damped_robot_is_never_stood_up_automatically():
    sm, _ = make()
    sm.handle(ev('join'))
    out = sm.handle(ev('robot_state', state={**GOOD, 'mode': 'damping'}))      # staff pressed E-STOP
    assert sm.state == S.HOLD and 'intent' not in kinds(out)
    out = sm.handle(ev('select', dest='front_2m'))
    assert goal(out) is None and 'intent' not in kinds(out)
    assert kinds(sm.handle(ev('join'))) == ['join_rejected']
    out = sm.handle(ev('robot_state', state=dict(GOOD)))                       # staff released it
    assert sm.state == S.AWAITING_INPUT and 'ready again' in out[0].data['text']


def test_gateway_down_during_escort_stops_and_holds():
    sm, _ = make()
    sm.handle(ev('join'))
    g = goal(sm.handle(ev('select', dest='front_2m')))
    sm.handle(ev('goal_accepted', id=g['id']))
    out = sm.handle(ev('gateway_down'))
    assert sm.state == S.HOLD and 'stop_robot' in kinds(out) and sm.goal_id is None
    sm.handle(ev('gateway_up'))
    sm.handle(ev('robot_state', state=dict(GOOD)))
    assert sm.state == S.AWAITING_INPUT and sm.session is not None


def test_link_lost_in_robot_state_holds():
    sm, _ = make()
    sm.handle(ev('join'))
    sm.handle(ev('robot_state', state={**GOOD, 'connected': False}))
    assert sm.state == S.HOLD


def test_low_battery_and_not_localized_refuse_new_escorts():
    sm, _ = make()
    sm.handle(ev('join'))
    sm.handle(ev('robot_state', state={**GOOD, 'battery': 0.12}))
    out = sm.handle(ev('select', dest='front_2m'))
    assert goal(out) is None and sm.state == S.BLOCKED and 'battery' in sm.reason
    sm.handle(ev('robot_state', state={**GOOD, 'localization': {'state': 'need_pose', 'ready': False}}))
    out = sm.handle(ev('select', dest='front_2m'))
    assert goal(out) is None and 'not placed' in sm.reason
    sm.handle(ev('robot_state', state=dict(GOOD)))
    assert goal(sm.handle(ev('select', dest='front_2m')))                     # recovered: retry works


def test_goal_timeouts():
    sm, clk = make(goal_receive_s=3, goal_accept_s=10, goal_total_s=60)
    sm.handle(ev('join'))
    sm.handle(ev('select', dest='front_2m'))
    clk.t += 3.5
    out = sm.tick()
    assert sm.state == S.BLOCKED and 'did not answer' in sm.reason and 'stop_robot' in kinds(out)
    g = goal(sm.handle(ev('select', dest='back_2m')))
    sm.handle(ev('goal_received', id=g['id']))
    clk.t += 11
    sm.tick()
    assert sm.state == S.BLOCKED and 'did not start' in sm.reason
    g = goal(sm.handle(ev('select', dest='right_2m')))
    sm.handle(ev('goal_accepted', id=g['id']))
    clk.t += 61
    out = sm.tick()
    assert sm.state == S.BLOCKED and 'too long' in sm.reason and kinds(out).count('stop_robot') == 1


def test_silence_prompts_then_ends_session_and_returns_home():
    sm, clk = make(input_prompt_s=10, input_timeout_s=20)
    sm.handle(ev('join'))
    go_and_arrive(sm, 'front_2m')
    clk.t += 11
    out = sm.tick()
    assert 'still there' in out[0].data['text'] and sm.session is not None
    clk.t += 10
    out = sm.tick()
    assert sm.session is None and 'session_closed' in kinds(out) and goal(out)['place'] == 'home' and sm.state == S.RETURNING_HOME


def test_guest_activity_resets_silence_timers():
    sm, clk = make(input_prompt_s=10, input_timeout_s=20)
    sm.handle(ev('join'))
    clk.t += 9
    sm.handle(ev('say', text='what can you do?'))
    clk.t += 9
    assert sm.tick() == [] and sm.session is not None


def test_guest_arriving_while_robot_returns_home_takes_over():
    sm, _ = make()
    sm.handle(ev('join'))
    go_and_arrive(sm, 'front_2m')
    g = goal(sm.handle(ev('end')))
    out = sm.handle(ev('join', nickname='Eve'))
    assert sm.state == S.AWAITING_INPUT and 'stop_robot' in kinds(out) and sm.session['nickname'] == 'Eve'
    sm.handle(ev('goal_canceled', id=g['id'], reason='stop'))
    assert sm.state == S.AWAITING_INPUT


def test_return_home_failure_ends_idle():
    sm, _ = make()
    sm.handle(ev('join'))
    go_and_arrive(sm, 'front_2m')
    g = goal(sm.handle(ev('end')))
    sm.handle(ev('goal_failed', id=g['id'], reason='blocked'))
    assert sm.state == S.IDLE


def test_questions_are_answered_without_moving():
    sm, _ = make()
    sm.handle(ev('join'))
    out = sm.handle(ev('say', text='what places do you know?'))
    assert kinds(out) == ['say'] and sm.state == S.AWAITING_INPUT and goal(out) is None
    out = sm.handle(ev('say', text='stop'))
    assert 'not moving' in out[0].data['text']


def test_events_without_a_session_are_ignored():
    sm, _ = make()
    assert sm.handle(ev('select', dest='front_2m')) == [] and sm.handle(ev('say', text='hi')) == [] and sm.handle(ev('end')) == []
    assert sm.state == S.IDLE


def test_random_event_sequences_keep_invariants():
    rnd = random.Random(7)
    for run in range(60):
        sm, clk = make(input_prompt_s=5, input_timeout_s=9, goal_receive_s=2, goal_accept_s=4, goal_total_s=20, prepare_s=5)
        sent = []
        for _ in range(250):
            c = rnd.random()
            gid = rnd.choice(sent) if sent and rnd.random() < .8 else 'nope'
            options = [
                lambda: ev('join', nickname='x'), lambda: ev('select', dest=rnd.choice(list(PLACES) + ['moon'])),
                lambda: ev('say', text=rnd.choice(['take me to the left', 'stop', 'home', 'bye', 'what', 'try again', 'right'])),
                lambda: ev('stop'), lambda: ev('end'), lambda: ev('goal_received', id=gid), lambda: ev('goal_accepted', id=gid),
                lambda: ev('goal_progress', id=gid, distance=1.0), lambda: ev('goal_reached', id=gid),
                lambda: ev('goal_failed', id=gid, reason='x'), lambda: ev('goal_canceled', id=gid, reason='x'),
                lambda: ev('goal_rejected', id=gid, reason='x'), lambda: ev('intent_done', id='up-1'), lambda: ev('intent_failed', id='up-1'),
                lambda: ev('robot_state', state=rnd.choice([dict(GOOD), {**GOOD, 'mode': 'lying'}, {**GOOD, 'mode': 'damping'},
                                                              {**GOOD, 'connected': False}, {**GOOD, 'battery': 0.1}])),
                lambda: ev('gateway_down'), lambda: ev('gateway_up'), lambda: ev('tick')]
            e = options[int(c * len(options))]()
            if e.kind == 'tick':
                clk.t += rnd.choice([0.2, 3, 8, 30])
                effects = sm.tick()
            else:
                effects = sm.handle(e)
            for f in effects:
                if f.kind == 'send_goal':
                    sent.append(f.data['id'])
            # invariants
            assert isinstance(sm.state, S)
            if sm.state not in (S.NAVIGATING, S.RETURNING_HOME):
                assert sm.goal_id is None, (sm.state, sm.goal_id)
            if sm.session is None:
                assert sm.state in (S.IDLE, S.RETURNING_HOME, S.HOLD), sm.state
            if sm.state == S.IDLE:
                assert sm.session is None
            if sm.state in (S.AWAITING_INPUT, S.ARRIVED, S.BLOCKED, S.STOPPED, S.NAVIGATING, S.PREPARING):
                assert sm.session is not None, sm.state
            assert sum(1 for f in effects if f.kind == 'send_goal') <= 1
            sm.snapshot()
            sm.options()


# ---------------------------------------------------------------- outcomes by tag (used to report request status)
def sel(sm, dest, tag):
    return sm.handle(ev('select', dest=dest, tag=tag))


def test_outcome_reached_by_tag():
    sm, _ = make()
    sm.handle(ev('join'))
    g = goal(sel(sm, 'front_2m', 'r1'))
    assert 'r1' not in sm.outcomes and sm.tag == 'r1'
    sm.handle(ev('goal_reached', id=g['id']))
    assert sm.outcomes['r1']['kind'] == 'reached' and 'Front' in sm.outcomes['r1']['message'] and sm.tag is None


def test_outcome_failed_rejected_and_refused():
    sm, _ = make()
    sm.handle(ev('join'))
    g = goal(sel(sm, 'front_2m', 'a'))
    sm.handle(ev('goal_failed', id=g['id'], reason='path blocked'))
    assert sm.outcomes['a'] == {'kind': 'failed', 'message': sm.outcomes['a']['message'], 'reason': 'execution_failed'}
    g = goal(sel(sm, 'front_2m', 'b'))
    sm.handle(ev('goal_rejected', id=g['id'], reason='not localized'))
    assert sm.outcomes['b']['reason'] == 'robot_unavailable'
    sm.handle(ev('robot_state', state={**GOOD, 'battery': 0.05}))
    sel(sm, 'back_2m', 'c')
    assert sm.outcomes['c']['kind'] == 'refused' and sm.outcomes['c']['reason'] == 'robot_unavailable' and 'battery' in sm.outcomes['c']['message']


def test_outcome_already_there_unknown_and_reroute_and_stop():
    sm, _ = make()
    sm.handle(ev('join'))
    sel(sm, 'home', 't1')
    assert sm.outcomes['t1']['kind'] == 'already_there'
    sel(sm, 'moon', 't2')
    assert sm.outcomes['t2']['reason'] == 'unsupported_request'
    g = goal(sel(sm, 'front_2m', 't3'))
    sm.handle(ev('goal_accepted', id=g['id']))
    goal(sel(sm, 'back_2m', 't4'))                                           # re-route: the first request ends, the second is current
    assert sm.outcomes['t3']['kind'] == 'rerouted' and sm.tag == 't4'
    sm.handle(ev('stop'))
    assert sm.outcomes['t4']['kind'] == 'stopped' and sm.tag is None


def test_outcome_stand_up_failure_and_hold():
    sm, _ = make()
    sm.handle(ev('robot_state', state={**GOOD, 'mode': 'lying'}))
    sm.handle(ev('join'))
    out = sel(sm, 'front_2m', 'u1')
    iid = [e for e in out if e.kind == 'intent'][0].data['id']
    sm.handle(ev('intent_failed', id=iid, reason='SDK refused'))
    assert sm.outcomes['u1']['kind'] == 'failed'
    sm.handle(ev('robot_state', state=dict(GOOD)))
    g = goal(sel(sm, 'front_2m', 'u2'))
    sm.handle(ev('goal_accepted', id=g['id']))
    sm.handle(ev('gateway_down'))
    assert sm.outcomes['u2']['kind'] == 'failed' and sm.outcomes['u2']['reason'] == 'robot_unavailable'


def test_session_end_finishes_the_active_request():
    sm, _ = make()
    sm.handle(ev('join'))
    g = goal(sel(sm, 'front_2m', 'e1'))
    sm.handle(ev('goal_accepted', id=g['id']))
    sm.handle(ev('end', reason='Your session has expired.'))
    assert sm.outcomes['e1']['kind'] == 'stopped'
    assert sm.state == S.RETURNING_HOME
