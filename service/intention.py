"""One typed intention per account and conservative, reviewed legacy cleanup."""
from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import uuid

CATALOG = json.loads((Path(__file__).resolve().parents[1] / 'schemas' / 'intention-fields.json').read_text(encoding='utf-8'))
DEFAULTS = CATALOG['defaults']
DEFINITIONS = {field['key']: field for field in CATALOG['fields']}
SALARY_KEYS = {field['key'] for field in CATALOG['salary_fields']}


def _list(value, maximum=100):
    from .app import AppError, _text
    if not isinstance(value, list) or len(value) > maximum:
        raise AppError('invalid_settings', '意向列表超出长度限制或格式不正确。')
    return list(dict.fromkeys(_text(item, 'Preference entry', 200, False).strip() for item in value))


def _date(value):
    from .app import AppError, _text
    if value is None:
        return None
    _text(value, 'Start date', 10, False)
    try:
        if not re.fullmatch(r'\d{4}(?:-\d{2})?(?:-\d{2})?', value):
            raise ValueError()
        datetime.strptime(value, {4: '%Y', 7: '%Y-%m', 10: '%Y-%m-%d'}[len(value)])
    except (KeyError, ValueError):
        raise AppError('invalid_date', '到岗日期须为 YYYY、YYYY-MM 或 YYYY-MM-DD。') from None
    return value


def _salary(incoming, current):
    from .app import AppError, _text
    if incoming is None:
        return None
    if not isinstance(incoming, dict) or not incoming or set(incoming) - SALARY_KEYS:
        raise AppError('invalid_settings', '薪资期望包含不支持的字段。')
    salary = dict(current) if isinstance(current, dict) else {'expected_amount': None, 'amount_min': None, 'amount_max': None, 'tax_basis': 'unknown'}
    salary.update(incoming)
    for key in ('expected_amount', 'amount_min', 'amount_max'):
        amount = salary.get(key)
        if amount is not None and (isinstance(amount, bool) or not isinstance(amount, (int, float)) or not 0 <= amount <= 1000000000 or not math.isfinite(amount)):
            raise AppError('invalid_settings', '薪资金额须为有效的非负数，空白表示未知。')
    _text(salary.get('currency'), 'Salary currency', 20, False)
    if not isinstance(salary.get('period'), str) or salary['period'] not in {'annual', 'monthly'} or not isinstance(salary.get('tax_basis'), str) or salary['tax_basis'] not in {'gross', 'net', 'unknown'}:
        raise AppError('invalid_settings', '填写薪资币种、金额周期和税前／税后含义。')
    if salary['amount_min'] is not None and salary['amount_max'] is not None and salary['amount_min'] > salary['amount_max']:
        raise AppError('invalid_settings', '薪资下限不能高于上限。')
    return salary


def effective(current):
    """Normalize old raw settings without letting new defaults hide old cities."""
    result = dict(copy.deepcopy(DEFAULTS), **copy.deepcopy(current))
    if 'allowed_cities' in current:
        result['cities'] = copy.deepcopy(current['allowed_cities'])
    elif 'cities' in current:
        result['allowed_cities'] = copy.deepcopy(current['cities'])
    return result


