"""Readiness checks for portable CGM24 scaling; never execute ScaleTool."""
import argparse
from contextlib import contextmanager
import importlib
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from marker_alignment import plan, canonical_name, anatomical_identity, report_text

ROOT = Path(__file__).resolve().parents[1]


def load_config(path):
    path = Path(path).resolve()
    c = json.loads(path.read_text(encoding='utf-8-sig'))
    for key in ('model', 'c3d', 'vsk', 'output', 'geometry_dir'):
        if c.get(key):
            p = Path(c[key]).expanduser()
            c[key] = str((path.parent/p).resolve() if not p.is_absolute() else p.resolve())
    return c


def check(c):
    checks = []
    def add(level, item, detail):
        checks.append(dict(level=level, item=item, detail=str(detail)))
    allowed={'subject','model','c3d','vsk','output','geometry_dir','start','end','reference_height_m','height_mm','height_override_confirmed','rotation','max_iterations','overwrite','marker_name_map'}
    if set(c)-allowed:
        add('ERROR','unknown configuration keys',sorted(set(c)-allowed))
    add('PASS', 'python', sys.version.split()[0]+' | '+sys.executable)
    paths = {}
    for key in ('c3d','vsk','model','output'):
        if not c.get(key):
            add('ERROR',key,'Missing required configuration field.');continue
        paths[key] = Path(c[key])
        if key != 'output':
            add('PASS' if paths[key].is_file() else 'ERROR',key,paths[key])
    if not re.fullmatch(r'[A-Za-z0-9_-]+',str(c.get('subject',''))):
        add('ERROR','subject','Use a subject ID with letters, digits, underscore or hyphen.')
    start,end,ref = c.get('start',.5),c.get('end',2),c.get('reference_height_m',1.8)
    window_ok = all(isinstance(v,(int,float)) and math.isfinite(v) for v in (start,end,ref)) and start<end and ref>0
    add('PASS' if window_ok else 'ERROR','time/reference height',f'{start}–{end} s; reference {ref} m')
    loops=c.get('max_iterations',5)
    if type(loops) is not int or not 1<=loops<=5:
        add('ERROR','max_iterations','Must be an integer from 1 to 5.')
    if type(c.get('overwrite',False)) is not bool:
        add('ERROR','overwrite','Must be a JSON boolean.')
    if c.get('height_mm') is not None and not c.get('height_override_confirmed',False):
        add('ERROR','height override','Set height_override_confirmed=true only after the user confirms the value/unit.')
    if 'output' in paths:
        out=paths['output']
        if any(out==p.parent or out==p for k,p in paths.items() if k!='output'):
            add('ERROR','output isolation','Output must be separate from source files and their parent folders.')
        elif out.exists() and not out.is_dir():
            add('ERROR','output','Output path is a file.')
        else:
            if out.is_dir() and any(out.iterdir()) and not c.get('overwrite',False):
                add('ERROR','existing output','Nonempty output requires explicitly authorized overwrite=true.')
            elif out.is_dir() and any(out.iterdir()):
                add('WARN','existing output','overwrite=true will replace final generated files; iteration batches remain.')
            parent=out
            while not parent.exists():parent=parent.parent
            try:
                with tempfile.TemporaryFile(dir=parent):pass
                add('PASS','output writable','Temporary write probe succeeded and was removed: '+str(parent))
            except OSError as e:add('ERROR','output writable',e)
    height=None
    try:
        if paths.get('vsk',Path()).is_file():
            params={e.get('NAME'):e.get('VALUE') for e in ET.parse(paths['vsk']).iter('StaticParameter')}
            mass=float(params['Bodymass']);height=float(c.get('height_mm') if c.get('height_mm') is not None else params['Height'])
            add('PASS' if math.isfinite(mass) and 0<mass<500 else 'ERROR','VSK mass kg',mass)
            add('PASS' if math.isfinite(height) and 300<height<3000 else 'ERROR','VSK height mm',f"raw={params['Height']}; effective={height}")
            if c.get('height_mm') is not None:add('WARN','height override','Using explicitly confirmed height; original VSK stays unchanged.')
            source_id=re.match(r'S\d+',paths['vsk'].stem,re.I)
            if source_id and source_id[0].upper()!=c.get('subject','').upper():
                add('WARN','VSK subject identity','Filename ID differs from requested subject; verify identity.')
    except Exception as e:add('ERROR','VSK parse',e)
    model=None;names=[];alignment=None;canonical_targets={}
    try:
        if paths.get('model',Path()).is_file():
            model=ET.parse(paths['model']);defs={e.get('name'):e for e in model.iter('Marker')};names=list(defs)
            canonical_defs={anatomical_identity(n,c.get('marker_name_map',{}).get(n)):e for n,e in defs.items()}
            reference=json.loads((ROOT/'assets/model_marker_reference.json').read_text(encoding='utf-8'))
            mismatch=[]
            for n,v in reference.items():
                if n not in canonical_defs:mismatch.append(n+': absent');continue
                e=canonical_defs[n];loc=list(map(float,e.findtext('location').split()))
                if e.findtext('socket_parent_frame')!=v['parent'] or len(loc)!=3 or any(abs(x-y)>1e-8 for x,y in zip(loc,v['location'])):mismatch.append(n+': parent/location changed')
            add('ERROR' if mismatch else 'PASS','CGM24 marker definitions',mismatch or '22 reference definitions match.')
            add('PASS' if model.findtext('.//length_units','').strip()=='meters' else 'ERROR','model units',model.findtext('.//length_units'))
            expected={e.get('name') for e in ET.parse(ROOT/'assets/axis_scale_template.xml').iter('BodyScale')}
            bodies={e.get('name') for e in model.iter('Body')}
            add('ERROR' if expected-bodies else 'PASS','body names',sorted(expected-bodies) or 'All axis-template bodies present.')
            missing=[]
            for name in {e.text.strip() for e in model.iter('mesh_file') if e.text}:
                candidates=[paths['model'].parent/name,paths['model'].parent/'Geometry'/name]
                if c.get('geometry_dir'):candidates.append(Path(c['geometry_dir'])/name)
                if not any(p.is_file() for p in candidates):missing.append(name)
            add('WARN' if missing else 'PASS','display geometry',missing or 'All referenced meshes found.')
            add('WARN','anatomical applicability','Names/marker definitions pass does not certify segment axes, anatomy or the 1.80 m reference for a modified model.')
    except Exception as e:add('ERROR','model XML',e)
    # Some OpenSim builds create a default log on import; confine it to a temporary folder.
    previous=Path.cwd()
    with tempfile.TemporaryDirectory(prefix='opensim_preflight_') as tmp:
        os.chdir(tmp)
        try:
            modules={}
            for n in ('numpy','opensim'):
                try:
                    modules[n]=importlib.import_module(n);add('PASS',n+' import',getattr(modules[n],'__version__','available'))
                except Exception as e:add('ERROR',n+' import',f'{e}; use a Python environment containing the OpenSim Python bindings and NumPy.')
            if len(modules)==2:
                np,o=modules['numpy'],modules['opensim'];o.Logger.removeFileSink()
                add('PASS','OpenSim runtime',o.GetVersion())
                rotation=np.asarray(c.get('rotation',[-1,0,0,0,0,1,0,1,0]),dtype=float)
                valid_rotation=rotation.size==9 and np.isfinite(rotation).all()
                if valid_rotation:
                    rotation=rotation.reshape(3,3);valid_rotation=np.allclose(rotation@rotation.T,np.eye(3)) and np.isclose(np.linalg.det(rotation),1)
                add('PASS' if valid_rotation else 'ERROR','rotation','Proper orthogonal rotation required; lab-to-model direction still needs visual confirmation.')
                if model is not None:
                    try:
                        if c.get('geometry_dir'):o.ModelVisualizer.addDirToGeometrySearchPaths(c['geometry_dir'])
                        m=o.Model(str(paths['model']));s=m.initSystem();total=m.getTotalMass(s)
                        add('PASS' if math.isfinite(total) and total>0 else 'ERROR','model load/init',f'Unscaled mass {total:g} kg')
                    except Exception as e:add('ERROR','model load/init',e)
                if paths.get('c3d',Path()).is_file():
                    try:
                        a=o.C3DFileAdapter();a.setLocationForForceExpression(o.C3DFileAdapter.ForceLocation_OriginOfForcePlate);tables=a.read(str(paths['c3d']));t=a.getMarkersTable(tables)
                        units=t.getTableMetaDataAsString('Units').strip().lower();add('PASS' if units=='mm' else 'ERROR','C3D units',units)
                        ts=np.array(list(t.getIndependentColumn()));labels=list(t.getColumnLabels())
                        alignment=plan(names,labels,c.get('marker_name_map'))
                        add('PASS' if alignment['ready'] else 'ERROR','marker name alignment',f"{alignment['renamed_count']} model names will follow C3D; "+'; '.join(alignment['errors']))
                        if alignment['vicon_without_model']:
                            add('WARN','Vicon points missing from model','如需使用这些候选点，需要手动添加模型点和指定身体归属/坐标：'+', '.join(alignment['vicon_without_model']))
                        canonical_targets={alignment['identities'][n]:target for n,target in alignment['mapping'].items()}
                        names=[alignment['mapping'].get(n,n) for n in names]
                        missing=set(names)-set(labels)
                        add('ERROR' if missing else 'PASS','C3D marker names',sorted(missing) or f'{len(names)} model markers found.')
                        if len(ts)<2 or not np.isfinite(ts).all() or not np.all(np.diff(ts)>0):add('ERROR','C3D time','Time must be finite and strictly increasing.')
                        elif window_ok:
                            w=(ts>=start-1e-9)&(ts<=end+1e-9)
                            add('PASS' if start>=ts[0] and end<=ts[-1] and w.sum()>=2 else 'ERROR','C3D window',f'Available {ts[0]:g}–{ts[-1]:g} s; selected {int(w.sum())} samples.')
                            if names and not missing:
                                arr=np.array([[[t.getDependentColumn(n)[i][k] for k in range(3)] for n in names] for i in range(len(ts))])
                                bad=int((~np.isfinite(arr)).sum());zero=int(np.all(arr[w]==0,axis=2).sum())
                                add('ERROR' if bad or zero else 'PASS','C3D marker samples',f'Nonfinite coordinates across trial: {bad}; zero points in window: {zero}. No automatic gap filling.')
                                if w.any():
                                    spread=float(np.nanmax(np.linalg.norm(np.nanmax(arr[w],axis=0)-np.nanmin(arr[w],axis=0),axis=1)))
                                    add('WARN' if spread>20 else 'PASS','window motion',f'Max marker range-vector norm {spread:.2f} mm; 20 mm is a screening choice, not an official standard.')
                                    if valid_rotation and all(canonical_targets.get(n) in names for n in ('RASI','LASI','LTOE','LHEE')):
                                        mean=np.nanmean(arr[w],axis=0)@rotation.T
                                        lr=mean[names.index(canonical_targets['RASI'])]-mean[names.index(canonical_targets['LASI'])];forward=mean[names.index(canonical_targets['LTOE'])]-mean[names.index(canonical_targets['LHEE'])]
                                        add('PASS' if lr[2]>0 and forward[0]>0 else 'WARN','orientation screen',f'RASI-LASI model Z={lr[2]:.1f} mm; toe-heel model X={forward[0]:.1f} mm. Numerical screen only.')
                        f=a.getForcesTable(tables);labs=list(f.getColumnLabels());plates=len(labs)//3
                        if not labs or not f.getNumRows():add('WARN','forces','No force data; marker-only scaling can proceed and no force MOT is invented.')
                        else:
                            expected=[s+str(i) for i in range(1,plates+1) for s in ('f','p','m')]
                            bad=any(not all(math.isfinite(f.getDependentColumn(n)[i][k]) for k in range(3)) for n in labs for i in range(f.getNumRows()))
                            add('PASS' if labs==expected and not bad else 'ERROR','force conversion readiness',f'{plates} plates, {f.getNumRows()} samples; origin representation. Current converter requires finite values and f/p/m labels.')
                    except Exception as e:add('ERROR','C3D read/conversion',e)
        except Exception as e:add('ERROR','runtime checks',e)
        finally:os.chdir(previous)
    errors=sum(x['level']=='ERROR' for x in checks);warnings=sum(x['level']=='WARN' for x in checks)
    return dict(ready=errors==0,status='BLOCKED' if errors else 'READY_WITH_WARNINGS' if warnings else 'READY',errors=errors,warnings=warnings,checks=checks,marker_alignment=alignment)


