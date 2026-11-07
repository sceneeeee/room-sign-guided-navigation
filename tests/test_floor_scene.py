import unittest
from roomnav.floor_scene import FloorScene

class FloorSceneTests(unittest.TestCase):
    def test_uses_rectified_raster_and_resolves_room(self):
        s = FloorScene()
        self.assertTrue(s.source_raster.name.endswith('floor_1f_base_modified.tif'))
        self.assertEqual(s.room('lss119')['id'], 'room_LSS119')
        self.assertTrue(s.portals_for('LSS119'))

    def test_candidate_route_is_not_executable(self):
        s = FloorScene()
        ctx = s.observation_context('LSS119')
        self.assertTrue(ctx['candidate_routes'])
        self.assertIn('disabled', ctx['route_execution'])

if __name__ == '__main__': unittest.main()