def validate(incoming, current):
    """Merge only supplied keys; preserve false, zero, null and unknown facts."""
    from .app import AppError, _text
    if not isinstance(incoming, dict) or set(incoming) - set(DEFINITIONS):
        raise AppError('invalid_settings', 'Preferences contain unknown fields.')
    result = effective(current)
    for key, value in incoming.items():
        field = DEFINITIONS[key]
        typ = field['value_type']
        if key == 'salary':
            result[key] = _salary(value, result.get(key))
        elif key == 'earliest_start_date':
            result[key] = _date(value)
        elif typ == 'boolean':
            if value is not None and not isinstance(value, bool):
                raise AppError('invalid_settings', '是否接受其他城市须为是、否或未知。')
            result[key] = value
        elif typ == 'string_array_array':
            if not isinstance(value, list) or len(value) > field['max_items']:
                raise AppError('invalid_settings', '城市优先级须为有序的城市列表。')
            tiers, seen = [], set()
            for tier in value:
                cities = _list(tier, field['tier_max_items'])
                if not cities or any(city in seen for city in cities):
                    raise AppError('invalid_settings', '城市优先级不能含空层级或跨层级重复城市。')
                seen.update(cities)
                tiers.append(cities)
            result[key] = tiers
        elif typ == 'string_array':
            values = _list(value, field['max_items'])
            if key == 'recruitment_types' and (not values or any(item not in field['options'] for item in values)):
                raise AppError('invalid_settings', 'Select at least one supported recruitment type.')
            result[key] = values
        else:
            result[key] = _text(value, field['label'], field['max_length'])
    if 'cities' in incoming and 'allowed_cities' in incoming and result['cities'] != result['allowed_cities']:
        raise AppError('invalid_settings', '原有城市列表与可接受城市不一致，请填写同一份城市列表。')
    if 'cities' in incoming:
        result['allowed_cities'] = copy.deepcopy(result['cities'])
    elif 'allowed_cities' in incoming:
        result['cities'] = copy.deepcopy(result['allowed_cities'])
    return result


INTENTION_LABELS = {
    'preferred_cities': 'allowed_cities', 'preferred_city_tiers': 'city_tiers',
    'allowed_cities': 'allowed_cities', 'other_cities_allowed': 'other_cities_allowed',
    'earliest_start_month': 'earliest_start_date', 'earliest_start_date': 'earliest_start_date',
    'expected_annual_salary': 'salary', 'primary_directions': 'primary_directions',
    'exploratory_directions': 'exploratory_directions', 'excluded_directions': 'excluded_directions',
}
TARGETS = {
    **{key: 'intention' for key in INTENTION_LABELS},
    'city_policy': 'intention', 'research_directions': 'intention', 'initial_excluded_role_focus': 'intention',
    'expected_graduation_month': 'education',
    'company_specific_graduation_policy': 'company_answer', 'application_specific_city_exceptions': 'company_answer',
    'site_date_placeholders_tencent': 'company_answer', 'excluded_research': 'resume_selection',
    'application_preparation_policy': 'historical_instruction', 'execution_priority': 'historical_instruction',
    'fact_source_priority': 'historical_instruction', 'developer_note': 'historical_instruction',
    'development_policy': 'historical_instruction', 'profile_editing_notes': 'historical_instruction',
}
# These labels describe source/development bookkeeping, never an authorization.
ARCHIVABLE_INSTRUCTIONS = {'fact_source_priority', 'developer_note', 'development_policy', 'profile_editing_notes'}


def _legacy_records(app, db):
    return [app._record(row) for row in db.execute("SELECT * FROM records WHERE user_id=? AND kind='preference' AND archived=0 ORDER BY created_at,id", (app.user_id,))]


def _education_records(app, db):
    return [app._record(row) for row in db.execute("SELECT * FROM records WHERE user_id=? AND kind='education' AND archived=0 ORDER BY created_at,id", (app.user_id,))]


def _raw_preferences(app, db):
    row = db.execute('SELECT data FROM settings WHERE user_id=?', (app.user_id,)).fetchone()
    return json.loads(row['data']).get('preferences', {}) if row else {}


