import adsk.core
import adsk.fusion
import traceback
import json
import time
import math
import threading
from pathlib import Path

app = None
ui = None
stop_thread = False
monitor_thread = None

COMM_DIR = Path.home() / "fusion_mcp_comm"

# =============================================================================
# MAIN-THREAD BRIDGE
# =============================================================================
# monitor_commands() runs on a background thread. Most geometry API calls
# tolerate that, but anything that drives Fusion's command pipeline
# (executeTextCommand, UI command definitions) must run on the main thread or
# Fusion misbehaves or crashes outright. Those calls are bounced through a
# custom event, which Fusion always delivers on the main thread.

MAIN_THREAD_EVENT_ID = 'FusionMCPMainThreadEvent'
_main_thread_event = None
_main_thread_handler = None
_main_thread_job = None
_main_thread_lock = threading.Lock()


class _MainThreadHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        job = _main_thread_job
        if not job:
            return
        try:
            job['result'] = job['func']()
        except Exception as e:
            job['result'] = {"success": False,
                             "error": str(e) + "\n" + traceback.format_exc()}
        finally:
            job['done'].set()


def run_on_main_thread(func, timeout=300):
    """Run func() on Fusion's main thread and return its result."""
    global _main_thread_job
    if _main_thread_event is None:
        # Registration failed (older Fusion build?). Run inline as a last resort.
        return func()
    with _main_thread_lock:
        job = {'func': func, 'result': None, 'done': threading.Event()}
        _main_thread_job = job
        app.fireCustomEvent(MAIN_THREAD_EVENT_ID)
        if not job['done'].wait(timeout):
            return {"success": False,
                    "error": "Main-thread operation timed out after %ds. "
                             "Fusion may be waiting on a dialog - check the Fusion window."
                             % timeout}
        return job['result']


def run(context):
    global app, ui, monitor_thread, stop_thread
    global _main_thread_event, _main_thread_handler
    try:
        app = adsk.core.Application.get()
        ui = app.userInterface
        COMM_DIR.mkdir(exist_ok=True)

        # Register the main-thread bridge. Unregister first so reloading the
        # add-in doesn't collide with a stale registration.
        try:
            app.unregisterCustomEvent(MAIN_THREAD_EVENT_ID)
        except:
            pass
        try:
            _main_thread_event = app.registerCustomEvent(MAIN_THREAD_EVENT_ID)
            _main_thread_handler = _MainThreadHandler()
            _main_thread_event.add(_main_thread_handler)
        except:
            _main_thread_event = None
            _main_thread_handler = None

        stop_thread = False
        monitor_thread = threading.Thread(target=monitor_commands, daemon=True)
        monitor_thread.start()
        ui.messageBox('Fusion MCP v7.3 Started!\n\nMesh tools enabled.\n\nListening at:\n'
                      + str(COMM_DIR))
    except:
        if ui:
            ui.messageBox('Failed:\n' + traceback.format_exc())


def stop(context):
    global stop_thread, ui, _main_thread_event, _main_thread_handler
    try:
        stop_thread = True
        try:
            if _main_thread_event and _main_thread_handler:
                _main_thread_event.remove(_main_thread_handler)
            app.unregisterCustomEvent(MAIN_THREAD_EVENT_ID)
        except:
            pass
        _main_thread_event = None
        _main_thread_handler = None
        if ui:
            ui.messageBox('Fusion MCP Stopped')
    except:
        pass


def monitor_commands():
    global stop_thread
    while not stop_thread:
        try:
            cmd_files = list(COMM_DIR.glob("command_*.json"))
            for cmd_file in cmd_files:
                resp_file = None
                cmd_id = None
                try:
                    with open(cmd_file, 'r') as f:
                        command = json.load(f)
                    cmd_id = command['id']
                    resp_file = COMM_DIR / f"response_{cmd_id}.json"
                    # Delete command first to avoid re-processing
                    try:
                        cmd_file.unlink()
                    except:
                        pass
                    result = execute_command(command)
                    with open(resp_file, 'w') as f:
                        json.dump(result, f, indent=2)
                except Exception as e:
                    if resp_file and cmd_id:
                        try:
                            with open(resp_file, 'w') as f:
                                json.dump({"success": False, "error": str(e)}, f)
                        except:
                            pass
            time.sleep(0.1)
        except:
            pass


def execute_command(command):
    global app
    tool_name = command.get('name')
    params = command.get('params', {})
    try:
        if tool_name == 'batch':
            return execute_batch(params)

        design = app.activeProduct
        if not design:
            return {"success": False, "error": "No active design"}
        rootComp = design.rootComponent

        HANDLERS = {
            # Sketch
            'create_sketch': create_sketch,
            'finish_sketch': finish_sketch,
            'draw_rectangle': draw_rectangle,
            'draw_circle': draw_circle,
            'draw_line': draw_line,
            'draw_arc': draw_arc,
            'draw_polygon': draw_polygon,
            # 3D features
            'extrude': extrude_profile,
            'revolve': revolve_profile,
            'fillet': add_fillet,
            'chamfer': add_chamfer,
            'shell': add_shell,
            'draft': add_draft,
            'pattern_rectangular': pattern_rectangular,
            'pattern_circular': pattern_circular,
            'mirror': mirror_body,
            # View / info
            'fit_view': fit_view,
            'get_design_info': get_design_info,
            'get_body_info': get_body_info,
            'measure': measure,
            # Components
            'create_component': create_component,
            'body_to_component': body_to_component,
            # Arbitrary scripting - the whole Fusion API
            'execute_script': execute_script,
            # T-Splines
            'create_tspline': create_tspline,
            'list_tspline_bodies': list_tspline_bodies,
            'export_tspline_tsm': export_tspline_tsm,
            'list_components': list_components,
            'delete_component': delete_component,
            'check_interference': check_interference,
            'move_component': move_component,
            'rotate_component': rotate_component,
            # Joints
            'create_revolute_joint': create_revolute_joint,
            'create_slider_joint': create_slider_joint,
            'set_joint_angle': set_joint_angle,
            'set_joint_distance': set_joint_distance,
            # Boolean
            'loft': loft_profiles,
            'create_3d_sketch': create_3d_sketch,
            'draw_3d_line': draw_3d_line,
            'draw_3d_spline': draw_3d_spline,
            'combine': combine_bodies,
            # Utility
            'undo': undo_ops,
            'delete_body': delete_body,
            'delete_sketch': delete_sketch,
            # Export / Import
            'export_stl': export_stl,
            'export_step': export_step,
            'export_3mf': export_3mf,
            'import_mesh': import_mesh,
            'import_step': import_step,
            # Mesh tools
            'get_mesh_bodies': get_mesh_bodies,
            'get_mesh_bounding_box': get_mesh_bounding_box,
            'mesh_to_brep': mesh_to_brep,
            'reduce_mesh': reduce_mesh,
        }

        handler = HANDLERS.get(tool_name)
        if handler:
            return handler(design, rootComp, params)
        return {"success": False, "error": f"Unknown tool: {tool_name}"}

    except Exception as e:
        return {"success": False, "error": str(e) + "\n" + traceback.format_exc()}


# =============================================================================
# BATCH
# =============================================================================

