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


def _cov_pair(metrics, *names):
    """从 metrics 列表按名称取 (covered, total)，取不到返回 (0, 0)。"""
    if not isinstance(metrics, list):
        return 0, 0
    by_name = {}
    for m in metrics:
        if isinstance(m, dict) and m.get('name'):
            by_name[m['name']] = (m.get('covered', 0), m.get('total', 0))
    for n in names:
        if n in by_name:
            return by_name[n]
    return 0, 0


def _op_summary(odir, data, name=None, dir_name=None):
    """从单份 coverage.json 提取算子摘要（op 模块 函数/行/分支覆盖率 + 覆盖数/总数）。

    name/dir_name 用于平坦结构（任务内嵌）时自定义算子名与目录名；默认取目录名。
    """
    g = data.get('granularities') or {}
    gr = g.get('func') or g.get('file') or {}
    cov = gr.get('coverage') or {}
    op = cov.get('op') or {}
    metrics = op.get('metrics') or []
    oi = data.get('operatorInfo') or {}
    func_c, func_t = _cov_pair(metrics, '函数覆盖率')
    line_c, line_t = _cov_pair(metrics, '行覆盖率', '语句覆盖率')
    branch_c, branch_t = _cov_pair(metrics, '分支覆盖率')
    return {
        'dir_name': dir_name or odir.name,
        'operator': name or odir.name,
        'domain': oi.get('domain', ''),
        'has_analysis': bool(list(odir.glob('*.md'))),
        'count': op.get('count', 0) or func_t,
        'func': _cov_rate(metrics, '函数覆盖率'),
        'func_covered': func_c,
        'func_total': func_t,
        'line': _cov_rate(metrics, '行覆盖率', '语句覆盖率'),
        'line_covered': line_c,
        'line_total': line_t,
        'branch': _cov_rate(metrics, '分支覆盖率'),
        'branch_covered': branch_c,
        'branch_total': branch_t,
    }


def _is_flat(base):
    """平坦结构：覆盖率 json 直接在 base 下（任务内嵌 runs/<run>/ops_cov_report/）。"""
    return base.is_dir() and bool(list(base.glob('*.json')))


def _latest_json(d):
    """取目录下最新一份覆盖率 json。

    优先取文件名含时间戳（_YYYYMMDD_HHMMSS）且最新的一份；
    无时间戳时回退到按修改时间最新的任意 *.json。
    """
    import re as _re
    ts_re = _re.compile(r'_(\d{8})_(\d{6})\.json$')
    ts_files = []
    for f in d.glob('*.json'):
        m = ts_re.search(f.name)
        if m:
            ts_files.append((m.group(1) + m.group(2), f))
    if ts_files:
        ts_files.sort(key=lambda x: x[0])
        return ts_files[-1][1]
    files = list(d.glob('*.json'))
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def _node_summary(data):
    """从任一 coverage json 归一化出摘要。

    兼容三种形态：
    - 汇总层（有 level 字段）：summary.<gran>.metrics[]
    - 目录级合并 / 叶子（有 operator + granularities）：granularities.func.coverage.<gran>.metrics[]
    统一取 op 粒度（叶子）或 host 粒度（汇总）的 函数/行/分支 covered/total/rate。
    """
    def from_metrics(metrics):
        fc, ft = _cov_pair(metrics, '函数覆盖率')
        lc, lt = _cov_pair(metrics, '行覆盖率', '语句覆盖率')
        bc, bt = _cov_pair(metrics, '分支覆盖率')
        return {
            'func': _cov_rate(metrics, '函数覆盖率'), 'func_covered': fc, 'func_total': ft,
            'line': _cov_rate(metrics, '行覆盖率', '语句覆盖率'), 'line_covered': lc, 'line_total': lt,
            'branch': _cov_rate(metrics, '分支覆盖率'), 'branch_covered': bc, 'branch_total': bt,
        }

    s = data.get('summary')
    if isinstance(s, dict) and s:
        # 汇总层：优先 op 粒度，回退 host
        gran = s.get('op') or s.get('host') or {}
        return from_metrics(gran.get('metrics') or [])

    g = data.get('granularities') or {}
    gr = g.get('func') or g.get('file') or {}
    cov = gr.get('coverage') or {}
    op = cov.get('op') or {}
    return from_metrics(op.get('metrics') or [])


def _classify(data, node_dir):
    """判定节点类型：'agg' | 'merged' | 'leaf'。

    - 顶层有 level 字段 → agg（根/库/域 汇总文件）
    - 顶层有 operator + mergedFrom → merged（目录级合并文件）
    - 顶层有 operator + granularities → leaf（叶子或单算子目录的叶子拷贝）
    """
    if isinstance(data, dict):
        if data.get('level'):
            return 'agg'
        if data.get('operator') and data.get('mergedFrom'):
            return 'merged'
        if data.get('operator') and data.get('granularities'):
            return 'leaf'
    return 'leaf'