def _digest(app, records, settings, education, raw_preferences):
    payload = {'purpose': 'legacy-preferences-v1', 'user_id': app.user_id, 'records': records, 'settings': settings, 'education': education, 'raw_preferences': raw_preferences}
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _decode(raw):
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _proposal(record):
    data = record['data']
    label = re.split(r'[;；]', data.get('notes') or '', maxsplit=1)[0].strip()
    target = TARGETS.get(label, 'review')
    item = {'id': record['id'], 'revision': record['revision'], 'source': record['source'], 'scope': record['scope'],
            'status': record['status'], 'target': target, 'label': label, 'original_data': copy.deepcopy(data),
            'proposed_fields': {}, 'issues': [], 'action': 'review'}
    if label in ARCHIVABLE_INSTRUCTIONS:
        item['action'] = 'archive_instruction'
        return item
    if target == 'intention':
        key = INTENTION_LABELS.get(label)
        if key is None:
            item['issues'].append('旧值需拆分或解释，先保留待核对。')
        elif record['status'] != 'confirmed' or record['scope'] != 'global':
            item['issues'].append('旧意向未确认或适用范围不同，不自动启用。')
        else:
            candidate = _decode(data.get('value'))
            if key == 'salary':
                # Only a fully explicit typed object is unambiguous.
                if not isinstance(candidate, dict) or candidate.get('period') != 'annual':
                    item['issues'].append('旧薪资须明确金额、币种和年薪含义，不把期望值推断为上下限。')
            if not item['issues']:
                from .app import AppError
                try:
                    normalized = validate({key: candidate}, DEFAULTS)[key]
                    if normalized == DEFAULTS[key]:
                        item['issues'].append('旧值未提供额外意向，保留待核对。')
                    else:
                        item['proposed_fields'][key] = normalized
                except AppError:
                    item['issues'].append('旧值格式不能直接转换为当前意向字段。')
    elif target == 'education':
        item['issues'].append('毕业日期须对应具体教育经历，预计日期不能代替实际毕业。')
    elif target == 'company_answer':
        item['issues'].append('公司答案需核对公司、字段及适用条件后单独保存。')
    elif target == 'resume_selection':
        item['issues'].append('简历选择需绑定具体资料和方案，不扩大为全局排除方向。')
    elif target == 'historical_instruction':
        item['issues'].append('历史执行指令不自动恢复为当前任务授权。')
    else:
        item['issues'].append('未识别为可安全迁移的字段，保留待核对。')
    return item


def _preview(app, records, settings, education, raw_preferences):
    items = [_proposal(record) for record in records]
    current = effective(raw_preferences)
    candidates = {}
    for item in items:
        for key, value in item['proposed_fields'].items():
            candidates.setdefault(key, []).append((item, value))
    proposed, conflicts = {}, []
    for key, values in candidates.items():
        first = values[0][1]
        # Both city names are aliases of one current list.
        specified = key in raw_preferences or (key in {'cities', 'allowed_cities'} and bool({'cities', 'allowed_cities'} & set(raw_preferences)))
        if any(value != first for _, value in values) or (specified and current[key] != first):
            conflicts.append({'field': key, 'current_value': copy.deepcopy(current[key]), 'record_ids': [item['id'] for item, _ in values]})
            for item, _ in values:
                item['issues'].append('当前值或其他旧记录不同，不自动覆盖。')
            continue
        if not specified:
            proposed[key] = first
        for item, _ in values:
            item['action'] = 'migrate' if key in proposed else 'archive_duplicate'
    summary = {target: sum(item['target'] == target for item in items) for target in CATALOG['legacy_targets']}
    return {'user_id': app.user_id, 'mode': 'read_only_preview', 'items': items, 'total': len(items),
            'preview_digest': _digest(app, records, settings, education, raw_preferences), 'settings_revision': settings['_revision'],
            'proposed_changes': proposed, 'conflicts': conflicts, 'summary': summary,
            'review_count': sum(item['action'] == 'review' for item in items)}