def execute_batch(params):
    global app
    commands = params.get('commands', [])
    results = []
    for cmd in commands:
        result = execute_command(cmd)
        results.append({"name": cmd.get('name'), "result": result})
        if not result.get('success'):
            return {
                "success": False,
                "error": f"Batch stopped at '{cmd.get('name')}': {result.get('error')}",
                "results": results
            }
    return {"success": True, "count": len(results), "results": results}


# =============================================================================
# SKETCH
# =============================================================================

def create_sketch(design, rootComp, params):
    plane_name = params.get('plane', 'XY').upper()
    offset = params.get('offset', 0)

    plane_map = {
        'XY': rootComp.xYConstructionPlane,
        'XZ': rootComp.xZConstructionPlane,
        'YZ': rootComp.yZConstructionPlane,
    }
    base_plane = plane_map.get(plane_name)
    if not base_plane:
        return {"success": False, "error": f"Unknown plane: {plane_name}. Use XY, XZ or YZ."}

    if offset != 0:
        planes = rootComp.constructionPlanes
        planeInput = planes.createInput()
        planeInput.setByOffset(base_plane, adsk.core.ValueInput.createByReal(offset))
        base_plane = planes.add(planeInput)

    sketch = rootComp.sketches.add(base_plane)
    return {"success": True, "sketch_name": sketch.name}


def finish_sketch(design, rootComp, params):
    return {"success": True}


def create_3d_sketch(design, rootComp, params):
    """Create a 3D sketch (not tied to a plane) for drawing rail curves."""
    sketch = rootComp.sketches.add(rootComp.xZConstructionPlane)
    sketch.is3D = True
    name = params.get('name')
    if name:
        sketch.name = name
    idx = rootComp.sketches.count - 1
    return {"success": True, "sketch_index": idx, "sketch_name": sketch.name}


def draw_3d_line(design, rootComp, params):
    """Draw a line in 3D space in the most recently created sketch."""
    sketch = _active_sketch(design, rootComp)
    if not sketch:
        return {"success": False, "error": "No active sketch"}
    p1 = adsk.core.Point3D.create(params['x1'], params['y1'], params['z1'])
    p2 = adsk.core.Point3D.create(params['x2'], params['y2'], params['z2'])
    sketch.sketchCurves.sketchLines.addByTwoPoints(p1, p2)
    return {"success": True}


def draw_3d_spline(design, rootComp, params):
    """Draw a fit-point spline through a list of 3D points [[x,y,z], ...] in the active sketch."""
    sketch = _active_sketch(design, rootComp)
    if not sketch:
        return {"success": False, "error": "No active sketch"}
    pts = params['points']
    if len(pts) < 2:
        return {"success": False, "error": "Need at least 2 points"}
    pt_collection = adsk.core.ObjectCollection.create()
    for p in pts:
        pt_collection.add(adsk.core.Point3D.create(p[0], p[1], p[2]))
    spline = sketch.sketchCurves.sketchFittedSplines.add(pt_collection)
    return {"success": True, "spline_name": spline.entityToken if hasattr(spline, 'entityToken') else "spline"}


def draw_rectangle(design, rootComp, params):
    sketch = _active_sketch(design, rootComp)
    if not sketch:
        return {"success": False, "error": "No active sketch. Call create_sketch first."}
    p1 = adsk.core.Point3D.create(params['x1'], params['y1'], 0)
    p2 = adsk.core.Point3D.create(params['x2'], params['y2'], 0)
    sketch.sketchCurves.sketchLines.addTwoPointRectangle(p1, p2)
    return {"success": True}


def draw_circle(design, rootComp, params):
    sketch = _active_sketch(design, rootComp)
    if not sketch:
        return {"success": False, "error": "No active sketch. Call create_sketch first."}
    center = adsk.core.Point3D.create(params['center_x'], params['center_y'], 0)
    sketch.sketchCurves.sketchCircles.addByCenterRadius(center, params['radius'])
    return {"success": True}


def draw_line(design, rootComp, params):
    sketch = _active_sketch(design, rootComp)
    if not sketch:
        return {"success": False, "error": "No active sketch."}
    p1 = adsk.core.Point3D.create(params['x1'], params['y1'], 0)
    p2 = adsk.core.Point3D.create(params['x2'], params['y2'], 0)
    sketch.sketchCurves.sketchLines.addByTwoPoints(p1, p2)
    return {"success": True}


def draw_arc(design, rootComp, params):
    sketch = _active_sketch(design, rootComp)
    if not sketch:
        return {"success": False, "error": "No active sketch."}
    center = adsk.core.Point3D.create(params['center_x'], params['center_y'], 0)
    start  = adsk.core.Point3D.create(params['start_x'],  params['start_y'],  0)
    end    = adsk.core.Point3D.create(params['end_x'],    params['end_y'],    0)
    sketch.sketchCurves.sketchArcs.addByCenterStartEnd(center, start, end)
    return {"success": True}


def draw_polygon(design, rootComp, params):
    sketch = _active_sketch(design, rootComp)
    if not sketch:
        return {"success": False, "error": "No active sketch."}
    cx    = params['center_x']
    cy    = params['center_y']
    r     = params['radius']
    sides = int(params.get('sides', 6))
    pts = [
        adsk.core.Point3D.create(
            cx + r * math.cos(2 * math.pi * i / sides),
            cy + r * math.sin(2 * math.pi * i / sides),
            0
        )
        for i in range(sides)
    ]
    lines = sketch.sketchCurves.sketchLines
    for i in range(sides):
        lines.addByTwoPoints(pts[i], pts[(i + 1) % sides])
    return {"success": True, "sides": sides}


# =============================================================================
# 3D FEATURES
# =============================================================================

