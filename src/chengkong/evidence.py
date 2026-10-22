"""Budgeted evidence lookup over decision-visible records only; no simulator truth access."""
from __future__ import annotations

from copy import deepcopy

from .contracts import ContractError, integer, number, stable_id, validate_world


def generate_requests(world: dict, analysis: dict | None = None, *, source_layer: str = 'conflict_analysis',
                      max_queries: int = 1, deadline_s: float | None = None) -> list[dict]:
    """Rule trigger for unconfirmed completion; decision-value estimation remains unimplemented."""
    validate_world(world)
    if source_layer not in ('conflict_analysis', 'maintenance_decision'):
        raise ContractError('unknown evidence source_layer')
    integer(max_queries, 'max_queries')
    deadline = world['horizon_s'][1] if deadline_s is None else number(deadline_s, 'deadline_s', minimum=0)
    requests = []
    for activity in world['activities']:
        if activity['completion_status'] == 'confirmed':
            continue
        related = set()
        # Root report may expose top-level conflicts or per-task results. No hidden data is consulted.
        report = analysis or {}
        conflicts = list(report.get('conflicts', []))
        for result in report.get('task_results', report.get('task_reports', [])):
            conflicts.extend(result.get('conflicts', []))
        for conflict in conflicts:
            if conflict.get('object_id') == activity['id'] and conflict.get('task_id'):
                related.add(conflict['task_id'])
        requests.append({
            'id': f'evidence:{source_layer}:{world["scenario_id"]}:{activity["id"]}:{world["environment_version"]}',
            'source_layer': source_layer, 'object_id': activity['id'], 'field': 'completion_status',
            'related_task_ids': sorted(related), 'required_evidence': 'Visible, source-linked completion confirmation with effective time',
            'possible_impact': 'May shorten activity occupancy; physical facility occupancy still remains',
            'deadline_s': deadline, 'max_queries': max_queries, 'environment_version': world['environment_version'],
            'scenario_id': world['scenario_id'], 'rule_version': world['rule_version'], 'model_version': world['model_version'],
            'trigger': 'deterministic_unknown_field_rule', 'decision_value_estimation': 'not_implemented',
            'allowed_source': 'world.known_sources and world.event_log visible at world.as_of_s',
        })
    return requests


def query_evidence(world: dict, request: dict, *, now_s: float | None = None, queries_used: int = 0) -> dict:
    """One unit-cost local query. Unknown answers stay unknown and do not update the world."""
    validate_world(world)
    stable_id(request.get('id'), 'request.id')
    integer(queries_used, 'queries_used')
    budget = integer(request.get('max_queries'), 'max_queries')
    deadline = number(request.get('deadline_s'), 'deadline_s', minimum=0)
    now = world['as_of_s'] if now_s is None else number(now_s, 'now_s', minimum=0)
    if now < world['as_of_s']:
        raise ContractError('evidence query now_s cannot precede its visible snapshot')
    result = {'request_id': request['id'], 'status': 'unknown', 'answer': None, 'evidence': [],
              'queries_used': queries_used, 'query_cost': 0, 'remaining_queries': max(0, budget - queries_used),
              'as_of_s': world['as_of_s'], 'queried_at_s': now,
              'environment_version': world['environment_version'], 'scenario_id': world['scenario_id'],
              'rule_version': world['rule_version'], 'model_version': world['model_version'],
              'world_updated': False}
    if any(request.get(key) != world[key] for key in ('environment_version', 'scenario_id', 'rule_version', 'model_version')):
        result.update(status='stale_request', reason='Regenerate request against the current visible snapshot')
        return result
    if now >= deadline:
        result.update(status='deadline_exceeded', reason='Evidence deadline reached without changing unknown state')
        return result
    if queries_used >= budget:
        result.update(status='budget_exhausted', reason='Query budget exhausted without changing unknown state')
        return result
    if request.get('field') != 'completion_status':
        result.update(status='unsupported', reason='v1 evidence lookup supports completion_status only')
        return result
    activity = next((a for a in world['activities'] if a['id'] == request.get('object_id')), None)
    if activity is None:
        raise ContractError('request references missing activity')
    result.update(queries_used=queries_used + 1, query_cost=1, remaining_queries=budget - queries_used - 1)
    visible_sources = {s['id']: s for s in world['known_sources'] if s['observed_at_s'] <= world['as_of_s']}
    records = [deepcopy(entry) for entry in world['event_log']
               if entry['object_id'] == activity['id'] and entry['observed_at_s'] <= world['as_of_s']
               and entry['event']['kind'] == 'activity_completion_confirmed'
               and all(source_id in visible_sources for source_id in entry['source_ids'])]
    if activity['completion_status'] == 'confirmed':
        # The startup snapshot can also contain a source-backed confirmed record.
        ids = activity.get('source_ids', [activity['source_id']])
        sources = [deepcopy(visible_sources[source_id]) for source_id in ids if source_id in visible_sources]
        if records or sources:
            result.update(status='resolved', answer={'completion_status': 'confirmed',
                          'completion_confirmed_at_s': activity['completion_confirmed_at_s']},
                          evidence=records or sources, reason='Confirmation already present in visible records')
            return result
    result.update(status='unknown', reason='No visible completion confirmation; planned end is not evidence of completion')
    return result


def resolve_requests(world: dict, requests: list[dict]) -> list[dict]:
    return [query_evidence(world, request) for request in requests]
