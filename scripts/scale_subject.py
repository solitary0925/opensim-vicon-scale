"""Validated CGM24 axis-specific scaling. Source files are read-only inputs."""
import argparse
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import opensim as osim

TEMPLATE = Path(__file__).resolve().parents[1] / 'assets' / 'axis_scale_template.xml'


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('subject', 'c3d', 'vsk', 'model', 'output'):
        p.add_argument('--' + name, required=True)
    p.add_argument('--geometry-dir')
    p.add_argument('--start', type=float, default=0.5)
    p.add_argument('--end', type=float, default=2.0)
    p.add_argument('--reference-height-m', type=float, default=1.8)
    p.add_argument('--height-mm', type=float, help='Explicit user-confirmed height override; preserve original VSK.')
    p.add_argument('--rotation', nargs=9, type=float, default=[-1, 0, 0, 0, 0, 1, 0, 1, 0])
    p.add_argument('--overwrite', action='store_true')
    return p.parse_args()


def vec(v):
    return [v[i] for i in range(3)]


def marker_defs(path):
    return {x.get('name'): x for x in ET.parse(path).iter('Marker')}


def write_trc(path, times, labels, values, rate):
    with path.open('w', encoding='utf-8') as f:
        f.write(f'PathFileType\t4\t(X/Y/Z)\t{path.name}\n')
        f.write('DataRate\tCameraRate\tNumFrames\tNumMarkers\tUnits\tOrigDataRate\tOrigDataStartFrame\tOrigNumFrames\n')
        f.write(f'{rate}\t{rate}\t{len(times)}\t{len(labels)}\tm\t{rate}\t1\t{len(times)}\n')
        f.write('Frame#\tTime\t' + '\t\t\t'.join(labels) + '\t\t\n\t\t')
        f.write('\t'.join(f'X{i}\tY{i}\tZ{i}' for i in range(1, len(labels)+1)) + '\n\n')
        for i, (time, row) in enumerate(zip(times, values)):
            f.write(f'{i+1}\t{time:.9g}\t' + '\t'.join(f'{v:.10g}' for v in row.ravel()) + '\n')


def export_forces(table, output, rotation):
    if not table.getNumColumns() or not table.getNumRows():
        return None
    labels = list(table.getColumnLabels())
    plates = len(labels)//3
    if labels != [s+str(i) for i in range(1, plates+1) for s in ('f', 'p', 'm')]:
        raise ValueError('Unexpected C3D force labels: ' + str(labels))
    times = np.array(list(table.getIndependentColumn()))
    values = np.empty((len(times), len(labels)*3))
    # Native C3D mm-based point/moment values: mm->m, Nmm->Nm.
    for j, label in enumerate(labels):
        col = table.getDependentColumn(label)
        arr = np.array([vec(col[i]) for i in range(len(times))])
        values[:, j*3:j*3+3] = arr @ rotation.T * (1 if label.startswith('f') else .001)
    if not np.isfinite(values).all():
        raise ValueError('Non-finite plate-origin force data; do not silently replace it.')
    columns = ['time']
    for i in range(1, plates+1):
        columns += [f'ground_force_{i}_v{x}' for x in 'xyz']
        columns += [f'ground_force_{i}_p{x}' for x in 'xyz']
        columns += [f'ground_torque_{i}_{x}' for x in 'xyz']
    with output.open('w', encoding='utf-8') as f:
        f.write(f'Plate-origin forces; N, m, Nm\nversion=1\nnRows={len(times)}\nnColumns={len(columns)}\ninDegrees=no\nendheader\n')
        f.write('\t'.join(columns)+'\n')
        np.savetxt(f, np.column_stack([times, values]), delimiter='\t', fmt='%.10g')
    return {'plates': plates, 'rows': len(times), 'mean_vertical_N': float(values[:, 1::9].sum(axis=1).mean()), 'rate_hz': float(1/np.median(np.diff(times)))}


