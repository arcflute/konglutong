"""Preview effective time uses current knowledge, while saved worlds stay intact."""
from copy import deepcopy
from pathlib import Path
import unittest

from chengkong.presentation import spatial_view
from chengkong.world import load_example,visible_world,apply_event

EXAMPLE=Path(__file__).resolve().parents[1]/'examples/park_crane_synthetic.json'


class PresentationTests(unittest.TestCase):
    def test_late_geometry_and_removed_facility_preview_valid_time(self):
        before=visible_world(load_example(EXAMPLE),'end_unconfirmed',400)
        saved=deepcopy(before)
        geometry=[{'type':'aabb','min_m':[10,20,0],'max_m':[20,30,40]}]
        common={'observed_at_s':400,'source_kind':'synthetic_preview_test'}
        current=apply_event(before,{**common,'id':'late_geometry','kind':'facility_geometry_changed',
            'effective_at_s':200,'facility_id':'crane_1','physical_geometry':geometry})
        current=apply_event(current,{**common,'id':'complete','kind':'activity_completion_confirmed',
            'effective_at_s':300,'activity_id':'lifting_1'})
        current=apply_event(current,{**common,'id':'remove','kind':'facility_removal_confirmed',
            'effective_at_s':350,'facility_id':'crane_1'})
        old=spatial_view(current,150);new=spatial_view(current,250);gone=spatial_view(current,360)
        self.assertEqual(len([o for o in old['objects'] if o['id']=='crane_1']),2)
        self.assertEqual([o['geometry'] for o in new['objects'] if o['id']=='crane_1'],geometry)
        self.assertFalse(any(o['id'] in ('crane_1','lifting_1') for o in gone['objects']))
        self.assertEqual(new['temporal_status'],'reconstructed_from_current_knowledge')
        self.assertEqual(new['known_as_of_s'],400)
        self.assertEqual(before,saved)
        # A saved decision world still contains only the old geometry/unknown end.
        historical=spatial_view(saved,360)
        self.assertTrue(any(o['id']=='lifting_1' for o in historical['objects']))
        self.assertEqual(len([o for o in historical['objects'] if o['id']=='crane_1']),2)


if __name__=='__main__':
    unittest.main()
