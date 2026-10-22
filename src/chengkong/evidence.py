"""Budgeted evidence workflow, with explicit hypothetical comparison triggers.

Counterfactual branches are used to value a query, never as source evidence.
Only currently released records may produce an event, and that event passes the
same first-layer validator and event reducer as all other construction updates.
"""
from __future__ import annotations

from copy import deepcopy

from .contracts import ContractError, integer, number, stable_id, validate_world, validate_event
from .world import generate_constraints, apply_event
from .analysis import check_task


def _branch_comparison(world, activity, candidates=None):
    constraints = generate_constraints(world)
    # The planned end is a possible branch boundary, not a confirmed end. The
    # alternative remains occupied through the evaluation horizon without a probability.
    boundary = max(world['as_of_s'], activity['planned_interval_s'][1])
    hypothetical = []
    for constraint in constraints:
        row = deepcopy(constraint)
        if row['object_id'] == activity['id']:
            row['interval_s'][1] = min(row['interval_s'][1], boundary)
        if row['interval_s'][0] < row['interval_s'][1]:
            hypothetical.append(row)
    branch_reports = {}
    for name, rows in (('unconfirmed_occupancy', constraints), ('hypothetical_completion', hypothetical)):
        branch_reports[name] = {task['id']: check_task(task, rows, world) for task in world['tasks']}
    changed = [t['id'] for t in world['tasks'] if
               branch_reports['unconfirmed_occupancy'][t['id']]['status'] !=
               branch_reports['hypothetical_completion'][t['id']]['status']]
    affected = sorted({t['id'] for t in world['tasks'] if any(
        c['object_id'] == activity['id'] for c in branch_reports['unconfirmed_occupancy'][t['id']]['conflicts'])})
    actions = {}
    # This bounded policy chooses retain only when construction checks are clear.
    # Joint/resource feasibility is independently required before any execution.
    for name, reports in branch_reports.items():
        actions[name] = {tid: 'retain_pending_joint_review' if r['status'] == 'clear' else
                         'repair_or_hold' if r['status'] == 'conflict' else 'hold_for_missing_information'
                         for tid, r in reports.items()}
    preferred = {}
    if candidates:
        from .verification import verify_candidate
        base_reviews = {candidate['id']: verify_candidate(candidate, world, constraints) for candidate in candidates}
        for name, rows in (('unconfirmed_occupancy', constraints), ('hypothetical_completion', hypothetical)):
            feasible = []
            for candidate in candidates:
                review = base_reviews[candidate['id']]
                # Authoritative verification deliberately regenerates real-world
                # constraints. Never pass a hypothetical replacement to it. Keep
                # its task/resource/version checks, then separately compare the
                # bounded branch geometry solely for evidence-query priority.
                non_geometric = [v for v in review.get('violations', []) if v.get('reason') not in
                                 ('object_conflicts', 'independent_reference_object_conflicts')]
                branch_checks = [check_task(t, rows, world) for t in candidate.get('tasks', [])]
                eligible = (review.get('status') != 'needs_recheck' and not non_geometric and
                            not review.get('unchecked') and branch_checks and
                            all(r['status'] == 'clear' for r in branch_checks))
                if eligible:
                    changes = candidate.get('changes', [])
                    cost = (sum(bool(r.get('changed')) for r in changes),
                            sum(max(0, r.get('arrival_shift_s', 0)) for r in changes),
                            sum(max(0, r.get('distance_delta_m', 0)) for r in changes))
                    feasible.append((cost, candidate['id']))
            preferred[name] = min(feasible)[1] if feasible else 'hold_no_verified_candidate'
    return {'method': 'visible_information_two_branch_robust_comparison',
            'hypothetical_boundary_s': boundary, 'hypothetical_boundary_is_evidence': False,
            'branch_probabilities': None, 'changed_task_ids': changed, 'affected_task_ids': affected,
            'classification_change_count': len(changed), 'actions_by_branch': actions,
            'preferred_candidate_by_branch': preferred,
            'candidate_comparison_scope': 'query_priority_only; original task/resource review plus branch geometry; no adoption authority',
            'recommendation_changes': actions['unconfirmed_occupancy'] != actions['hypothetical_completion'] or
                len(set(preferred.values())) > 1,
            'limitations': 'Two declared completion branches, not expected utility or a calibrated probability.'}


