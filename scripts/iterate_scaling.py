"""Up to five bounded static marker-calibration trials; preserve all candidates."""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np

RUNNER = Path(__file__).with_name('scale_subject.py').resolve()
ADJUSTABLE = {'LFMH', 'LSMH', 'LVMH', 'RFMH', 'RSMH', 'RVMH'}


def markers(tree):
    return {e.get('name'): e for e in tree.iter('Marker')}


def location(e):
    return np.array(list(map(float, e.findtext('location').split())))


def propose(original, current, final, scales):
    """Correct only forefoot points, in unscaled coordinates; geometry stays original."""
    orig, cur, placed = markers(original), markers(current), markers(final)
    changes = []
    for name in sorted(ADJUSTABLE & orig.keys()):
        body = cur[name].findtext('socket_parent_frame').split('/')[-1]
        scale = np.array(scales[body])
        # Automatic MarkerPlacer local displacement expressed back on the original body.
        residual = location(placed[name]) / scale - location(cur[name])
        step = 0.5 * residual
        size = np.linalg.norm(step * scale)
        if size > .005:
            step *= .005 / size  # max 5 mm per trial in scaled-body coordinates
        proposed = location(cur[name]) + step
        total = proposed - location(orig[name])
        size = np.linalg.norm(total * scale)
        if size > .015:
            proposed = location(orig[name]) + total * .015 / size
        movement = float(np.linalg.norm((proposed-location(cur[name]))*scale)*1000)
        if movement < .01:
            continue
        changes.append(dict(marker=name, body=body, from_unscaled_m=location(cur[name]).tolist(),
                            to_unscaled_m=proposed.tolist(), step_scaled_mm=movement,
                            total_scaled_mm=float(np.linalg.norm((proposed-location(orig[name]))*scale)*1000)))
    # Preserve the original local medial/lateral order of all three metatarsal markers.
    candidate = {n:location(e) for n,e in cur.items()}
    candidate.update({c['marker']:np.array(c['to_unscaled_m']) for c in changes})
    for side in ('L', 'R'):
        sequence = [side+s for s in ('FMH','SMH','VMH')]
        if all(n in orig for n in sequence):
            for a,b in zip(sequence,sequence[1:]):
                if (location(orig[b])[2]-location(orig[a])[2])*(candidate[b][2]-candidate[a][2]) <= 0:
                    raise ValueError('Correction would reverse metatarsal medial/lateral order.')
    for change in changes:
        cur[change['marker']].find('location').text = ' '.join(f'{x:.12g}' for x in change['to_unscaled_m'])
    return changes


