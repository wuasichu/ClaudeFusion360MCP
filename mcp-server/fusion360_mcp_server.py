#!/usr/bin/env python3
"""
Fusion 360 MCP Server v7.2 - ENHANCED
=======================================
Based on v6.0 Optimized, with critical new features:

NEW TOOLS:
  o move_component    - Position components after creation (CRITICAL)
  o rotate_component  - Rotate components around axes
  o shell             - Create hollow parts/enclosures (CRITICAL)
  o draft             - Injection molding draft angles
  o pattern_rectangular - Linear arrays
  o pattern_circular  - Radial arrays
  o mirror            - Symmetric geometry
  o measure           - Dimension verification
  o get_body_info     - Edge/face listing for selection

ENHANCED TOOLS:
  o create_sketch     - Now supports offset parameter
  o extrude           - Now supports taper_angle and profile_index
  o fillet            - Now supports selective edge indices
  o chamfer           - Now supports selective edge indices

PRESERVED:
  o Batch operations (5-10x faster)
  o 50ms polling
  o 45s timeout
  o All v6.0 features
"""
from mcp.server.fastmcp import FastMCP
import json
import time
from pathlib import Path

COMM_DIR = Path.home() / "fusion_mcp_comm"
COMM_DIR.mkdir(exist_ok=True)

mcp = FastMCP("Fusion 360 v7.2 Enhanced")

def send_fusion_command(tool_name: str, params: dict, timeout: int = 45) -> dict:
    """Send command to Fusion 360 via file system.

    timeout: seconds to wait for a response. Mesh operations need far more
    than the 45s default - converting or reducing a dense mesh can take
    minutes of Fusion compute.
    """
    timestamp = int(time.time() * 1000)
    cmd_file = COMM_DIR / f"command_{timestamp}.json"
    resp_file = COMM_DIR / f"response_{timestamp}.json"

    with open(cmd_file, 'w') as f:
        json.dump({"type": "tool", "name": tool_name, "params": params, "id": timestamp}, f)

    for _ in range(int(timeout / 0.05)):
        time.sleep(0.05)  # 50ms polling
        if resp_file.exists():
            with open(resp_file, 'r') as f:
                result = json.load(f)
            try:
                cmd_file.unlink()
                resp_file.unlink()
            except:
                pass
            if not result.get("success"):
                raise Exception(result.get("error", "Unknown error"))
            return result

    raise Exception(f"Timeout after {timeout}s - is Fusion 360 running with FusionMCP add-in?")

# =============================================================================
# BATCH OPERATIONS
# =============================================================================

@mcp.tool()
def batch(commands: list) -> dict:
    """
    Execute multiple Fusion commands in a single call - MUCH faster for complex operations.
    
    Example: batch([
        {"name": "create_sketch", "params": {"plane": "XY"}},
        {"name": "draw_rectangle", "params": {"x1": -5, "y1": -5, "x2": 5, "y2": 5}},
        {"name": "draw_circle", "params": {"center_x": 0, "center_y": 0, "radius": 2}},
        {"name": "finish_sketch", "params": {}},
        {"name": "extrude", "params": {"distance": 3}}
    ])
    
    This executes all commands in one round-trip instead of 5 separate calls.
    Stops on first error and returns partial results.
    """
    return send_fusion_command("batch", {"commands": commands})

# =============================================================================
# SKETCH CREATION (ENHANCED)
# =============================================================================

@mcp.tool()
def create_sketch(plane: str, offset: float = 0) -> dict:
    """
    Create a new sketch on a construction plane (XY, XZ, or YZ) and enter edit mode.
    
    Args:
        plane: "XY" (horizontal), "XZ" (vertical front), or "YZ" (side)
        offset: Distance to offset sketch plane from origin (cm). Default 0.
    
    Examples:
        create_sketch(plane="XY")           # Horizontal at origin
        create_sketch(plane="XZ", offset=5) # Vertical, 5cm forward
    """
    return send_fusion_command("create_sketch", {"plane": plane, "offset": offset})

@mcp.tool()
def finish_sketch() -> dict:
    """Exit sketch editing mode"""
    return send_fusion_command("finish_sketch", {})

