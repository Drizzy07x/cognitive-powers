#!/usr/bin/env python3
"""Observe the completed fixture from a fresh real browser and fail closed."""
from __future__ import annotations
import argparse, hashlib, json, os, subprocess
from pathlib import Path

EXPECTED = ["search:QCU-217", "select:QCU-217", "save", "ready"]
def sha256(path: Path) -> str: return hashlib.sha256(path.read_bytes()).hexdigest()
def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument("--fixture",type=Path,required=True)
    parser.add_argument("--node-path",required=True); args=parser.parse_args(); root=args.fixture.resolve()
    env=dict(os.environ); env["NODE_PATH"]=args.node_path
    completed=subprocess.run(["node","host_driver.js","observe"],cwd=root,env=env,text=True,capture_output=True,timeout=30)
    if completed.returncode: raise SystemExit(completed.stderr or completed.stdout)
    state=json.loads((root/"state.json").read_text(encoding="utf-8"))
    action=json.loads((root/"host-receipt.json").read_text(encoding="utf-8"))
    observer=json.loads((root/"observer-receipt.json").read_text(encoding="utf-8"))
    assert state == {"ticket":"QCU-217","selected":True,"assignee":"Maya Chen","priority":"High",
                     "note":"Validated in staging","status":"Ready","events":EXPECTED}
    assert action["host"] == "chromium-public-surface" and action["visible"]["status"] == "Ready"
    assert action["screenshotSha256"] == sha256(root/"final.png")
    assert observer["host"] == "fresh-chromium-public-surface" and observer["visible"]["status"] == "Ready"
    assert observer["events"] == EXPECTED and observer["actionReceiptSha256"] == sha256(root/"host-receipt.json")
    print(json.dumps({"passed":True,"observerSha256":sha256(root/"observer-receipt.json")})); return 0
if __name__ == "__main__": raise SystemExit(main())