def summary(session, records, selected, args):
    rows=[]
    baseline=records[0]['metrics'] if records and 'metrics' in records[0] else None
    for r in records:
        if 'metrics' not in r:
            rows.append(f"| {r['iteration']} | 失败 | — | — | — | 否 | {r.get('error','')} |")
            continue
        d=r['metrics']
        rows.append(f"| {r['iteration']} | {d['rms_mm']:.4f} | {d['max_error_mm']:.4f} | {d['max_error_marker']} | {r['score']:.6f} | {'是' if r['eligible'] else '否'} | {'最终选择' if selected==r['iteration'] else '保留'} |")
    text=f'''# {args.subject} 静态检查与有限校正记录

最多 {args.max_iterations} 次（包括第 1 次基线），本批保留 {len(records)} 次尝试。最终选择：第 {selected if selected else '无'} 次。

| 次数 | RMS mm | Max mm | 最大误差点 | 综合分数 | 可选 | 结果 |
|---|---:|---:|---|---:|---|---|
'''+ '\n'.join(rows)+'''

## 校正与选取规则

- 每次从原始骨架几何和质量开始，更新候选模型的局部标记坐标后重新计算缩放。没有把上一轮缩放系数重复作用于已缩放骨架。
- 固定所有用于分轴测量的骨盆、膝、踝、足跟、足尖点。仅允许 L/R FMH、SMH、VMH 六个未参与尺度测量的跖骨头点调整；它们仍是解剖标记，自动调整不等于确认贴点正确。
- 根据前一轮 MarkerPlacer 的局部残差，用 50% 步长提议下一轮坐标；单轮最多 5 mm，累计相对原始模型最多 15 mm（按缩放后身体局部坐标计），不反转原来的内外侧顺序。所有点位置、步长记录在各轮 correction.json。上述幅度是本流程的保守工程设置，不是 OpenSim 官方标准。
- 同一静态数据、时间窗、全部标记及权重保持不变。比较标记重新放置前的 RMS 和 Max。候选须质量和输出检查通过，且 RMS、Max 都不高于基线；综合分数 (RMS/20)^2 + (Max/40)^2 越小越优，并列时取较早一轮。20/40 仅用于平衡两个指标，不是官方验收分数。
- 无可移动增量则提前停止；求解失败的轮次保留日志，停止继续修改并从成功候选中选择。第一轮失败则不覆盖最终输出。每轮模型、配置、报告和日志全部保留。

## 解释边界

这是使用同一静态数据进行有限模型标记校准；随着模型点位置变化，指标下降可能反映对该静态数据的拟合，并非独立证明解剖位置或骨架更准确。本策略刻意固定缩放测量点，因此各轮骨架缩放系数应保持一致。它不等同于有照片/实测定位支持的人工解剖校正。需要检查候选点位、静态姿态并通过动态 IK 验证。不能直接选择标记移动后几乎为零的误差。

[OpenSim 建议结合静态姿态及人体测量检查缩放，不应只依赖标记误差](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53090613/Understanding%2Bthe%2BOpenSim%2BPipeline)。
'''
    (session/'迭代记录.md').write_text(text,encoding='utf-8')
    (session/'iterations.json').write_text(json.dumps(dict(selected_iteration=selected, trials=records),ensure_ascii=False,indent=2),encoding='utf-8')
    return text


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for key in ('subject','model','output'):
        p.add_argument('--'+key,required=True)
    p.add_argument('--max-iterations',type=int,default=5,choices=range(1,6))
    p.add_argument('--overwrite',action='store_true')
    args,remaining=p.parse_known_args()
    model=Path(args.model).resolve();out=Path(args.output).resolve()
    if not model.is_file() or model.parent==out:
        raise ValueError('Require an original source model and separate output folder.')
    if out.exists() and any(out.iterdir()) and not args.overwrite:
        raise ValueError('Nonempty final output requires authorized --overwrite.')
    session=out/'iterations'/datetime.now().strftime('%Y%m%d_%H%M%S_%f')
    session.mkdir(parents=True)
    (session/'input_models').mkdir()
    original=ET.parse(model);current=ET.parse(model)
    records=[];changes=[];best=None
    for i in range(1,args.max_iterations+1):
        trial=session/f'iteration_{i:02d}';trial.mkdir()
        candidate=session/'input_models'/f'iteration_{i:02d}.osim'
        current.write(candidate,encoding='utf-8',xml_declaration=True)
        shutil.copy2(candidate,trial/'input_model.osim')
        (trial/'correction.json').write_text(json.dumps(dict(source_model=str(model),source_sha256=hashlib.sha256(model.read_bytes()).hexdigest(),changes=changes),ensure_ascii=False,indent=2),encoding='utf-8')
        cmd=[sys.executable,str(RUNNER),'--subject',args.subject,'--model',str(candidate),'--output',str(trial),'--overwrite']+remaining
        with (trial/'run.log').open('w',encoding='utf-8') as log:
            process=subprocess.run(cmd,stdout=log,stderr=subprocess.STDOUT)
        r=dict(iteration=i,folder=str(trial))
        if process.returncode:
            r['error']='Scale runner failed; see run.log';records.append(r);summary(session,records,best['iteration'] if best else None,args);break
        d=json.loads((trial/'metrics.json').read_text(encoding='utf-8'))
        if records and any(not np.allclose(d['scales'][body],records[0]['metrics']['scales'][body],atol=1e-6) for body in d['scales']):
            r['error']='Unexpected geometry scale change; candidate rejected';records.append(r);summary(session,records,best['iteration'] if best else None,args);break
        baseline=records[0]['metrics'] if records else d
        r.update(metrics=d,score=(d['rms_mm']/20)**2+(d['max_error_mm']/40)**2,
                 eligible=d['rms_mm']<=baseline['rms_mm']+1e-6 and d['max_error_mm']<=baseline['max_error_mm']+1e-6)
        records.append(r)
        if r['eligible'] and (best is None or r['score']<best['score']-1e-10):
            best=r
        summary(session,records,best['iteration'],args)
        print(f"{args.subject} trial {i}: RMS={d['rms_mm']:.3f} Max={d['max_error_mm']:.3f}",flush=True)
        if i==args.max_iterations:
            break
        changes=propose(original,current,ET.parse(trial/f'{args.subject}_scaled.osim'),d['scales'])
        if not changes:
            break
    if best is None:
        raise RuntimeError('No valid trial; existing final results have not been replaced.')
    report=summary(session,records,best['iteration'],args)
    selected=Path(best['folder'])
    shutil.copy2(session/'input_models'/f'iteration_{best["iteration"]:02d}.osim',out/'selected_input_model.osim')
    if best['metrics']['force'] is None and (out/'static_forces_origin.mot').exists():
        (out/'static_forces_origin.mot').unlink()
    # Only known runner outputs are promoted; all iteration directories remain intact.
    for item in selected.iterdir():
        if item.name in ('input_model.osim','correction.json'):
            continue
        target=out/item.name
        if item.is_dir():
            shutil.copytree(item,target,dirs_exist_ok=True)
        else:
            shutil.copy2(item,target)
    d=best['metrics'].copy()
    d['iteration_selection']=dict(iteration=best['iteration'],session=str(session),count=len(records),original_model=str(model))
    (out/'metrics.json').write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
    (out/'迭代记录.md').write_text(report+'\n本批详细记录目录：`'+str(session)+'`\n',encoding='utf-8')
    with (out/'缩放报告.md').open('a',encoding='utf-8') as f:
        f.write(f'\n## 检查与校正循环\n\n本批运行 {len(records)} 次，选择第 {best["iteration"]} 次。比较表和限制见同目录 `迭代记录.md`。六个跖骨头标记允许有限位置校准，缩放测量点和骨架尺度固定，误差降低不代表独立解剖验证。\n')
    shutil.copy2(out/'scale_run.log',out/'opensim.log')
    print(json.dumps(dict(subject=args.subject,selected=best['iteration'],trials=len(records),rms_mm=d['rms_mm'],max_mm=d['max_error_mm'],session=str(session)),ensure_ascii=False))


if __name__=='__main__':
    main()