def generate_requests(world: dict, analysis: dict | None = None, *, source_layer: str = 'conflict_analysis',
                      max_queries: int = 1, deadline_s: float | None = None,
                      candidates: list[dict] | None = None) -> list[dict]:
    """Query only when a visible counterfactual changes a conflict or decision."""
    validate_world(world)
    if source_layer not in ('conflict_analysis', 'maintenance_decision'):
        raise ContractError('unknown evidence source_layer')
    integer(max_queries, 'max_queries')
    deadline = world['horizon_s'][1] if deadline_s is None else number(deadline_s, 'deadline_s', minimum=0)
    requests = []
    for activity in world['activities']:
        if activity['completion_status'] == 'confirmed':
            continue
        comparison = _branch_comparison(world, activity, candidates)
        triggered = comparison['classification_change_count'] > 0 if source_layer == 'conflict_analysis' else comparison['recommendation_changes']
        if not triggered:
            continue
        requests.append({
            'id': f'evidence:{source_layer}:{world["scenario_id"]}:{activity["id"]}:{world["environment_version"]}',
            'source_layer': source_layer, 'object_id': activity['id'], 'field': 'completion_status',
            'related_task_ids': comparison['affected_task_ids'],
            'required_evidence': 'Visible, source-linked completion confirmation with effective time',
            'possible_impact': 'May change conflict classification or maintenance choice; facility occupancy remains',
            'deadline_s': deadline, 'max_queries': max_queries, 'cost_budget': max_queries,
            'query_unit_cost': 1, 'environment_version': world['environment_version'],
            'scenario_id': world['scenario_id'], 'rule_version': world['rule_version'], 'model_version': world['model_version'],
            'trigger': 'conflict_sensitivity_branch_change' if source_layer == 'conflict_analysis' else 'maintenance_decision_branch_change',
            'decision_value_estimation': 'bounded_counterfactual_comparison_no_probabilities',
            'comparison': comparison, 'status': 'pending',
            'allowed_source': 'observed world records or explicitly configured available_records/synthetic_evidence_source',
        })
    return sorted(requests, key=lambda r: (-r['comparison']['classification_change_count'], r['id']))


