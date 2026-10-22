"""Dependency-aware task/constraint pair cache; joint resources always rechecked.

Pair keys include all visible task and constraint fields (except output version
stamps) and rule/model/scenario/horizon. New geometry is always checked against
all tasks, so an old association graph cannot hide a newly affected task.
"""
from copy import deepcopy
import hashlib
import json

from .analysis import check_task, check_joint_tasks
from .world import generate_constraints
from .contracts import validate_world


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _stamp(report, world):
    result = deepcopy(report)
    def visit(value):
        if isinstance(value, dict):
            for key in ('environment_version', 'scenario_id', 'rule_version', 'model_version', 'as_of_s', 'horizon_s'):
                if key in value:
                    value[key] = deepcopy(world.get(key))
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(result)
    return result


class DependencyEngine:
    """In-memory optimization only. No cached result grants adoption authority."""
    def __init__(self):
        self._pairs = {}
        self._constraint_fingerprints = {}
        self._last_version = None

    def analyze(self, world, candidates=None):
        validate_world(world)
        constraints = generate_constraints(world)
        context = {k: world.get(k) for k in ('scenario_id', 'rule_version', 'model_version', 'horizon_s')}
        reports, reused, computed = {}, 0, 0
        next_cache, fingerprints = {}, {}
        changed, recomputed_tasks = set(), set()
        dependencies = {'object_constraints': {}, 'constraint_tasks': {}, 'task_candidates': {},
                        'candidate_tasks': {}, 'candidate_constraints': {}}
        for constraint in constraints:
            signature = {k: v for k, v in constraint.items() if k not in ('environment_version', 'generated_as_of_s')}
            fingerprint = _fingerprint(signature)
            fingerprints[constraint['id']] = fingerprint
            if self._constraint_fingerprints.get(constraint['id']) != fingerprint:
                changed.add(constraint['id'])
            dependencies['object_constraints'].setdefault(constraint['object_id'], []).append(constraint['id'])
            dependencies['constraint_tasks'][constraint['id']] = []
        changed.update(set(self._constraint_fingerprints) - set(fingerprints))
        for task in world['tasks']:
            report = check_task(task, [], world)
            task_hash = _fingerprint({'task': task, 'context': context})
            for constraint in constraints:
                key = (task['id'], constraint['id'], task_hash, fingerprints[constraint['id']])
                if key in self._pairs:
                    pair = _stamp(self._pairs[key], world)
                    reused += 1
                else:
                    pair = check_task(task, [constraint], world)
                    computed += 1
                    recomputed_tasks.add(task['id'])
                next_cache[key] = deepcopy(pair)
                report['conflicts'].extend(pair['conflicts'])
                report['associations'].extend(pair['associations'])
                for row in pair['unchecked']:
                    if row not in report['unchecked']:
                        report['unchecked'].append(deepcopy(row))
                report['screened_out_pairs'] += pair['screened_out_pairs']
                report['continuous_pairs_checked'] += pair['continuous_pairs_checked']
                if pair['associations'] or pair['unchecked']:
                    dependencies['constraint_tasks'][constraint['id']].append(task['id'])
            report['checks_completed'] = not report['unchecked']
            report['status'] = 'conflict' if report['conflicts'] else 'not_checked' if report['unchecked'] else 'clear'
            reports[task['id']] = report
        invalidated = []
        for candidate in candidates or []:
            cid = candidate['id']
            task_ids = [t['id'] for t in candidate.get('tasks', [])]
            dependencies['candidate_tasks'][cid] = task_ids
            # Candidate paths can introduce previously absent associations: until
            # independent review establishes exact dependencies, all constraints.
            dependencies['candidate_constraints'][cid] = [c['id'] for c in constraints]
            for task_id in task_ids:
                dependencies['task_candidates'].setdefault(task_id, []).append(cid)
            if (candidate.get('base_environment_version') != world['environment_version']
                    or (world.get('reservation_version') is not None and candidate.get('base_reservation_version') != world['reservation_version'])
                    or any(candidate.get(key,world[key]) != world[key] for key in ('scenario_id','rule_version','model_version'))
                    or any(
                    candidate.get('base_plan_versions', {}).get(t['id']) != t['plan_version'] for t in world['tasks'])):
                invalidated.append(cid)
        result = {'baseline_analysis': reports, 'joint_analysis': check_joint_tasks(world['tasks'], world),
                  'constraints': constraints, 'dependencies': dependencies,
                  'invalidated_candidate_ids': invalidated, 'changed_constraint_ids': sorted(changed),
                  'cache': {'pairs_reused': reused, 'pairs_recomputed': computed,
                            'recomputed_task_ids': sorted(recomputed_tasks), 'joint_resources': 'full_recompute',
                            'candidate_dependency_policy': 'conservative_all_constraints_until_independent_review'},
                  'environment_version': world['environment_version']}
        self._pairs, self._constraint_fingerprints = next_cache, fingerprints
        self._last_version = world['environment_version']
        return result

    update = analyze
