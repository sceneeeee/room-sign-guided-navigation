import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]/'data/maps/1f'
m=json.loads((root/'SCENE_MANIFEST.json').read_text())
for key in ['rectified_source','qgis_project','vector_draft','semantic_scene','classroom_targets','candidate_route','route_preview']:
    assert (root/m[key]).exists(), key
assert m['rectified_source']=='floor_1f_base_modified.tif'
assert m['checks']['uses_unrectified_photo'] is False
assert m['checks']['direct_execution_disabled_until_portal_verification'] is True
print('manifest validation: PASS')
