"""Legacy review projections and one bounded write: an atomic human constraints copy."""
import copy
import hashlib
import json
import os
import re
import tempfile
import threading
from pathlib import Path
from .. import paths

_LOCK = threading.Lock()
ARTIFACTS = {'constraints', 'constraint_check', 'cases', 'execution_result', 'quality_gate', 'generation_summary', 'generate_result', 'analysis'}

class Conflict(ValueError):
    pass

def read(path):
    return json.loads(path.read_text(encoding='utf-8'))

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None

def run_file(root, run_id, rel):
    return paths.resolve_within(paths.resolve_run(root, run_id), rel)


def _cov_rate(metrics, *names):
    """从 metrics 列表按名称取 rate（函数/行·语句/分支），取不到返回 0。"""
    if not isinstance(metrics, list):
        return 0.0
    by_name = {}
    for m in metrics:
        if isinstance(m, dict) and m.get('name'):
            by_name[m['name']] = m.get('rate', 0)
    for n in names:
        if n in by_name:
            return by_name[n]
    return 0.0


def _op_summary(odir, data):
    """从单份 coverage.json 提取算子摘要（op 模块 函数/行/分支覆盖率）。"""
    g = data.get('granularities') or {}
    gr = g.get('func') or g.get('file') or {}
    cov = gr.get('coverage') or {}
    op = cov.get('op') or {}
    metrics = op.get('metrics') or []
    oi = data.get('operatorInfo') or {}
    return {
        'dir_name': odir.name,
        # 新命名约定以算子名作为目录名（如 aclnnLogSumExp），优先用目录名；JSON 的 operator 字段可能滞后
        'operator': odir.name,
        'domain': oi.get('domain', ''),
        'has_analysis': bool(list(odir.glob('*.md'))),
        'func': _cov_rate(metrics, '函数覆盖率'),
        'line': _cov_rate(metrics, '行覆盖率', '语句覆盖率'),
        'branch': _cov_rate(metrics, '分支覆盖率'),
    }


def _coverage_tree(base):
    """四级目录树：base/<版本>/<算子库>/<算子目录>/<算子名称>/{*.json 覆盖率, *.md 分析}。

    算子库层只携带其下算子的聚合覆盖率（无 md）；算子名称层才带 coverage + analysis。
    """
    versions = []
    if not base.is_dir():
        return {'versions': versions}
    for vdir in sorted(base.glob('*')):
        if not vdir.is_dir() or vdir.is_symlink():
            continue
        libs = []
        for ldir in sorted(vdir.glob('*')):
            if not ldir.is_dir() or ldir.is_symlink():
                continue
            dirs = []
            for ddir in sorted(ldir.glob('*')):
                if not ddir.is_dir() or ddir.is_symlink():
                    continue
                ops = []
                for odir in sorted(ddir.glob('*')):
                    if not odir.is_dir() or odir.is_symlink():
                        continue
                    files = sorted(odir.glob('*.json'))
                    if not files:
                        continue
                    try:
                        rel = str(odir.relative_to(base)) + '/' + files[0].name
                        data = read(paths.resolve_within(base, rel))
                    except (ValueError, OSError):
                        continue
                    ops.append(_op_summary(odir, data))
                if ops:
                    dirs.append({'name': ddir.name, 'ops': ops})
            if dirs:
                libs.append({'name': ldir.name, 'dirs': dirs})
        if libs:
            versions.append({'version': vdir.name, 'libraries': libs})
    return {'versions': versions}


def _coverage_detail(base, rel_parts, kind):
    """按四级相对路径读取单算子报告。rel_parts=(版本, 算子库, 算子目录, 算子名称)；kind=coverage|analysis。"""
    rel = '/'.join(rel_parts)
    opdir = paths.resolve_within(base, rel)
    if kind == 'coverage':
        files = sorted(opdir.glob('*.json'))
        if not files:
            raise FileNotFoundError('没有覆盖报告')
        return read(paths.resolve_within(base, rel + '/' + files[0].name))
    md_files = sorted(opdir.glob('*.md'))
    if not md_files:
        raise FileNotFoundError('没有分析文档')
    doc = paths.resolve_within(base, rel + '/' + md_files[0].name)
    return {'filename': doc.name, 'content': doc.read_text(encoding='utf-8')}


