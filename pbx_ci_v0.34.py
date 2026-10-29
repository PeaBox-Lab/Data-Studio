import argparse, json, pickle, sys
from pathlib import Path

def main():
    ap=argparse.ArgumentParser(description='PBX Data Eng v0.34 CI quality gate')
    ap.add_argument('--project'); ap.add_argument('--report',default='ci-report.json'); args=ap.parse_args()
    result={'version':'0.34','status':'PASSED','checks':[]}
    if args.project:
        p=Path(args.project)
        if not p.exists(): result['checks'].append({'status':'FAIL','message':'Project not found'}); result['status']='FAILED'
        else:
            try:
                with p.open('rb') as f: payload=pickle.load(f)
                datasets=payload.get('datasets',{}); result['checks'].append({'status':'PASS' if datasets else 'FAIL','message':f'Datasets available: {len(datasets)}'})
                suites=payload.get('contract_test_suites',{}); result['checks'].append({'status':'PASS' if suites else 'WARN','message':f'Contract suites available: {len(suites)}'})
            except Exception as exc: result['checks'].append({'status':'FAIL','message':str(exc)}); result['status']='FAILED'
    else: result['checks'].append({'status':'PASS','message':'Static CI runner smoke test'})
    if any(x['status']=='FAIL' for x in result['checks']): result['status']='FAILED'
    Path(args.report).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2)); return 1 if result['status']=='FAILED' else 0
if __name__=='__main__': sys.exit(main())
