"""Typed personal facts, separate evidence, explicit links and reviewed migration."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'schemas' / 'profile-fields.json').read_text(encoding='utf-8'))


def fields(kind):
    return CATALOG['records'].get(kind, {}).get('fields', [])


def validate_record_fields(kind, data, *, check_category=True):
    """The service and both transports use the same bounded typed fields."""
    from .app import AppError, _text, _url
    definitions = {field['key']: field for field in fields(kind)}
    if not isinstance(data, dict) or not data or set(data) - set(definitions):
        raise AppError('invalid_fields', 'Record contains unsupported fields.')
    for key, value in data.items():
        if value is None:
            continue
        field = definitions[key]
        if check_category and field.get('categories') and data.get('category') not in field['categories']:
            raise AppError('invalid_fields', 'This field does not belong to the selected experience category.')
        typ = field.get('value_type', 'string')
        if typ == 'string':
            _text(value, field['label'], field['max_length'])
            if 'options' in field and (kind, key) != ('family', 'employment_status') and value not in field['options']:
                raise AppError('invalid_choice', 'Record field is not an allowed choice.')
            if field.get('type') == 'url' and value:
                _url(value)
            if field.get('format') == 'partial_date' and value:
                try:
                    if not re.fullmatch(r'\d{4}(?:-\d{2})?(?:-\d{2})?', value):
                        raise ValueError()
                    datetime.strptime(value, {4: '%Y', 7: '%Y-%m', 10: '%Y-%m-%d'}[len(value)])
                except (ValueError, KeyError):
                    raise AppError('invalid_date', '日期须为 YYYY、YYYY-MM 或 YYYY-MM-DD，保留原有精度。') from None
        elif typ == 'boolean':
            if not isinstance(value, bool):
                raise AppError('invalid_field', 'This field must be boolean or null.')
        elif typ in {'number', 'integer'}:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or (typ == 'integer' and not isinstance(value, int)) or not field['minimum'] <= value <= field['maximum'] or not math.isfinite(value):
                raise AppError('invalid_field', 'Numeric field is outside its allowed range.')
        elif typ == 'string_array':
            if not isinstance(value, list) or len(value) > field['max_items']:
                raise AppError('invalid_field', 'This field must be a bounded text list or null.')
            for item in value:
                _text(item, field['label'], field['item_max_length'], False)
                if field.get('options') and item not in field['options']:
                    raise AppError('invalid_choice', 'List contains an unsupported choice.')
        elif typ in {'json', 'object'}:
            if typ == 'object' and not isinstance(value, dict):
                raise AppError('invalid_field', 'This field must be an object or null.')
            _bounded_json(value, field.get('max_bytes', 24000), field.get('max_depth', 5))
        else:
            raise AppError('invalid_field', 'Unsupported field type.')


def _bounded_json(value, max_bytes=24000, max_depth=5):
    from .app import AppError, _json, _text
    def visit(item, depth):
        if depth > max_depth:
            raise AppError('invalid_field', 'Structured value is nested too deeply.')
        if item is None or isinstance(item, bool):
            return
        if isinstance(item, str):
            _text(item, 'Structured text', max_bytes)
        elif isinstance(item, (int, float)):
            if not -1000000000000000 <= item <= 1000000000000000 or not math.isfinite(item):
                raise AppError('invalid_field', 'Structured numeric value is outside its range.')
        elif isinstance(item, list):
            if len(item) > 200:
                raise AppError('invalid_field', 'Structured list exceeds its limit.')
            for child in item:
                visit(child, depth + 1)
        elif isinstance(item, dict):
            if len(item) > 200:
                raise AppError('invalid_field', 'Structured object exceeds its limit.')
            for key, child in item.items():
                _text(key, 'Structured key', 500, False)
                visit(child, depth + 1)
        else:
            raise AppError('invalid_field', 'Unsupported structured value.')
    visit(value, 0)
    if len(_json(value).encode('utf-8')) > max_bytes:
        raise AppError('invalid_field', 'Structured value exceeds its size limit.')


def capture_sources(db, after, before=None):
    """Keep field evidence separate from the values handed to resume forms."""
    from .app import _json, now
    if after['kind'] not in CATALOG['records'] or after['archived']:
        return
    metadata_changed = before and any(before[key] != after[key] for key in ('source', 'scope', 'status'))
    for key, value in after['data'].items():
        if before and not metadata_changed and key in before['data'] and before['data'][key] == value:
            continue
        db.execute('INSERT INTO profile_sources VALUES(?,?,?,?,?,?,?,?,?,?)',
                   (uuid.uuid4().hex, after['user_id'], after['id'], key, after['revision'],
                    after['source'], after['scope'], after['status'], _json(value), now()))


def _sources(db, user_id, record_id):
    result = []
    current_row = db.execute('SELECT data FROM records WHERE id=? AND user_id=?', (record_id, user_id)).fetchone()
    current = json.loads(current_row['data']) if current_row else {}
    rows = db.execute('SELECT * FROM profile_sources WHERE user_id=? AND record_id=? ORDER BY revision DESC,created_at DESC,id', (user_id, record_id))
    seen = set()
    for row in rows:
        value = dict(row)
        decoded = json.loads(value['value_json'])
        if value['field_path'] not in current or decoded != current[value['field_path']]:
            continue
        if value['field_path'] in seen:
            continue
        seen.add(value['field_path'])
        value['value'] = json.loads(value.pop('value_json'))
        result.append(value)
    return result


def export_extras(app, db):
    sources = [dict(row) for row in db.execute('SELECT p.* FROM profile_sources p JOIN records r ON r.id=p.record_id AND r.user_id=p.user_id WHERE p.user_id=? AND r.archived=0 ORDER BY p.created_at,p.id', (app.user_id,))]
    relations = [_relation(row) for row in db.execute('SELECT p.* FROM profile_relations p JOIN records a ON a.id=p.from_id AND a.user_id=p.user_id JOIN records b ON b.id=p.to_id AND b.user_id=p.user_id WHERE p.user_id=? AND p.archived=0 AND a.archived=0 AND b.archived=0 ORDER BY p.created_at,p.id', (app.user_id,))]
    for item in sources + relations:
        item.pop('user_id', None)
    for item in sources:
        item['value'] = json.loads(item.pop('value_json'))
    return {'profile_sources': sources, 'profile_relations': relations}


def import_extras(app, db, payload, id_map, actor):
    """Restore portable references atomically without overwriting local edits."""
    from .app import AppError, _integer, _text, _json, now
    sources, relations = payload.get('profile_sources', []), payload.get('profile_relations', [])
    if not isinstance(sources, list) or len(sources) > 50000 or not isinstance(relations, list) or len(relations) > 10000:
        raise AppError('invalid_profile', 'Portable profile evidence or relations exceed the limit.')
    def local(source_id):
        if not isinstance(source_id, str) or source_id not in id_map:
            raise AppError('invalid_profile', 'Portable evidence references an unavailable record.')
        record = app._get_record(db, id_map[source_id])
        if record['archived'] or record['kind'] not in CATALOG['records']:
            raise AppError('invalid_profile', 'Portable evidence must reference active personal records.')
        return record
    for item in sources:
        allowed = {'id', 'record_id', 'field_path', 'revision', 'source', 'scope', 'status', 'value', 'created_at'}
        if not isinstance(item, dict) or set(item) != allowed:
            raise AppError('invalid_profile', 'Portable field evidence has invalid fields.')
        _text(item['id'], 'Evidence id', 100, False)
        record = local(item['record_id'])
        if not isinstance(item['field_path'], str) or item['field_path'] not in {field['key'] for field in fields(record['kind'])}:
            raise AppError('invalid_profile', 'Portable evidence references an unsupported field.')
        _integer(item['revision'], 'Evidence revision', 1)
        for key in ('source', 'scope', 'created_at'):
            _text(item[key], 'Evidence metadata', 500, False)
        if not isinstance(item['status'], str) or item['status'] not in {'unconfirmed', 'confirmed', 'observed', 'unknown'}:
            raise AppError('invalid_profile', 'Portable evidence has invalid status.')
        try:
            value = item['value']
            validate_record_fields(record['kind'], {item['field_path']: value}, check_category=False)
            _text(_json(value), 'Evidence value', 60000)
        except (ValueError, TypeError):
            raise AppError('invalid_profile', 'Portable evidence value is invalid.') from None
        source_id = item['id'] if record['id'] == item['record_id'] else uuid.uuid5(uuid.NAMESPACE_URL, f"{app.user_id}:{record['id']}:{item['id']}").hex
        existing = db.execute('SELECT record_id,user_id FROM profile_sources WHERE id=?', (source_id,)).fetchone()
        if existing:
            if existing['user_id'] != app.user_id or existing['record_id'] != record['id']:
                raise AppError('invalid_profile', 'Portable evidence identity conflicts with this workspace.')
            continue
        db.execute('INSERT INTO profile_sources VALUES(?,?,?,?,?,?,?,?,?,?)', (source_id, app.user_id, record['id'], item['field_path'], min(item['revision'], record['revision']), item['source'], item['scope'], item['status'], _json(value), item['created_at']))
    for item in relations:
        required = {'id', 'from_id', 'to_id', 'relation_type', 'revision', 'source', 'status', 'created_at', 'updated_at', 'archived'}
        if not isinstance(item, dict) or set(item) != required or item['archived'] is not False or not isinstance(item['relation_type'], str) or item['relation_type'] not in RELATION_TYPES:
            raise AppError('invalid_profile', 'Portable relation has invalid fields.')
        _text(item['id'], 'Relation id', 100, False)
        _integer(item['revision'], 'Relation revision', 1)
        a, b = local(item['from_id']), local(item['to_id'])
        if any(record['kind'] != 'experience' or record['data'].get('category') != category for record, category in zip((a, b), RELATION_TYPES[item['relation_type']])):
            raise AppError('invalid_relation', 'Portable relation endpoint categories do not match.')
        for key in ('source', 'created_at', 'updated_at'):
            _text(item[key], 'Relation metadata', 500, False)
        if not isinstance(item['status'], str) or item['status'] not in {'unconfirmed', 'confirmed', 'observed', 'unknown'}:
            raise AppError('invalid_profile', 'Portable relation has invalid status.')
        existing = db.execute('SELECT id FROM profile_relations WHERE user_id=? AND from_id=? AND to_id=? AND relation_type=? AND archived=0', (app.user_id, a['id'], b['id'], item['relation_type'])).fetchone()
        if existing:
            continue
        relation_id = item['id'] if [a['id'], b['id']] == [item['from_id'], item['to_id']] else uuid.uuid5(uuid.NAMESPACE_URL, f"{app.user_id}:{a['id']}:{b['id']}:{item['id']}").hex
        collision = db.execute('SELECT * FROM profile_relations WHERE id=?', (relation_id,)).fetchone()
        if collision:
            if collision['user_id'] != app.user_id or collision['from_id'] != a['id'] or collision['to_id'] != b['id'] or collision['relation_type'] != item['relation_type']:
                raise AppError('invalid_profile', 'Portable relation identity conflicts with this workspace.')
            # A local archived relationship is an explicit edit, not restored by reimport.
            continue
        db.execute('INSERT INTO profile_relations VALUES(?,?,?,?,?,?,?,?,?,?,0)', (relation_id, app.user_id, a['id'], b['id'], item['relation_type'], item['revision'], item['source'], item['status'], item['created_at'], item['updated_at']))
        after = _relation(db.execute('SELECT * FROM profile_relations WHERE id=?', (relation_id,)).fetchone())
        db.execute('INSERT INTO profile_relation_events VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, app.user_id, relation_id, actor, now(), None, _json(after)))


def _records(app, db):
    return [app._record(row) for row in db.execute("SELECT * FROM records WHERE user_id=? AND archived=0 AND kind NOT IN ('application','company_answer','resume_variant','company_decision') ORDER BY created_at,id", (app.user_id,))]


def _digest(user_id, records):
    payload = {'user_id': user_id, 'schema_version': CATALOG['schema_version'], 'records': records}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def section_for(record):
    for section in CATALOG['sections']:
        if section['kind'] != record['kind']:
            continue
        category = section.get('category')
        if category is None or record['data'].get('category') == category:
            return section['id']
    return 'uncategorized'


def embedded(text):
    """Only extract explicit keyed old text; preserve ambiguity and the original."""
    if not isinstance(text, str):
        return {}
    matches = list(re.finditer(r'(?m)^([a-z_]+)：', text))
    result = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw = text[match.end():end].strip()
        if match.group(1) in result:
            return {}  # Duplicate labels are not an unambiguous migration source.
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            value = {'True': True, 'False': False}.get(raw, raw)
        result[match.group(1)] = value
    return result


PREFERENCE_LABELS = {
    'preferred_cities': 'intention', 'preferred_city_tiers': 'intention',
    'allowed_cities': 'intention', 'other_cities_allowed': 'intention',
    'city_policy': 'intention', 'research_directions': 'intention',
    'expected_graduation_month': 'education_fact', 'earliest_start_month': 'intention',
    'expected_annual_salary': 'intention', 'initial_excluded_role_focus': 'intention_review',
    'application_preparation_policy': 'historical_task_policy',
    'execution_priority': 'historical_task_policy', 'fact_source_priority': 'provenance_policy',
    'company_specific_graduation_policy': 'company_answer_review',
    'application_specific_city_exceptions': 'company_answer',
    'site_date_placeholders_tencent': 'company_date_conversion',
    'excluded_research': 'resume_selection',
}


def preview(record):
    from .app import AppError
    data = record['data']
    result = {'id': record['id'], 'revision': record['revision'], 'kind': record['kind'],
              'section': section_for(record), 'source': record['source'], 'scope': record['scope'],
              'status': 'proposed', 'proposed_fields': {}, 'issues': [],
              'changes': {}, 'would_change': False, 'review_required': False}
    if record['kind'] == 'preference':
        notes = data.get('notes') or ''
        label = notes.split(';', 1)[0].strip()
        target = PREFERENCE_LABELS.get(label)
        if target is None:
            target = 'intention_review' if data.get('category') in {'cities', 'roles', 'industries', 'excluded'} and record['scope'] == 'global' else 'manual_review'
        result.update(target=target, original_value=data.get('value'), original_notes=notes)
        if target.endswith('review') or target == 'historical_task_policy':
            result['issues'].append('需核对当前适用范围和最新本人决定，不能自动启用历史指令。')
        result['review_required'] = bool(result['issues'])
        return result
    result['target'] = result['section']
    if record['kind'] in {'education', 'experience'}:
        result['original_value'] = data.get('description')
    if record['kind'] == 'education':
        old = embedded(data.get('description'))
        aliases = {'laboratory': 'laboratory', 'research_fields': 'research_directions',
                   'study_mode': 'study_mode', 'double_degree': 'double_degree'}
        for key, target in aliases.items():
            if key in old:
                result['proposed_fields'][target] = old[key]
        supervisor = old.get('supervisor')
        if isinstance(supervisor, dict):
            for key in ('name', 'title'):
                if key in supervisor:
                    result['proposed_fields']['supervisor_' + key] = supervisor[key]
            if set(supervisor) - {'name', 'title'}:
                result['issues'].append('导师原文包含尚未映射的字段，保留待核对。')
        elif 'supervisor' in old:
            result['issues'].append('导师原文的结构无法直接转换，保留待核对。')
        if 'end_month_is_expected' in old and not isinstance(old['end_month_is_expected'], bool):
            result['issues'].append('结束日期的预计标记无法直接转换，保留原文待核对。')
        if old.get('end_month_is_expected') is True:
            result['proposed_fields']['expected_graduation_date'] = data.get('end_date')
            result['issues'].append('预计毕业不是实际毕业或拿证日期，需核实当前状态。')
        elif old.get('end_month_is_expected') is False:
            result['proposed_fields']['graduation_date'] = data.get('end_date')
        elif data.get('end_date'):
            result['issues'].append('结束日期的预计 / 实际含义未知，不能自动转换。')
        if 'academic_ranking' in old:
            result['issues'].append('历史网站排名选项须核对，不能自动转换为精确排名。')
    if record['kind'] == 'experience' and data.get('category') == 'project':
        text = data.get('description') or ''
        for key, label in [('description', '项目描述'), ('responsibilities', '个人职责'), ('results', '项目成果')]:
            found = re.findall(label + r'：(.*?)(?=\n(?:个人职责|项目成果|来源)：|\Z)', text, re.S)
            if len(found) == 1:
                result['proposed_fields'][key] = found[0].strip()
            elif len(found) > 1:
                result['issues'].append('同名项目字段重复，需人工核对。')
    if result['section'] == 'uncategorized':
        result['issues'].append('经历类别未知，请先分类；不会按标题猜测。')
    if record['kind'] == 'education':
        old = embedded(data.get('description'))
        if 'education_status' in old or 'status' in old:
            result['proposed_fields']['education_status'] = old.get('education_status', old.get('status'))
        ranking = old.get('academic_ranking')
        if isinstance(ranking, dict) and ranking.get('type') == 'percent':
            result['proposed_fields']['rank_percent'] = old['academic_ranking'].get('value')
        consumed = {'laboratory', 'research_fields', 'supervisor', 'study_mode', 'double_degree', 'education_status', 'status', 'end_month_is_expected'}
        if old and not set(old) - consumed:
            result['proposed_fields']['description'] = None
        elif old and set(old) - consumed:
            result['issues'].append('教育原文含尚未迁移的字段，保留待整理。')
    if record['kind'] == 'experience' and data.get('category') == 'publication':
        old = embedded(data.get('description'))
        allowed = {f['key'] for f in fields('experience') if f.get('categories') == ['publication']}
        for key in set(old) & allowed:
            result['proposed_fields'][key] = old[key]
        if old and not set(old) - allowed:
            result['proposed_fields']['description'] = None
    changes = {}
    translations = {
        'study_mode': {'全日制': 'full_time', '非全日制': 'part_time'},
        'education_status': {'在读': 'studying', '已毕业': 'completed', '肄业': 'withdrawn'},
        'double_degree': {'是': True, '否': False},
    }
    for key, candidate in result['proposed_fields'].items():
        if isinstance(candidate, str):
            candidate = translations.get(key, {}).get(candidate, candidate)
        try:
            validate_record_fields(record['kind'], {**data, key: candidate})
        except AppError:
            result['issues'].append(f"{CATALOG['migration_labels'].get(key, key)}的旧值无法直接转换，保留原文待核对。")
            continue
        existing = data.get(key)
        # A description is cleared only when all its explicit keyed facts moved.
        if key == 'description' and candidate is None:
            if all(k in data or k in result['proposed_fields'] for k in ('laboratory', 'study_mode') if k in embedded(data.get('description'))):
                changes[key] = None
        elif key == 'description' and record['kind'] == 'experience' and data.get('category') == 'project' and (data.get('description') or '').startswith('项目描述：'):
            changes[key] = candidate
        elif key not in data or existing is None:
            changes[key] = candidate
        elif existing != candidate:
            result['issues'].append(f"{CATALOG['migration_labels'].get(key, key)}已有不同值，保留现值待核对。")
    # Never clear a keyed source if any extracted field failed or conflicted.
    if result['issues'] and changes.get('description', 'unchanged') is None:
        changes.pop('description')
    result['changes'] = changes
    result['would_change'] = bool(changes)
    result['review_required'] = bool(result['issues'])
    return result


def execute(app, operation, args, actor):
    from .app import AppError, _integer
    if operation.startswith('profile.relation.'):
        return _relation_execute(app, operation, args, actor)
    if operation == 'profile.migration.apply':
        return _migration_apply(app, args, actor)
    if operation == 'profile.schema':
        if args:
            raise AppError('invalid_request', 'Profile schema does not accept arguments.')
        return copy.deepcopy(CATALOG)
    if operation in {'profile.get', 'profile.migration.preview'}:
        if set(args) - {'section', 'limit', 'offset'}:
            raise AppError('invalid_request', 'Profile reads accept section, limit and offset only.')
        section = args.get('section')
        if section is not None and (not isinstance(section, str) or section not in {s['id'] for s in CATALOG['sections']} | {'uncategorized'}):
            raise AppError('invalid_section', 'Unknown profile section.')
        limit = _integer(args.get('limit', 20), 'Limit', 1, 100)
        offset = _integer(args.get('offset', 0), 'Offset')
        with app._connect() as db:
            db.execute('BEGIN')
            all_records = _records(app, db)
            records = all_records
            source_values = {r['id']: _sources(db, app.user_id, r['id']) for r in records} if operation == 'profile.get' else {}
        if section is not None:
            records = [r for r in records if section_for(r) == section]
        items = records[offset:offset + limit]
        return {'user_id': app.user_id, 'schema_version': CATALOG['schema_version'],
                'mode': 'read_only_preview' if operation.endswith('preview') else 'current_records',
                'items': [preview(r) for r in items] if operation.endswith('preview') else [dict(r, field_sources=source_values[r['id']]) for r in items],
                **({'preview_digest': _digest(app.user_id, all_records), 'source_revisions': {r['id']: r['revision'] for r in all_records}} if operation.endswith('preview') else {}),
                'total': len(records), 'offset': offset, 'limit': limit,
                'next_offset': offset + len(items) if offset + len(items) < len(records) else None}
    if operation == 'profile.patch':
        if set(args) - {'id', 'revision', 'changes', 'clear', 'status', 'scope', 'source'} or not {'id', 'revision'} <= set(args):
            raise AppError('invalid_request', 'Patch requires a record id and its current revision.')
        changes, clear = args.get('changes', {}), args.get('clear', [])
        if not isinstance(changes, dict) or not isinstance(clear, list) or any(not isinstance(key, str) for key in clear) or len(set(clear)) != len(clear) or set(changes) & set(clear) or not (changes or clear):
            raise AppError('invalid_patch', 'Supply changed fields or distinct explicitly cleared fields.')
        with app._connect() as db:
            current = app._get_record(db, args['id'])
        if current['kind'] not in CATALOG['records'] or current['kind'] in {'preference', 'company_answer', 'resume_variant', 'company_decision'}:
            raise AppError('invalid_kind', 'Patch personal facts; manage intentions through their own interface.')
        allowed = {f['key'] for f in fields(current['kind'])}
        if (set(changes) | set(clear)) - allowed:
            raise AppError('invalid_fields', 'Patch contains an unsupported field.')
        data = copy.deepcopy(current['data'])
        data.update(changes)
        data.update({key: None for key in clear})
        save = {'id': current['id'], 'revision': args['revision'], 'kind': current['kind'], 'data': data}
        for key in ('status', 'scope', 'source'):
            if key in args:
                save[key] = args[key]
        return app.execute('record.save', save, actor)
    if operation == 'intention.get':
        if args:
            raise AppError('invalid_request', 'Intention read does not accept arguments.')
        with app._connect() as db:
            settings = app._settings(db)
            total = db.execute("SELECT COUNT(*) FROM records WHERE user_id=? AND kind='preference' AND archived=0", (app.user_id,)).fetchone()[0]
        return {'user_id': app.user_id, 'revision': settings['_revision'], 'data': settings['preferences'],
                'authority': 'settings.preferences', 'legacy_records_pending_review': total,
                'legacy_notice': '旧偏好中的事实、公司例外和历史约束仍需迁移核对；这里不会自动覆盖它们。'}
    if operation == 'intention.patch':
        if set(args) != {'revision', 'changes'} or not isinstance(args['changes'], dict) or not args['changes']:
            raise AppError('invalid_request', 'Intention patch requires revision and changed fields.')
        return app.execute('settings.save', {'revision': args['revision'], 'settings': {'preferences': args['changes']}}, actor)
    raise AppError('unknown_operation', 'Unknown profile operation.', 404)


def _migration_apply(app, args, actor):
    from .app import AppError, _json, now
    if actor != 'user':
        raise AppError('user_action_required', '请在网页审阅资料迁移后确认执行。', 403)
    if set(args) != {'preview_digest'} or not isinstance(args['preview_digest'], str) or not re.fullmatch('[0-9a-f]{64}', args['preview_digest']):
        raise AppError('invalid_request', 'Migration requires the digest of the reviewed preview.')
    with app._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        previous = db.execute('SELECT result_json FROM profile_migrations WHERE user_id=? AND preview_digest=?', (app.user_id, args['preview_digest'])).fetchone()
        if previous:
            return dict(json.loads(previous['result_json']), already_applied=True)
        records = _records(app, db)
        if _digest(app.user_id, records) != args['preview_digest']:
            raise AppError('revision_conflict', '资料在预览后已修改，请重新查看迁移对照。', 409)
        proposals = [preview(r) for r in records]
        result = {'migration_id': uuid.uuid4().hex, 'applied_count': 0, 'unchanged_count': 0,
                  'review_count': sum(bool(p.get('issues')) for p in proposals), 'id_map': {r['id']: r['id'] for r in records}}
        for record, proposed in zip(records, proposals):
            changes = proposed.get('changes', {})
            if not changes:
                result['unchanged_count'] += 1
                continue
            app._save_in_transaction(db, {'id': record['id'], 'revision': record['revision'], 'kind': record['kind'],
                                         'data': dict(record['data'], **changes), 'source': record['source'],
                                         'scope': record['scope'], 'status': record['status']}, actor, 'profile.migration')
            result['applied_count'] += 1
        db.execute('INSERT INTO profile_migrations VALUES(?,?,?,?,?,?)', (result['migration_id'], app.user_id, args['preview_digest'], actor, now(), _json(result)))
        return result


RELATION_TYPES = {'project_publication': ('project', 'publication'), 'research_publication': ('research', 'publication'), 'project_research': ('project', 'research')}


def _relation(row):
    result = dict(row)
    result['archived'] = bool(result['archived'])
    return result


def check_record_relations(db, record, data):
    from .app import AppError
    if record['kind'] != 'experience':
        return
    for relation in db.execute('SELECT * FROM profile_relations WHERE user_id=? AND archived=0 AND (from_id=? OR to_id=?)', (record['user_id'], record['id'], record['id'])):
        expected = RELATION_TYPES[relation['relation_type']][0 if relation['from_id'] == record['id'] else 1]
        if data.get('category') != expected:
            raise AppError('linked_record_type', '请先解除相关资料关联，再修改条目类别。', 409)


def _relation_execute(app, operation, args, actor):
    from .app import AppError, _integer, _text, _json, now
    if operation == 'profile.relation.list':
        if set(args) - {'record_id', 'limit', 'offset'}:
            raise AppError('invalid_request', 'Relation reads accept record_id, limit and offset.')
        limit, offset = _integer(args.get('limit', 20), 'Limit', 1, 100), _integer(args.get('offset', 0), 'Offset')
        with app._connect() as db:
            db.execute('BEGIN')
            clauses, params = ['r.user_id=?', 'r.archived=0', 'a.archived=0', 'b.archived=0'], [app.user_id]
            if args.get('record_id') is not None:
                app._get_record(db, args['record_id'])
                clauses.append('(r.from_id=? OR r.to_id=?)')
                params += [args['record_id'], args['record_id']]
            query = ' FROM profile_relations r JOIN records a ON a.id=r.from_id AND a.user_id=r.user_id JOIN records b ON b.id=r.to_id AND b.user_id=r.user_id WHERE ' + ' AND '.join(clauses)
            total = db.execute('SELECT COUNT(*)' + query, params).fetchone()[0]
            items = [_relation(row) for row in db.execute('SELECT r.*' + query + ' ORDER BY r.created_at,r.id LIMIT ? OFFSET ?', params + [limit, offset])]
        return {'items': items, 'total': total, 'next_offset': offset + len(items) if offset + len(items) < total else None}
    if operation not in {'profile.relation.save', 'profile.relation.archive'}:
        raise AppError('unknown_operation', 'Unknown relation operation.', 404)
    allowed = {'id', 'revision'} if operation.endswith('archive') else {'id', 'revision', 'from_id', 'to_id', 'relation_type', 'source', 'status'}
    if set(args) - allowed:
        raise AppError('invalid_request', 'Unsupported relation arguments.')
    with app._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        before = None
        if args.get('id'):
            _text(args['id'], 'Relation id', 100, False)
            row = db.execute('SELECT * FROM profile_relations WHERE id=? AND user_id=?', (args['id'], app.user_id)).fetchone()
            if not row:
                raise AppError('not_found', 'Relation was not found.', 404)
            before = _relation(row)
            if before['revision'] != _integer(args.get('revision'), 'Revision', 1):
                raise AppError('revision_conflict', 'Relation changed. Reload before editing.', 409)
            if before['archived']:
                raise AppError('relation_archived', 'Relation is already archived.', 409)
        if operation.endswith('archive'):
            if not before:
                raise AppError('invalid_request', 'Archive requires id and revision.')
            db.execute('UPDATE profile_relations SET archived=1,revision=revision+1,updated_at=? WHERE id=? AND user_id=?', (now(), before['id'], app.user_id))
            relation_id = before['id']
        else:
            relation_type = args.get('relation_type', before['relation_type'] if before else None)
            if not isinstance(relation_type, str) or relation_type not in RELATION_TYPES:
                raise AppError('invalid_relation', 'Choose a supported explicit relation type.')
            endpoints = [args.get(key, before[key] if before else None) for key in ('from_id', 'to_id')]
            if before and (endpoints != [before['from_id'], before['to_id']] or relation_type != before['relation_type']):
                raise AppError('invalid_relation', 'Archive a relation before changing its endpoints.')
            for record_id, category in zip(endpoints, RELATION_TYPES[relation_type]):
                record = app._get_record(db, record_id)
                if record['archived'] or record['kind'] != 'experience' or record['data'].get('category') != category:
                    raise AppError('invalid_relation', 'Relation endpoints must be active matching personal records.')
            if not before:
                existing = db.execute('SELECT * FROM profile_relations WHERE user_id=? AND from_id=? AND to_id=? AND relation_type=? AND archived=0', (app.user_id, *endpoints, relation_type)).fetchone()
                if existing:
                    return _relation(existing)
            source, _, status = app._metadata(args, actor, before)
            relation_id = before['id'] if before else uuid.uuid4().hex
            timestamp = now()
            db.execute('INSERT INTO profile_relations VALUES(?,?,?,?,?,?,?,?,?,?,0) ON CONFLICT(id) DO UPDATE SET source=excluded.source,status=excluded.status,revision=excluded.revision,updated_at=excluded.updated_at',
                       (relation_id, app.user_id, *endpoints, relation_type, before['revision'] + 1 if before else 1, source, status, before['created_at'] if before else timestamp, timestamp))
        after = _relation(db.execute('SELECT * FROM profile_relations WHERE id=? AND user_id=?', (relation_id, app.user_id)).fetchone())
        db.execute('INSERT INTO profile_relation_events VALUES(?,?,?,?,?,?,?)', (uuid.uuid4().hex, app.user_id, relation_id, actor, now(), _json(before) if before else None, _json(after)))
        return after