def configure(args, names, mass, height, output):
    root = ET.parse(TEMPLATE)
    tool = root.getroot().find('ScaleTool')
    tool.set('name', args.subject)
    tool.find('mass').text = str(mass)
    tool.find('height').text = str(height)
    tool.find('age').text = '-1'
    tool.find('notes').text = 'User axis-specific CGM24 scheme; see report for assumptions.'
    tool.find('./GenericModelMaker/model_file').text = str(args.model)
    tool.find('./GenericModelMaker/marker_set_file').text = 'Unassigned'
    for tag in ('ModelScaler', 'MarkerPlacer'):
        node = tool.find(tag)
        node.find('marker_file').text = 'static_markers.trc'
        node.find('time_range').text = f'{args.start} {args.end}'
    sc = tool.find('ModelScaler')
    sc.find('output_model_file').text = 'scaled_only.osim'
    sc.find('output_scale_file').text = 'applied_scales.xml'
    sc.find('./ScaleSet/objects/Scale/scales').text = ' '.join([str(height/1000/args.reference_height_m)]*3)
    # Use the model's own markers; verify all enabled template markers exist.
    used = set()
    for measurement in sc.findall('./MeasurementSet/objects/Measurement'):
        if measurement.findtext('apply', 'true').strip() == 'true':
            for pair in measurement.findall('./MarkerPairSet/objects/MarkerPair'):
                used.update(pair.findtext('markers').split())
    if used - set(names):
        raise ValueError('Missing template measurement markers: '+str(used-set(names)))
    mp = tool.find('MarkerPlacer')
    tasks = mp.find('./IKTaskSet/objects')
    tasks.clear()
    for name in names:
        task = ET.SubElement(tasks, 'IKMarkerTask', name=name)
        ET.SubElement(task, 'apply').text = 'true'
        ET.SubElement(task, 'weight').text = '1'
    for tag, filename in [('output_model_file', f'{args.subject}_scaled.osim'), ('output_marker_file', f'{args.subject}_markerset.xml'), ('output_motion_file', f'{args.subject}_static_pose.mot')]:
        mp.find(tag).text = filename
    ET.indent(root)
    root.write(output/'scale_setup.xml', encoding='utf-8', xml_declaration=True)


