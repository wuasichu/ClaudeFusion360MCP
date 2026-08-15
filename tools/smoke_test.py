#!/usr/bin/env python3
"""Smoke test for the Fusion MCP.

Run after every Fusion update. Two of the capabilities here rest on
undocumented behaviour that Autodesk can withdraw without notice, and both
would fail SILENTLY rather than loudly:

  - executeTextCommand fallbacks (ParaMeshConvertCommand, ParaMeshReduceCommand)
  - the TSM format used to create T-Spline bodies

Usage:
    Open Fusion with the FusionMCP add-in running, on an EMPTY parametric
    design, then:

        python tools/smoke_test.py

Talks to the add-in through the comm directory directly, so it does not need
the MCP server or any client to be running.
"""
import json
import math
import os
import sys
import time

COMM = os.path.join(os.path.expanduser("~"), "fusion_mcp_comm")


def send(name, params=None, timeout=300):
    ts = int(time.time() * 1000)
    cmd = os.path.join(COMM, "command_%d.json" % ts)
    rsp = os.path.join(COMM, "response_%d.json" % ts)
    with open(cmd, "w") as f:
        json.dump({"type": "tool", "name": name,
                   "params": params or {}, "id": ts}, f)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if os.path.exists(rsp):
            time.sleep(0.3)                 # let the writer finish
            try:
                with open(rsp) as f:
                    return json.load(f)
            except ValueError:
                continue
        time.sleep(0.1)
    return {"success": False, "error": "timeout after %ds" % timeout}


RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print("  %-28s %s%s" % (name, "PASS" if ok else "FAIL",
                            "  " + detail if detail else ""))
    return ok


# --------------------------------------------------------------------------
def test_alive():
    r = send("get_design_info", timeout=30)
    return check("add-in responds", r.get("success"),
                 r.get("error", "")[:60])


def test_mesh_fields():
    r = send("get_design_info", timeout=30)
    return check("get_design_info sees meshes", "mesh_body_count" in r)


def test_main_thread_bridge():
    """execute_script defaults to the main thread; if the custom-event bridge
    is broken this times out rather than returning."""
    r = send("execute_script", {"code": "result = 6*7"}, timeout=60)
    return check("main-thread bridge", r.get("result") == 42,
                 r.get("error", "")[:60])


def test_script_error_reporting():
    r = send("execute_script", {"code": "1/0"}, timeout=60)
    ok = (not r.get("success")) and "ZeroDivisionError" in str(r.get("error"))
    return check("script errors reported", ok)


def test_tspline_roundtrip():
    """The one that matters: generate TSM, push it, read it back."""
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
        from tsm_gen import build_tsm, torus
    except ImportError:
        return check("T-Spline round trip", False, "tsm_gen.py not importable")

    tsm, stats = build_tsm(*torus(R=10, r=3, nu=12, nv=8))
    if stats["euler"] != 0:
        return check("T-Spline round trip", False, "bad cage euler")

    path = os.path.join(COMM, "_smoke.tsm")
    with open(path, "w") as f:
        f.write(tsm)

    before = send("list_tspline_bodies", timeout=60).get("count", 0)
    r = send("create_tspline", {"filepath": path, "name": "_smoke"}, timeout=300)
    after = send("list_tspline_bodies", timeout=60).get("count", 0)
    ok = r.get("success") and after == before + 1
    check("T-Spline creation", ok, str(r.get("error", ""))[:70])
    if not ok:
        return False

    e = send("export_tspline_tsm", {"index": after - 1}, timeout=120)
    return check("T-Spline export", bool(e.get("tsm_length")))


def test_text_command_fallback():
    """Exercises the undocumented ParaMesh* path indirectly: if Fusion renamed
    or removed those commands, mesh_to_brep can no longer fall back."""
    r = send("mesh_to_brep", {"mesh_index": 0}, timeout=60)
    # No mesh in an empty document is the EXPECTED answer here. What we are
    # checking is that the handler reports cleanly instead of blowing up.
    ok = (not r.get("success")) and "mesh" in str(r.get("error", "")).lower()
    return check("mesh_to_brep guards", ok, str(r.get("error", ""))[:60])


def main():
    if not os.path.isdir(COMM):
        sys.exit("No comm directory at %s - is the add-in installed?" % COMM)

    print("Fusion MCP smoke test")
    print("Comm dir:", COMM)
    print()

    if not test_alive():
        sys.exit("\nAdd-in is not responding. Start Fusion and run the "
                 "FusionMCP add-in, then retry.")

    test_mesh_fields()
    test_main_thread_bridge()
    test_script_error_reporting()
    test_text_command_fallback()
    test_tspline_roundtrip()

    failed = [n for n, ok, _ in RESULTS if not ok]
    print()
    print("%d/%d passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED:", ", ".join(failed))
        print("See docs/KNOWN_ISSUES.md #21 - the undocumented paths are the "
              "usual suspects after a Fusion update.")
        sys.exit(1)
    print("All good. Leftover test bodies are in the design - delete them.")


if __name__ == "__main__":
    main()
