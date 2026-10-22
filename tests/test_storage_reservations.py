"""Persist all joint resource occupancies, including waits between flights."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from chengkong.analysis import check_joint_tasks
from chengkong.storage import PlanStore
from chengkong.world import load_example,visible_world,advance_world,generate_constraints
from chengkong.verification import verify_candidate

ROOT=Path(__file__).resolve().parents[1]


class CompleteReservationStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        world=visible_world(load_example(ROOT/'examples/park_crane_synthetic.json'),'end_unconfirmed',60)
        world['activities'][0]['activity_envelope']['center_xy_m']=[700,100]
        world['tasks'][1]['aircraft_id']=world['tasks'][0]['aircraft_id']
        world['aircraft_turnaround_model']={'ground_transfer_speed_mps':20.,'ground_transfer_energy_per_m':.01,
                                           'turnaround_s':10.,'recharge_rate_per_s':1.}
        world['waiting_areas']=[{'id':'wait_at_west','position_m':[100,400,0],'capacity':1}]
        task=world['tasks'][0]
        for point in task['trajectory'][1:]:point['time_s']+=5
        task['trajectory'].insert(1,{'time_s':95,'position_m':[100,400,0]})
        task['ground_wait_interval_s']=[90,95];task['actual_departure_time_s']=95
        self.store=PlanStore(Path(self.tmp.name)/'plans.sqlite3');self.store.initialize(world)

    def snapshot(self):return (self.store.current_world(),self.store.current_reservations(),self.store.history())

    def assert_complete_rows(self,world,wait_end,turnaround_start):
        snapshot=self.store.current_reservations();rows=snapshot['reservations']
        self.assertEqual(snapshot['schema_version'],'reservation-snapshot-v2')
        self.assertEqual(rows,check_joint_tasks(world['tasks'],world)['reservations'])
        wait=next(row for row in rows if row['resource_id']=='wait_at_west')
        self.assertEqual(wait['interval_s'],[90,wait_end])
        turnaround=next(row for row in rows if 'aircraft_turnaround_and_charge' in row['kinds'])
        self.assertEqual(turnaround['interval_s'],[turnaround_start,350])
        self.assertEqual(turnaround['related_task_ids'],['task_1','task_2'])

    def test_full_joint_reservations_initialize_adopt_replace_and_rollback(self):
        world=self.store.current_world();self.assert_complete_rows(world,95,249)
        tasks=copy.deepcopy(world['tasks']);task=tasks[0]
        for point in task['trajectory'][1:]:point['time_s']+=5
        task['ground_wait_interval_s']=[90,100];task['actual_departure_time_s']=100
        candidate={'id':'complete-reservations','candidate_schema_version':2,'tasks':tasks,
            'base_environment_version':world['environment_version'],'base_reservation_version':world['reservation_version'],
            'base_plan_versions':{t['id']:t['plan_version'] for t in world['tasks']},
            'scenario_id':world['scenario_id'],'rule_version':world['rule_version'],'model_version':world['model_version'],
            'valid_interval_s':world['horizon_s']}
        self.assertTrue(verify_candidate(candidate,world,generate_constraints(world))['feasible'])
        before=self.snapshot()
        with patch.object(self.store,'_after_state_write',side_effect=RuntimeError('injected after full reservation write')):
            with self.assertRaisesRegex(RuntimeError,'injected'):self.store.adopt(candidate,explicit=True)
        self.assertEqual(self.snapshot(),before)
        self.assertTrue(self.store.adopt(candidate,explicit=True)['adopted'])
        current=self.store.current_world();self.assert_complete_rows(current,100,254)
        replacement=advance_world(current,70)
        replacement['waiting_areas'][0]['capacity']=2;replacement['environment_version']+=1
        before=self.snapshot()
        with patch.object(self.store,'_after_state_write',side_effect=RuntimeError('injected full replacement')):
            with self.assertRaisesRegex(RuntimeError,'injected'):self.store.replace_world(replacement,current['state_revision'])
        self.assertEqual(self.snapshot(),before)
        updated=self.store.replace_world(replacement,current['state_revision'])
        self.assertEqual(updated['reservation_version'],current['reservation_version']+1)
        self.assert_complete_rows(updated,100,254)
        self.assertEqual(PlanStore(self.store.path).current_reservations(),self.store.current_reservations())

    def test_missing_turnaround_model_never_claims_complete_reservation_ledger(self):
        world=self.store.current_world();world.pop('aircraft_turnaround_model');world['environment_version']+=1
        self.store.replace_world(world,world['state_revision'])
        snapshot=self.store.current_reservations()
        self.assertFalse(snapshot['checks_completed'])
        self.assertIn('aircraft_turnaround_unmodeled',{row.get('kind') for row in snapshot['unchecked']})


if __name__=='__main__':unittest.main()