def write_report(args, d):
    rows = '\n'.join('| '+n+' | '+' | '.join(f'{x:.5f}' for x in v)+' |' for n, v in d['scales'].items())
    moves = '、'.join(f'{n} {v:.2f} mm' for n, v in d['marker_movements_mm'][:6])
    force = d['force']
    force_text = '未发现测力台数据，未生成测力 MOT。'
    if force:
        force_text = f"两块板" if force['plates']==2 else f"{force['plates']} 块板"
        force_text += f"，{force['rate_hz']:.1f} Hz、{force['rows']} 帧；竖直力合计平均 {force['mean_vertical_N']:.1f} N，为 VSK 体重对应重力的 {100*force['mean_vertical_N']/(d['mass_kg']*9.81):.1f}%。这是数据核查，差异原因未确认，不能单独据此判断模型或校准错误。尚未与独立测力导出逐帧核对。"
    text = f'''# {args.subject} 分轴缩放报告

## 结果与输入

- Scale Tool 已完成，模型可重新加载；总质量 {d['output_mass_kg']:.6f} kg。**静态结果尚未通过人工可视化或动态 IK 验证。**
- C3D：`{args.c3d}`；VSK：`{args.vsk}`；模型：`{args.model}`。
- VSK 体重 {d['mass_kg']:g} kg、身高 {d['height_mm']:g} mm；OpenSim {d['opensim_version']}。
- VSK 原始身高字段：{d.get('vsk_height_raw', d['height_mm'])}；{'用户明确确认后使用 --height-mm 覆盖为 '+str(d['height_mm'])+' mm；原始 VSK 未修改。' if d.get('height_override_mm') is not None else '未覆盖身高。'}
- 采用用户原 S02 配置的分轴测量方案，替代先前各轴等比例方案。标记共 {d['marker_count']} 个，{d['marker_rate_hz']:g} Hz，{d['marker_frames']} 帧；平均区间 {args.start:g}–{args.end:g} s（{d['window_frames']} 帧）。区间内所有模型标记均有有效非零数值，未进行补点。时间窗沿用统一设置，未声称为最优静止窗；区间内各点最大三轴范围合成值为 {d['window_max_range_mm']:.2f} mm。

## 分轴方案及实际系数

骨盆 X 用同侧 ASIS–PSIS 两对距离比例的均值，Z 用左右 ASIS 和左右 PSIS 距离比例的均值。股骨 Y 用同侧 ASIS–内外膝距离比例均值，Z 用内外膝宽；胫骨 Y 用外膝–外踝距离。距骨、跟骨和趾骨使用足跟–足尖比例进行三轴缩放。

**保留原配置：左右胫骨 Z 均使用右侧 RANK–RMED；左侧 LANK–LMED 测量关闭。骨盆 Y、股骨 X、胫骨 X 保持 1。** 这些是用户方案设置，不能自动推广为其他研究的规则。所有静态标记权重为 1。

| 身体分段 | X | Y | Z |
|---|---:|---:|---:|
{rows}

躯干没有实验标记拟合。采用身高 {d['height_mm']/1000:g} / 参考身高 {args.reference_height_m:g} m = {d['height_mm']/1000/args.reference_height_m:.6f} 在 XYZ 等比例缩放。参考值来自 [官方 Gait2392 约 1.80 m 身高说明](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53086215/Gait%2B2392%2Band%2B2354%2BModels)，不是此 CGM24 文件记录的原始个体身高。此前对比发现该 CGM24 的躯干质量、质量中心、惯量及部分关节偏移与官方模型一致，仍保留近似假设。体重用于模型总质量，`preserve_mass_distribution=true` 保留原模型分段质量比例；比例并非实测。

## 拟合与质量核查

- 标记重新放置前的平均静态姿态拟合 RMS **{d['rms_mm']:.2f} mm**，最大 **{d['max_error_mm']:.2f} mm（{d['max_error_marker']}）**。
- [OpenSim IK 经验建议](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim33/pages/53674534) 为 RMS 小于 20 mm，最大通常小于 20–40 mm，依模型和动作而变，并非缩放的硬性验收标准。本次 RMS {'低于' if d['rms_mm']<20 else '高于'} 20 mm；最大误差 {'低于 20 mm' if d['max_error_mm']<20 else '处于 20–40 mm 区间' if d['max_error_mm']<=40 else '高于 40 mm'}。
- 模型局部标记移动最大项：{moves}；详见 `marker_movements.csv`。重新放置会减小贴点差异，不能将移动后的吻合当作正确骨段尺度的证明。
- [官方流程说明](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53090613/Understanding%2Bthe%2BOpenSim%2BPipeline) 建议结合姿态和人体测量评估，不能仅看标记误差。请复核足部、左右尺寸、膝踝宽标记的对应、静态关节姿态与动态 IK。
- 全部身体分段尺度为有限正数；模型质量与输入体重一致，分段相对质量保持误差 {d['mass_fraction_max_abs_error']:.2g}。显示几何缺失项：{d['missing_geometry']}。

## 转换与文件

- `static_markers.trc` 是模型标记筛选后轨迹，原单位 mm 转 m。标记、力、力矩和作用点采用同一正交旋转矩阵：`{d['rotation']}`。默认为模型 (X,Y,Z)=实验 (-X,Z,Y)，根据 S02–S04 左右骨盆及足部方向检查，仍需预览朝向。
- 测力：{force_text} `static_forces_origin.mot` 使用测力台原点处力矩和作用点，单位 N、m、N·m；不可与 COP 点和自由力矩混用。[官方 C3D 文档](https://opensimconfluence.atlassian.net/wiki/spaces/OpenSim/pages/53090837)。
- `{args.subject}_static_pose.mot` 是静态关节坐标，与测力 MOT 不同。`scaled_only.osim` 是标记放置前模型，`{args.subject}_scaled.osim` 为最终输出，配置为 `scale_setup.xml`，实际尺度为 `applied_scales.xml`，可复核日志为 `scale_run.log`。
- `metrics.json` 保存指标和输入 SHA256，`Geometry/` 保存显示几何。源数据未修改。本次为用户授权覆盖既有生成结果的分轴版本。
'''
    (args.output/'缩放报告.md').write_text(text, encoding='utf-8')


