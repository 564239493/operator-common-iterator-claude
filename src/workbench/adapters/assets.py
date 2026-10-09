"""Asset-page content loader: one reviewed JSONC file, no discovery or inference.

Other endpoints keep their own responsibilities. This endpoint never reads agent
prose, manifests, skill directories, user home directories, or model services.
"""
import json
from pathlib import Path

from ..paths import resolve_asset

MAX_CONTENT_BYTES = 4 * 1024 * 1024
RELATIONS = {'required', 'conditional', 'reference', 'denied', 'unmentioned'}
DISPLAY_RELATIONS = RELATIONS | {'out_of_scope', 'unavailable'}
AVAILABILITY = {'ready', 'missing', 'unavailable'}
UI_KEYS = set('''title subtitle refresh refreshing refresh_failed agents_unit skills_unit knowledge_unit item_unit
    select_agent viewing view_capability agent_unavailable current_agent when_to_use outcome
    legend_required legend_allowed legend_unknown skills_title skills_empty knowledge_title
    family_label knowledge_intro knowledge_empty
    detail_label detail_knowledge detail_skill purpose relation_heading scope scope_note back overview
    choose_agent unavailable_description
    no_description'''.split())


def _without_comments(text):
    """Remove JSONC comments lexically, preserving strings and line positions.

    This is format parsing only, never semantic extraction of asset relations.
    Trailing commas remain invalid, as do unfinished strings or comments.
    """
    result, index, quoted = [], 0, False
    while index < len(text):
        char = text[index]
        if quoted:
            result.append(char)
            if char == '\\' and index + 1 < len(text):
                index += 1
                result.append(text[index])
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
            result.append(char)
        elif text.startswith('//', index):
            while index < len(text) and text[index] not in '\r\n':
                result.append(' ')
                index += 1
            continue
        elif text.startswith('/*', index):
            end = text.find('*/', index + 2)
            if end < 0:
                raise ValueError('资产内容：块注释未闭合')
            result.extend(c if c in '\r\n' else ' ' for c in text[index:end + 2])
            index = end + 2
            continue
        else:
            result.append(char)
        index += 1
    return ''.join(result)


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'资产内容：重复字段 {key}')
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError('资产内容：非法 JSON 数值 ' + value)


def _require(condition, message):
    if not condition:
        raise ValueError('资产内容：' + message)


def _text(record, key):
    value = record.get(key)
    _require(isinstance(value, str) and bool(value.strip()), f'{key} 必须为非空文字')
    return value


def _records(data, key):
    items = data.get(key)
    _require(isinstance(items, list) and all(isinstance(i, dict) for i in items), f'{key} 必须为对象列表')
    return items


def validate_contents(data):
    """Validate both schema and graph references before publishing any content."""
    _require(isinstance(data, dict), '内容根必须为对象')
    _require(type(data.get('schema_version')) is int and data['schema_version'] == 1, '不支持的 schema_version')
    _text(data, 'content_version')
    ui = data.get('ui')
    _require(isinstance(ui, dict), '缺少 ui')
    for key in UI_KEYS:
        _text(ui, key)
    _require('{agent}' in ui['relation_heading'], 'relation_heading 必须包含 {agent}')
    labels = data.get('relation_labels')
    _require(isinstance(labels, dict) and set(labels) == DISPLAY_RELATIONS, '关系标签集合不完整')
    for value in labels.values():
        _require(isinstance(value, dict), '关系标签必须为对象')
        _text(value, 'label')
        _text(value, 'description')
    scopes = data.get('scopes')
    _require(isinstance(scopes, dict) and bool(scopes), '缺少知识分类')
    for key in scopes:
        _text(scopes, key)
    families = set()
    for family in _records(data, 'families'):
        key = _text(family, 'id')
        _require(key not in families, '重复知识类型 ' + key)
        families.add(key)
        _text(family, 'title')
        _text(family, 'description')
        expanded = family.get('expanded_scopes')
        _require(isinstance(expanded, list) and all(isinstance(s, str) and s in scopes for s in expanded), '展开分类引用不存在')
    _require(_text(data, 'default_family') in families, '默认知识类型不存在')
    ids = set()
    for collection, kind in (('skills', 'skill'), ('knowledge', 'knowledge')):
        for item in _records(data, collection):
            key = _text(item, 'id')
            _require(key not in ids, '重复资产 id ' + key)
            ids.add(key)
            for field in ('name', 'title', 'description'):
                _text(item, field)
            _require(item.get('kind') == kind, '资产分类与集合不一致')
            _require(isinstance(item.get('availability'), str) and item['availability'] in AVAILABILITY, '无效的资产可用状态')
            if kind == 'knowledge':
                _require(isinstance(item.get('family'), str) and item['family'] in families, '知识类型引用不存在')
                _require(isinstance(item.get('scope'), str) and item['scope'] in scopes, '知识分类引用不存在')
    agent_ids = set()
    for agent in _records(data, 'agents'):
        key = _text(agent, 'name')
        _require(key not in agent_ids, '重复智能体 ' + key)
        agent_ids.add(key)
        for field in ('role', 'description', 'when_to_use', 'outcome', 'color'):
            _text(agent, field)
        _require(isinstance(agent.get('availability'), str) and agent['availability'] in AVAILABILITY, '无效的智能体可用状态')
        relations = agent.get('relations')
        _require(isinstance(relations, dict), 'relations 必须为对象')
        for target, state in relations.items():
            _require(target in ids, '关联的资产不存在：' + target)
            _require(isinstance(state, str) and state in RELATIONS, '无效的关联状态')
    _require(_text(data, 'default_agent') in agent_ids, '默认智能体不存在')
    return data


def load_assets(root):
    """Read fresh on each request; never fall back to dynamic directory scans."""
    source = resolve_asset(Path(root).resolve() / 'src', 'contents.jsonc')
    _require(source.is_file(), '内容源不是普通文件')
    with source.open('rb') as stream:
        raw = stream.read(MAX_CONTENT_BYTES + 1)
    _require(len(raw) <= MAX_CONTENT_BYTES, '内容文件过大')
    content = json.loads(_without_comments(raw.decode('utf-8-sig')), object_pairs_hook=_unique_keys,
                         parse_constant=_invalid_constant)
    return validate_contents(content)