def extrude_profile(design, rootComp, params):
    sketches = rootComp.sketches
    if sketches.count == 0:
        return {"success": False, "error": "No sketches"}
    sketch = sketches.item(sketches.count - 1)
    if sketch.profiles.count == 0:
        return {"success": False, "error": "No profiles in sketch"}

    profile_index = min(int(params.get('profile_index', 0)), sketch.profiles.count - 1)
    profile = sketch.profiles.item(profile_index)
    distance = params['distance']
    taper_angle = params.get('taper_angle', 0)

    extrudes = rootComp.features.extrudeFeatures
    extInput = extrudes.createInput(profile, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    extInput.setDistanceExtent(False, adsk.core.ValueInput.createByReal(distance))
    if taper_angle != 0:
        extInput.taperAngle = adsk.core.ValueInput.createByReal(math.radians(taper_angle))
    extrude = extrudes.add(extInput)
    return {"success": True, "feature_name": extrude.name}


def revolve_profile(design, rootComp, params):
    sketches = rootComp.sketches
    if sketches.count == 0:
        return {"success": False, "error": "No sketches"}
    sketch = sketches.item(sketches.count - 1)
    if sketch.profiles.count == 0:
        return {"success": False, "error": "No profiles"}
    profile = sketch.profiles.item(0)
    axis = rootComp.yConstructionAxis
    revolves = rootComp.features.revolveFeatures
    revInput = revolves.createInput(profile, axis, adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
    revInput.setAngleExtent(False, adsk.core.ValueInput.createByReal(math.radians(params['angle'])))
    revolve = revolves.add(revInput)
    return {"success": True, "feature_name": revolve.name}


def loft_profiles(design, rootComp, params):
    """Loft through sketch profiles with optional guide rails.

    sketch_indices: list of sketch indices to loft through (profiles/sections)
    rail_sketch_indices: list of sketch indices containing rail curves (guide lines)
    profile_index: which profile to use per sketch if multiple exist (default 0)
    """
    sketches = rootComp.sketches
    sketch_indices = params.get('sketch_indices')
    rail_indices = params.get('rail_sketch_indices', [])

    if sketch_indices is None:
        sketch_indices = list(range(sketches.count))
    if len(sketch_indices) < 2:
        return {"success": False, "error": "Need at least 2 sketches to loft"}

    loftFeats = rootComp.features.loftFeatures
    loftInput = loftFeats.createInput(adsk.fusion.FeatureOperations.NewBodyFeatureOperation)

    # Add section profiles
    profile_idx = params.get('profile_index', 0)
    added = 0
    for idx in sketch_indices:
        if idx >= sketches.count:
            return {"success": False, "error": f"Sketch index {idx} out of range (total: {sketches.count})"}
        sketch = sketches.item(idx)
        if sketch.profiles.count == 0:
            return {"success": False, "error": f"Sketch {idx} ({sketch.name}) has no profiles"}
        profile = sketch.profiles.item(min(profile_idx, sketch.profiles.count - 1))
        loftInput.loftSections.add(profile)
        added += 1

    # Add guide rails (each curve in each rail sketch becomes a rail)
    rails_added = 0
    for rail_idx in rail_indices:
        if rail_idx >= sketches.count:
            return {"success": False, "error": f"Rail sketch index {rail_idx} out of range"}
        rail_sketch = sketches.item(rail_idx)
        curves = rail_sketch.sketchCurves
        for i in range(curves.count):
            try:
                loftInput.centerLineOrRails.addRail(curves.item(i))
                rails_added += 1
            except Exception as re:
                pass  # some curves may not be valid rails

    loftFeature = loftFeats.add(loftInput)
    return {"success": True, "feature_name": loftFeature.name, "sections": added, "rails": rails_added}


def add_fillet(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    edge_indices = params.get('edges')
    edges = adsk.core.ObjectCollection.create()
    if edge_indices is not None:
        for i in edge_indices:
            if i < body.edges.count:
                edges.add(body.edges.item(i))
    else:
        for edge in body.edges:
            edges.add(edge)

    if edges.count == 0:
        return {"success": False, "error": "No edges selected"}

    fillets = rootComp.features.filletFeatures
    filletInput = fillets.createInput()
    filletInput.addConstantRadiusEdgeSet(edges, adsk.core.ValueInput.createByReal(params['radius']), True)
    fillet = fillets.add(filletInput)
    return {"success": True, "feature_name": fillet.name}


def add_chamfer(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    edge_indices = params.get('edges')
    edges = adsk.core.ObjectCollection.create()
    if edge_indices is not None:
        for i in edge_indices:
            if i < body.edges.count:
                edges.add(body.edges.item(i))
    else:
        for edge in body.edges:
            edges.add(edge)

    if edges.count == 0:
        return {"success": False, "error": "No edges selected"}

    chamfers = rootComp.features.chamferFeatures
    chamferInput = chamfers.createInput(edges, True)
    chamferInput.setToEqualDistance(adsk.core.ValueInput.createByReal(params['distance']))
    chamfer = chamfers.add(chamferInput)
    return {"success": True, "feature_name": chamfer.name}


def add_shell(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    faces = adsk.core.ObjectCollection.create()
    face_indices = params.get('faces_to_remove')
    if face_indices:
        for i in face_indices:
            if i < body.faces.count:
                faces.add(body.faces.item(i))

    shells = rootComp.features.shellFeatures
    shellInput = shells.createInput(faces)
    shellInput.insideThickness = adsk.core.ValueInput.createByReal(params['thickness'])
    shell = shells.add(shellInput)
    return {"success": True, "feature_name": shell.name}


def add_draft(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    # Choose neutral plane based on pull direction (default +Z → XY plane)
    pull_z = params.get('pull_z', 1)
    pull_y = params.get('pull_y', 0)
    if abs(pull_z) >= abs(pull_y):
        neutral_plane = rootComp.xYConstructionPlane
    else:
        neutral_plane = rootComp.xZConstructionPlane

    face_indices = params.get('faces')
    faces = adsk.core.ObjectCollection.create()
    if face_indices is not None:
        for i in face_indices:
            if i < body.faces.count:
                faces.add(body.faces.item(i))
    else:
        for face in body.faces:
            faces.add(face)

    if faces.count == 0:
        return {"success": False, "error": "No faces selected"}

    drafts = rootComp.features.draftFeatures
    draftInput = drafts.createInput(
        neutral_plane,
        adsk.core.ValueInput.createByReal(math.radians(params['angle'])),
        faces,
        True
    )
    draft = drafts.add(draftInput)
    return {"success": True, "feature_name": draft.name}


def pattern_rectangular(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    inputEntities = adsk.core.ObjectCollection.create()
    inputEntities.add(body)

    x_count   = int(params['x_count'])
    x_spacing = params['x_spacing']
    y_count   = int(params.get('y_count', 1))
    y_spacing = params.get('y_spacing', 0)

    xAxis = rootComp.xConstructionAxis
    yAxis = rootComp.yConstructionAxis

    rectPatterns = rootComp.features.rectangularPatternFeatures
    rectInput = rectPatterns.createInput(
        inputEntities, xAxis,
        adsk.core.ValueInput.createByReal(x_count),
        adsk.core.ValueInput.createByReal(x_spacing),
        adsk.fusion.PatternDistanceType.SpacingPatternDistanceType
    )
    if y_count > 1:
        rectInput.setDirectionTwo(
            yAxis,
            adsk.core.ValueInput.createByReal(y_count),
            adsk.core.ValueInput.createByReal(y_spacing)
        )
    pattern = rectPatterns.add(rectInput)
    return {"success": True, "feature_name": pattern.name, "total": x_count * y_count}


def pattern_circular(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    inputEntities = adsk.core.ObjectCollection.create()
    inputEntities.add(body)

    axis_map = {
        'X': rootComp.xConstructionAxis,
        'Y': rootComp.yConstructionAxis,
        'Z': rootComp.zConstructionAxis,
    }
    axis = axis_map.get(params.get('axis', 'Z').upper(), rootComp.zConstructionAxis)

    circPatterns = rootComp.features.circularPatternFeatures
    circInput = circPatterns.createInput(inputEntities, axis)
    circInput.quantity  = adsk.core.ValueInput.createByReal(int(params['count']))
    circInput.totalAngle = adsk.core.ValueInput.createByReal(math.radians(params.get('angle', 360)))
    circInput.isSymmetric = False
    pattern = circPatterns.add(circInput)
    return {"success": True, "feature_name": pattern.name}


def mirror_body(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    inputEntities = adsk.core.ObjectCollection.create()
    inputEntities.add(body)

    plane_map = {
        'XY': rootComp.xYConstructionPlane,
        'XZ': rootComp.xZConstructionPlane,
        'YZ': rootComp.yZConstructionPlane,
    }
    mirror_plane = plane_map.get(params.get('plane', 'YZ').upper(), rootComp.yZConstructionPlane)

    mirrors = rootComp.features.mirrorFeatures
    mirrorInput = mirrors.createInput(inputEntities, mirror_plane)
    mirror = mirrors.add(mirrorInput)
    return {"success": True, "feature_name": mirror.name}


# =============================================================================
# VIEW / INFO
# =============================================================================

def fit_view(design, rootComp, params):
    global app
    app.activeViewport.fit()
    return {"success": True}


def get_design_info(design, rootComp, params):
    bodies = [{"index": i, "name": rootComp.bRepBodies.item(i).name}
              for i in range(rootComp.bRepBodies.count)]
    sketches = [{"index": i, "name": rootComp.sketches.item(i).name}
                for i in range(rootComp.sketches.count)]

    # Mesh bodies are a separate collection from bRepBodies. Without this a
    # design holding an imported STL reports as completely empty.
    meshes = []
    try:
        for i in range(rootComp.meshBodies.count):
            mb = rootComp.meshBodies.item(i)
            entry = {"index": i, "name": mb.name}
            try:
                entry["triangle_count"] = mb.mesh.triangleCount
            except:
                pass
            meshes.append(entry)
    except:
        pass

    return {
        "success": True,
        "design_name": design.parentDocument.name,
        "body_count": rootComp.bRepBodies.count,
        "mesh_body_count": len(meshes),
        "sketch_count": rootComp.sketches.count,
        "component_count": rootComp.allOccurrences.count,
        "bodies": bodies,
        "mesh_bodies": meshes,
        "sketches": sketches,
    }


def get_body_info(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies in design"}

    edges = []
    for i in range(body.edges.count):
        edge = body.edges.item(i)
        try:
            length = round(edge.length, 4)
        except:
            length = 0
        edges.append({"index": i, "length_cm": length})

    faces = []
    for i in range(body.faces.count):
        face = body.faces.item(i)
        try:
            area = round(face.area, 4)
        except:
            area = 0
        faces.append({"index": i, "area_cm2": area})

    return {
        "success": True,
        "body_name": body.name,
        "edge_count": len(edges),
        "face_count": len(faces),
        "edges": edges,
        "faces": faces,
    }


def measure(design, rootComp, params):
    measure_type = params.get('type', 'body')
    body = _get_body(rootComp, params.get('body_index'))

    if measure_type == 'body':
        if not body:
            return {"success": False, "error": "No bodies"}
        props = body.physicalProperties
        bb = body.boundingBox
        return {
            "success": True,
            "volume_cm3": round(props.volume, 4),
            "surface_area_cm2": round(props.area, 4),
            "bounding_box_cm": {
                "x": round(bb.maxPoint.x - bb.minPoint.x, 4),
                "y": round(bb.maxPoint.y - bb.minPoint.y, 4),
                "z": round(bb.maxPoint.z - bb.minPoint.z, 4),
            }
        }

    elif measure_type == 'edge':
        edge_index = params.get('edge_index', 0)
        if not body or edge_index >= body.edges.count:
            return {"success": False, "error": "Invalid edge"}
        try:
            length = round(body.edges.item(edge_index).length, 4)
        except:
            length = 0
        return {"success": True, "length_cm": length}

    elif measure_type == 'face':
        face_index = params.get('face_index', 0)
        if not body or face_index >= body.faces.count:
            return {"success": False, "error": "Invalid face"}
        return {"success": True, "area_cm2": round(body.faces.item(face_index).area, 4)}

    return {"success": False, "error": f"Unknown type: {measure_type}. Use body, edge or face."}


# =============================================================================
# COMPONENTS
# =============================================================================

def create_component(design, rootComp, params):
    """Create an EMPTY component. NOTE: this does not move any body into it -
    use body_to_component for that."""
    name = params.get('name')
    occ = rootComp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    if name:
        occ.component.name = name
    return {"success": True, "component_name": occ.component.name,
            "index": rootComp.allOccurrences.count - 1}


# =============================================================================
# ARBITRARY SCRIPTING
# =============================================================================
# One handler instead of a tool per API feature. Every tool costs context just
# by describing itself, so a 300-tool MCP is worse than a 50-tool one. This
# gives access to the whole Fusion API without that cost.

def execute_script(design, rootComp, params):
    """Run arbitrary Fusion API Python inside the add-in.

    Params:
        code: Python source. Assign to `result` to return a value; anything
              printed is captured and returned as stdout.
        main_thread: run on Fusion's main thread (default True). Required for
              anything touching the UI or executeTextCommand.

    Pre-bound names: adsk, app, ui, design, root (= rootComp), math, json.

    This executes whatever it is given, in-process, with no sandbox. A bad
    script can take Fusion down with it. Errors come back as a full traceback
    rather than a crash, but that is damage reporting, not prevention.
    """
    code = params.get('code')
    if not code:
        return {"success": False, "error": "No code provided"}

    def _run():
        import io
        import contextlib
        buf = io.StringIO()
        scope = {
            'adsk': adsk, 'app': app, 'ui': ui,
            'design': design, 'root': rootComp, 'rootComp': rootComp,
            'math': math, 'json': json, 'time': time,
            'result': None,
        }
        try:
            with contextlib.redirect_stdout(buf):
                exec(code, scope)
        except Exception as e:
            return {"success": False,
                    "error": "%s: %s" % (type(e).__name__, e),
                    "traceback": traceback.format_exc(),
                    "stdout": buf.getvalue()}
        out = scope.get('result')
        try:
            json.dumps(out)
        except (TypeError, ValueError):
            out = repr(out)      # non-serialisable objects come back as repr
        return {"success": True, "result": out, "stdout": buf.getvalue()}

    if params.get('main_thread', True):
        return run_on_main_thread(_run, timeout=params.get('timeout', 300))
    return _run()


# =============================================================================
# T-SPLINES (Form bodies)
# =============================================================================
# There is no public API to author T-Splines directly, but TSplineBodies can be
# created from TSM (T-Spline Mesh) data. That is the whole doorway: generate TSM
# text externally, push it in here, and Fusion builds a real editable Form body.

def _form_features(design, rootComp):
    if design.designType == adsk.fusion.DesignTypes.DirectDesignType:
        return None, ("T-Spline creation needs a parametric design. This document is in "
                      "direct modelling mode - turn the timeline back on.")
    return rootComp.features.formFeatures, None


def create_tspline(design, rootComp, params):
    """Create a T-Spline (Form) body from TSM data.

    Params (one of):
        tsm_text: TSM-formatted description string
        filepath: path to a .tsm file
    Optional:
        name: name for the resulting T-Spline body
    """
    tsm_text = params.get('tsm_text')
    filepath = params.get('filepath')
    if not tsm_text and not filepath:
        return {"success": False, "error": "Provide tsm_text or filepath"}

    forms, err = _form_features(design, rootComp)
    if err:
        return {"success": False, "error": err}

    feat = forms.add()
    feat.startEdit()
    try:
        bodies = feat.tSplineBodies
        if tsm_text:
            tb = bodies.addByTSMDescription(tsm_text)
        else:
            tb = bodies.addByTSMFile(filepath)
        if params.get('name'):
            tb.name = params['name']
        body_name = tb.name
    except Exception as e:
        try:
            feat.finishEdit()
        except:
            pass
        return {"success": False,
                "error": "TSM rejected by Fusion: %s" % e,
                "hint": "The TSM text or file is malformed. Export a working body "
                        "with export_tspline_tsm and compare."}
    feat.finishEdit()

    return {"success": True, "form_feature": feat.name, "tspline_body": body_name,
            "tspline_count": feat.tSplineBodies.count}


def list_tspline_bodies(design, rootComp, params):
    """List every T-Spline body in the design, with its parent form feature."""
    result = []
    try:
        for comp in design.allComponents:
            for f in comp.features.formFeatures:
                for i in range(f.tSplineBodies.count):
                    tb = f.tSplineBodies.item(i)
                    result.append({"index": len(result), "name": tb.name,
                                   "form_feature": f.name, "component": comp.name})
    except Exception as e:
        return {"success": False, "error": str(e)}
    return {"success": True, "count": len(result), "tspline_bodies": result}


def export_tspline_tsm(design, rootComp, params):
    """Export a T-Spline body as TSM. Writes a file if filepath is given, and
    always returns the description text so the format can be studied."""
    idx = params.get('index', 0)
    filepath = params.get('filepath')

    found = []
    for comp in design.allComponents:
        for f in comp.features.formFeatures:
            for i in range(f.tSplineBodies.count):
                found.append(f.tSplineBodies.item(i))
    if not found:
        return {"success": False,
                "error": "No T-Spline bodies in this design. Create one first "
                         "(Form workspace, or mesh_to_brep with method='organic')."}
    if idx >= len(found):
        return {"success": False, "error": "No T-Spline body at index %d (found %d)"
                                           % (idx, len(found))}
    tb = found[idx]
    out = {"success": True, "name": tb.name}
    try:
        desc = tb.getTSMDescription()
        out["tsm_length"] = len(desc) if desc else 0
        out["tsm_text"] = desc
    except Exception as e:
        out["description_error"] = str(e)
    if filepath:
        try:
            tb.saveAsTSMFile(filepath)
            out["filepath"] = filepath
        except Exception as e:
            out["file_error"] = str(e)
    return out


def body_to_component(design, rootComp, params):
    """Move a root body into its own new component, so it can be positioned,
    rotated and jointed independently.

    Params:
        body_index: which root body (default: last)
        name: name for the new component

    Body indices shift as bodies leave the root collection, so when converting
    several bodies, work from the highest index down - or just call this
    repeatedly with no body_index and let it take the last one each time.
    """
    if rootComp.bRepBodies.count == 0:
        return {"success": False, "error": "No bodies in the root component"}

    idx = params.get('body_index')
    if idx is None:
        idx = rootComp.bRepBodies.count - 1
    if idx >= rootComp.bRepBodies.count:
        return {"success": False,
                "error": "No body at index %d (root has %d)"
                         % (idx, rootComp.bRepBodies.count)}

    body = rootComp.bRepBodies.item(idx)
    body_name = body.name
    occ = rootComp.occurrences.addNewComponent(adsk.core.Matrix3D.create())
    name = params.get('name')
    if name:
        occ.component.name = name

    try:
        moved = body.moveToComponent(occ)
    except Exception as e:
        occ.deleteMe()
        return {"success": False, "error": "moveToComponent failed: %s" % e}

    return {
        "success": True,
        "component_name": occ.component.name,
        "body_name": body_name,
        "new_body_name": getattr(moved, 'name', body_name),
        "bodies_left_in_root": rootComp.bRepBodies.count,
        "component_count": rootComp.allOccurrences.count,
    }


def list_components(design, rootComp, params):
    occs = rootComp.allOccurrences
    components = []
    for i in range(occs.count):
        occ = occs.item(i)
        t = occ.transform
        pos = t.translation
        try:
            bb = occ.boundingBox
            bbox = {
                "min": [round(bb.minPoint.x, 3), round(bb.minPoint.y, 3), round(bb.minPoint.z, 3)],
                "max": [round(bb.maxPoint.x, 3), round(bb.maxPoint.y, 3), round(bb.maxPoint.z, 3)],
            }
        except:
            bbox = None
        components.append({
            "index": i,
            "name": occ.component.name,
            "position": [round(pos.x, 3), round(pos.y, 3), round(pos.z, 3)],
            "bounding_box": bbox,
        })
    return {"success": True, "count": len(components), "components": components}


def delete_component(design, rootComp, params):
    occ = _get_occurrence(rootComp, params.get('index'), params.get('name'))
    if not occ:
        return {"success": False, "error": "Component not found"}
    name = occ.component.name
    occ.deleteMe()
    return {"success": True, "deleted": name}


def check_interference(design, rootComp, params):
    occs = rootComp.allOccurrences
    if occs.count < 2:
        return {"success": True, "interference": False, "message": "Less than 2 components"}
    overlaps = []
    for i in range(occs.count):
        for j in range(i + 1, occs.count):
            try:
                bb_a = occs.item(i).boundingBox
                bb_b = occs.item(j).boundingBox
                if (bb_a.minPoint.x <= bb_b.maxPoint.x and bb_a.maxPoint.x >= bb_b.minPoint.x and
                        bb_a.minPoint.y <= bb_b.maxPoint.y and bb_a.maxPoint.y >= bb_b.minPoint.y and
                        bb_a.minPoint.z <= bb_b.maxPoint.z and bb_a.maxPoint.z >= bb_b.minPoint.z):
                    overlaps.append({
                        "a": occs.item(i).component.name,
                        "b": occs.item(j).component.name,
                    })
            except:
                pass
    return {"success": True, "interference": len(overlaps) > 0, "overlaps": overlaps}


def move_component(design, rootComp, params):
    occ = _get_occurrence(rootComp, params.get('index'), params.get('name'))
    if not occ:
        return {"success": False, "error": "Component not found"}

    x, y, z = params.get('x', 0), params.get('y', 0), params.get('z', 0)
    absolute = params.get('absolute', True)
    transform = occ.transform

    if absolute:
        transform.translation = adsk.core.Vector3D.create(x, y, z)
    else:
        cur = transform.translation
        transform.translation = adsk.core.Vector3D.create(cur.x + x, cur.y + y, cur.z + z)

    occ.transform = transform
    return {"success": True}


def rotate_component(design, rootComp, params):
    occ = _get_occurrence(rootComp, params.get('index'), params.get('name'))
    if not occ:
        return {"success": False, "error": "Component not found"}

    angle = math.radians(params['angle'])
    axis_name = params.get('axis', 'Z').upper()
    ox = params.get('origin_x', 0)
    oy = params.get('origin_y', 0)
    oz = params.get('origin_z', 0)

    axis_vectors = {'X': (1, 0, 0), 'Y': (0, 1, 0), 'Z': (0, 0, 1)}
    ax, ay, az = axis_vectors.get(axis_name, (0, 0, 1))
    axis_vec = adsk.core.Vector3D.create(ax, ay, az)
    origin   = adsk.core.Point3D.create(ox, oy, oz)

    rotation = adsk.core.Matrix3D.create()
    rotation.setToRotation(angle, axis_vec, origin)

    current = occ.transform
    current.transformBy(rotation)
    occ.transform = current
    return {"success": True}


# =============================================================================
# JOINTS
# =============================================================================

def _joint_direction(ax, ay, az):
    dirs = {
        (1, 0, 0): adsk.fusion.JointDirections.XAxisJointDirection,
        (0, 1, 0): adsk.fusion.JointDirections.YAxisJointDirection,
        (0, 0, 1): adsk.fusion.JointDirections.ZAxisJointDirection,
    }
    return dirs.get((round(ax), round(ay), round(az)),
                    adsk.fusion.JointDirections.ZAxisJointDirection)


def create_revolute_joint(design, rootComp, params):
    occs = rootComp.allOccurrences
    if occs.count < 2:
        return {"success": False, "error": "Need at least 2 components"}

    occ1 = occs.item(params.get('component1_index', 0))
    occ2 = occs.item(params.get('component2_index', 1))

    geo1 = adsk.fusion.JointGeometry.createByPoint(occ1.component.originConstructionPoint)
    geo2 = adsk.fusion.JointGeometry.createByPoint(occ2.component.originConstructionPoint)

    jointInput = rootComp.joints.createInput(geo1, geo2)
    direction = _joint_direction(params.get('axis_x', 0), params.get('axis_y', 0), params.get('axis_z', 1))
    jointInput.setAsRevoluteJointMotion(direction)

    min_angle = params.get('min_angle')
    max_angle = params.get('max_angle')
    if min_angle is not None or max_angle is not None:
        try:
            limits = jointInput.jointMotion.rotationLimits
            if max_angle is not None:
                limits.isMaximumValueEnabled = True
                limits.maximumValue = math.radians(max_angle)
            if min_angle is not None:
                limits.isMinimumValueEnabled = True
                limits.minimumValue = math.radians(min_angle)
        except:
            pass

    joint = rootComp.joints.add(jointInput)
    return {"success": True, "joint_name": joint.name}


def create_slider_joint(design, rootComp, params):
    occs = rootComp.allOccurrences
    if occs.count < 2:
        return {"success": False, "error": "Need at least 2 components"}

    occ1 = occs.item(params.get('component1_index', 0))
    occ2 = occs.item(params.get('component2_index', 1))

    geo1 = adsk.fusion.JointGeometry.createByPoint(occ1.component.originConstructionPoint)
    geo2 = adsk.fusion.JointGeometry.createByPoint(occ2.component.originConstructionPoint)

    jointInput = rootComp.joints.createInput(geo1, geo2)
    direction = _joint_direction(params.get('axis_x', 1), params.get('axis_y', 0), params.get('axis_z', 0))
    jointInput.setAsSliderJointMotion(direction)

    min_dist = params.get('min_distance')
    max_dist = params.get('max_distance')
    if min_dist is not None or max_dist is not None:
        try:
            limits = jointInput.jointMotion.slideLimits
            if max_dist is not None:
                limits.isMaximumValueEnabled = True
                limits.maximumValue = max_dist
            if min_dist is not None:
                limits.isMinimumValueEnabled = True
                limits.minimumValue = min_dist
        except:
            pass

    joint = rootComp.joints.add(jointInput)
    return {"success": True, "joint_name": joint.name}


def set_joint_angle(design, rootComp, params):
    joints = rootComp.joints
    if joints.count == 0:
        return {"success": False, "error": "No joints"}
    idx = params.get('joint_index', joints.count - 1)
    if idx >= joints.count:
        return {"success": False, "error": "Joint index out of range"}
    joints.item(idx).jointMotion.rotationValue = math.radians(params['angle'])
    return {"success": True}


def set_joint_distance(design, rootComp, params):
    joints = rootComp.joints
    if joints.count == 0:
        return {"success": False, "error": "No joints"}
    idx = params.get('joint_index', joints.count - 1)
    if idx >= joints.count:
        return {"success": False, "error": "Joint index out of range"}
    joints.item(idx).jointMotion.slideValue = params['distance']
    return {"success": True}


# =============================================================================
# BOOLEAN
# =============================================================================

def combine_bodies(design, rootComp, params):
    target_idx  = params['target_body']
    tool_indices = params['tool_bodies']
    operation   = params.get('operation', 'cut').lower()
    keep_tools  = params.get('keep_tools', False)

    if target_idx >= rootComp.bRepBodies.count:
        return {"success": False, "error": f"Target body index {target_idx} out of range"}

    target = rootComp.bRepBodies.item(target_idx)
    tools  = adsk.core.ObjectCollection.create()
    for i in tool_indices:
        if i < rootComp.bRepBodies.count:
            tools.add(rootComp.bRepBodies.item(i))

    op_map = {
        'cut':       adsk.fusion.FeatureOperations.CutFeatureOperation,
        'join':      adsk.fusion.FeatureOperations.JoinFeatureOperation,
        'intersect': adsk.fusion.FeatureOperations.IntersectFeatureOperation,
    }
    op = op_map.get(operation, adsk.fusion.FeatureOperations.CutFeatureOperation)

    combineInput = rootComp.features.combineFeatures.createInput(target, tools)
    combineInput.operation = op
    combineInput.isKeepToolBodies = keep_tools
    result = rootComp.features.combineFeatures.add(combineInput)
    return {"success": True, "feature_name": result.name}


# =============================================================================
# UTILITY
# =============================================================================

def undo_ops(design, rootComp, params):
    global app
    count = int(params.get('count', 1))
    done = 0
    for _ in range(count):
        try:
            app.executeTextCommand('Commands.Undo')
            done += 1
        except:
            break
    return {"success": True, "undone_count": done}


def delete_body(design, rootComp, params):
    body = _get_body(rootComp, params.get('body_index'))
    if not body:
        return {"success": False, "error": "No bodies"}
    name = body.name
    body.deleteMe()
    return {"success": True, "deleted": name}


def delete_sketch(design, rootComp, params):
    sketches = rootComp.sketches
    if sketches.count == 0:
        return {"success": False, "error": "No sketches"}
    idx = params.get('sketch_index')
    if idx is None:
        idx = sketches.count - 1
    if idx >= sketches.count:
        return {"success": False, "error": "Sketch index out of range"}
    name = sketches.item(idx).name
    sketches.item(idx).deleteMe()
    return {"success": True, "deleted": name}


# =============================================================================
# EXPORT / IMPORT
# =============================================================================

def export_stl(design, rootComp, params):
    filepath = params['filepath']
    exportMgr = adsk.fusion.ExportManager.cast(design.exportManager)
    options = exportMgr.createSTLExportOptions(rootComp)
    options.filename = filepath
    options.sendToPrintUtility = False
    exportMgr.execute(options)
    return {"success": True, "filepath": filepath}


def export_step(design, rootComp, params):
    filepath = params['filepath']
    exportMgr = adsk.fusion.ExportManager.cast(design.exportManager)
    options = exportMgr.createSTEPExportOptions(filepath, rootComp)
    exportMgr.execute(options)
    return {"success": True, "filepath": filepath}


def export_3mf(design, rootComp, params):
    filepath = params['filepath']
    exportMgr = adsk.fusion.ExportManager.cast(design.exportManager)
    options = exportMgr.create3MFExportOptions(rootComp)
    options.filename = filepath
    exportMgr.execute(options)
    return {"success": True, "filepath": filepath}


def import_mesh(design, rootComp, params):
    """Import STL mesh file into the current design as a mesh body."""
    global app
    filepath = params['filepath']
    # Try the newer API first, fall back to older approach
    try:
        importMgr = app.importManager
        # Fusion 360 2.0+ API for mesh import
        meshOptions = importMgr.createMeshImportOptions(filepath)
        importMgr.importToTarget(meshOptions, rootComp)
    except AttributeError:
        # Alternative: use insertMesh via the fusion API
        try:
            meshOptions = app.importManager.createSATImportOptions(filepath)
            app.importManager.importToTarget(meshOptions, rootComp)
        except Exception as e2:
            return {"success": False, "error": f"Mesh import not supported in this Fusion version. Use import_step for STEP files. Error: {str(e2)}"}
    return {"success": True, "filepath": filepath}


def import_step(design, rootComp, params):
    """Open a STEP file as a new document in Fusion 360."""
    global app
    filepath = params['filepath']
    importMgr = app.importManager
    stepOptions = importMgr.createSTEPImportOptions(filepath)
    # importToNewDocument opens it as a new tab in Fusion
    newDoc = importMgr.importToNewDocument(stepOptions)
    if newDoc:
        newDoc.activate()
        return {"success": True, "filepath": filepath, "document": newDoc.name}
    return {"success": False, "error": "importToNewDocument returned None"}


def get_mesh_bodies(design, rootComp, params):
    meshes = rootComp.meshBodies
    result = []
    for i in range(meshes.count):
        m = meshes.item(i)
        bb = m.boundingBox
        tri_count = None
        node_count = None
        try:
            tri_count = m.mesh.triangleCount
            node_count = m.mesh.nodeCount
        except:
            pass
        result.append({
            "index": i,
            "name": m.name,
            "triangle_count": tri_count,
            "node_count": node_count,
            "bounding_box": {
                "min_x": round(bb.minPoint.x * 10, 3),
                "min_y": round(bb.minPoint.y * 10, 3),
                "min_z": round(bb.minPoint.z * 10, 3),
                "max_x": round(bb.maxPoint.x * 10, 3),
                "max_y": round(bb.maxPoint.y * 10, 3),
                "max_z": round(bb.maxPoint.z * 10, 3),
                "size_x": round((bb.maxPoint.x - bb.minPoint.x) * 10, 3),
                "size_y": round((bb.maxPoint.y - bb.minPoint.y) * 10, 3),
                "size_z": round((bb.maxPoint.z - bb.minPoint.z) * 10, 3),
            }
        })
    return {"success": True, "count": meshes.count, "meshes": result}


def get_mesh_bounding_box(design, rootComp, params):
    idx = params.get('mesh_index', 0)
    meshes = rootComp.meshBodies
    if idx >= meshes.count:
        return {"success": False, "error": f"No mesh at index {idx}"}
    m = meshes.item(idx)
    bb = m.boundingBox
    return {
        "success": True,
        "name": m.name,
        "min_x": round(bb.minPoint.x * 10, 3),
        "min_y": round(bb.minPoint.y * 10, 3),
        "min_z": round(bb.minPoint.z * 10, 3),
        "max_x": round(bb.maxPoint.x * 10, 3),
        "max_y": round(bb.maxPoint.y * 10, 3),
        "max_z": round(bb.maxPoint.z * 10, 3),
        "size_x_mm": round((bb.maxPoint.x - bb.minPoint.x) * 10, 3),
        "size_y_mm": round((bb.maxPoint.y - bb.minPoint.y) * 10, 3),
        "size_z_mm": round((bb.maxPoint.z - bb.minPoint.z) * 10, 3),
        "center_x": round((bb.minPoint.x + bb.maxPoint.x) * 5, 3),
        "center_y": round((bb.minPoint.y + bb.maxPoint.y) * 5, 3),
        "center_z": round((bb.minPoint.z + bb.maxPoint.z) * 5, 3),
    }


# Conversion methods. Values match adsk.fusion.MeshConvertMethodTypes; the
# names are resolved dynamically where available so this keeps working if
# Autodesk renumbers the enum.
_MESH_METHODS = {
    'faceted':   ('FacetedMeshConvertMethodType', 0, 'infoMethodTriangleBased'),
    'prismatic': ('PrismaticMeshConvertMethodType', 1, 'infoMethodPrismatic'),
    'organic':   ('OrganicMeshConvertMethodType', 2, 'infoMethodOrganic'),
}


def _enum_value(enum_class_name, member_name, fallback):
    try:
        return getattr(getattr(adsk.fusion, enum_class_name), member_name)
    except:
        return fallback


def _brep_result(via, mesh_name, method, parametric, rootComp, prev_count, tri_count):
    new_count = rootComp.bRepBodies.count
    new_bodies = []
    total_faces = 0
    for i in range(prev_count, new_count):
        b = rootComp.bRepBodies.item(i)
        try:
            fcount = b.faces.count
        except:
            fcount = None
        if fcount:
            total_faces += fcount
        new_bodies.append({"index": i, "name": b.name, "face_count": fcount})

    result = {
        "success": True,
        "via": via,
        "mesh_name": mesh_name,
        "method": method,
        "parametric": parametric,
        "triangle_count": tri_count,
        "face_count": total_faces or None,
        "new_bodies": new_bodies,
        "body_count": new_count,
    }

    # Quality check: prismatic is supposed to merge coplanar facets. One face
    # per triangle means the method never took effect and you actually got a
    # faceted conversion, which is usable but not editable as CAD.
    if method != 'faceted' and tri_count and total_faces:
        if total_faces >= tri_count * 0.95:
            result["warning"] = (
                "Requested '%s' but got %d faces for %d triangles - the method did "
                "not take effect, this is a faceted result." % (method, total_faces, tri_count))
        else:
            result["reduction"] = "%d triangles merged into %d faces (%.1f%% fewer)" % (
                tri_count, total_faces, 100.0 * (1 - float(total_faces) / tri_count))
    return result


def mesh_to_brep(design, rootComp, params):
    """Convert a mesh body into a BRep (solid) body.

    Params:
        mesh_index: which mesh body (default 0)
        method:     "prismatic" (default; merges coplanar facets into real
                    planar faces - best for mechanical parts), "faceted"
                    (1 triangle = 1 face) or "organic" (T-Splines)
        parametric: True keeps a parametric feature in the timeline but is
                    capped at roughly 10k facets. False (default) creates a
                    base feature with no facet limit.
    """
    global app
    idx = params.get('mesh_index', 0)
    method = str(params.get('method', 'prismatic')).lower()
    parametric = bool(params.get('parametric', False))

    if method not in _MESH_METHODS:
        return {"success": False,
                "error": "Unknown method '%s'. Use prismatic, faceted or organic." % method}

    meshes = rootComp.meshBodies
    if meshes.count == 0:
        return {"success": False,
                "error": "No mesh bodies in this design. Import one with import_mesh first."}
    if idx >= meshes.count:
        return {"success": False,
                "error": "No mesh at index %d (design has %d)" % (idx, meshes.count)}

    m = meshes.item(idx)
    mesh_name = m.name
    prev_count = rootComp.bRepBodies.count

    tri_count = None
    try:
        tri_count = m.mesh.triangleCount
    except:
        pass

    enum_name, enum_fallback, text_cmd_method = _MESH_METHODS[method]

    # -- Method 1: native MeshConvertFeatures API (Fusion, July 2025 onwards) --
    native_error = None
    try:
        mcFeats = rootComp.features.meshConvertFeatures
        inputBodies = adsk.core.ObjectCollection.create()
        inputBodies.add(m)
        try:
            mcInput = mcFeats.createInput(inputBodies)
        except TypeError:
            mcInput = mcFeats.createInput()
            mcInput.inputBodies = inputBodies
        mcInput.meshConvertMethodType = _enum_value(
            'MeshConvertMethodTypes', enum_name, enum_fallback)
        mcInput.meshConvertOperationType = _enum_value(
            'MeshConvertOperationTypes',
            'ParametricFeatureMeshConvertOperationType' if parametric
            else 'BaseFeatureMeshConvertOperationType',
            0 if parametric else 1)
        mcFeats.add(mcInput)
        if rootComp.bRepBodies.count > prev_count:
            return _brep_result("meshConvertFeatures", mesh_name, method,
                                parametric, rootComp, prev_count, tri_count)
        native_error = "meshConvertFeatures ran but produced no new body"
    except Exception as e:
        native_error = str(e)

    # -- Method 2: UI text command, marshalled onto the main thread ------------
    # Undocumented and version-dependent, but it is what the Convert Mesh
    # dialog itself drives. Only reached if the native API is unavailable.
    def _text_command_convert():
        sels = ui.activeSelections
        sels.clear()
        sels.add(m)
        app.executeTextCommand(u'Commands.Start ParaMeshConvertCommand')
        # The dialog needs a beat to build its inputs, and the first SetString
        # after opening it gets swallowed - so let the UI settle, then write the
        # method twice. Without this the command silently keeps its default
        # (faceted) and you get one face per triangle.
        adsk.doEvents()
        time.sleep(0.3)
        set_method = u'Commands.SetString MeshToBREPAlgorithmInput ' + text_cmd_method
        app.executeTextCommand(set_method)
        adsk.doEvents()
        app.executeTextCommand(set_method)
        adsk.doEvents()
        # The operation input has been renamed between releases; a failure here
        # only means we fall back to whatever the dialog defaults to.
        for op_input in (u'ConvertMeshInfoOperation', u'MeshToBREPOperationInput'):
            try:
                app.executeTextCommand(
                    u'Commands.SetString ' + op_input + u' '
                    + (u'infoOperationParametric' if parametric
                       else u'infoOperationBaseFeature'))
                adsk.doEvents()
            except:
                pass
        app.executeTextCommand(u'NuCommands.CommitCmd')
        adsk.doEvents()
        return {"ok": True}

    text_error = None
    try:
        r = run_on_main_thread(_text_command_convert)
        if isinstance(r, dict) and r.get("error"):
            text_error = r.get("error")
        # Conversion completes asynchronously - wait for the body to show up.
        for _ in range(120):
            if rootComp.bRepBodies.count > prev_count:
                break
            time.sleep(0.5)
        if rootComp.bRepBodies.count > prev_count:
            return _brep_result("ParaMeshConvertCommand", mesh_name, method,
                                parametric, rootComp, prev_count, tri_count)
    except Exception as e:
        text_error = str(e)

    hint = ""
    if tri_count and parametric and tri_count > 10000:
        hint = (" The mesh has %d triangles and parametric mode is capped at about "
                "10000 facets - retry with parametric=False, or reduce_mesh first."
                % tri_count)
    elif tri_count and tri_count > 200000:
        hint = (" The mesh has %d triangles, which is a lot for Convert Mesh - "
                "try reduce_mesh first." % tri_count)

    return {
        "success": False,
        "error": "Mesh conversion failed." + hint,
        "native_api_error": native_error,
        "text_command_error": text_error,
        "mesh_name": mesh_name,
        "triangle_count": tri_count,
        "manual_fallback": "Mesh workspace -> Modify -> Convert Mesh",
    }


def reduce_mesh(design, rootComp, params):
    """Decimate a mesh body down to a target triangle count.

    Params:
        mesh_index:   which mesh body (default 0)
        target_faces: desired triangle count (default 10000)

    Uses the undocumented ParaMeshReduceCommand text command - there is no
    public API for mesh reduction.
    """
    global app
    idx = params.get('mesh_index', 0)
    target = int(params.get('target_faces', 10000))

    meshes = rootComp.meshBodies
    if idx >= meshes.count:
        return {"success": False,
                "error": "No mesh at index %d (design has %d)" % (idx, meshes.count)}
    m = meshes.item(idx)

    before = None
    try:
        before = m.mesh.triangleCount
    except:
        pass
    if before and target >= before:
        return {"success": True, "skipped": True,
                "reason": "Mesh already has %d triangles (target %d)" % (before, target),
                "triangles_before": before, "triangles_after": before}

    def _do_reduce():
        sels = ui.activeSelections
        sels.clear()
        sels.add(m)
        for c in [u'Commands.Start ParaMeshReduceCommand',
                  u'Commands.SetString infoReduceType infoTriCount',
                  u'Commands.SetString infoMeshingType infoAdaptiveType',
                  u'Commands.SetDouble infoFacets %d' % target,
                  u'NuCommands.CommitCmd']:
            app.executeTextCommand(c)
        adsk.doEvents()
        return {"ok": True}

    r = run_on_main_thread(_do_reduce)
    if isinstance(r, dict) and r.get("error"):
        return {"success": False, "error": r["error"], "triangles_before": before}

    after = None
    for _ in range(60):
        try:
            after = rootComp.meshBodies.item(idx).mesh.triangleCount
        except:
            after = None
        if after is not None and before is not None and after < before:
            break
        time.sleep(0.5)

    if before is not None and after is not None and after >= before:
        return {"success": False,
                "error": "Reduce ran but the triangle count did not change. The "
                         "ParaMeshReduceCommand inputs may have changed in this "
                         "Fusion version - reduce manually via Mesh -> Modify -> Reduce.",
                "triangles_before": before, "triangles_after": after}

    return {"success": True, "triangles_before": before,
            "triangles_after": after, "target": target}


# =============================================================================
# HELPERS
# =============================================================================

def _active_sketch(design, rootComp):
    """Return the most recently created sketch."""
    if rootComp.sketches.count > 0:
        return rootComp.sketches.item(rootComp.sketches.count - 1)
    return None


def _get_body(rootComp, body_index=None):
    if rootComp.bRepBodies.count == 0:
        return None
    if body_index is None:
        body_index = rootComp.bRepBodies.count - 1
    if body_index >= rootComp.bRepBodies.count:
        return None
    return rootComp.bRepBodies.item(body_index)


def _get_occurrence(rootComp, index=None, name=None):
    occs = rootComp.allOccurrences
    if name:
        for i in range(occs.count):
            occ = occs.item(i)
            if occ.component.name == name:
                return occ
    if index is not None and index < occs.count:
        return occs.item(index)
    return None