def execute(app, operation, args, actor):
    from .app import AppError, _integer
    if operation == 'intention.schema':
        if args:
            raise AppError('invalid_request', 'Intention schema does not accept arguments.')
        return copy.deepcopy(CATALOG)
    if operation == 'intention.get':
        if args:
            raise AppError('invalid_request', 'Intention read does not accept arguments.')
        with app._connect() as db:
            db.execute('BEGIN')
            settings = app._settings(db)
            raw_preferences = _raw_preferences(app, db)
            count = len(_legacy_records(app, db))
        return {'user_id': app.user_id, 'revision': settings['_revision'], 'data': effective(raw_preferences),
                'authority': 'settings.preferences', 'legacy_count': count, 'legacy_records_pending_review': count,
                'legacy_notice': '旧记录通过整理预览核对；历史执行指令不会自动成为当前授权。'}
    if operation == 'intention.patch':
        if actor != 'user':
            raise AppError('user_action_required', 'Only the applicant can change job-search preferences.', 403)
        if set(args) != {'revision', 'changes'} or not isinstance(args['changes'], dict) or not args['changes']:
            raise AppError('invalid_request', 'Intention patch requires revision and changed fields.')
        return app.execute('settings.save', {'revision': args['revision'], 'settings': {'preferences': args['changes']}}, actor)
    if operation == 'legacy.preview':
        if set(args) - {'limit', 'offset'}:
            raise AppError('invalid_request', 'Legacy preview accepts limit and offset.')
        limit, offset = _integer(args.get('limit', 20), 'Limit', 1, 100), _integer(args.get('offset', 0), 'Offset')
        with app._connect() as db:
            db.execute('BEGIN')
            result = _preview(app, _legacy_records(app, db), app._settings(db), _education_records(app, db), _raw_preferences(app, db))
        result['items'] = result['items'][offset:offset + limit]
        result.update(offset=offset, limit=limit, next_offset=offset + len(result['items']) if offset + len(result['items']) < result['total'] else None)
        return result
    if operation == 'legacy.apply':
        return _apply(app, args, actor)
    raise AppError('unknown_operation', 'Unknown intention operation.', 404)


def _apply(app, args, actor):
    from .app import AppError, _json, now
    if actor != 'user':
        raise AppError('user_action_required', '请在网页审阅旧资料整理预览后执行。', 403)
    if set(args) != {'preview_digest'} or not isinstance(args['preview_digest'], str) or not re.fullmatch('[0-9a-f]{64}', args['preview_digest']):
        raise AppError('invalid_request', 'Legacy consolidation requires the reviewed preview digest.')
    with app._connect() as db:
        db.execute('BEGIN IMMEDIATE')
        prior = db.execute('SELECT result_json FROM profile_migrations WHERE user_id=? AND preview_digest=?', (app.user_id, args['preview_digest'])).fetchone()
        if prior:
            return dict(json.loads(prior['result_json']), already_applied=True)
        records, settings, education = _legacy_records(app, db), app._settings(db), _education_records(app, db)
        preview = _preview(app, records, settings, education, _raw_preferences(app, db))
        if preview['preview_digest'] != args['preview_digest']:
            raise AppError('revision_conflict', '旧资料或求职意向在预览后已修改，请重新查看。', 409)
        changes = preview['proposed_changes']
        revision = settings.pop('_revision')
        if changes:
            settings['preferences'] = validate(changes, settings['preferences'])
            db.execute('INSERT INTO settings(user_id,data,revision) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET data=excluded.data,revision=excluded.revision', (app.user_id, _json(settings), revision + 1))
        result = {'migration_id': uuid.uuid4().hex, 'applied_fields': sorted(changes), 'archived_count': 0,
                  'review_count': preview['review_count'], 'settings_revision': revision + bool(changes),
                  'archived_ids': [], 'conflicts': preview['conflicts']}
        by_id = {record['id']: record for record in records}
        for item in preview['items']:
            if item['action'] not in {'migrate', 'archive_duplicate', 'archive_instruction'}:
                continue
            before = by_id[item['id']]
            db.execute('UPDATE records SET archived=1,revision=revision+1,updated_at=? WHERE id=? AND user_id=?', (now(), before['id'], app.user_id))
            after = app._get_record(db, before['id'])
            app._audit(db, before, after, 'legacy.consolidate:' + item['action'], actor)
            result['archived_count'] += 1
            result['archived_ids'].append(before['id'])
        db.execute('INSERT INTO profile_migrations VALUES(?,?,?,?,?,?)', (result['migration_id'], app.user_id, args['preview_digest'], actor, now(), _json(result)))
        return result