def save_report(result, stem):
    stem=Path(stem).resolve();stem.parent.mkdir(parents=True,exist_ok=True)
    stem.with_suffix('.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    lines=['# OpenSim 运行前检查','',f"状态：**{result['status']}**；阻止运行项 {result['errors']}，提醒 {result['warnings']}。",'','检查只验证运行准备情况，不运行缩放，也不证明解剖准确性。输出写权限检查会创建并立即移除临时探针。','','| 等级 | 项目 | 说明 |','|---|---|---|']
    lines += ['| '+r['level']+' | '+r['item']+' | '+r['detail'].replace('|','/').replace('\n',' ')+' |' for r in result['checks']]
    if result.get('marker_alignment'):lines+=['',report_text(result['marker_alignment'])]
    stem.with_suffix('.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',required=True);p.add_argument('--report',required=True,help='Report stem, e.g. reports/preflight')
    args=p.parse_args()
    try:r=check(load_config(args.config))
    except Exception as e:r=dict(ready=False,status='BLOCKED',errors=1,warnings=0,checks=[dict(level='ERROR',item='configuration',detail=str(e))])
    save_report(r,args.report);print(json.dumps({k:v for k,v in r.items() if k!='checks'},ensure_ascii=False));sys.exit(0 if r['ready'] else 2)


if __name__=='__main__':main()
