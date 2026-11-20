"""Isaac interpreter process. Only the RGB filename/index handshake reaches the policy host."""
import argparse
import json
from pathlib import Path
import sys
import shutil
import tempfile
import traceback
from types import SimpleNamespace


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--font", type=Path, required=True)
    p.add_argument("--width", type=int, default=768)
    p.add_argument("--height", type=int, default=432)
    args, kit_args = p.parse_known_args()
    sys.argv[1:] = kit_args
    args.output = args.output.resolve()
    from isaacsim import SimulationApp
    app = SimulationApp({"headless": True, "multi_gpu": False, "renderer": "RaytracedLighting",
                         "width": args.width, "height": args.height})
    try:
        import numpy as np
        import omni.usd
        import omni.replicator.core as rep
        from PIL import Image
        from pxr import Gf, UsdGeom
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "isaac_single_door"))
        from scene import Corridor

        directory = args.output
        directory.mkdir(parents=True, exist_ok=True)
        # USD's atomic layer writer needs a native filesystem on Windows. Python
        # copies the authored scene and textures to the shared episode directory.
        native = tempfile.TemporaryDirectory(prefix="roomnav-isaac-", ignore_cleanup_errors=True)
        scene_dir = Path(native.name)
        context = omni.usd.get_context()
        context.new_stage()
        app.update()
        if not context.save_as_stage(str(scene_dir / "corridor.usda")):
            raise RuntimeError("Unable to create USD stage")
        stage = context.get_stage()
        scene = Corridor(stage, scene_dir, args.font, variant="wall_only")
        stage.RemovePrim("/World/Plaque0")
        labels = ["LSS101", "LSS124", "LSS118", "LSS119"]
        centers = [-2.85, -.95, .95, 2.85]
        plates = []
        for i, (x, label) in enumerate(zip(centers, labels)):
            scene.door(x, i)
            center = (x+.73 if i==1 else x, -.14, 1.56)
            scene.plaque(f"RoomPlate{i}", center, label, args.font)
            plates.append(center)
        scene.plaque("BlankPlate", (-2.05,-.14,1.56), "", args.font)
        scene.plaque("Directory", (0,-.14,2.52), "101-124 →", args.font)
        scene.box("PartialScreen", (.58,-.44,.85), (.65,.13,1.70), (.46,.47,.46))
        scene.door_centers = centers
        stage.GetRootLayer().Save()
        shutil.copytree(scene_dir, directory, dirs_exist_ok=True)
        product = rep.create.render_product(scene.camera.GetPath(), (args.width,args.height))
        rgb = rep.AnnotatorRegistry.get_annotator("rgb")
        rgb.attach([product])
        rep.orchestrator.set_capture_on_play(False)
        with (directory / "evaluator.jsonl").open("x") as truth:
            print("ROOMNAV_READY", flush=True)
            for line in sys.stdin:
                cmd = json.loads(line)
                if cmd.get("end"):
                    break
                pose = SimpleNamespace(**cmd["pose"])
                scene.set_pose(pose)
                for _ in range(3):
                    rep.orchestrator.step(rt_subframes=4, delta_time=1/60)
                    app.update()
                rgba = np.asarray(rgb.get_data())
                if rgba.shape != (args.height,args.width,4):
                    raise ValueError(f"Invalid render shape {rgba.shape}")
                index = cmd["index"]
                Image.fromarray(rgba[:,:,:3].astype(np.uint8)).save(directory / f"rgb_{index:05}.png")
                # Independent evaluator projections. Only score visible, fully in-view plates.
                view = scene.camera_transform.Get().GetInverse()
                items = []
                for door in scene.ground_truth():
                    i = door["door_index"]
                    box = door["bbox_xyxy_norm"]
                    if box[0]>=box[2] or box[1]>=box[3]:
                        continue
                    items.append(["door",box,str(i),labels[i]])
                    x,y,z = plates[i]
                    corners = [view.Transform(Gf.Vec3d(x+dx,y,z+dz))
                               for dx in (-.15,.15) for dz in (-.055,.055)]
                    if any(v[2]>=0 for v in corners):
                        continue
                    px = [.5+v[0]*20/(-v[2]*24) for v in corners]
                    py = [.5-v[1]*20/(-v[2]*13.5) for v in corners]
                    if min(px)>=0 and max(px)<=1 and min(py)>=0 and max(py)<=1:
                        items.append(["sign",[min(px),min(py),max(px),max(py)],str(i),labels[i]])
                truth.write(json.dumps({"index":index,"items":items,"partially_occluded_door":"2"})+"\n")
                truth.flush()
                print(f"ROOMNAV_FRAME {index}", flush=True)
        rgb.detach([product.path])
        product.destroy()
        context.close_stage()
        native.cleanup()
        print("ROOMNAV_CLOSED", flush=True)
    except Exception:
        traceback.print_exc()
        raise
    finally:
        app.close()


if __name__ == "__main__":
    main()