# =============================================================================
# SKETCH GEOMETRY
# =============================================================================

@mcp.tool()
def draw_rectangle(x1: float, y1: float, x2: float, y2: float) -> dict:
    """Draw a rectangle in the active sketch (units: cm)"""
    return send_fusion_command("draw_rectangle", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

@mcp.tool()
def draw_circle(center_x: float, center_y: float, radius: float) -> dict:
    """Draw a circle in the active sketch (units: cm)"""
    return send_fusion_command("draw_circle", {"center_x": center_x, "center_y": center_y, "radius": radius})

@mcp.tool()
def draw_line(x1: float, y1: float, x2: float, y2: float) -> dict:
    """Draw a straight line in the active sketch (units: cm)"""
    return send_fusion_command("draw_line", {"x1": x1, "y1": y1, "x2": x2, "y2": y2})

@mcp.tool()
def draw_arc(center_x: float, center_y: float, start_x: float, start_y: float, end_x: float, end_y: float) -> dict:
    """Draw an arc in the active sketch (units: cm)"""
    return send_fusion_command("draw_arc", {
        "center_x": center_x, "center_y": center_y,
        "start_x": start_x, "start_y": start_y,
        "end_x": end_x, "end_y": end_y
    })

@mcp.tool()
def draw_polygon(center_x: float, center_y: float, radius: float, sides: int = 6) -> dict:
    """Draw a regular polygon in the active sketch (units: cm). Default is hexagon."""
    return send_fusion_command("draw_polygon", {
        "center_x": center_x, "center_y": center_y, 
        "radius": radius, "sides": sides
    })

# =============================================================================
# 3D FEATURE OPERATIONS (ENHANCED)
# =============================================================================

@mcp.tool()
def extrude(distance: float, profile_index: int = 0, taper_angle: float = 0) -> dict:
    """
    Extrude the most recent sketch profile (units: cm).
    
    Args:
        distance: Extrusion distance (positive or negative)
        profile_index: Which profile if multiple exist (default 0)
        taper_angle: Draft angle during extrusion in degrees (default 0)
    """
    return send_fusion_command("extrude", {
        "distance": distance, 
        "profile_index": profile_index, 
        "taper_angle": taper_angle
    })

@mcp.tool()
def revolve(angle: float) -> dict:
    """Revolve the most recent sketch profile around an axis (degrees)"""
    return send_fusion_command("revolve", {"angle": angle})

@mcp.tool()
def fillet(radius: float, edges: list = None, body_index: int = None) -> dict:
    """
    Add fillets to edges of a body (units: cm).
    
    Args:
        radius: Fillet radius
        edges: Optional list of edge indices. If None, fillets all edges.
        body_index: Which body (default: most recent)
    
    Use get_body_info() to see available edge indices.
    """
    params = {"radius": radius}
    if edges is not None:
        params["edges"] = edges
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("fillet", params)

@mcp.tool()
def chamfer(distance: float, edges: list = None, body_index: int = None) -> dict:
    """
    Add chamfers to edges of a body (units: cm).
    
    Args:
        distance: Chamfer distance
        edges: Optional list of edge indices. If None, chamfers all edges.
        body_index: Which body (default: most recent)
    
    Use get_body_info() to see available edge indices.
    """
    params = {"distance": distance}
    if edges is not None:
        params["edges"] = edges
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("chamfer", params)

# =============================================================================
# NEW: SHELL, DRAFT, PATTERNS, MIRROR
# =============================================================================

@mcp.tool()
def shell(thickness: float, faces_to_remove: list = None, body_index: int = None) -> dict:
    """
    Create a hollow shell from a solid body (units: cm).
    
    Args:
        thickness: Wall thickness
        faces_to_remove: List of face indices to remove (create openings). Default: closed shell.
        body_index: Which body (default: most recent)
    
    Examples:
        shell(thickness=0.2)                        # 2mm closed shell
        shell(thickness=0.15, faces_to_remove=[0])  # Open-top container
    
    Use get_body_info() to see available face indices.
    """
    params = {"thickness": thickness}
    if faces_to_remove is not None:
        params["faces_to_remove"] = faces_to_remove
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("shell", params)

@mcp.tool()
def draft(angle: float, faces: list = None, body_index: int = None, 
          pull_x: float = 0, pull_y: float = 0, pull_z: float = 1) -> dict:
    """
    Apply draft angles to faces for injection molding (angle in degrees).
    
    Args:
        angle: Draft angle (typically 0.5-3 degrees, guideline: 1 degree per inch)
        faces: List of face indices. If None, drafts all faces.
        body_index: Which body (default: most recent)
        pull_x, pull_y, pull_z: Pull direction vector (default: +Z)
    """
    params = {"angle": angle, "pull_x": pull_x, "pull_y": pull_y, "pull_z": pull_z}
    if faces is not None:
        params["faces"] = faces
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("draft", params)

@mcp.tool()
def pattern_rectangular(x_count: int, x_spacing: float, 
                        y_count: int = 1, y_spacing: float = 0, 
                        body_index: int = None) -> dict:
    """
    Create a rectangular (linear) pattern of a body (spacing in cm).
    
    Args:
        x_count: Number of instances in X direction
        x_spacing: Spacing between instances in X
        y_count: Number of instances in Y direction (default 1)
        y_spacing: Spacing between instances in Y
        body_index: Which body (default: most recent)
    
    Example:
        pattern_rectangular(x_count=4, x_spacing=2.5, y_count=3, y_spacing=2.5)
        # Creates 4x3 = 12 instances
    """
    params = {"x_count": x_count, "x_spacing": x_spacing, "y_count": y_count, "y_spacing": y_spacing}
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("pattern_rectangular", params)

@mcp.tool()
def pattern_circular(count: int, angle: float = 360, axis: str = "Z", body_index: int = None) -> dict:
    """
    Create a circular (radial) pattern of a body.
    
    Args:
        count: Number of instances
        angle: Total angle in degrees (default 360 for full circle)
        axis: Rotation axis - "X", "Y", or "Z" (default "Z")
        body_index: Which body (default: most recent)
    
    Example:
        pattern_circular(count=6, angle=360, axis="Z")
        # 6 instances evenly spaced around Z axis
    """
    params = {"count": count, "angle": angle, "axis": axis}
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("pattern_circular", params)

@mcp.tool()
def mirror(plane: str = "YZ", body_index: int = None) -> dict:
    """
    Create a mirrored copy of a body.
    
    Args:
        plane: Mirror plane - "XY", "XZ", or "YZ" (default "YZ" for left-right symmetry)
        body_index: Which body (default: most recent)
    """
    params = {"plane": plane}
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("mirror", params)

# =============================================================================
# VIEW & DESIGN INFO
# =============================================================================

@mcp.tool()
def fit_view() -> dict:
    """Fit the viewport to show all geometry"""
    return send_fusion_command("fit_view", {})

@mcp.tool()
def get_design_info() -> dict:
    """Get information about the current design (name, body count, sketch count, component count, active sketch status)"""
    return send_fusion_command("get_design_info", {})

# =============================================================================
# NEW: MEASUREMENT & INSPECTION
# =============================================================================

@mcp.tool()
def get_body_info(body_index: int = None) -> dict:
    """
    Get detailed information about a body including all edges and faces.
    
    Args:
        body_index: Which body (default: most recent)
    
    Returns edge indices with lengths, face indices with areas.
    Use this to find indices for selective fillet, chamfer, shell, or draft.
    """
    params = {}
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("get_body_info", params)

@mcp.tool()
def measure(type: str = "body", body_index: int = None, 
            edge_index: int = None, face_index: int = None) -> dict:
    """
    Measure dimensions of bodies, edges, or faces.
    
    Args:
        type: What to measure - "body", "edge", or "face"
        body_index: Which body (default: most recent)
        edge_index: Edge index (for type="edge")
        face_index: Face index (for type="face")
    
    Returns:
        body: volume, surface_area, bounding_box with size
        edge: length
        face: area
    """
    params = {"type": type}
    if body_index is not None:
        params["body_index"] = body_index
    if edge_index is not None:
        params["edge_index"] = edge_index
    if face_index is not None:
        params["face_index"] = face_index
    return send_fusion_command("measure", params)

# =============================================================================
# COMPONENT & ASSEMBLY
# =============================================================================

@mcp.tool()
def create_component(name: str = None) -> dict:
    """Convert the most recent body into a new component for assembly"""
    params = {}
    if name:
        params["name"] = name
    return send_fusion_command("create_component", params)

@mcp.tool()
def list_components() -> dict:
    """List all components with names, positions, and bounding boxes"""
    return send_fusion_command("list_components", {})

@mcp.tool()
def delete_component(name: str = None, index: int = None) -> dict:
    """Delete a component by name or index"""
    params = {}
    if name:
        params["name"] = name
    if index is not None:
        params["index"] = index
    return send_fusion_command("delete_component", params)

@mcp.tool()
def check_interference() -> dict:
    """Check if any components overlap (bounding box collision detection)"""
    return send_fusion_command("check_interference", {})

# =============================================================================
# NEW: COMPONENT POSITIONING (CRITICAL)
# =============================================================================

@mcp.tool()
def move_component(x: float = 0, y: float = 0, z: float = 0,
                   index: int = None, name: str = None, 
                   absolute: bool = True) -> dict:
    """
    Move a component to a new position (units: cm).
    
    Args:
        x, y, z: Target position or offset
        index: Component index (from list_components)
        name: Component name (alternative to index)
        absolute: If True, set absolute position. If False, move by offset.
    
    Examples:
        move_component(x=0, y=10, z=0, index=1)                    # Move to Y=10
        move_component(x=5, y=0, z=0, index=0, absolute=False)     # Move 5cm in X
    
    CRITICAL: Use this to position components after creation to avoid overlaps.
    """
    params = {"x": x, "y": y, "z": z, "absolute": absolute}
    if index is not None:
        params["index"] = index
    if name is not None:
        params["name"] = name
    return send_fusion_command("move_component", params)

@mcp.tool()
def rotate_component(angle: float, axis: str = "Z",
                     index: int = None, name: str = None,
                     origin_x: float = 0, origin_y: float = 0, origin_z: float = 0) -> dict:
    """
    Rotate a component around an axis (angle in degrees).
    
    Args:
        angle: Rotation angle in degrees
        axis: Rotation axis - "X", "Y", or "Z" (default "Z")
        index: Component index (from list_components)
        name: Component name (alternative to index)
        origin_x, origin_y, origin_z: Rotation origin point (cm)
    """
    params = {
        "angle": angle, "axis": axis,
        "origin_x": origin_x, "origin_y": origin_y, "origin_z": origin_z
    }
    if index is not None:
        params["index"] = index
    if name is not None:
        params["name"] = name
    return send_fusion_command("rotate_component", params)

# =============================================================================
# JOINTS
# =============================================================================

@mcp.tool()
def create_revolute_joint(
    component1_index: int = None,
    component2_index: int = None,
    x: float = 0, y: float = 0, z: float = 0,
    axis_x: float = 0, axis_y: float = 0, axis_z: float = 1,
    min_angle: float = None, max_angle: float = None,
    flip: bool = False
) -> dict:
    """Create a revolute (rotating) joint between two components"""
    params = {"x": x, "y": y, "z": z, "axis_x": axis_x, "axis_y": axis_y, "axis_z": axis_z, "flip": flip}
    if component1_index is not None:
        params["component1_index"] = component1_index
    if component2_index is not None:
        params["component2_index"] = component2_index
    if min_angle is not None:
        params["min_angle"] = min_angle
    if max_angle is not None:
        params["max_angle"] = max_angle
    return send_fusion_command("create_revolute_joint", params)

@mcp.tool()
def create_slider_joint(
    component1_index: int = None,
    component2_index: int = None,
    x: float = 0, y: float = 0, z: float = 0,
    axis_x: float = 1, axis_y: float = 0, axis_z: float = 0,
    min_distance: float = None, max_distance: float = None
) -> dict:
    """Create a slider (linear) joint between two components"""
    params = {"x": x, "y": y, "z": z, "axis_x": axis_x, "axis_y": axis_y, "axis_z": axis_z}
    if component1_index is not None:
        params["component1_index"] = component1_index
    if component2_index is not None:
        params["component2_index"] = component2_index
    if min_distance is not None:
        params["min_distance"] = min_distance
    if max_distance is not None:
        params["max_distance"] = max_distance
    return send_fusion_command("create_slider_joint", params)

@mcp.tool()
def set_joint_angle(angle: float, joint_index: int = None) -> dict:
    """Animate a revolute joint to a specific angle (degrees)"""
    params = {"angle": angle}
    if joint_index is not None:
        params["joint_index"] = joint_index
    return send_fusion_command("set_joint_angle", params)

@mcp.tool()
def set_joint_distance(distance: float, joint_index: int = None) -> dict:
    """Animate a slider joint to a specific distance (cm)"""
    params = {"distance": distance}
    if joint_index is not None:
        params["joint_index"] = joint_index
    return send_fusion_command("set_joint_distance", params)

# =============================================================================
# BOOLEAN OPERATIONS (v7.1 - Added combine)
# =============================================================================

@mcp.tool()
def combine(target_body: int, tool_bodies: list, operation: str = "cut", keep_tools: bool = False) -> dict:
    """
    Boolean operations: cut, join, or intersect bodies.
    
    Args:
        target_body: Index of body to modify (0 = first body)
        tool_bodies: List of body indices to use as tools
        operation: "cut" (subtract), "join" (add), or "intersect"
        keep_tools: If True, keep tool bodies after operation
    
    Examples:
        combine(target_body=0, tool_bodies=[1], operation="cut")  # Cut body1 from body0
        combine(target_body=0, tool_bodies=[1,2], operation="join")  # Merge 3 bodies
    
    Use get_body_info() to verify body indices before combining.
    """
    return send_fusion_command("combine", {
        "target_body": target_body,
        "tool_bodies": tool_bodies,
        "operation": operation,
        "keep_tools": keep_tools
    })
# =============================================================================
# UTILITY OPERATIONS (v7.2 - undo, delete_body, delete_sketch)
# =============================================================================

@mcp.tool()
def undo(count: int = 1) -> dict:
    """
    Undo recent operations.
    
    Args:
        count: Number of operations to undo (default 1)
    
    Returns:
        undone_count: How many operations were actually undone
    """
    return send_fusion_command("undo", {"count": count})

@mcp.tool()
def delete_body(body_index: int = None) -> dict:
    """
    Delete a body by index.
    
    Args:
        body_index: Index of body to delete (default: most recent body)
    
    Use get_design_info() to see body count, get_body_info() for details.
    """
    params = {}
    if body_index is not None:
        params["body_index"] = body_index
    return send_fusion_command("delete_body", params)

@mcp.tool()
def delete_sketch(sketch_index: int = None) -> dict:
    """
    Delete a sketch by index.
    
    Args:
        sketch_index: Index of sketch to delete (default: most recent sketch)
    
    Use get_design_info() to see sketch count.
    """
    params = {}
    if sketch_index is not None:
        params["sketch_index"] = sketch_index
    return send_fusion_command("delete_sketch", params)
# =============================================================================
# EXPORT
# =============================================================================

@mcp.tool()
def export_stl(filepath: str) -> dict:
    """Export the design as STL file for 3D printing"""
    return send_fusion_command("export_stl", {"filepath": filepath})

@mcp.tool()
def export_step(filepath: str) -> dict:
    """Export the design as STEP file (CAD standard)"""
    return send_fusion_command("export_step", {"filepath": filepath})

@mcp.tool()
def export_3mf(filepath: str) -> dict:
    """Export the design as 3MF file (modern 3D printing format)"""
    return send_fusion_command("export_3mf", {"filepath": filepath})

# =============================================================================
# IMPORT
# =============================================================================

@mcp.tool()
def loft(sketch_indices: list, profile_index: int = 0, rail_sketch_indices: list = None) -> dict:
    """
    Loft through multiple sketch profiles to create a solid body.
    sketch_indices: list of section sketch indices (e.g. [0,1,2,...,9])
    rail_sketch_indices: optional list of sketch indices containing guide rail curves
    profile_index: which profile per sketch if multiple exist (default 0)
    """
    params = {"sketch_indices": sketch_indices, "profile_index": profile_index}
    if rail_sketch_indices:
        params["rail_sketch_indices"] = rail_sketch_indices
    return send_fusion_command("loft", params)

@mcp.tool()
def create_3d_sketch(name: str = None) -> dict:
    """Create a 3D sketch (not tied to a plane) for drawing rail curves in 3D space."""
    return send_fusion_command("create_3d_sketch", {"name": name} if name else {})

@mcp.tool()
def draw_3d_line(x1: float, y1: float, z1: float, x2: float, y2: float, z2: float) -> dict:
    """Draw a line in 3D space in the current sketch. Coordinates in cm."""
    return send_fusion_command("draw_3d_line", {"x1": x1, "y1": y1, "z1": z1, "x2": x2, "y2": y2, "z2": z2})

@mcp.tool()
def draw_3d_spline(points: list) -> dict:
    """Draw a spline through 3D points in the current sketch. points = [[x,y,z], ...] in cm."""
    return send_fusion_command("draw_3d_spline", {"points": points})

@mcp.tool()
def import_mesh(filepath: str, unit: str = "mm") -> dict:
    """Import STL, OBJ, or 3MF mesh file. Units: mm, cm, or in"""
    return send_fusion_command("import_mesh", {"filepath": filepath, "unit": unit})

@mcp.tool()
def import_step(filepath: str) -> dict:
    """Import a STEP (.step/.stp) file as a solid body into the current design"""
    return send_fusion_command("import_step", {"filepath": filepath})

@mcp.tool()
def body_to_component(body_index: int = None, name: str = None) -> dict:
    """
    Move a root body into its own new component so it can be positioned,
    rotated and jointed independently of the others.

    This is what you want for assemblies. create_component only makes an EMPTY
    component - it does not move any body into it.

    Args:
        body_index: Which root body (default: the last one)
        name: Name for the new component

    Body indices shift as bodies leave the root, so when converting several
    bodies work from the highest index down, or call this repeatedly with no
    body_index and let it take the last one each time.
    """
    params = {}
    if body_index is not None:
        params["body_index"] = body_index
    if name:
        params["name"] = name
    return send_fusion_command("body_to_component", params)

# =============================================================================
# ARBITRARY SCRIPTING
# =============================================================================

@mcp.tool()
def execute_script(code: str, main_thread: bool = True, timeout: int = 300) -> dict:
    """
    Run arbitrary Fusion API Python inside the add-in. This reaches the ENTIRE
    Fusion API - appearances, saving, construction planes, section analysis,
    joints, anything - without needing a dedicated tool for each.

    Reach for this when no specific tool covers what you need. Prefer the
    dedicated tools when they exist: they validate inputs and return clean
    errors.

    Args:
        code: Python source. Assign to `result` to return a value; print()
              output is captured separately.
        main_thread: Run on Fusion's main thread (default True). REQUIRED for
              anything touching the UI or executeTextCommand - calling those
              from the background thread makes Fusion misbehave or crash.
        timeout: Seconds to wait (default 300).

    Pre-bound: adsk, app, ui, design, root, math, json, time.

    Example - paint a body:
        code = '''
        body = root.bRepBodies.item(0)
        lib = app.materialLibraries.itemByName('Fusion Appearance Library')
        body.appearance = lib.appearances.itemByName('Paint - Enamel Glossy (Black)')
        result = body.appearance.name
        '''

    No sandbox: this executes exactly what it is given, in the user's live
    Fusion session. Errors return a full traceback instead of crashing, but
    a genuinely destructive script will do genuinely destructive things.
    Do not call it with code whose effect you have not reasoned through.
    """
    return send_fusion_command(
        "execute_script",
        {"code": code, "main_thread": main_thread, "timeout": timeout},
        timeout=timeout + 30)

# =============================================================================
# T-SPLINES
# =============================================================================

@mcp.tool()
def create_tspline(tsm_text: str = None, filepath: str = None, name: str = None) -> dict:
    """
    Create a T-Spline (Form) body from TSM data - real organic, editable
    Form-workspace geometry, not a faceted solid.

    Fusion has no API to author T-Splines from scratch, but TSplineBodies can be
    built from TSM (T-Spline Mesh) data. Generate the TSM externally and push it
    in here. Requires a parametric design (timeline on).

    Args:
        tsm_text: TSM-formatted description string
        filepath: path to a .tsm file (use instead of tsm_text)
        name: name for the resulting body

    Use export_tspline_tsm on a known-good body first to learn the format.
    """
    params = {}
    if tsm_text:
        params["tsm_text"] = tsm_text
    if filepath:
        params["filepath"] = filepath
    if name:
        params["name"] = name
    return send_fusion_command("create_tspline", params, timeout=300)

@mcp.tool()
def list_tspline_bodies() -> dict:
    """List all T-Spline (Form) bodies in the design with their parent form features."""
    return send_fusion_command("list_tspline_bodies", {})

@mcp.tool()
def export_tspline_tsm(index: int = 0, filepath: str = None) -> dict:
    """
    Export a T-Spline body as TSM. Always returns the description text; also
    writes a .tsm file when filepath is given.

    This is the way to learn the TSM format: make a simple Form body, export it,
    study the text, then generate your own with create_tspline.
    """
    params = {"index": index}
    if filepath:
        params["filepath"] = filepath
    return send_fusion_command("export_tspline_tsm", params, timeout=300)

# =============================================================================
# MESH
# =============================================================================

@mcp.tool()
def get_mesh_bodies() -> dict:
    """
    List all mesh bodies (imported STL/OBJ/3MF) in the design.

    Mesh bodies are a separate collection from solid bodies - get_design_info
    reports them under "mesh_bodies". Returns index, name, triangle count and
    bounding box in mm for each.

    Check triangle_count before converting: over ~10k triangles you need
    parametric=False on mesh_to_brep, and over ~200k you should reduce_mesh first.
    """
    return send_fusion_command("get_mesh_bodies", {})

@mcp.tool()
def get_mesh_bounding_box(mesh_index: int = 0) -> dict:
    """Get the bounding box, size (mm) and centre of a mesh body."""
    return send_fusion_command("get_mesh_bounding_box", {"mesh_index": mesh_index})

@mcp.tool()
def reduce_mesh(mesh_index: int = 0, target_faces: int = 10000) -> dict:
    """
    Decimate a mesh body down to a target triangle count.

    Run this before mesh_to_brep on dense scans - conversion time scales badly
    with facet count. 5,000-10,000 triangles is plenty for a mechanical part.

    Args:
        mesh_index: Which mesh body (default 0)
        target_faces: Desired triangle count (default 10000)

    Returns triangles_before and triangles_after so you can verify it worked.
    No-ops safely if the mesh is already below the target.
    """
    return send_fusion_command(
        "reduce_mesh", {"mesh_index": mesh_index, "target_faces": target_faces},
        timeout=600)

@mcp.tool()
def mesh_to_brep(mesh_index: int = 0, method: str = "prismatic",
                 parametric: bool = False) -> dict:
    """
    Convert a mesh body (imported STL) into a real BRep solid body.

    Args:
        mesh_index: Which mesh body (default 0)
        method:
            "prismatic" (default) - merges coplanar facets into genuine planar
                faces. Best for mechanical parts; the result can be filleted,
                cut and dimensioned like normal CAD geometry.
            "faceted" - one triangle becomes one face. Fast and geometrically
                faithful, but the body is heavy and effectively uneditable.
                Fine when you only need booleans or a STEP export.
            "organic" - T-Spline surfaces, for free-form curved shapes.
        parametric: False (default) creates a base feature with no facet limit.
            True keeps a parametric feature in the timeline but is capped at
            roughly 10,000 facets and will fail above that.

    Prefer reduce_mesh first on anything dense. Conversion can take minutes;
    this tool waits up to 10 minutes.

    Returns the indices and names of the new solid bodies, which you can then
    pass to get_body_info, fillet, combine or export_step.
    """
    return send_fusion_command(
        "mesh_to_brep",
        {"mesh_index": mesh_index, "method": method, "parametric": parametric},
        timeout=600)

# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    mcp.run()
