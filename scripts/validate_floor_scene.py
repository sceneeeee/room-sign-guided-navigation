#!/usr/bin/env python3
import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'data/maps/1f'
scene=json.loads((root/'floor_1f_experiment_scene.json').read_text())
route=json.loads((root/'route_LSS101_LSS124_LSS119.json').read_text())
assert scene['source']['rectified_raster']=='floor_1f_base_modified.tif'
assert (root/'floor_1f_base_modified.tif').exists()
rooms={x['id'] for x in scene['rooms']}
assert route['start'] in rooms and route['goal'] in rooms
portals={x['id'] for x in scene['portals']}
assert set(route['candidate_portals']) <= portals
assert route['status']=='candidate'
assert 'verify' in route['execution_policy']
print('scene validation: PASS')
print('raster:', root/'floor_1f_base_modified.tif')
print('route:', route['route_id'])
print('status:', route['status'])
