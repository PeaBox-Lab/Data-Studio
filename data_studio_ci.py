from __future__ import annotations
import argparse, ast, json, py_compile, subprocess, sys
from pathlib import Path

def check(condition, name, details=""):
    return {"status":"PASS" if condition else "FAIL","check":name,"details":details}

def main():
    ap=argparse.ArgumentParser(description="Data Studio quality gate")
    ap.add_argument("--project",default="."); ap.add_argument("--output",default="ci_smoke.json")
    args=ap.parse_args(); root=Path(args.project).resolve(); results=[]
    required=["legacy/data_studio_v0.38.py","data_studio_core.py","data_studio_state.py","data_studio_diagnostics.py","data_studio_jobs.py","data_studio_tasks.py","data_studio_headless.py","requirements.txt","README.md","USER_GUIDE.md","data_studio/ui/app.py"]
    missing=[x for x in required if not (root/x).exists()]; results.append(check(not missing,"required release files",", ".join(missing)))
    for name in ["legacy/data_studio_v0.38.py","data_studio_core.py","data_studio_state.py","data_studio_diagnostics.py","data_studio_jobs.py","data_studio_tasks.py","data_studio_headless.py","data_studio/ui/app.py"]:
        try: py_compile.compile(str(root/name),doraise=True); results.append(check(True,f"compile {name}"))
        except Exception as exc: results.append(check(False,f"compile {name}",str(exc)))
    try:
        tree=ast.parse((root/"legacy/data_studio_v0.38.py").read_text(encoding="utf-8")); cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=="Workbench"); methods={x.name for x in cls.body if isinstance(x,ast.FunctionDef)}
        required_methods={"show_data_quality_profile","show_validation_rules","show_data_contracts","show_contract_test_automation","show_cicd_integration","show_release_gate","show_environment_promotion","show_deployment_observability","show_rollback_center","execute_rollback","show_diagnostics_center"}; absent=sorted(required_methods-methods); results.append(check(not absent,"governance and rollback actions",", ".join(absent)))
    except Exception as exc: results.append(check(False,"governance and rollback actions",str(exc)))
    try:
        proc=subprocess.run([sys.executable,"-m","unittest","discover","-s","tests","-p","test_*.py"],cwd=root,text=True,capture_output=True,timeout=120)
        results.append(check(proc.returncode==0,"core unit tests",(proc.stdout+proc.stderr)[-2000:]))
    except Exception as exc: results.append(check(False,"core unit tests",str(exc)))
    status="PASSED" if all(x["status"]=="PASS" for x in results) else "FAILED"; payload={"product":"Data Studio","status":status,"checks":results}; (root/args.output).write_text(json.dumps(payload,indent=2),encoding="utf-8"); print(json.dumps(payload,indent=2)); return 0 if status=="PASSED" else 1
if __name__=="__main__": raise SystemExit(main())
