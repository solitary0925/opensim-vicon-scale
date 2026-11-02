"""Check all configured inputs first; only start scaling if readiness passes."""
import argparse
from pathlib import Path
import subprocess
import sys
import json
from preflight import load_config, check, save_report


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--check-only',action='store_true');p.add_argument('--report',required=True)
    args=p.parse_args()
    try:
        c=load_config(args.config);r=check(c)
    except Exception as e:
        r=dict(ready=False,status='BLOCKED',errors=1,warnings=0,checks=[dict(level='ERROR',item='configuration',detail=str(e))])
    save_report(r,args.report)
    print(r['status'],flush=True)
    if not r['ready']:sys.exit(2)
    if args.check_only:return
    cmd=[sys.executable,str(Path(__file__).with_name('iterate_scaling.py'))]
    for key in ('subject','model','c3d','vsk','output','geometry_dir','start','end','reference_height_m','height_mm','max_iterations'):
        if c.get(key) is not None:cmd+=['--'+key.replace('_','-'),str(c[key])]
    if c.get('rotation') is not None:cmd+=['--rotation']+[str(v) for v in c['rotation']]
    if c.get('marker_name_map'):
        mapping_path=Path(args.report).resolve().with_suffix('.marker_map.json')
        mapping_path.write_text(json.dumps(c['marker_name_map'],ensure_ascii=False,indent=2),encoding='utf-8')
        cmd+=['--marker-map',str(mapping_path)]
    if c.get('overwrite',False):cmd+=['--overwrite']
    subprocess.run(cmd,check=True)


if __name__=='__main__':main()
