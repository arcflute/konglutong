"""Layer 1: evidence-visible snapshots, validated events and rule occupancy generation."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Any

from .contracts import ContractError, number, validate_example, validate_event, validate_world, validate_geometry

RULE_VERSION = 'rules-v1'
MODEL_VERSION = 'none'


def load_example(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding='utf-8') as stream:
        raw = json.load(stream, parse_constant=lambda value: (_ for _ in ()).throw(ContractError(f'nonfinite JSON number: {value}')))
    validate_example(raw)
    return raw


def visible_world(raw: dict, scenario_id: str, as_of_s: float) -> dict:
    """Simulator boundary. Only released records cross into the decision world."""
    validate_example(raw)
    as_of_s = number(as_of_s, 'as_of_s', minimum=raw['observed_snapshot']['as_of_s'])
    if as_of_s > raw['evaluation_horizon_s'][1]:
        raise ContractError('as_of_s exceeds the declared evaluation horizon')
    scenarios = {scenario['id']: scenario for scenario in raw['scenario_definitions_for_simulator_only']}
    if scenario_id not in scenarios:
        raise ContractError(f'unknown scenario_id: {scenario_id}')
    world = deepcopy(raw['observed_snapshot'])
    world.update({
        'schema_version': 'world-v1', 'scenario_id': scenario_id, 'as_of_s': as_of_s,
        'horizon_s': deepcopy(raw['evaluation_horizon_s']),
        'coordinate_system': deepcopy(raw['coordinate_system']), 'time_system': deepcopy(raw['time_system']),
        'rule_version': RULE_VERSION, 'model_version': MODEL_VERSION,
        'data_kind': raw['data_kind'], 'is_real_case': False, 'event_log': [],
        'scenario_semantics': 'simulator_branch_label_only; no hidden future events or probabilities',
        'assumptions': ['Unconfirmed activity remains possible until horizon end; horizon end is not completion.',
                        'Facility physical envelopes persist independently of activity completion.'],
        'ground_resource_scope': raw.get('ground_resource_scope'),
    })
    released = [e for e in scenarios[scenario_id]['events'] if e['observed_at_s'] <= as_of_s]
    for event in sorted(released, key=lambda e: (e['observed_at_s'], e['effective_at_s'], e['id'])):
        world = apply_event(world, event)
    world['information_gaps'] = [
        {'object_id': a['id'], 'field': 'completion_status', 'status': 'unknown',
         'planned_end_s': a['planned_interval_s'][1], 'not_a_prediction': True}
        for a in world['activities'] if a['completion_status'] != 'confirmed'
    ]
    validate_world(world)
    return world


def apply_event(world: dict, event: dict) -> dict:
    """Return a new validated world; identical replay is idempotent, conflicts reject."""
    validate_world(world)
    validate_event(event, world)
    if event['observed_at_s'] > world['as_of_s']:
        raise ContractError('event not yet visible at this world.as_of_s')
    for entry in world['event_log']:
        if entry['id'] == event['id']:
            if entry['event'] != event:
                raise ContractError(f'event id {event["id"]} reused with conflicting payload')
            return deepcopy(world)
    updated = deepcopy(world)
    is_facility = event['kind'] == 'facility_removal_confirmed'
    object_id = event['facility_id'] if is_facility else event['activity_id']
    objects = updated['facilities'] if is_facility else updated['activities']
    obj = next(item for item in objects if item['id'] == object_id)
    previous = deepcopy(obj)
    if event['kind'] == 'planned_end_changed':
        if obj['completion_status'] == 'confirmed':
            raise ContractError('completed activity cannot be reopened by a plan-end update')
        if event['new_planned_end_s'] <= obj['planned_interval_s'][0]:
            raise ContractError('new planned end must be after activity start')
        obj['planned_interval_s'][1] = event['new_planned_end_s']
    elif event['kind'] == 'activity_completion_confirmed':
        if event['effective_at_s'] < obj['planned_interval_s'][0]:
            raise ContractError('completion precedes activity start')
        if obj['completion_status'] == 'confirmed' and obj['completion_confirmed_at_s'] != event['effective_at_s']:
            raise ContractError('conflicting completion times require explicit correction workflow')
        obj['completion_status'] = 'confirmed'
        obj['completion_confirmed_at_s'] = event['effective_at_s']
    else:
        if any(a['facility_id'] == object_id and (a['completion_status'] != 'confirmed' or a['completion_confirmed_at_s'] > event['effective_at_s']) for a in updated['activities']):
            raise ContractError('facility removal requires its activities to have confirmed completion')
        obj['installed'] = False
        obj['removal_confirmed'] = True
        obj['removal_confirmed_at_s'] = event['effective_at_s']
    source_id = f'source:{event["id"]}'
    if any(s['id'] == source_id for s in updated['known_sources']):
        raise ContractError('event source id collides with an existing source')
    updated['known_sources'].append({
        'id': source_id, 'kind': event['source_kind'], 'observed_at_s': event['observed_at_s'],
        'effective_at_s': event['effective_at_s'], 'event_id': event['id'],
        'information_type': 'synthetic_record_assertion',
    })
    obj['source_ids'] = list(dict.fromkeys([obj['source_id'], *obj.get('source_ids', []), source_id]))
    old_version = world['environment_version']
    updated['environment_version'] = old_version + 1
    updated['event_log'].append({
        'id': event['id'], 'event': deepcopy(event), 'object_id': object_id,
        'observed_at_s': event['observed_at_s'], 'effective_at_s': event['effective_at_s'],
        'before': previous, 'after': deepcopy(obj),
        'changed_fields': [k for k in obj if previous.get(k) != obj[k]],
        'old_environment_version': old_version, 'new_environment_version': old_version + 1,
        'source_ids': [source_id], 'scenario_id': world['scenario_id'], 'rule_version': world['rule_version'],
        'affected_object_ids': [object_id],
    })
    updated['information_gaps'] = [
        {'object_id': a['id'], 'field': 'completion_status', 'status': 'unknown',
         'planned_end_s': a['planned_interval_s'][1], 'not_a_prediction': True}
        for a in updated['activities'] if a['completion_status'] != 'confirmed'
    ]
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
        constraints.append({
            'id': identifier, 'object_id': obj['id'], 'kind': kind, 'geometry': deepcopy(geometry),
            'geometry_status': validate_geometry(geometry), 'interval_s': interval,
            'source_ids': list(dict.fromkeys([obj['source_id'], *obj.get('source_ids', [])])),
            'environment_version': world['environment_version'], 'scenario_id': world['scenario_id'],
            'rule_version': world['rule_version'], 'model_version': world['model_version'],
            'reason': reason, 'information_type': 'synthetic_record_and_rule',
            'observed_at_s': max(source['observed_at_s'] for source in world['known_sources'] if source['id'] in [obj['source_id'], *obj.get('source_ids', [])]),
            'generated_as_of_s': world['as_of_s'], 'validity_horizon_s': list(world['horizon_s']),
            'protection_semantics': 'Raw obstacle envelope; aircraft protection_radius_m applied once by checker.',
            **extra,
        })

    for building in world['buildings']:
        append(f'physical:{building["id"]}:0', building, 'physical', building.get('geometry'), [start, end], 'building physical occupancy')
    for facility in world['facilities']:
        until = min(end, facility.get('removal_confirmed_at_s', end)) if facility['removal_confirmed'] else end
        # A confirmed removal only closes the interval; historical physical occupancy is retained.
        if facility['installed'] or facility['removal_confirmed']:
            geometries = facility['physical_geometry'] or [None]
            for i, geometry in enumerate(geometries):
                append(f'physical:{facility["id"]}:{i}', facility, 'physical', geometry, [start, until], 'facility physical occupancy independent of activity completion')
    for activity in world['activities']:
        confirmed = activity['completion_status'] == 'confirmed'
        until = min(end, activity['completion_confirmed_at_s']) if confirmed else end
        append(f'activity:{activity["id"]}', activity, 'activity', activity.get('activity_envelope'),
               [max(start, activity['planned_interval_s'][0]), until],
               'confirmed activity interval' if confirmed else 'completion unknown: bounded conservative activity occupancy',
               completion_status=activity['completion_status'], planned_interval_s=list(activity['planned_interval_s']),
               end_is_confirmed=confirmed, calibrated=False,
               beyond_horizon_status='unknown' if not confirmed else 'completion_confirmed')
    return constraints