def _coverage_node(base, d):
    """递归构造一个目录节点（统一形状）。

    返回 { name, path, kind, summary, has_analysis, children } 或 None（无可用覆盖率数据）。
    子节点一律来自文件系统子目录（汇总层 agg 文件无 sources，不能靠 sources 重建）。
    """
    jf = _latest_json(d)
    if jf is None:
        return None
    try:
        data = read(jf)
    except (ValueError, OSError):
        return None
    kind = _classify(data, d)
    try:
        rel_dir = d.relative_to(base).as_posix()
    except ValueError:
        rel_dir = d.name
    try:
        has_analysis = bool(list(d.glob('*.md')))
    except OSError:
        has_analysis = False
    node = {
        'name': d.name,
        'path': rel_dir,
        'kind': kind,
        'summary': _node_summary(data),
        'has_analysis': has_analysis,
    }
    # merged/leaf 层补充 operator 名与 domain
    oi = data.get('operatorInfo') or {} if isinstance(data, dict) else {}
    if kind in ('merged', 'leaf'):
        node['operator'] = data.get('operator') or d.name
        node['domain'] = oi.get('domain', '')
    # 递归子目录
    children = []
    try:
        subs = sorted([p for p in d.iterdir() if p.is_dir() and not p.is_symlink()])
    except OSError:
        subs = []
    for sub in subs:
        child = _coverage_node(base, sub)
        if child is not None:
            children.append(child)
    node['children'] = children
    return node


def _coverage_tree(base):
    """覆盖率树。两种结构：

    - 平坦（任务内嵌 runs/<run>/ops_cov_report/）：json/md 直接在 base 下，包装成单算子树。
    - 递归（全局 ops_final_cov_report/）：base/<版本>/<库>/<域>/<目录>/<算子>/…
      按 JSON 形态（level/operator/mergedFrom）递归，每节点带预聚合 summary 与 children。
    """
    versions = []
    if not base.is_dir():
        return {'versions': versions}
    # 平坦结构（任务内嵌）
    if _is_flat(base):
        files = sorted(base.glob('*.json'))
        try:
            data = read(files[0])
        except (ValueError, OSError):
            return {'versions': versions}
        summary = _op_summary(base, data, name=data.get('operator') or base.parent.name,
                              dir_name=base.parent.name)
        # 包装成与递归树同构的节点（versions[0] 即单算子节点）
        node = {
            'name': summary['dir_name'],
            'path': summary['dir_name'],
            'kind': 'leaf',
            'summary': {
                'func': summary['func'], 'func_covered': summary['func_covered'], 'func_total': summary['func_total'],
                'line': summary['line'], 'line_covered': summary['line_covered'], 'line_total': summary['line_total'],
                'branch': summary['branch'], 'branch_covered': summary['branch_covered'], 'branch_total': summary['branch_total'],
            },
            'has_analysis': summary['has_analysis'],
            'operator': summary['operator'],
            'domain': summary['domain'],
            'children': [],
        }
        return {'versions': [node]}
    # 递归结构：base 下每个子目录是一个版本节点
    for vdir in sorted(base.glob('*')):
        if not vdir.is_dir() or vdir.is_symlink():
            continue
        node = _coverage_node(base, vdir)
        if node is not None:
            node['version'] = vdir.name
            versions.append(node)
    return {'versions': versions}


def _coverage_detail(base, rel_parts, kind):
    """读取任意深度节点的 coverage json 或 analysis md。rel_parts 为相对 base 的路径段；kind=coverage|analysis。

    平坦结构（任务内嵌）时忽略 rel_parts，直接读 base 下的 json/md。
    """
    if _is_flat(base):
        if kind == 'coverage':
            files = sorted(base.glob('*.json'))
            if not files:
                raise FileNotFoundError('没有覆盖报告')
            return read(files[0])
        md_files = sorted(base.glob('*.md'))
        if not md_files:
            raise FileNotFoundError('没有分析文档')
        return {'filename': md_files[0].name, 'content': md_files[0].read_text(encoding='utf-8')}
    rel = '/'.join(rel_parts)
    node_dir = paths.resolve_within(base, rel)
    if kind == 'coverage':
        jf = _latest_json(node_dir)
        if jf is None:
            raise FileNotFoundError('没有覆盖报告')
        return read(paths.resolve_within(base, rel + '/' + jf.name))
    md_files = sorted(node_dir.glob('*.md'))
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
        # 任务内嵌覆盖率：runs/<run>/ops_cov_report/（平坦：json/md 直接在目录下）
        if parts[2] == 'cover':
            base = run / 'ops_cov_report'
            if len(parts) == 4 and parts[3] in ('coverage', 'analysis'):
                return _coverage_detail(base, [], parts[3])
            if parts[3:] == ['tree']:
                return _coverage_tree(base)
            if len(parts) == 8 and parts[7] in ('coverage', 'analysis'):
                return _coverage_detail(base, parts[3:7], parts[7])
    if parts and parts[0] == 'cover':
        base = root / 'ops_final_cov_report'
        if parts == ['cover', 'tree']:
            return _coverage_tree(base)
        if parts == ['cover', 'runs']:
            result = []
            for run_dir in sorted((root / 'runs').glob('*')):
                if not run_dir.is_dir() or run_dir.is_symlink():
                    continue
                cov_base = run_dir / 'ops_cov_report'
                if not cov_base.is_dir():
                    continue
                files = sorted(cov_base.glob('*.json'))
                if not files:
                    continue
                try:
                    data = read(files[0])
                except (ValueError, OSError):
                    continue
                s = _op_summary(cov_base, data, name=data.get('operator'), dir_name=run_dir.name)
                result.append({'run_id': run_dir.name, 'operator': s['operator'], 'domain': s['domain'],
                               'has_analysis': s['has_analysis'], 'count': s['count'],
                               'func': s['func'], 'line': s['line'], 'branch': s['branch']})
            return {'runs': result}
        # 变长路径详情：cover/<p1>/<p2>/…/<pn>/<kind>，kind ∈ {coverage, analysis}
        if len(parts) >= 3 and parts[-1] in ('coverage', 'analysis'):
            return _coverage_detail(base, parts[1:-1], parts[-1])
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