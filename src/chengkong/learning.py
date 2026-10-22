"""Learning boundaries; optional trained synthetic artifacts are explicitly loaded."""
from __future__ import annotations

from typing import Any, Protocol

from .contracts import ContractError


class MultimodalParser(Protocol):
    def parse(self, records: list[dict], context: dict) -> dict:
        """Return status, candidate_fields, evidence_locations, unknown_fields, model_version."""
        ...


class SensitivityModel(Protocol):
    def predict(self, task_constraint_features: list[dict], context: dict) -> dict:
        """Return ordered pair_ids and scores for scheduling checks; never safety decisions."""
        ...


class AssociationModel(Protocol):
    def predict(self, relation_graph: dict, context: dict) -> dict:
        """Return task repair/review order; preserve every required checking edge."""
        ...


class UncertaintyCalibrator(Protocol):
    def calibrate(self, known_features: dict, context: dict) -> dict:
        """Return calibrated intervals only with independent residuals and stated assumptions."""
        ...


class RepairRankingModel(Protocol):
    def rank(self, proposals: list[dict], context: dict) -> dict:
        """Order unsolved repair actions; exclude future truth and post-solve outcomes."""
        ...


def _result(capability: str, status: str, *, fallback: dict | None = None) -> dict:
    return {'capability': capability, 'status': status, 'model_version': 'none',
            'output': None, 'fallback': fallback, 'trained': False,
            'reason': 'No configured API' if status == 'not_configured' else 'No trained artifact or calibration dataset',
            'safety_authority': 'none; independent geometry, timing, task and resource checks remain required'}


class UnconfiguredMultimodalParser:
    def parse(self, records: list[dict], context: dict) -> dict:
        result = _result('vlm_llm_parser', 'not_configured')
        result.update(candidate_fields=[], evidence_locations=[], unknown_fields=['unparsed_input'])
        return result


class UntrainedSensitivityModel:
    def predict(self, task_constraint_features: list[dict], context: dict) -> dict:
        # Sorting is only an optional deterministic processing order, never a pruning threshold.
        ordered = sorted(task_constraint_features,
                         key=lambda f: (-int(bool(f.get('has_unknown_fields'))), str(f.get('pair_id', ''))))
        return _result('xgboost_sensitivity', 'not_trained', fallback={
            'source': 'deterministic_rule', 'rule_version': 'rules-v1',
            'ordered_pair_ids': [f['pair_id'] for f in ordered], 'scores': None,
            'rule': 'unknown fields first, then stable pair id; check all pairs', 'allows_check_skipping': False})


class UntrainedAssociationModel:
    def predict(self, relation_graph: dict, context: dict) -> dict:
        # The explicit graph is retained; a GNN prediction is not fabricated.
        return _result('gnn_association', 'not_trained', fallback={
            'source': 'explicit_relation_graph', 'graph': relation_graph, 'predicted_scores': None})


class UntrainedCalibrator:
    def calibrate(self, known_features: dict, context: dict) -> dict:
        result = _result('quantile_conformal_calibration', 'not_trained')
        result.update(calibrated=False, calibration_sample_count=0, coverage=None,
                      interval=None, distribution_assumptions=None)
        return result


class UntrainedRepairRanker:
    def rank(self, proposals: list[dict], context: dict) -> dict:
        actions = {'keep': 0, 'delay': 1, 'detour': 2, 'hold': 3}
        if any('id' not in proposal for proposal in proposals):
            raise ContractError('repair ranking requires proposal ids')
        ordered = sorted(proposals, key=lambda p: (actions.get(p.get('action'), 4), p['id']))
        return _result('learning_repair_ranking', 'not_trained', fallback={
            'source': 'deterministic_rule', 'rule_version': 'rules-v1',
            'ordered_proposal_ids': [p['id'] for p in ordered], 'scores': None,
            'rule': 'keep, delay, detour, hold; id tie-break; actual solved feasibility/cost decides selection'})


def capability_statuses(model_dir=None) -> dict[str, dict[str, Any]]:
    statuses = {name: _result(name, status) for name, status in (
        ('vlm_llm_parser', 'not_configured'), ('xgboost_sensitivity', 'not_trained'),
        ('gnn_association', 'not_trained'), ('quantile_conformal_calibration', 'not_trained'),
        ('learning_repair_ranking', 'not_trained'), ('learned_world_evolution', 'not_trained'))}
    if model_dir is not None:
        import json
        from pathlib import Path
        manifest = json.loads((Path(model_dir) / 'manifest.json').read_text())
        for name in ('xgboost_sensitivity', 'gnn_association',
                     'quantile_conformal_calibration', 'learning_repair_ranking'):
            statuses[name].update(status='trained_synthetic', trained=True,
                model_version=manifest['model_version'], reason='Loaded synthetic experiment artifact',
                default_enabled=False, real_world_validated=False,
                training_scope=manifest['scope'])
    else:
        for row in statuses.values():
            if row['status'] == 'not_trained':
                row['reason'] = 'No artifact loaded in this runtime; explicit model_dir required'
    return statuses
