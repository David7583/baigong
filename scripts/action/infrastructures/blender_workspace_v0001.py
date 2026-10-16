# ============================================================
# 文件名: blender_workspace_v0001.py
# 中文名: Blender 固定操作与场景结果文件
# 版本号: v0001
# 主层级: action
# 层级: workbench / integration
# 脚本定位: Blender 固定操作与场景结果文件
# 职责说明: 通过已有索引和显式 JSON 契约提供真实信息
# 本脚本做什么: Blender 固定操作与场景结果文件
# 本脚本不做什么: 不自动授权模型、不安装软件、不卸载系统应用
# 制度边界声明: 只在明确操作后执行；用户数据保留；错误可追溯
# 可更新: True
# ============================================================
# ALIAS_META
# alias: blender_workspace_v0001
# family: blender_workspace
# role: managed_integration
# version: v0001
# status: active
# entry_point: scripts/action/infrastructures/blender_workspace_v0001.py
# input: authenticated explicit operation or JSON request
# output: factual inventory or versioned result
# depends_on: installed Blender bpy and Python stdlib
# used_by: workbench_server_v0011
# ============================================================

from __future__ import annotations
import hashlib
import json
import math
import sys
from pathlib import Path
import bpy
from mathutils import Vector

SCRIPT_NAME = "blender_workspace_v0001"
SCRIPT_FAMILY = "blender_workspace"
SCRIPT_VERSION = "v0001"
DEFAULT_ENCODING = "utf-8"

def main():
    args = sys.argv[sys.argv.index("--")+1:]
    request, response = map(Path, args)
    raw = request.read_bytes()
    req = json.loads(raw)
    result = {"schema_version": "workbench.blender-job.v1", "run_id": req.get("run_id"),
              "source_sha256": hashlib.sha256(raw).hexdigest(),
              "producer": {"script": SCRIPT_NAME, "version": SCRIPT_VERSION}}
    try:
        if req.get("schema_version") != result["schema_version"] or req.get("producer") != {"script": "workbench_software_v0001", "version": "v0001"} or req.get("operation") not in {"demo", "inspect", "render", "transform"}:
            raise ValueError("invalid_blender_contract")
        source = req.get("source")
        operation = req["operation"]
        if source:
            file = Path(source).resolve(strict=True)
            if file.suffix.lower() != ".blend":
                raise ValueError("blend_required")
            if hashlib.sha256(file.read_bytes()).hexdigest() != req.get("source_file_sha256"):
                raise ValueError("source_changed")
            bpy.ops.wm.open_mainfile(filepath=str(file), load_ui=False, use_scripts=False)
        elif operation != "demo":
            raise ValueError("source_required")
        if operation == "demo":
            bpy.ops.object.select_all(action="SELECT")
            bpy.ops.object.delete(use_global=False)
            bpy.ops.mesh.primitive_cube_add(location=(0, 0, 1))
            cube = bpy.context.object
            cube.name = "Workbench Cube"
            material = bpy.data.materials.new("Workbench Sage")
            material.diffuse_color = (0.20, 0.48, 0.39, 1.0)
            cube.data.materials.append(material)
            bevel = cube.modifiers.new("Soft edges", "BEVEL")
            bevel.width, bevel.segments = 0.12, 3
            bpy.ops.mesh.primitive_plane_add(size=16)
            bpy.context.object.name = "Ground"
            bpy.ops.object.camera_add(location=(7, -9, 7))
            camera = bpy.context.object
            camera.rotation_euler = (Vector((0, 0, 1)) - camera.location).to_track_quat("-Z", "Y").to_euler()
            camera.data.type, camera.data.ortho_scale = "ORTHO", 8
            bpy.context.scene.camera = camera
            for position, energy, size in [((0, -3, 7), 1100, 5), ((4, 4, 5), 800, 4)]:
                bpy.ops.object.light_add(type="AREA", location=position)
                light = bpy.context.object
                light.data.energy, light.data.shape, light.data.size = energy, "DISK", size
                light.rotation_euler = (Vector((0, 0, 0)) - light.location).to_track_quat("-Z", "Y").to_euler()
        if operation == "transform":
            obj = bpy.data.objects.get(req.get("object", ""))
            if obj is None:
                raise ValueError("object_missing")
            for key in ("location", "rotation_degrees", "scale"):
                value = req.get(key)
                if value is not None:
                    if not isinstance(value, list) or len(value) != 3 or any(type(x) not in (float, int) or not math.isfinite(x) or abs(x) > 10000 for x in value):
                        raise ValueError("invalid_transform")
                    if key == "rotation_degrees":
                        obj.rotation_euler = [math.radians(x) for x in value]
                    else:
                        setattr(obj, key, value)
        scene = bpy.context.scene
        output = {"blender_version": bpy.app.version_string, "objects": [
            {"name": obj.name, "type": obj.type, "location": list(obj.location),
             "rotation_degrees": [math.degrees(x) for x in obj.rotation_euler], "scale": list(obj.scale)}
            for obj in scene.objects], "source": source, "render": None, "blend": None}
        if operation in {"demo", "transform"}:
            target = response.parent / "scene.blend"
            if target.exists():
                raise ValueError("output_already_exists")
            bpy.ops.wm.save_as_mainfile(filepath=str(target))
            output["blend"] = str(target)
        if operation in {"demo", "render", "transform"}:
            if not scene.camera:
                raise ValueError("scene_has_no_camera")
            scene.render.engine = "CYCLES"
            scene.cycles.device = "CPU"
            scene.cycles.samples = 12
            scene.render.resolution_x, scene.render.resolution_y, scene.render.resolution_percentage = 720, 540, 100
            scene.render.image_settings.file_format = "PNG"
            scene.render.filepath = str(response.parent / "preview.png")
            scene.render.use_compositing = False
            scene.render.use_sequencer = False
            bpy.ops.render.render(write_still=True)
            output["render"] = scene.render.filepath
        result.update(status="completed", result=output)
    except Exception as exc:
        result.update(status="failed", error=type(exc).__name__ + ": " + str(exc)[:300])
    tmp = response.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding=DEFAULT_ENCODING)
    tmp.replace(response)
    if result["status"] != "completed":
        raise RuntimeError(result["error"])

if __name__ == "__main__":
    main()