def query_evidence(world: dict, request: dict, *, now_s: float | None = None, queries_used: int = 0,
                   evidence_source: dict | None = None, timeout_s: float = 5) -> dict:
    """One unit-cost query; sources contain only existing, timestamped records.

    An explicit synthetic source may declare simulated_delay_s to exercise the
    timeout path without sleeping. No simulator scenario definitions are accepted.
    """
    validate_world(world)
    stable_id(request.get('id'), 'request.id')
    integer(queries_used, 'queries_used')
    budget = integer(request.get('max_queries'), 'max_queries')
    deadline = number(request.get('deadline_s'), 'deadline_s', minimum=0)
    timeout = number(timeout_s, 'timeout_s', minimum=0)
    now = world['as_of_s'] if now_s is None else number(now_s, 'now_s', minimum=0)
    if now < world['as_of_s']:
        raise ContractError('evidence query now_s cannot precede its visible snapshot')
    result = {'request_id': request['id'], 'object_id': request.get('object_id'),
              'field': request.get('field'), 'status': 'unknown', 'answer': None, 'evidence': [],
              'queries_used': queries_used, 'query_cost': 0, 'remaining_queries': max(0, budget - queries_used),
              'as_of_s': world['as_of_s'], 'queried_at_s': now,
              'environment_version': world['environment_version'], 'scenario_id': world['scenario_id'],
              'rule_version': world['rule_version'], 'model_version': world['model_version'], 'world_updated': False}
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
        result.update(status='unsupported', reason='Evidence lookup currently supports completion_status only')
        return result
    activity = next((a for a in world['activities'] if a['id'] == request.get('object_id')), None)
    if activity is None:
        raise ContractError('request references missing activity')
    result.update(queries_used=queries_used + 1, query_cost=1, remaining_queries=budget - queries_used - 1)
    visible_sources = {s['id']: s for s in world['known_sources'] if s['observed_at_s'] <= world['as_of_s']}
    records = [deepcopy(entry) for entry in world['event_log']
               if entry['object_id'] == activity['id'] and entry['observed_at_s'] <= world['as_of_s']
               and entry['effective_at_s'] <= world['as_of_s']
               and entry['event']['kind'] == 'activity_completion_confirmed'
               and all(source_id in visible_sources for source_id in entry['source_ids'])]
    if activity['completion_status'] == 'confirmed':
        ids = activity.get('source_ids', [activity['source_id']])
        sources = [deepcopy(visible_sources[source_id]) for source_id in ids if source_id in visible_sources]
        if records or sources:
            result.update(status='resolved', answer={'completion_status': 'confirmed',
                          'completion_confirmed_at_s': activity['completion_confirmed_at_s']},
                          evidence=records or sources, reason='Confirmation already present in visible records')
            return result
    if evidence_source is not None:
        source_id = stable_id(evidence_source.get('id'), 'evidence_source.id')
        source_kind = evidence_source.get('kind')
        if source_kind not in ('available_records', 'synthetic_evidence_source'):
            raise ContractError('evidence_source must explicitly identify available records or synthetic evidence')
        if 'scenario_definitions_for_simulator_only' in evidence_source:
            raise ContractError('evidence source cannot be a simulator truth payload')
        delay = number(evidence_source.get('simulated_delay_s', 0), 'simulated_delay_s', minimum=0)
        if delay and source_kind != 'synthetic_evidence_source':
            raise ContractError('only explicitly synthetic sources can declare simulated delay')
        if delay > timeout or now + delay >= deadline:
            result.update(status='timeout' if delay > timeout else 'deadline_exceeded',
                          reason='Explicit synthetic source response exceeds query timeout/deadline; unknown preserved',
                          source_id=source_id, simulated_delay_s=delay)
            return result
        visible = []
        for record in evidence_source.get('records', []):
            validate_event(record)
            if record['observed_at_s'] > world['as_of_s'] or record['effective_at_s'] > world['as_of_s']:
                continue
            if record['kind'] == 'activity_completion_confirmed' and record.get('activity_id') == activity['id']:
                visible.append(record)
        times = {record['effective_at_s'] for record in visible}
        if len(times) > 1:
            result.update(status='conflicting_evidence', reason='Conflicting visible completion times require explicit source reconciliation')
            return result
        if visible:
            event = deepcopy(sorted(visible, key=lambda e: (e['observed_at_s'], e['id']))[-1])
            event['id'] = f'evidence:{source_id}:{event["id"]}'
            event['evidence_refs'] = list(dict.fromkeys([*event.get('evidence_refs', []), source_id]))
            # Validate state transition on a disposable world before offering a write.
            apply_event(world, event)
            result.update(status='resolved', answer={'completion_status': 'confirmed',
                          'completion_confirmed_at_s': event['effective_at_s']}, proposed_event=event,
                          evidence=[{'source_id': source_id, 'source_kind': source_kind, 'event': deepcopy(event)}],
                          reason='Existing visible source record validated by first layer; update still explicit')
            return result
    result.update(status='unknown', reason='No visible completion confirmation; planned end is not evidence of completion')
    return result


def apply_evidence_result(world: dict, result: dict) -> dict:
    """No-result is a no-op; resolved updates must cross the layer-1 boundary."""
    validate_world(world)
    if result.get('status') != 'resolved' or 'proposed_event' not in result:
        return deepcopy(world)
    if any(result.get(key) != world[key] for key in ('environment_version', 'scenario_id', 'rule_version', 'model_version')):
        raise ContractError('stale evidence result requires a fresh query')
    event = result['proposed_event']
    if event.get('kind') != 'activity_completion_confirmed' or event.get('activity_id') != result.get('object_id'):
        raise ContractError('evidence result event does not match requested object/field')
    if event['effective_at_s'] != result.get('answer', {}).get('completion_confirmed_at_s'):
        raise ContractError('evidence answer differs from its event')
    if not any(row.get('event') == event and row.get('source_kind') in ('available_records', 'synthetic_evidence_source')
               for row in result.get('evidence', [])):
        raise ContractError('evidence update requires a traceable visible source record')
    return apply_event(world, event)


def resolve_requests(world: dict, requests: list[dict], *, evidence_source: dict | None = None) -> list[dict]:
    return [query_evidence(world, request, evidence_source=evidence_source) for request in requests]
