#!/usr/bin/env python3
"""Query the 1F static semantic scene used by room/sign experiments."""
from __future__ import annotations
import argparse, json
from pathlib import Path

DEFAULT = Path(__file__).resolve().parents[1] / 'data/maps/1f/floor_1f_experiment_scene.json'

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--scene',type=Path,default=DEFAULT)
    ap.add_argument('--room')
    ap.add_argument('--route-to')
    args=ap.parse_args()
    scene=json.loads(args.scene.read_text(encoding='utf8'))
    if args.room:
        needle=args.room.upper()
        rows=[r for r in scene['rooms'] if r['label'].upper()==needle]
        if not rows:
            raise SystemExit(f'room not found: {args.room}')
        print(json.dumps({'room':rows[0],'candidate_portals':[x for x in scene['portals'] if x['space_a']==rows[0]['id']]},ensure_ascii=False,indent=2))
        return
    if args.route_to:
        needle=args.route_to.upper()
        rows=[r for r in scene['candidate_routes'] if r['goal'].replace('room_','').upper()==needle]
        print(json.dumps({'routes':rows,'route_execution_policy':scene['observation_contract']['route_execution']},ensure_ascii=False,indent=2))
        return
    print(json.dumps({'scene_id':scene['scene_id'],'status':scene['status'],'room_count':len(scene['rooms']),'portal_count':len(scene['portals']),'routes':scene['candidate_routes']},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
