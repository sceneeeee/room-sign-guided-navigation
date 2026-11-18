"""Small teaching corridor, using the earlier teaching-floor USD conventions."""

import math

from PIL import Image, ImageDraw, ImageFont
from pxr import Gf, Sdf, UsdGeom, UsdLux, UsdShade


class Corridor:
    def __init__(self, stage, directory, font, *, variant="standard"):
        self.stage = stage
        self.directory = directory
        UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
        UsdGeom.SetStageMetersPerUnit(stage, 1)
        stage.SetDefaultPrim(UsdGeom.Xform.Define(stage, "/World").GetPrim())
        self.box("Floor", (0, -3, -.10), (8, 10, .2), (.5, .51, .49))
        self.box("Ceiling", (0, -3, 3.25), (8, 10, .2), (.82, .81, .77))
        self.box("BackWall", (0, .12, 1.6), (8, .2, 3.2), (.72, .75, .74))
        for index, x in enumerate((-4, 4)):
            self.box(f"Side{index}", (x, -3, 1.6), (.15, 10, 3.2), (.7, .72, .71))
        for i, y in enumerate((-6, -4, -2, 0)):
            self.box(f"FloorJoint{i}", (0, y, .002), (8, .008, .004), (.35, .36, .35))
        self.box("Skirting", (0, -.015, .07), (8, .1, .14), (.29, .34, .39))
        door_centers = (-1.0, 1.0) if variant == "competing_doors" else (0.0,)
        self.door_centers = () if variant == "wall_only" else door_centers
        self.is_occluded = False
        if variant != "wall_only":
            for i, x in enumerate(door_centers):
                self.door(x, i)
        text = "" if variant == "blank_plate" else "LSS119"
        for i, x in enumerate(door_centers):
            plaque_x = x + 1.02 if variant == "wall_plate" else x
            self.plaque(f"Plaque{i}", (plaque_x, -.115, 1.58),
                        "LSS118" if variant == "competing_doors" and i else text, font)
        self.occluder = self.box("Occluder", (0, -1, 1.45), (2.4, .15, 2.9), (.38, .4, .41))
        UsdGeom.Imageable(self.occluder.GetPrim()).MakeInvisible()
        for i, y in enumerate((-5, -2)):
            light = UsdLux.SphereLight.Define(stage, f"/World/Light{i}")
            light.AddTranslateOp().Set(Gf.Vec3d(0, y, 2.9))
            light.CreateRadiusAttr(.2)
            light.CreateIntensityAttr(18000)
        dome = UsdLux.DomeLight.Define(stage, "/World/Ambient")
        dome.CreateIntensityAttr(250)
        self.camera = UsdGeom.Camera.Define(stage, "/World/Camera")
        self.camera.CreateFocalLengthAttr(20)
        self.camera.CreateHorizontalApertureAttr(24)
        self.camera.CreateVerticalApertureAttr(13.5)
        self.camera.CreateClippingRangeAttr(Gf.Vec2f(.05, 50))
        self.camera_transform = self.camera.AddTransformOp()

    def box(self, name, center, size, color):
        box = UsdGeom.Cube.Define(self.stage, f"/World/{name}")
        box.CreateSizeAttr(1)
        box.AddTranslateOp().Set(Gf.Vec3d(*center))
        box.AddScaleOp().Set(Gf.Vec3f(*size))
        box.CreateDisplayColorAttr([Gf.Vec3f(*color)])
        return box

    def door(self, x, index):
        self.box(f"Door{index}", (x, -.035, 1.1), (1.15, .14, 2.2), (.52, .32, .16))
        for side_index, side in enumerate((-1, 1)):
            self.box(f"Frame{index}_{side_index}", (x + side * .62, -.08, 1.14),
                     (.09, .15, 2.28), (.26, .28, .29))
        self.box(f"FrameTop{index}", (x, -.08, 2.26), (1.33, .15, .09), (.26, .28, .29))
        self.box(f"Window{index}", (x, -.112, 1.93), (.72, .012, .3), (.17, .25, .27))
        self.box(f"Handle{index}", (x + .43, -.15, .98), (.055, .08, .21), (.72, .73, .74))
        self.box(f"Panel{index}", (x, -.111, .59), (.91, .014, .85), (.47, .28, .14))

    def plaque(self, name, center, text, font):
        texture_dir = self.directory / "textures"
        texture_dir.mkdir(exist_ok=True)
        image = Image.new("RGB", (600, 220), "#193c52")
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((7, 7, 593, 213), radius=12, outline="#ccd9db", width=5)
        draw.text((300, 110), text, font=ImageFont.truetype(str(font), 110),
                  anchor="mm", fill="white")
        image.save(texture_dir / f"{name}.png")
        x, y, z = center
        width, height = .30, .11
        mesh = UsdGeom.Mesh.Define(self.stage, f"/World/{name}")
        mesh.CreatePointsAttr([(x - width/2, y, z - height/2), (x + width/2, y, z - height/2),
                              (x + width/2, y, z + height/2), (x - width/2, y, z + height/2)])
        mesh.CreateFaceVertexCountsAttr([4])
        mesh.CreateFaceVertexIndicesAttr([0, 1, 2, 3])
        mesh.CreateSubdivisionSchemeAttr("none")
        mesh.CreateDoubleSidedAttr(True)
        UsdGeom.PrimvarsAPI(mesh).CreatePrimvar("st", Sdf.ValueTypeNames.TexCoord2fArray,
                                              UsdGeom.Tokens.vertex).Set([(0, 0), (1, 0), (1, 1), (0, 1)])
        material = UsdShade.Material.Define(self.stage, f"/World/Materials/{name}")
        surface = UsdShade.Shader.Define(self.stage, f"{material.GetPath()}/Surface")
        surface.CreateIdAttr("UsdPreviewSurface")
        uv = UsdShade.Shader.Define(self.stage, f"{material.GetPath()}/UV")
        uv.CreateIdAttr("UsdPrimvarReader_float2")
        uv.CreateInput("varname", Sdf.ValueTypeNames.Token).Set("st")
        texture = UsdShade.Shader.Define(self.stage, f"{material.GetPath()}/Texture")
        texture.CreateIdAttr("UsdUVTexture")
        texture.CreateInput("file", Sdf.ValueTypeNames.Asset).Set(f"./textures/{name}.png")
        texture.CreateInput("sourceColorSpace", Sdf.ValueTypeNames.Token).Set("sRGB")
        texture.CreateInput("st", Sdf.ValueTypeNames.Float2).ConnectToSource(uv.CreateOutput("result", Sdf.ValueTypeNames.Float2))
        rgb = texture.CreateOutput("rgb", Sdf.ValueTypeNames.Float3)
        surface.CreateInput("diffuseColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(rgb)
        surface.CreateInput("emissiveColor", Sdf.ValueTypeNames.Color3f).ConnectToSource(rgb)
        material.CreateSurfaceOutput().ConnectToSource(surface.CreateOutput("surface", Sdf.ValueTypeNames.Token))
        UsdShade.MaterialBindingAPI.Apply(mesh.GetPrim()).Bind(material)

    def set_pose(self, pose):
        angle = math.radians(pose.yaw_deg)
        eye = Gf.Vec3d(pose.x, pose.y, 1.48)
        facing = Gf.Vec3d(-math.sin(angle), math.cos(angle), 0)
        self.camera_transform.Set(Gf.Matrix4d(1).SetLookAt(eye, eye + facing, Gf.Vec3d(0, 0, 1)).GetInverse())

    def occlude(self):
        UsdGeom.Imageable(self.occluder.GetPrim()).MakeVisible()
        self.is_occluded = True

    def ground_truth(self):
        """Project authored door geometry solely for post-hoc evaluation."""
        view = self.camera_transform.Get().GetInverse()
        boxes = []
        for index, center in enumerate(self.door_centers):
            pixels = []
            for x in (center - .575, center + .575):
                for z in (0, 2.2):
                    point = view.Transform(Gf.Vec3d(x, -.105, z))
                    if point[2] >= 0:
                        continue
                    pixels.append((.5 + point[0] * 20 / (-point[2] * 24),
                                   .5 - point[1] * 20 / (-point[2] * 13.5)))
            if len(pixels) == 4:
                boxes.append({"door_index": index, "visible": not self.is_occluded,
                              "bbox_xyxy_norm": [max(0., min(p[0] for p in pixels)),
                                                 max(0., min(p[1] for p in pixels)),
                                                 min(1., max(p[0] for p in pixels)),
                                                 min(1., max(p[1] for p in pixels))]})
        return boxes
