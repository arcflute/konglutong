import copy,json,tempfile,unittest
from pathlib import Path
from chengkong.world import load_example,visible_world,generate_constraints
from chengkong.planning import generate_candidates
from chengkong.cooperation import stability_cost
from chengkong.verification import verify_candidate


def fixture():
    w=visible_world(load_example('examples/park_crane_synthetic.json'),'end_unconfirmed',60)
    w['scene_relations']=[];w['activities']=[];w['facilities']=[];w['buildings']=[];w['tasks']=w['tasks'][:1]
    return w

class Round4EvaluationTests(unittest.TestCase):
    def test_full_sequence_groups_and_future_records_are_isolated(self):
        from chengkong.synthetic_round4 import freeze,make_sequence
        from chengkong.synthetic_round3 import graph_features
        from chengkong.world import advance_world
        c=json.loads(Path('configs/evaluation-round4.json').read_text())
        with tempfile.TemporaryDirectory() as folder:
            c['data_dir']=folder;p=freeze(c)
            parents={}
            for row in p['templates']:
                parents.setdefault(row['parent_template'],set()).add(row['split'])
            self.assertTrue(all(len(x)==1 for x in parents.values()))
            self.assertEqual(len(p['final_test_ids']),6)
            seq=make_sequence(p['templates'][0]);raw=seq['input']
            original=visible_world(raw,seq['sequence_id'],0)
            altered=copy.deepcopy(raw)
            altered['scenario_definitions_for_simulator_only'][0]['events'][-1]['effective_at_s']+=5
            altered['scenario_definitions_for_simulator_only'][0]['events'][-1]['observed_at_s']+=5
            hidden=visible_world(altered,seq['sequence_id'],0)
            self.assertEqual(original,hidden)
            self.assertEqual(graph_features(original,generate_constraints(original)),graph_features(hidden,generate_constraints(hidden)))
            at=seq['decision_times_s'][1]
            arrived=visible_world(raw,seq['sequence_id'],at)
            self.assertTrue(all(e['observed_at_s']<=at for e in arrived['observed_events']))
            self.assertEqual(len(arrived['observed_events']),1)

    def test_clear_object_but_energy_invalid_task_joins_repair(self):
        w=fixture();t=w['tasks'][0]
        t['energy_assumptions']['capacity']=145
        # Original route has an unnecessary 100-second hover before its service;
        # the direct required path remains feasible with unchanged service/window.
        t['trajectory'].insert(2,{'time_s':t['trajectory'][1]['time_s']+100,'position_m':list(t['trajectory'][1]['position_m'])})
        for p in t['trajectory'][3:]:p['time_s']+=100
        d=[];cs=generate_candidates(w,generate_constraints(w),d)
        self.assertTrue(any(x.get('status')=='repair_set_requirement_expanded' for x in d))
        self.assertTrue(cs,d)
        self.assertTrue(cs[0]['review']['feasible'])
        self.assertEqual(cs[0]['tasks'][0]['mandatory_services'],t['mandatory_services'])
        self.assertEqual(cs[0]['tasks'][0]['deadline_s'],t['deadline_s'])

    def test_ground_wait_prefix_replanning_refreshes_future_takeoff_metadata(self):
        from chengkong.execution import simulate_context,suffix_task,restore_full_task,frozen_prefix
        from chengkong.verification import _task_requirements
        w=fixture();task=w['tasks'][0];start=task['departure_time_s']
        for p in task['trajectory'][1:]:p['time_s']+=20
        task['trajectory'].insert(1,{'time_s':start+20,'position_m':list(task['trajectory'][0]['position_m'])})
        task['actual_departure_time_s']=start+20;task['ground_wait_interval_s']=[start,start+20]
        w['as_of_s']=start+10;context=simulate_context(w);suffix=suffix_task(task,context)
        suffix['trajectory'].pop(1)
        for p in suffix['trajectory'][1:]:p['time_s']-=10
        result=restore_full_task(task,suffix,context)
        self.assertEqual(frozen_prefix(result,w['as_of_s']),context['tasks'][task['id']]['frozen_prefix'])
        self.assertEqual(result['actual_departure_time_s'],start+10)
        self.assertEqual(result['ground_wait_interval_s'],[start,start+10])
        violations,unchecked,_=_task_requirements(result,task,w,context)
        self.assertFalse(violations);self.assertFalse(unchecked)

    def test_same_aircraft_priority_cannot_reverse_continuation(self):
        w=fixture();first=w['tasks'][0];second=copy.deepcopy(first);second['id']='later';second['priority']=0
        second['departure_time_s']+=170
        for p in second['trajectory']:p['time_s']+=170
        second['mandatory_services'][0]['window_s']=[260,800]
        first['energy_assumptions']['capacity']=second['energy_assumptions']['capacity']=300
        w['tasks'].append(second)
        w['aircraft_turnaround_model']={'ground_transfer_speed_mps':40.,'ground_transfer_energy_per_m':.01,'turnaround_s':10.,'recharge_rate_per_s':1.}
        # Energy does not explain the initial overlap/transition; only chronology.
        d=[];cs=generate_candidates(w,generate_constraints(w),d)
        self.assertTrue(cs,d)
        for c in cs:
            order=c['search_priority_order']
            if order:self.assertLess(order.index(first['id']),order.index('later'))
        self.assertTrue(cs[0]['review']['feasible'])

if __name__=='__main__':unittest.main()
