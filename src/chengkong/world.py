"""Layer 1: bitemporal records, pure event replay and conservative occupancy.

Record receipt never rewrites a prior world object. A late record can revise the
current interpretation of earlier valid time; stored decision snapshots retain
what was actually known at that decision. Tasks are deliberately excluded from
construction replay so adoption and flight progress cannot be rolled back.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .contracts import (ContractError, number, validate_example, validate_event, validate_world,
                        validate_geometry, validate_snapshot)

RULE_VERSION = 'rules-v2'
MODEL_VERSION = 'none'
_REPLAY_FIELDS = ('facilities', 'activities', 'known_sources', 'scene_relations')
_EVENT_ORDER = {'facility_added': 0, 'activity_started': 1,
                'activity_completion_confirmed': 3, 'facility_removal_confirmed': 4}


def _effective_order(event):
    return (event['effective_at_s'], _EVENT_ORDER.get(event['kind'], 2),
            event['observed_at_s'], event['id'])


def load_example(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding='utf-8') as stream:
        raw = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ContractError(f'nonfinite JSON number: {value}')))
    validate_example(raw)
    return raw


def _gaps(world):
    world['information_gaps'] = [
        {'object_id': a['id'], 'field': 'completion_status', 'status': 'unknown',
         'planned_end_s': a['planned_interval_s'][1], 'not_a_prediction': True}
        for a in world['activities'] if a['completion_status'] != 'confirmed']


def visible_world(raw: dict, scenario_id: str, as_of_s: float) -> dict:
    """Simulator boundary: unreleased events and hidden branches never cross it."""
    validate_example(raw)
    as_of_s = number(as_of_s, 'as_of_s', minimum=raw['observed_snapshot']['as_of_s'])
    if as_of_s > raw['evaluation_horizon_s'][1]:
        raise ContractError('as_of_s exceeds the declared evaluation horizon')
    scenarios = {scenario['id']: scenario for scenario in raw['scenario_definitions_for_simulator_only']}
    if scenario_id not in scenarios:
        raise ContractError(f'unknown scenario_id: {scenario_id}')
    world = deepcopy(raw['observed_snapshot'])
    world.update({
        'schema_version': 'world-v2', 'scenario_id': scenario_id,
        'horizon_s': deepcopy(raw['evaluation_horizon_s']),
        'coordinate_system': deepcopy(raw['coordinate_system']), 'time_system': deepcopy(raw['time_system']),
        'rule_version': RULE_VERSION, 'model_version': MODEL_VERSION,
        'data_kind': raw['data_kind'], 'is_real_case': False, 'event_log': [],
        'observed_events': [], 'pending_events': [], 'activation_log': [],
        'scenario_semantics': 'simulator_branch_label_only; no hidden future events or probabilities',
        'assumptions': ['Unconfirmed activity remains possible until horizon end; horizon end is not completion.',
                        'Facility physical envelopes persist independently of activity completion.',
                        'Pause retains the previous conservative envelope unless an explicit replacement is supplied.',
                        'Late records revise present knowledge of valid time, never old decision snapshots.'],
        'ground_resource_scope': raw.get('ground_resource_scope'),
    })
    world['initial_state'] = {key: deepcopy(world[key]) for key in _REPLAY_FIELDS}
    world['initial_state_as_of_s'] = world['as_of_s']
    _gaps(world)
    released = [e for e in scenarios[scenario_id]['events'] if e['observed_at_s'] <= as_of_s]
    return advance_world(world, as_of_s, released)


def _object_id(event):
    if event['kind'] == 'facility_added':
        return event['facility']['id']
    if event['kind'] == 'activity_started' and 'activity' in event:
        return event['activity']['id']
    return event.get('facility_id', event.get('activity_id'))


def _objects(world):
    return {o['id']: o for o in world['facilities'] + world['activities']}


def _source(event):
    return {'id': f'source:{event["id"]}', 'kind': event['source_kind'],
            'observed_at_s': event['observed_at_s'], 'effective_at_s': event['effective_at_s'],
            'event_id': event['id'], 'information_type': 'synthetic_record_assertion',
            'evidence_refs': deepcopy(event.get('evidence_refs', []))}


def _replay(world):
    """Reconstruct object state in valid-time order from visible evidence only."""
    for key in _REPLAY_FIELDS:
        world[key] = deepcopy(world['initial_state'][key])
    history = {}
    for obj in world['facilities'] + world['activities']:
        history[obj['id']] = [{'effective_at_s': world['horizon_s'][0], 'object': deepcopy(obj)}]
    source_ids = {s['id'] for s in world['known_sources']}
    for event in world['observed_events']:
        source = _source(event)
        if source['id'] in source_ids:
            raise ContractError('event source id collides with an existing source')
        world['known_sources'].append(source)
        source_ids.add(source['id'])
    active = sorted((e for e in world['observed_events'] if e['effective_at_s'] <= world['as_of_s']),
                    key=_effective_order)
    for event in active:
        kind, oid, effective = event['kind'], _object_id(event), event['effective_at_s']
        source_id = f'source:{event["id"]}'
        if kind == 'facility_added':
            if oid in _objects(world):
                raise ContractError(f'facility already exists: {oid}')
            obj = deepcopy(event['facility'])
            obj.update(installed=True, removal_confirmed=False, removal_confirmed_at_s=None,
                       installed_at_s=effective, source_id=source_id, source_ids=[source_id])
            world['facilities'].append(obj)
        elif kind == 'activity_started' and 'activity' in event:
            if oid in _objects(world):
                raise ContractError(f'activity already exists: {oid}; use activity_id to start an existing activity')
            obj = deepcopy(event['activity'])
            facility = next((f for f in world['facilities'] if f['id'] == obj['facility_id']), None)
            if facility is None or not facility['installed']:
                raise ContractError('activity start requires an installed facility')
            if obj['planned_interval_s'][1] <= effective:
                raise ContractError('activity start must precede planned end')
            obj['planned_interval_s'][0] = effective
            obj.update(completion_status='not_confirmed', completion_confirmed_at_s=None,
                       operational_status='active', started_at_s=effective, source_id=source_id, source_ids=[source_id])
            world['activities'].append(obj)
            world['scene_relations'].append({'subject': obj['facility_id'], 'predicate': 'performs', 'object': oid})
        else:
            obj = _objects(world).get(oid)
            if obj is None:
                raise ContractError(f'event references object absent at its effective time: {oid}')
            if kind.startswith('facility_') and kind != 'facility_removal_confirmed' and not obj['installed']:
                raise ContractError('geometry change cannot reinstall a removed facility')
            if kind == 'planned_end_changed':
                if obj['completion_status'] == 'confirmed':
                    raise ContractError('completed activity cannot be reopened by a plan-end update')
                if event['new_planned_end_s'] <= obj['planned_interval_s'][0]:
                    raise ContractError('new planned end must be after activity start')
                obj['planned_interval_s'][1] = event['new_planned_end_s']
            elif kind == 'activity_completion_confirmed':
                if effective < obj['planned_interval_s'][0]:
                    raise ContractError('completion precedes activity start')
                if obj['completion_status'] == 'confirmed' and obj['completion_confirmed_at_s'] != effective:
                    raise ContractError('conflicting completion times require explicit correction workflow')
                obj.update(completion_status='confirmed', completion_confirmed_at_s=effective,
                           operational_status='completed')
            elif kind == 'facility_removal_confirmed':
                if any(a['facility_id'] == oid and (a['completion_status'] != 'confirmed' or
                       a['completion_confirmed_at_s'] > effective) for a in world['activities']):
                    raise ContractError('facility removal requires its activities to have confirmed completion')
                if obj['removal_confirmed'] and obj['removal_confirmed_at_s'] != effective:
                    raise ContractError('conflicting removal times require explicit correction workflow')
                obj.update(installed=False, removal_confirmed=True, removal_confirmed_at_s=effective)
            elif kind in ('facility_geometry_changed', 'facility_pose_changed'):
                obj['physical_geometry'] = deepcopy(event['physical_geometry'])
                if 'pose' in event:
                    obj['pose'] = deepcopy(event['pose'])
                if 'parameters' in event:
                    obj['parameters'] = deepcopy(event['parameters'])
            elif kind in ('activity_started', 'activity_paused', 'activity_resumed', 'activity_envelope_changed'):
                if obj['completion_status'] == 'confirmed':
                    raise ContractError('completed activity requires a new activity ID for further work')
                if effective < obj['planned_interval_s'][0]:
                    raise ContractError('activity state update precedes its start')
                if kind == 'activity_started':
                    obj['started_at_s'] = effective
                    obj['operational_status'] = 'active'
                elif kind == 'activity_paused':
                    obj['operational_status'] = 'paused'
                    obj['pause_occupancy_policy'] = 'explicit_envelope' if 'activity_envelope' in event else 'retain_conservative_envelope'
                elif kind == 'activity_resumed':
                    obj['operational_status'] = 'active'
                if 'activity_envelope' in event:
                    obj['activity_envelope'] = deepcopy(event['activity_envelope'])
            else:
                raise ContractError(f'unsupported replay event: {kind}')
            obj['source_ids'] = list(dict.fromkeys([obj['source_id'], *obj.get('source_ids', []), source_id]))
        # Occupancy histories retain geometry and paused/active state changes. Plan-end
        # changes and completion are reflected by final metadata and the end boundary.
        if kind in ('facility_added', 'facility_geometry_changed', 'facility_pose_changed',
                    'activity_started', 'activity_paused', 'activity_resumed', 'activity_envelope_changed'):
            versions = history.setdefault(oid, [])
            if versions and versions[-1]['effective_at_s'] == effective:
                versions[-1] = {'effective_at_s': effective, 'object': deepcopy(obj)}
            else:
                versions.append({'effective_at_s': effective, 'object': deepcopy(obj)})
    world['occupancy_history'] = history
    world['pending_events'] = [deepcopy(e) for e in world['observed_events'] if e['effective_at_s'] > world['as_of_s']]
    _gaps(world)


def apply_event(world: dict, event: dict) -> dict:
    """Record a visible event atomically; queue future validity, replay late records."""
    validate_world(world)
    validate_event(event)
    if event['observed_at_s'] > world['as_of_s']:
        raise ContractError('event not yet visible at this world.as_of_s')
    for entry in world['event_log']:
        if entry['id'] == event['id']:
            if entry['event'] != event:
                raise ContractError(f'event id {event["id"]} reused with conflicting payload')
            return deepcopy(world)
    updated = deepcopy(world)
    if 'initial_state' not in updated:
        # Legacy externally constructed worlds cannot silently discard prior events.
        if updated['event_log']:
            raise ContractError('legacy world with event history requires re-import before v2 event replay')
        updated['initial_state'] = {key: deepcopy(updated[key]) for key in _REPLAY_FIELDS}
        updated['observed_events'] = []
    old_version, oid = world['environment_version'], _object_id(event)
    previous = deepcopy(_objects(world).get(oid))
    updated['observed_events'].append(deepcopy(event))
    _replay(updated)
    if updated['pending_events']:
        # Check known pending records for impossible references/transitions now,
        # using a disposable projection. This does not release their occupancy
        # or confirmation into the decision world before validity begins.
        projected = deepcopy(updated)
        projected['as_of_s'] = max(e['effective_at_s'] for e in projected['pending_events'])
        _replay(projected)
        validate_snapshot(projected)
    obj = deepcopy(_objects(updated).get(oid))
    updated['environment_version'] = old_version + 1
    updated['event_log'].append({
        'id': event['id'], 'event': deepcopy(event), 'object_id': oid,
        'observed_at_s': event['observed_at_s'], 'received_at_s': world['as_of_s'],
        'effective_at_s': event['effective_at_s'], 'before': previous, 'after': obj,
        'changed_fields': [k for k in (obj or {}) if (previous or {}).get(k) != obj[k]],
        'old_environment_version': old_version, 'new_environment_version': old_version + 1,
        'source_ids': [f'source:{event["id"]}'], 'scenario_id': world['scenario_id'], 'rule_version': world['rule_version'],
        'affected_object_ids': [oid], 'status_at_receipt': 'pending_effective_time' if event['effective_at_s'] > world['as_of_s'] else 'applied',
        'late_information': event['effective_at_s'] < event['observed_at_s'],
    })
    validate_world(updated)
    return updated


def advance_world(world: dict, as_of_s: float, events: list[dict] | None = None) -> dict:
    """Advance simulation knowledge time. New events must already be observed.

    The caller releases simulator records; this function receives no raw future
    branch. Construction replay never restores tasks from the initial snapshot.
    """
    validate_world(world)
    now = number(as_of_s, 'as_of_s', minimum=world['as_of_s'])
    if now > world['horizon_s'][1]:
        raise ContractError('as_of_s exceeds the declared evaluation horizon')
    updated = deepcopy(world)
    activated = [e for e in updated.get('pending_events', []) if e['effective_at_s'] <= now]
    updated['as_of_s'] = now
    if activated:
        _replay(updated)
        old_version = updated['environment_version']
        updated['environment_version'] += 1
        updated.setdefault('activation_log', []).append({'activated_at_s': now,
            'event_ids': [e['id'] for e in activated], 'old_environment_version': old_version,
            'new_environment_version': updated['environment_version']})
    for event in sorted(events or [], key=lambda e: (e['observed_at_s'], *_effective_order(e))):
        updated = apply_event(updated, event)
    if 'occupancy_history' not in updated and 'initial_state' in updated:
        _replay(updated)
    _gaps(updated)
    validate_world(updated)
    return updated


def generate_constraints(world: dict) -> list[dict]:
    validate_world(world)
    constraints: list[dict] = []
    start, end = world['horizon_s']

    def append(identifier: str, obj: dict, kind: str, geometry: dict | None, interval: list,
               reason: str, **extra: Any) -> None:
        if interval[0] >= interval[1]:
            return
        ids = list(dict.fromkeys([obj['source_id'], *obj.get('source_ids', [])]))
        constraints.append({
            'id': identifier, 'object_id': obj['id'], 'kind': kind, 'geometry': deepcopy(geometry),
            'geometry_status': validate_geometry(geometry), 'interval_s': interval,
            'source_ids': ids,
            'environment_version': world['environment_version'], 'scenario_id': world['scenario_id'],
            'rule_version': world['rule_version'], 'model_version': world['model_version'],
            'reason': reason, 'information_type': 'synthetic_record_and_rule',
            'observed_at_s': max(source['observed_at_s'] for source in world['known_sources'] if source['id'] in ids),
            'generated_as_of_s': world['as_of_s'], 'validity_horizon_s': list(world['horizon_s']),
            'protection_semantics': 'Raw obstacle envelope; aircraft protection_radius_m applied once by checker.',
            **extra,
        })

    def versions(obj):
        rows = world.get('occupancy_history', {}).get(obj['id'], [{'effective_at_s': start, 'object': obj}])
        for i, row in enumerate(rows):
            until = rows[i + 1]['effective_at_s'] if i + 1 < len(rows) else end
            # Preserve current provenance and completion metadata on historical shapes.
            shape = deepcopy(obj if i == len(rows) - 1 else row['object'])
            shape['source_ids'] = list(dict.fromkeys([*shape.get('source_ids', []), *obj.get('source_ids', [])]))
            yield i, shape, max(start, row['effective_at_s']), min(end, until), len(rows)

    for building in world['buildings']:
        append(f'physical:{building["id"]}:0', building, 'physical', building.get('geometry'), [start, end], 'building physical occupancy')
    for facility in world['facilities']:
        until = min(end, facility['removal_confirmed_at_s']) if facility['removal_confirmed'] else end
        if facility['installed'] or facility['removal_confirmed']:
            for version, obj, begin, finish, count in versions(facility):
                for i, geometry in enumerate(obj['physical_geometry'] or [None]):
                    cid = f'physical:{facility["id"]}:{i}' + (f':v{version}' if count > 1 else '')
                    append(cid, obj, 'physical', geometry, [begin, min(finish, until)],
                           'facility physical occupancy independent of activity completion', geometry_version=version)
    for activity in world['activities']:
        confirmed = activity['completion_status'] == 'confirmed'
        until = min(end, activity['completion_confirmed_at_s']) if confirmed else end
        for version, obj, begin, finish, count in versions(activity):
            cid = f'activity:{activity["id"]}' + (f':v{version}' if count > 1 else '')
            append(cid, obj, 'activity', obj.get('activity_envelope'),
                   [max(begin, activity['planned_interval_s'][0]), min(finish, until)],
                   'confirmed activity interval' if confirmed else 'completion unknown: bounded conservative activity occupancy',
                   completion_status=activity['completion_status'], planned_interval_s=list(activity['planned_interval_s']),
                   end_is_confirmed=confirmed, calibrated=False, geometry_version=version,
                   operational_status=obj.get('operational_status', 'active_or_planned'),
                   pause_occupancy_policy=obj.get('pause_occupancy_policy'),
                   beyond_horizon_status='unknown' if not confirmed else 'completion_confirmed')
    return constraints