def main():
    args = parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]+', args.subject):
        raise ValueError('Use a simple subject ID without path separators.')
    for key in ('c3d', 'vsk', 'model', 'output'):
        setattr(args, key, Path(getattr(args, key)).resolve())
    if args.output in (args.model.parent, args.c3d.parent, args.vsk.parent):
        raise ValueError('Use a separate output folder, not a source-data folder.')
    if args.output.exists() and any(args.output.iterdir()) and not args.overwrite:
        raise ValueError('Nonempty output; rerun with --overwrite only when authorized.')
    if not args.start < args.end or args.reference_height_m <= 0:
        raise ValueError('Invalid window or reference height.')
    rotation = np.array(args.rotation).reshape(3, 3)
    if not np.allclose(rotation@rotation.T, np.eye(3)) or not np.isclose(np.linalg.det(rotation), 1):
        raise ValueError('Rotation must be proper orthogonal (determinant +1).')
    parameters = {e.get('NAME'): e.get('VALUE') for e in ET.parse(args.vsk).iter('StaticParameter')}
    mass, height = float(parameters['Bodymass']), float(parameters['Height'])
    if args.height_mm is not None:
        height = args.height_mm
    if not (0 < mass < 500 and 300 < height < 3000):
        raise ValueError('VSK mass/height invalid for kg/mm convention.')
    names = list(marker_defs(args.model))
    adapter = osim.C3DFileAdapter()
    adapter.setLocationForForceExpression(osim.C3DFileAdapter.ForceLocation_OriginOfForcePlate)
    tables = adapter.read(str(args.c3d))
    markers = adapter.getMarkersTable(tables)
    if markers.getTableMetaDataAsString('Units').strip().lower() != 'mm':
        raise ValueError('Native conversion currently requires C3D marker units mm.')
    times = np.array(list(markers.getIndependentColumn()))
    window = (times >= args.start-1e-9) & (times <= args.end+1e-9)
    if args.start < times[0] or args.end > times[-1] or window.sum() < 2:
        raise ValueError('Requested averaging window is not covered.')
    values = np.empty((len(times), len(names), 3))
    labels = list(markers.getColumnLabels())
    for j, name in enumerate(names):
        if name not in labels:
            raise ValueError('Missing C3D marker ' + name)
        col = markers.getDependentColumn(name)
        values[:, j, :] = np.array([vec(col[i]) for i in range(len(times))]) @ rotation.T * .001
    if not np.isfinite(values).all() or np.any(np.all(values[window]==0, axis=2)):
        raise ValueError('Missing or zero marker samples; inspect data before scaling.')
    args.output.mkdir(parents=True, exist_ok=True)
    # Outputs below are limited to this generated directory.
    os.chdir(args.output)
    force_path = args.output/'static_forces_origin.mot'
    if force_path.exists():
        force_path.unlink()  # prevent stale force output if current C3D has no plates
    force = export_forces(adapter.getForcesTable(tables), force_path, rotation)
    write_trc(args.output/'static_markers.trc', times, names, values, float(markers.getTableMetaDataAsString('DataRate')))
    missing_geometry = []
    geometry = args.output/'Geometry'
    geometry.mkdir(exist_ok=True)
    for name in sorted({e.text.strip() for e in ET.parse(args.model).iter('mesh_file') if e.text}):
        candidates = [args.model.parent/name, args.model.parent/'Geometry'/name]
        if args.geometry_dir:
            candidates.append(Path(args.geometry_dir)/name)
        src = next((p for p in candidates if p.is_file()), None)
        if src:
            target = geometry/Path(name).name
            if src.resolve()!=target.resolve():
                shutil.copy2(src, target)
        else:
            missing_geometry.append(name)
    osim.ModelVisualizer.addDirToGeometrySearchPaths(str(geometry))
    original = osim.Model(str(args.model))
    state = original.initSystem()
    original_mass = original.getTotalMass(state)
    fractions = {original.getBodySet().get(i).getName(): original.getBodySet().get(i).getMass()/original_mass for i in range(original.getBodySet().getSize())}
    configure(args, names, mass, height, args.output)
    log_path = args.output/'scale_run.log'
    osim.Logger.removeFileSink()
    log_path.write_text('', encoding='utf-8')
    osim.Logger.addFileSink(str(log_path))
    try:
        if not osim.ScaleTool(str(args.output/'scale_setup.xml')).run():
            raise RuntimeError('Scale Tool returned failure.')
    finally:
        osim.Logger.removeFileSink()
    log = log_path.read_text(encoding='utf-8')
    fit = re.search(r'RMS\s*=\s*([\deE.+-]+),\s*max\s*=\s*([\deE.+-]+)\s*\(([^)]+)\)', log)
    if not fit:
        raise RuntimeError('Missing pre-relocation fitting metrics in log.')
    final_path = args.output/f'{args.subject}_scaled.osim'
    final = osim.Model(str(final_path))
    final_state = final.initSystem()
    final_mass = final.getTotalMass(final_state)
    if not math.isclose(final_mass, mass, abs_tol=1e-6):
        raise RuntimeError('Output model mass mismatch.')
    scales = {x.findtext('segment'): list(map(float, x.findtext('scales').split())) for x in ET.parse(args.output/'applied_scales.xml').iter('Scale')}
    bodies = {final.getBodySet().get(i).getName() for i in range(final.getBodySet().getSize())}
    if not bodies.issubset(scales) or not all(all(math.isfinite(v) and v>0 for v in scales[b]) for b in bodies):
        raise RuntimeError('Invalid or missing body scaling factors.')
    before, after = marker_defs(args.output/'scaled_only.osim'), marker_defs(final_path)
    movements = sorted([(name, math.dist(list(map(float, before[name].findtext('location').split())), list(map(float, after[name].findtext('location').split())))*1000) for name in names], key=lambda row: row[1], reverse=True)
    with (args.output/'marker_movements.csv').open('w', newline='', encoding='utf-8') as f:
        w = csv.writer(f); w.writerow(['marker', 'local_movement_mm']); w.writerows(movements)
    fraction_error = max(abs(final.getBodySet().get(n).getMass()/final_mass-v) for n, v in fractions.items())
    d = dict(subject=args.subject, mass_kg=mass, height_mm=height, output_mass_kg=final_mass,
             vsk_height_raw=parameters['Height'], height_override_mm=args.height_mm,
             opensim_version=osim.GetVersion(), marker_count=len(names), marker_frames=len(times),
             marker_rate_hz=float(markers.getTableMetaDataAsString('DataRate')), window_frames=int(window.sum()),
             window_max_range_mm=float(np.linalg.norm(np.ptp(values[window], axis=0), axis=1).max()*1000),
             rms_mm=float(fit[1])*1000, max_error_mm=float(fit[2])*1000, max_error_marker=fit[3],
             marker_movements_mm=movements, scales={n:scales[n] for n in scales if n in bodies},
             force=force, rotation=rotation.tolist(), missing_geometry=missing_geometry,
             mass_fraction_max_abs_error=fraction_error,
             source_sha256={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in (args.model,args.c3d,args.vsk,TEMPLATE)})
    if fraction_error > 1e-6:
        raise RuntimeError('Relative body masses were not preserved.')
    (args.output/'metrics.json').write_text(json.dumps(d, ensure_ascii=False, indent=2), encoding='utf-8')
    write_report(args, d)
    print(json.dumps({'subject': args.subject, 'rms_mm': d['rms_mm'], 'max_mm': d['max_error_mm'], 'mass_kg': final_mass, 'output':str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
