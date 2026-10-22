"""Independent grouping, labels, calibration and real ordering invariants."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from chengkong.synthetic_round3 import (make_event,split_templates,pair_features,graph_features,action_features,label_event,solve_action,
    PAIR_FEATURES,NODE_FEATURES,ACTION_FEATURES,QUANTILE_FEATURES)
try:
    import numpy as np
    from chengkong.learning_round3_runtime import finite_group_calibration,ResidualGNN,priority_checks
    from chengkong.model_runtime import ModelBundle,matrix
    LEARNING=True
except ImportError:
    LEARNING=False
from chengkong.world import generate_constraints


@unittest.skipUnless(LEARNING,'optional learning dependencies unavailable')
class Round3Contracts(unittest.TestCase):
    def test_fixed_parent_split_and_no_hidden_label_features(self):
        groups=split_templates(160,20261024);self.assertEqual(len(groups),160)
        self.assertEqual(sum(v=='calibration' for v in groups.values()),32)
        event=make_event(20261024,10);world=event['world'];constraints=generate_constraints(world);task=world['tasks'][0]
        before=(pair_features(task,constraints[0],world),graph_features(world,constraints),action_features(task,'delay',world,constraints))
        event['simulator_truth']['activities']['lifting_1']['actual_end_s']=1e99
        world['actual_end_s']=1e99;world['template_family']='label-carrying-id';world['coverage_mode']='would_succeed'
        task.update(feasible=True,actual_cost=-1e9,relevance=3,solve_status='found')
        self.assertEqual(before,(pair_features(task,constraints[0],world),graph_features(world,constraints),action_features(task,'delay',world,constraints)))
        self.assertFalse(set(PAIR_FEATURES+NODE_FEATURES+ACTION_FEATURES+QUANTILE_FEATURES)&{'actual_end_s','actual_cost','template_family','coverage_mode','feasible'})
    def test_finite_sample_quantile_never_clamps_when_n_insufficient(self):
        result=finite_group_calibration({str(i):[float(i),float(i)+1] for i in range(8)},.1)
        self.assertEqual(result['status'],'insufficient_independent_groups');self.assertIsNone(result['correction']);self.assertEqual(result['order_statistic_index_1based'],9)
        exact=finite_group_calibration({str(i):[float(i)] for i in range(9)},.1)
        self.assertEqual(exact['correction'],8);self.assertEqual(exact['independent_template_groups'],9)
    def test_no_artificial_graph_smoothing_and_equal_cost_rank_ties(self):
        event=make_event(20261024,4);row=label_event(event,64,.2)
        self.assertTrue(set(row['graph']['targets'])<={0.,1.})
        groups={}
        for action in row['actions']:
            if action['feasible']:groups.setdefault((action['query_id'],round(action['actual_cost'],6)),set()).add(action['relevance'])
        self.assertTrue(all(len(v)==1 for v in groups.values()))
    def test_started_local_actions_explicitly_unsupported(self):
        world=make_event(20261024,3)['world'];task=world['tasks'][0];world['as_of_s']=task['departure_time_s']+1
        row=solve_action(world,generate_constraints(world),task,'delay',64,.2)
        self.assertEqual(row['solve_status'],'unsupported_inflight_local_action');self.assertFalse(row['feasible'])
    def test_actual_precise_order_and_budget_keep_unknown_obligations(self):
        world=make_event(20261024,5)['world'];constraints=generate_constraints(world)
        expected=[f'{t["id"]}:{c["id"]}' for t in world['tasks'] for c in constraints]
        class Reverse:
            version='test-order-only'
            def sensitivity(self,w,c):return {'pair_order':expected[::-1]}
        result=priority_checks(world,constraints,Reverse(),max_pairs=1)
        self.assertEqual(result['executed'][0]['pair_id'],expected[-1]);self.assertEqual(len(result['unchecked_pair_ids']),len(expected)-1)
        for tid,report in result['reports'].items():
            if any(key.startswith(tid+':') for key in result['unchecked_pair_ids']):self.assertFalse(report['checks_completed']);self.assertNotEqual(report['status'],'clear')
        full=priority_checks(world,constraints,Reverse());self.assertTrue(full['all_pairs_checked'])
        from chengkong.analysis import check_task
        for task in world['tasks']:
            reference=check_task(task,constraints,world)
            self.assertEqual(sorted(json.dumps(x,sort_keys=True) for x in reference['conflicts']),sorted(json.dumps(x,sort_keys=True) for x in full['reports'][task['id']]['conflicts']))
    def test_pair_ids_with_colons_and_percent_cannot_drop_obligations(self):
        from chengkong.pair_keys import pair_key,split_pair_key
        for task_id in ['x','x:activity','x%3Aactivity','x%:a']:
            key=pair_key(task_id,'activity:a');self.assertEqual(split_pair_key(key),(task_id,'activity:a'))
        world=make_event(20261024,5)['world'];world['tasks']=world['tasks'][:2]
        world['tasks'][0]['id']='x';world['tasks'][1]['id']='x:activity'
        original=generate_constraints(world)[0];constraints=[]
        for cid in ['a','activity:a','other']:
            constraint=deepcopy(original);constraint['id']=cid;constraints.append(constraint)
        result=priority_checks(world,constraints)
        self.assertEqual(result['required_pairs'],6);self.assertEqual(result['completed_pairs'],6)
        self.assertEqual(len(set(result['order'])),6);self.assertTrue(result['all_pairs_checked'])
        root=Path('/root/autodl-tmp/chengkong-models/round3')
        if root.exists():
            bundle=ModelBundle(root);advice=bundle.sensitivity(world,constraints)
            self.assertEqual(len(advice['scores']),6);self.assertEqual(priority_checks(world,constraints,bundle)['completed_pairs'],6)
    def test_conditional_completion_uses_task_departure_not_decision_clock(self):
        from chengkong.evaluation_round3 import conditional_end_advice
        world={'as_of_s':10,'activities':[{'planned_interval_s':[0,100]}]}
        task={'departure_time_s':200,'deadline_s':220,'trajectory':[{'time_s':200},{'time_s':220}]}
        decision,needs=conditional_end_advice(world,task,20,10)
        self.assertEqual(decision,'delay_if_completion_confirmed')
        task['departure_time_s']=90
        self.assertEqual(conditional_end_advice(world,task,20,10)[0],'detour_or_hold_needs_solver')
        world['as_of_s']=100
        self.assertEqual(conditional_end_advice(world,task,20,10)[0],'requires_frozen_prefix_replan')

    def test_predictor_cannot_omit_required_pairs(self):
        world=make_event(20261024,5)['world'];constraints=generate_constraints(world)
        class Bad:
            version='bad'
            def sensitivity(self,w,c):return {'pair_order':[]}
        with self.assertRaises(ValueError):priority_checks(world,constraints,Bad())
    def test_graph_channels_gradients_and_adjacency_dependence(self):
        model=ResidualGNN(2,3,11);x=np.array([[.1,.8],[.9,.2],[.2,.3]]);a=[[1,2,0],[2,1,1],[0,1,1]];y=[0,1,0]
        loss,grad=model.loss_and_gradients(x,a,y)
        for key,index in [('W1',(0,0)),('W2',(0,)),('Wself',(0,)),('b1',(0,)),('b2',(0,))]:
            arr=getattr(model,key);old=arr[index];eps=1e-5;arr[index]=old+eps;plus=model.loss_and_gradients(x,a,y)[0];arr[index]=old-eps;minus=model.loss_and_gradients(x,a,y)[0];arr[index]=old
            self.assertAlmostEqual(grad[key][index],(plus-minus)/2/eps,places=7)
        self.assertGreater(float(np.max(abs(model.forward(x,a)[0]-model.forward(x,np.eye(3))[0]))),1e-7)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'g.npz';model.save(path);other=ResidualGNN.load(path);np.testing.assert_allclose(model.forward(x,a)[0],other.forward(x,a)[0])


@unittest.skipUnless(LEARNING and Path('/root/autodl-tmp/chengkong-models/round3/manifest.json').exists(),'round3 artifact not installed')
class Round3Artifacts(unittest.TestCase):
    def test_saved_models_load_dispatch_infer_and_selection_precedes_test(self):
        root=Path('/root/autodl-tmp/chengkong-models/round3');bundle=ModelBundle(root);world=make_event(20261024,5000)['world'];constraints=generate_constraints(world)
        self.assertEqual(type(bundle).__name__,'Round3Bundle')
        for result in [bundle.sensitivity(world,constraints),bundle.association(world,constraints),bundle.rank_proposals(world,constraints,[{'id':'delay','action':'delay'},{'id':'detour','action':'detour'}])]:
            self.assertEqual(result['status'],'trained_synthetic');self.assertFalse(result['allows_check_skipping']);self.assertTrue(np.isfinite(list(result['scores'].values())).all())
        from chengkong.synthetic import quantile_features
        interval=bundle.end_error_interval(quantile_features(world));self.assertTrue(interval['not_flight_safety_probability']);self.assertEqual(interval['calibration_groups'],32)
        frozen=json.loads((root/'selection-frozen.json').read_text());self.assertFalse(frozen['selection_uses_test'])
        from chengkong.training_round3 import sha
        self.assertEqual(frozen['test_sha256'],sha(Path(bundle.manifest['data_dir'])/'test.json'))
    def test_complete_groups_no_cross_split_and_saved_test_metrics_not_training_inputs(self):
        root=Path('/root/autodl-tmp/chengkong-models/round3');manifest=json.loads((root/'manifest.json').read_text());data=Path(manifest['data_dir']);families={};query_ids=set()
        for name in ['train','validation','calibration','test']:
            for row in json.loads((data/(name+'.json')).read_text()):
                families.setdefault(row['parent_template'],set()).add(name)
                groups={}
                for a in row['actions']:
                    groups.setdefault(a['query_id'],[]).append(a['action']);self.assertEqual(set(a['features']),set(ACTION_FEATURES))
                for query,actions in groups.items():self.assertNotIn(query,query_ids);query_ids.add(query);self.assertEqual(sorted(actions),['delay','detour','keep'])
        self.assertTrue(all(len(x)==1 for x in families.values()));self.assertEqual(len(families),160)