def get(root, parts, query, token):
    if parts == ['runs']:
        result = []
        for entry in sorted((root / 'runs').glob('*/run_state.json')):
            try:
                state = read(run_file(root, entry.parent.name, 'run_state.json'))
                result.append(dict(state, dir_name=entry.parent.name, operator=Path(state.get('operator_doc', '')).stem))
            except (ValueError, OSError):
                continue
        return result
    if parts[:1] == ['runs'] and len(parts) >= 2:
        run_id = parts[1]
        run = paths.resolve_run(root, run_id)
        if len(parts) == 2:
            return read(run_file(root, run_id, 'run_state.json'))
        if parts[2:] == ['iters']:
            return {'dirs': [p.name for _, p in paths.iter_dirs(run)]}
        if parts[2:] == ['operator_doc']:
            state = read(run_file(root, run_id, 'run_state.json'))
            source = query.get('source', ['doc'])[0]
            if source == 'doc':
                name = Path(state.get('operator_doc', '').replace('\\', '/')).name
                rel = 'inputs/' + name
            elif source == 'supplement':
                choices = ['inputs/supplement_constraints.md', 'inputs/supplementary-doc.md']
                existing = [p for p in choices if (run / p).is_file()]
                if len(existing) != 1:
                    raise ValueError('补充来源无法唯一定位，请查看对应补充证据文件')
                rel = existing[0]
            else:
                raise ValueError('该来源没有可唯一定位的文档快照')
            doc = run_file(root, run_id, rel)
            return {'filename': doc.name, 'content': doc.read_text(encoding='utf-8')}
        if len(parts) == 4 and re.fullmatch(r'iter_\d+', parts[2]) and parts[3] in ARTIFACTS:
            path = run_file(root, run_id, parts[2] + '/' + parts[3] + '.json')
            raw_bytes = path.read_bytes()
            data = json.loads(raw_bytes)
            if parts[3] == 'constraints':
                copy_path = path.parent / 'constraints_copy.json'
                if copy_path.is_symlink():
                    raise paths.PathEscapeError('拒绝符号链接修改副本')
                data['_review'] = {'base_sha256': hashlib.sha256(raw_bytes).hexdigest(), 'copy_sha256': digest(copy_path), 'token': token}
            return data
        # 任务内嵌覆盖率：runs/<run>/ops_cov_report/<版本>/<算子库>/<算子目录>/<算子名称>/（同全局四级结构）
        if parts[2] == 'cover':
            base = run / 'ops_cov_report'
            if parts[3:] == ['tree']:
                return _coverage_tree(base)
            if len(parts) == 8 and parts[7] in ('coverage', 'analysis'):
                return _coverage_detail(base, parts[3:7], parts[7])
    if parts and parts[0] == 'cover':
        base = root / 'ops_final_cov_report'
        if parts == ['cover', 'tree']:
            return _coverage_tree(base)
        if len(parts) == 6 and parts[5] in ('coverage', 'analysis'):
            return _coverage_detail(base, parts[1:5], parts[5])
    raise FileNotFoundError('未知审核接口')

def save(root, run_id, iteration, request):
    if not isinstance(request, dict):
        raise ValueError('请求必须是对象')
    with _LOCK:
        source = run_file(root, run_id, iteration + '/constraints.json')
        target = source.parent / 'constraints_copy.json'
        if target.is_symlink():
            raise paths.PathEscapeError('拒绝符号链接修改副本')
        if request.get('base_sha256') != digest(source) or request.get('copy_sha256') != digest(target):
            raise Conflict('约束或修改副本已变化，请重新加载后再编辑')
        original = read(source)
        payload = request.get('constraints')
        cnp = payload.get('constraints_in_parameters') if isinstance(payload, dict) else None
        if not isinstance(cnp, (dict, list)) or not cnp:
            raise ValueError('参数间约束必须是非空对象或数组')
        old = original.get('constraints_in_parameters')
        if isinstance(old, dict) and (not isinstance(cnp, dict) or set(old) != set(cnp)):
            raise ValueError('编辑不能删除或新增产品分组')
        if isinstance(old, list) and not isinstance(cnp, list):
            raise ValueError('约束分组结构不能改变')
        cnp = copy.deepcopy(cnp)
        groups = list(cnp.values()) if isinstance(cnp, dict) else [cnp]
        ids = set()
        # Validate structure only; semantic verification belongs to the existing business workflow.
        for group in groups:
            if not isinstance(group, list):
                raise ValueError('产品约束必须是数组')
            for item in group:
                if not isinstance(item, dict) or not isinstance(item.get('expr'), str) or not item['expr'].strip():
                    raise ValueError('每条约束必须提供非空表达式')
                if not isinstance(item.get('expr_type'), str) or not item['expr_type']:
                    raise ValueError('每条约束必须提供类型')
                if not isinstance(item.get('relation_params'), list) or not all(isinstance(x, str) for x in item['relation_params']):
                    raise ValueError('关联参数必须是字符串数组')
                if not isinstance(item.get('src_text'), str):
                    raise ValueError('每条约束必须提供来源说明')
                if 'src_txt_line' in item and (not isinstance(item['src_txt_line'], list) or not all(type(n) is int and n > 0 for n in item['src_txt_line'])):
                    raise ValueError('原文行号必须是正整数数组')
                item.pop('status', None)
                ident = item.get('id')
                if ident:
                    if not isinstance(ident, str) or ident in ids:
                        raise ValueError('约束编号重复或不合法')
                    ids.add(ident)
        previous_groups = list(old.values()) if isinstance(old, dict) else [old or []]
        reserved = ids | {item.get('id') for group in previous_groups for item in group if isinstance(item, dict) and isinstance(item.get('id'), str)}
        index = 1
        for group in groups:
            for item in group:
                if not item.get('id'):
                    while 'C-%03d' % index in reserved:
                        index += 1
                    item['id'] = 'C-%03d' % index
                    reserved.add(item['id'])
        if cnp == old:
            raise ValueError('约束内容未变化')
        merged = dict(original, constraints_in_parameters=cnp)
        merged.pop('_review', None)
        content = (json.dumps(merged, ensure_ascii=False, indent=2) + '\n').encode()
        fd, name = tempfile.mkstemp(prefix='.constraints-copy-', dir=source.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            if request.get('base_sha256') != digest(source) or request.get('copy_sha256') != digest(target):
                raise Conflict('保存期间约束或副本已变化，请重新加载')
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        return {'ok': True, 'path': iteration + '/constraints_copy.json', 'sha256': hashlib.sha256(content).hexdigest(), 'execution_started': False, 'validation': 'structure_only'}
