"""Leakage, real graph gradients, grouped calibration and saved inference regressions."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from chengkong.synthetic import (make_event,split_templates,pair_features,graph_features,
    action_features,quantile_features,PAIR_FEATURES,NODE_FEATURES,ACTION_FEATURES,QUANTILE_FEATURES)
from chengkong.world import generate_constraints

try:
    import numpy as np
    from chengkong.model_runtime import ModelBundle,SmallGNN,matrix
    LEARNING=True
except ImportError:
    LEARNING=False

ROOT=Path(__file__).resolve().parents[1]


class VisibilityAndGrouping(unittest.TestCase):
    def test_hidden_end_and_post_solve_fields_never_reach_features(self):
        event=make_event(20261023,1);world=event['world'];constraints=generate_constraints(world)
        task=world['tasks'][0]
        before=(pair_features(task,constraints[0],world),graph_features(world,constraints),
                action_features(task,'detour',world,constraints),quantile_features(world))
        # Deliberately inject the answers in all tempting input locations.
        event['simulator_truth']['actual_end_s']=1e12
        world['actual_end_s']=1e12;world['hidden_branch']='would_finish_early'
        world['activities'][0]['actual_end_s']=-1
        task['actual_cost']=-1e12;task['feasible']=True;task['solve_status']='found'
        constraints[0]['future_actual_end_s']=-100
        after=(pair_features(task,constraints[0],world),graph_features(world,constraints),
               action_features(task,'detour',world,constraints),quantile_features(world))
        self.assertEqual(before,after)
        names=PAIR_FEATURES+NODE_FEATURES+ACTION_FEATURES+QUANTILE_FEATURES
        for forbidden in ('actual_end_s','actual_cost','feasible','relevance','solve_status','template_family'):
            self.assertNotIn(forbidden,names)

    def test_complete_template_variants_share_one_split(self):
        mapping=split_templates(48,20261023)
        sets={split:{g for g,s in mapping.items() if s==split} for split in ['train','validation','calibration','test']}
        self.assertEqual(len(set.union(*sets.values())),48)
        for a in sets:
            for b in sets:
                if a!=b:self.assertFalse(sets[a]&sets[b])
        for i in range(48):
            variants=[make_event(20261023,i,v) for v in range(3)]
            self.assertEqual(len({mapping[v['template_family']] for v in variants}),1)

    def test_missing_geometry_has_mask_and_stays_missing(self):
        world=make_event(20261023,0)['world'];world['activities'][0]['activity_envelope']=None
        constraints=generate_constraints(world);c=next(c for c in constraints if c['kind']=='activity')
        row=pair_features(world['tasks'][0],c,world)
        self.assertEqual(row['geometry_missing'],1)
        self.assertIsNone(world['activities'][0]['activity_envelope'])


@unittest.skipUnless(LEARNING,'optional requirements-learning.txt not installed')
class ActualGraphTraining(unittest.TestCase):
    def test_message_passing_changes_predictions_and_gradients_match_difference(self):
        model=SmallGNN(2,3,7);x=np.array([[.2,.7],[1.5,-.1]])
        adjacency=[[1,1],[1,1]];targets=[.2,.8]
        result,_=model.forward(x,adjacency);isolated,_=model.forward(x,[[1,0],[0,1]])
        self.assertGreater(float(np.max(abs(result-isolated))),1e-6)
        loss,grad=model.loss_and_gradients(x,adjacency,targets)
        self.assertGreater(float(np.linalg.norm(grad['W1'])),1e-7)
        for name,index in [('W1',(0,0)),('W2',(0,)),('b1',(0,)),('b2',(0,))]:
            parameter=getattr(model,name);old=parameter[index];eps=1e-5
            parameter[index]=old+eps;plus=model.loss_and_gradients(x,adjacency,targets)[0]
            parameter[index]=old-eps;minus=model.loss_and_gradients(x,adjacency,targets)[0]
            parameter[index]=old
            self.assertAlmostEqual(grad[name][index],(plus-minus)/(2*eps),places=7)

    def test_train_updates_message_passing_weights_and_roundtrips(self):
        rows=[dict(zip(NODE_FEATURES,np.linspace(0,i+1,len(NODE_FEATURES)))) for i in range(3)]
        graph={'node_features':rows,'adjacency':[[1,1,0],[1,1,1],[0,1,1]],'targets':[.05,.8,.9]}
        model=SmallGNN(len(NODE_FEATURES),4,3);old=model.W1.copy()
        loss=model.fit([graph],epochs=80,learning_rate=.03)
        self.assertLess(loss[-1],loss[0]);self.assertFalse(np.allclose(old,model.W1))
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'weights.npz';model.save(path);loaded=SmallGNN.load(path)
            np.testing.assert_allclose(model.forward(matrix(rows,NODE_FEATURES),graph['adjacency'])[0],
                                      loaded.forward(matrix(rows,NODE_FEATURES),graph['adjacency'])[0])


@unittest.skipUnless(LEARNING and (ROOT/'configs/learning-artifacts.json').exists(),'trained optional artifacts unavailable')
class SavedSyntheticArtifacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.summary=json.loads((ROOT/'configs/learning-artifacts.json').read_text())
        if not Path(cls.summary['artifact_dir']).exists():raise unittest.SkipTest('External data-disk model artifacts absent')
        cls.bundle=ModelBundle(cls.summary['artifact_dir'])
        cls.rows=json.loads((Path(cls.summary['data_dir'])/'dataset.json').read_text())

    def test_loaded_three_models_and_calibrator_infer_finite_values(self):
        world=make_event(20261023,7)['world'];constraints=generate_constraints(world);before=deepcopy(world)
        for value in [self.bundle.sensitivity(world,constraints),self.bundle.association(world,constraints),
                self.bundle.rank_proposals(world,constraints,[{'id':'delay','action':'delay'},{'id':'detour','action':'detour'}])]:
            self.assertEqual(value['status'],'trained_synthetic')
            self.assertFalse(value['allows_check_skipping'])
            self.assertTrue(np.isfinite(list(value['scores'].values())).all())
        interval=self.bundle.end_error_interval(quantile_features(world))
        self.assertLessEqual(*interval['interval_s']);self.assertTrue(interval['not_flight_safety_probability'])
        self.assertEqual(world,before)

    def test_saved_labels_are_separate_and_every_family_is_single_split(self):
        groups={}
        for row in self.rows:
            groups.setdefault(row['template_family'],set()).add(row['split'])
            for a in row['actions']:
                self.assertEqual(set(a['features']),set(ACTION_FEATURES))
                self.assertNotIn('feasible',a['features']);self.assertNotIn('actual_cost',a['features'])
            for p in row['pairs']:self.assertEqual(set(p['features']),set(PAIR_FEATURES))
        self.assertTrue(all(len(s)==1 for s in groups.values()))
        c=self.bundle.calibration
        self.assertEqual(set(c['calibration_template_ids']),{g for g,s in groups.items() if s=={'calibration'}})
        self.assertEqual(c['independent_template_groups'],len(c['calibration_template_ids']))
        self.assertLess(c['independent_template_groups'],c['sample_count'])
        self.assertFalse(self.bundle.manifest['data_manifest']['upstream_predictions_in_downstream_features'])

    def test_report_preserves_negative_results_and_default_rule(self):
        self.assertEqual(self.summary['default_policy'],'deterministic_rule')
        self.assertFalse(self.summary['default_enabled'])
        self.assertIn('rule_ndcg',self.summary['evaluation']['results']['test']['gnn'])
        self.assertIn('full_enumeration_min_mean_cost',self.summary['evaluation']['results']['test']['ranking'])
        self.assertEqual(self.summary['configuration_attempts_per_model'],1)
        self.assertLess(self.summary['gnn_training_loss']['final'],self.summary['gnn_training_loss']['initial'])
