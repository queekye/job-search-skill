"""Shared resume selections and scoped company answers; never submits a form."""
from __future__ import annotations
import copy
from datetime import datetime, timezone
import calendar
from datetime import date
import hashlib
import json

SECTIONS = {'summary': '个人简介', 'education': '教育经历', 'projects': '项目经历', 'research': '研究经历', 'publications': '论文', 'employment': '工作经历', 'skills': '技能'}
SELECTIONS = {'project_ids': 'project', 'research_ids': 'research', 'publication_ids': 'publication', 'employment_ids': 'work', 'skill_ids': 'skill'}
KINDS = {'resume': 'resume_variant', 'answer': 'company_answer', 'decision': 'company_decision'}


def remap_references(kind, data, id_map):
    from .app import AppError
    result = copy.deepcopy(data)
    def mapped(identifier):
        if identifier not in id_map:
            raise AppError('invalid_profile', '简历方案或公司答案引用了未导入的资料。')
        return id_map[identifier]
    if kind == 'resume_variant':
        for key in SELECTIONS:
            if result.get(key) is not None:
                result[key] = [mapped(identifier) for identifier in result[key]]
        if result.get('text_overrides') is not None:
            result['text_overrides'] = {mapped(key): value for key, value in result['text_overrides'].items()}
        if result.get('source_fingerprints') is not None:
            result['source_fingerprints'] = {mapped(key): value for key, value in result['source_fingerprints'].items()}
    elif kind == 'company_answer' and result.get('linked_record_id'):
        result['linked_record_id'] = mapped(result['linked_record_id'])
    return result


def validate(kind, data):
    from .app import AppError, _text
    from .resume_content import ensure_resume_content
    if kind == 'company_decision':
        for key in ('company_id', 'company_name', 'decision'):
            _text(data.get(key), 'Company decision field', 500, False)
    elif kind == 'company_answer':
        for key in ('company_id', 'question_key', 'label', 'value_type'):
            _text(data.get(key), 'Company answer field', 500, False)
        value = data.get('value')
        if value is not None:
            expected = {'string': str, 'boolean': bool, 'number': (int, float), 'array': list, 'object': dict}[data['value_type']]
            if not isinstance(value, expected) or data['value_type'] == 'number' and isinstance(value, bool):
                raise AppError('invalid_field', '答案的值与所选类型不一致。')
    elif kind == 'resume_variant':
        _text(data.get('name'), 'Resume name', 500, False)
        ensure_resume_content('basic', {'summary': data.get('summary_text')})
        for key in (*SELECTIONS, 'section_order'):
            values = data.get(key) or []
            if len(values) != len(set(values)):
                raise AppError('invalid_field', '简历方案不能重复选择同一条目或章节。')
        selected = {identifier for key in SELECTIONS for identifier in data.get(key) or []}
        if len(selected) > 100:
            raise AppError('invalid_selection', '单份方案最多选用 100 条资料。')
        overrides = data.get('text_overrides') or {}
        if set(overrides) - selected:
            raise AppError('invalid_field', '方案文案只能对应已选择的资料。')
        for values in overrides.values():
            if not isinstance(values, dict) or not values or set(values) - {'description', 'responsibilities', 'results', 'abstract'}:
                raise AppError('invalid_field', '方案文案包含不支持的字段。')
            for value in values.values():
                if value is not None:
                    _text(value, 'Resume wording', 12000)
            ensure_resume_content('experience', values)
        import re
        for identifier, digest in (data.get('source_fingerprints') or {}).items():
            _text(identifier, 'Source reference', 100, False)
            if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                raise AppError('invalid_field', '方案文案引用版本无效。')


def fingerprint(record):
    return hashlib.sha256(json.dumps(record['data'], ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def source_review(app, db, data):
    from .app import AppError
    identifiers = {identifier for key in SELECTIONS for identifier in data.get(key) or []}
    if data.get('summary_text'):
        basics = list(db.execute("SELECT * FROM records WHERE user_id=? AND kind='basic' AND archived=0", (app.user_id,)))
        if len(basics) == 1: identifiers.add(basics[0]['id'])
    current, sources = {}, []
    for identifier in sorted(identifiers):
        try:
            record = app._get_record(db, identifier)
            if not record['archived']:
                current[identifier] = fingerprint(record)
                sources.append(record)
        except AppError:
            pass
    dependent = set(data.get('text_overrides') or {})
    if data.get('summary_text'):
        dependent |= {row['id'] for row in db.execute("SELECT id FROM records WHERE user_id=? AND kind='basic' AND archived=0", (app.user_id,))}
    saved = data.get('source_fingerprints') or {}
    stale = sorted(identifier for identifier in dependent if current.get(identifier) != saved.get(identifier) or identifier not in current)
    return {'needs_review': bool(stale), 'stale_ids': stale, 'current_fingerprints': current,
            'current_sources': sources}


def prepare_sources(app, db, data, before, args, actor, action):
    from .app import AppError
    review = source_review(app, db, data)
    if action == 'import': return
    provided = args.get('reviewed_sources')
    if provided is not None:
        if actor != 'user': raise AppError('user_action_required', '文案引用的重新确认由本人执行。', 403)
        if provided != review['current_fingerprints']:
            raise AppError('revision_conflict', '原资料在审阅后已更新，请重新核对方案文案。', 409)
        data['source_fingerprints'] = review['current_fingerprints']
    elif before is None:
        data['source_fingerprints'] = review['current_fingerprints']
    else:
        saved = {key: value for key, value in (before['data'].get('source_fingerprints') or {}).items() if key in review['current_fingerprints']}
        for identifier, digest in review['current_fingerprints'].items():
            if identifier not in saved: saved[identifier] = digest
        data['source_fingerprints'] = saved


def answer_review(app, db, data):
    from .app import AppError
    linked = data.get('linked_record_id')
    if not linked: return {'needs_review': False, 'stale_ids': [], 'current_fingerprints': {}, 'current_sources': []}
    try:
        record = app._get_record(db, linked)
        if record['archived']: raise AppError('missing_source', 'Source is unavailable.')
        digest = fingerprint(record)
        stale = digest != data.get('source_fingerprint')
        return {'needs_review': stale, 'stale_ids': [linked] if stale else [], 'current_fingerprints': {linked: digest}, 'current_sources': [record]}
    except AppError:
        return {'needs_review': True, 'stale_ids': [linked], 'current_fingerprints': {}, 'current_sources': []}


def prepare_answer_source(app, db, data, before, args, actor, action):
    from .app import AppError
    if action == 'import': return
    linked = data.get('linked_record_id')
    if not linked:
        data.pop('source_fingerprint', None)
        return
    review = answer_review(app, db, data)
    provided = args.get('reviewed_sources')
    if provided is not None:
        if actor != 'user': raise AppError('user_action_required', '公司答案引用的重新确认由本人执行。', 403)
        if provided != review['current_fingerprints'] or not provided:
            raise AppError('revision_conflict', '关联资料在审阅后已更新，请重新核对答案。', 409)
        data['source_fingerprint'] = provided[linked]
    elif not before or before['data'].get('linked_record_id') != linked:
        data['source_fingerprint'] = review['current_fingerprints'][linked]
    else:
        data['source_fingerprint'] = before['data'].get('source_fingerprint')


def check_source_versions(app, db, args):
    from .app import AppError, _integer
    supplied = args.get('source_versions')
    if supplied is None: return
    if not isinstance(supplied, dict) or len(supplied) > 200:
        raise AppError('invalid_request', 'Source versions must be a bounded map.')
    for identifier, revision in supplied.items():
        record = app._get_record(db, identifier)
        if record['archived'] or record['revision'] != _integer(revision, 'Source revision', 1):
            raise AppError('revision_conflict', '原资料在编辑期间已更新，请重新读取后保存。', 409)


def _period_end(value):
    if len(value) == 4: return date(int(value), 12, 31)
    if len(value) == 7:
        year, month = map(int, value.split('-'))
        return date(year, month, calendar.monthrange(year, month)[1])
    return date.fromisoformat(value)


def check_references(app, db, kind, data):
    from .app import AppError
    from .profile import fields
    if kind == 'company_decision':
        return
    if kind == 'resume_variant':
        for key, category in SELECTIONS.items():
            for identifier in data.get(key) or []:
                record = app._get_record(db, identifier)
                if record['archived'] or record['kind'] != 'experience' or record['data'].get('category') != category:
                    raise AppError('invalid_selection', '选择的资料已归档、类别不匹配或不属于当前账号。', 409)
    elif kind == 'company_answer' and data.get('linked_record_id'):
        record = app._get_record(db, data['linked_record_id'])
        if record['archived'] or record['kind'] in {'application', 'company_answer', 'resume_variant', 'company_decision', 'preference'}:
            raise AppError('invalid_selection', '公司答案须关联当前账号的有效个人资料。', 409)
        if data.get('linked_field') and data['linked_field'] not in {f['key'] for f in fields(record['kind'])}:
            raise AppError('invalid_selection', '公司答案的关联字段不存在。')


def check_fact_change(app, db, record, data=None, *, archiving=False):
    from .app import AppError
    if record['kind'] in KINDS.values(): return
    for row in db.execute("SELECT * FROM records WHERE user_id=? AND kind IN ('resume_variant','company_answer') AND archived=0", (app.user_id,)):
        dependent = app._record(row)
        if dependent['kind'] == 'company_answer':
            used = dependent['data'].get('linked_record_id') == record['id']
            wrong = False
        else:
            categories = [category for key, category in SELECTIONS.items() if record['id'] in (dependent['data'].get(key) or [])]
            used = bool(categories)
            if record['kind'] == 'basic' and dependent['data'].get('summary_text') and record['id'] in (dependent['data'].get('source_fingerprints') or {}): used = True
            wrong = used and data is not None and any(data.get('category') != category for category in categories)
        if used and (archiving or wrong):
            raise AppError('referenced_record', '资料仍被简历方案或公司答案引用，请先移除选用或归档相关方案。', 409)


def execute(app, operation, args, actor):
    from .app import AppError, _integer, _text
    namespace, _, action = operation.partition('.')
    if namespace not in KINDS or action not in {'list', 'get', 'save', 'archive', 'compose'} or action == 'compose' and namespace != 'resume':
        raise AppError('unknown_operation', 'Unknown resume operation.', 404)
    kind = KINDS[namespace]
    if namespace == 'decision' and action in {'save', 'archive'} and actor != 'user':
        raise AppError('user_action_required', '公司暂停、排除及恢复由本人决定。', 403)
    if action == 'list':
        if set(args) - {'limit', 'offset', 'company_id'} or namespace == 'resume' and 'company_id' in args:
            raise AppError('invalid_request', 'Unsupported list arguments.')
        limit, offset = _integer(args.get('limit', 20), 'Limit', 1, 100), _integer(args.get('offset', 0), 'Offset')
        company = args.get('company_id')
        if company is not None:
            _text(company, 'Company id', 500, False)
        with app._connect() as db:
            rows = [app._record(row) for row in db.execute('SELECT * FROM records WHERE user_id=? AND kind=? AND archived=0 ORDER BY created_at,id', (app.user_id, kind))]
        if company is not None:
            rows = [r for r in rows if r['data'].get('company_id') == company]
        items = rows[offset:offset + limit]
        return {'user_id': app.user_id, 'items': items, 'total': len(rows), 'next_offset': offset + len(items) if offset + len(items) < len(rows) else None}
    if action in {'get', 'compose'}:
        if set(args) - ({'id', 'company_id', 'recruitment_scope'} if action == 'compose' else {'id'}) or 'id' not in args:
            raise AppError('invalid_request', 'Read requires the resume or answer id.')
        with app._connect() as db:
            db.execute('BEGIN')
            record = app._get_record(db, args['id'])
            if record['kind'] != kind or record['archived']:
                raise AppError('not_found', 'Resume or answer was not found.', 404)
            return compose(app, db, record, args) if action == 'compose' else dict(record, source_review=source_review(app, db, record['data']) if namespace == 'resume' else answer_review(app, db, record['data'])) if namespace != 'decision' else record
    if set(args) - ({'id', 'revision'} if action == 'archive' else {'id', 'revision', 'data', 'source', 'scope', 'status', 'reviewed_sources', 'source_versions'}):
        raise AppError('invalid_request', 'Unsupported resume edit arguments.')
    current = None
    if args.get('id'):
        with app._connect() as db:
            current = app._get_record(db, args['id'])
        if current['kind'] != kind:
            raise AppError('invalid_kind', 'This operation cannot edit another record kind.')
    if action == 'archive':
        if not current:
            raise AppError('invalid_request', 'Archive requires id and revision.')
        return app.execute('record.archive', args, actor)
    data = args.get('data', {})
    if not isinstance(data, dict) or not data and not current:
        raise AppError('invalid_request', 'Create a named resume or company answer with data.')
    save = dict(args, kind=kind, data={**(current['data'] if current else {}), **data})
    if current and actor == 'user' and 'source' not in save: save['source'] = current['source']
    if namespace == 'decision': save['status'] = 'confirmed'
    return app.execute('record.save', save, actor)


def compose(app, db, variant, args):
    from .app import AppError, _text
    from .resume_content import ensure_resume_content
    from . import family
    company = args.get('company_id')
    scope = args.get('recruitment_scope', 'general')
    for value in (company, scope):
        if value is not None:
            _text(value, 'Resume scope', 500, False)
    issues, pins = [], {}
    def issue(code, message, record_id=None, field=None):
        value = {'code': code, 'message': message}
        if record_id: value['record_id'] = record_id
        if field: value['field'] = field
        issues.append(value)
    if company:
        if company_excluded(app, db, company):
            issue('company_excluded', '当前求职意向已排除此公司，请先核对本人决定。')
        for decision in company_decisions(app, db, company):
            if decision['data']['decision'] in {'pause', 'exclude'}:
                issue('company_paused', '本人已暂停或排除该公司的推进，请先核对公司决定。', decision['id'])
    def bind(record):
        pins[record['id']] = {'id': record['id'], 'revision': record['revision'], 'kind': record['kind']}
        if record['status'] != 'confirmed':
            issue('unconfirmed_record', '资料或方案仍待确认。', record['id'])
        if record['scope'] not in {'global', 'company:' + company if company else 'global', company}:
            issue('scope_mismatch', '资料适用范围与目标不一致。', record['id'])
        ensure_resume_content(record['kind'], record['data'])
        result = copy.deepcopy(record)
        result['title'] = record['data'].get('title') or record['data'].get('school') or record['data'].get('name') or '资料'
        return result
    def applicable(record):
        return record['scope'] in {'global', company, 'company:' + company if company else 'global'}
    bind(variant)
    target = variant['data'].get('target_scope')
    if target and target.startswith('company:') and target != ('company:' + company if company else 'global'):
        issue('variant_scope_mismatch', '方案的目标范围与当前公司不一致。', variant['id'])
    review = source_review(app, db, variant['data'])
    for identifier in review['stale_ids']:
        issue('stale_wording', '原资料已更新，方案中的独立文案需要重新核对。', identifier)
    basics = [app._record(row) for row in db.execute("SELECT * FROM records WHERE user_id=? AND kind='basic' AND archived=0 ORDER BY created_at,id", (app.user_id,))]
    basics = [record for record in basics if applicable(record)]
    basic = bind(basics[0]) if len(basics) == 1 else None
    if len(basics) != 1:
        issue('basic_selection_required', '基础资料缺失或存在多个版本，需要核对。')
    grouped = {key: [] for key in SECTIONS}
    summary = variant['data'].get('summary_text')
    if summary is None and basic:
        summary = basic['data'].get('summary')
    if summary:
        grouped['summary'] = [dict(basic or bind(variant), data={'summary': summary}, title='个人简介')]
    for row in db.execute("SELECT * FROM records WHERE user_id=? AND kind='education' AND archived=0 ORDER BY created_at,id", (app.user_id,)):
        source = app._record(row)
        if not applicable(source):
            issue('scope_mismatch', '教育资料不适用于当前目标。', source['id'])
            continue
        record = bind(source)
        if record['data'].get('education_status') == 'studying' and record['data'].get('expected_graduation_date') and _period_end(record['data']['expected_graduation_date']) < datetime.now(timezone.utc).date():
            issue('education_review', '预计毕业时间已过，实际毕业及拿证状态仍需核对。', record['id'], 'graduation_date')
        grouped['education'].append(record)
    for key, category in SELECTIONS.items():
        section = {'project_ids': 'projects', 'research_ids': 'research', 'publication_ids': 'publications', 'employment_ids': 'employment', 'skill_ids': 'skills'}[key]
        for identifier in variant['data'].get(key) or []:
            try:
                original = app._get_record(db, identifier)
                if original['archived'] or original['kind'] != 'experience' or original['data'].get('category') != category:
                    raise AppError('invalid_selection', 'Selected record is no longer available.')
            except AppError:
                issue('missing_record', '选用条目已归档、改变类别或不可用。', identifier)
                continue
            if not applicable(original):
                issue('scope_mismatch', '选用资料不适用于当前目标，已从正文排除。', identifier)
                continue
            record = bind(original)
            override = (variant['data'].get('text_overrides') or {}).get(identifier)
            if override:
                record['data'].update(override)
                record['wording_override'] = True
                record['original_data'] = original['data']
            grouped[section].append(record)
    if variant['data'].get('document_id'):
        if not db.execute('SELECT 1 FROM documents WHERE id=? AND user_id=? AND archived=0', (variant['data']['document_id'], app.user_id)).fetchone():
            issue('document_missing', '参考简历材料不在当前账号的有效附件中。')
    answers = []
    if company:
        for row in db.execute("SELECT * FROM records WHERE user_id=? AND kind='company_answer' AND archived=0 ORDER BY created_at,id", (app.user_id,)):
            answer = app._record(row)
            if answer['data'].get('company_id') != company or (answer['data'].get('recruitment_scope') or 'general') != scope:
                continue
            item = bind(answer)
            usable = answer['status'] == 'confirmed' and answer['data'].get('value') is not None and answer['scope'] in {'global', company, 'company:' + company}
            linked = answer['data'].get('linked_record_id')
            if linked:
                try:
                    source = app._get_record(db, linked)
                    field = answer['data'].get('linked_field')
                    if source['archived'] or source['status'] != 'confirmed' or field and field not in source['data'] or source['scope'] not in {'global', company, 'company:' + company}: raise AppError('invalid_selection', 'Linked source unavailable.')
                    bind(source)
                    if fingerprint(source) != answer['data'].get('source_fingerprint'):
                        issue('answer_source_changed', '公司答案关联的原资料已更新，需要重新核对。', answer['id'])
                        usable = False
                except AppError:
                    issue('answer_source_unavailable', '公司答案引用的资料需要重新核对。', answer['id'])
                    usable = False
            expires = answer['data'].get('expires_at')
            if expires and _period_end(expires) < datetime.now(timezone.utc).date():
                issue('answer_expired', '公司答案已到有效期，需要重新核对。', answer['id'])
                item['usable'] = False
            else:
                item['usable'] = usable
            answers.append(item)
    by_question = {}
    for item in answers:
        if item['usable']: by_question.setdefault(item['data']['question_key'], []).append(item)
    for question, items in by_question.items():
        if len({json.dumps(item['data']['value'], ensure_ascii=False, sort_keys=True) for item in items}) > 1:
            issue('answer_conflict', '同一公司问题存在不同答案，需要本人选择。', field=question)
            for item in items: item['usable'] = False
    order = variant['data'].get('section_order')
    order = list(SECTIONS) if order is None else order
    sections = [{'key': key, 'label': SECTIONS[key], 'items': grouped[key]} for key in order if grouped[key]]
    family_result = family.evaluate(app, {'company': company} if company else {})
    if company and family_result['declaration'] == 'none' and family.evaluate(app, {})['declaration'] != 'none':
        family_result.update(declaration='review', requires_review=True, basis='company_employer_names_required')
        issue('family_scope_review', '已填写亲属任职，请按目标公司的实际单位名称核对。')
    company_form = None
    if company:
        from .forms import _package, _source_input
        from .form_contract import validate_input
        package = None
        try:
            package = _package(app, company)
            selection = {'basic': [r['id'] for r in basics], 'education': [r['id'] for r in grouped['education']],
                         'experience': [r['id'] for key in ('projects', 'research', 'publications', 'employment', 'skills') for r in grouped[key]]}
            inputs, form_pins, source_issues = _source_input(app, package, selection)
            validation = validate_input(package['form']['input_schema'], inputs)
            company_form = {'version': package['version'], 'digest': package['digest'], 'supported_scope': package['form'].get('scope', {}),
                            'missing': validation['missing'], 'invalid': validation['invalid'], 'sensitive': validation['sensitive'],
                            'source_issues': source_issues, 'ready': not validation['missing'] and not validation['invalid'] and not source_issues,
                            'scope_complete': False}
            for pin in form_pins: pins[pin['id']] = pin
        except AppError:
            if package is None:
                issue('company_form_unavailable', '该公司尚无可用的正式字段契约；可继续审阅通用简历内容。')
            else:
                issue('company_form_needs_review', '公司字段检查尚需核对个人资料的确认状态和适用范围。')
    result = {'user_id': app.user_id, 'mode': 'read_only', 'variant': {'id': variant['id'], 'revision': variant['revision'], 'status': variant['status'], 'name': variant['data']['name']}, 'company_id': company, 'sections': sections, 'issues': issues, 'pins': list(pins.values()), 'answers': answers, 'family': family_result, 'basic': basic, 'company_form': company_form}
    result['markdown'] = markdown(result)
    return result


def company_decisions(app, db, company=None, *, confirmed_only=True):
    query = "SELECT * FROM records WHERE user_id=? AND kind='company_decision' AND archived=0"
    if confirmed_only: query += " AND status='confirmed'"
    rows = [app._record(row) for row in db.execute(query + ' ORDER BY created_at,id', (app.user_id,))]
    if company is None: return rows
    aliases = company_aliases(app, company)
    return [row for row in rows if aliases & {row['data'].get('company_id'), row['data'].get('company_name')}]


def company_aliases(app, company):
    aliases = {company}
    try:
        from . import packages
        files, _ = packages._select(app, company)
        manifest, _ = packages.validate_files(files)
        aliases.add(manifest['name'])
    except (KeyError, ValueError, OSError):
        pass
    except Exception as error:
        from .app import AppError
        if not isinstance(error, AppError): raise
    return aliases


def company_excluded(app, db, company):
    from .recommendations import activity_company_key
    excluded = {activity_company_key(value) for value in app._settings(db)['preferences']['excluded_companies']}
    return bool(excluded & {activity_company_key(value) for value in company_aliases(app, company)})


def guard_company(app, company):
    from .app import AppError
    with app._connect() as db:
        stopped = [record for record in company_decisions(app, db, company) if record['data']['decision'] in {'pause', 'exclude'}]
        excluded = company_excluded(app, db, company)
    if stopped or excluded:
        raise AppError('company_paused', '本人已暂停或排除此公司，请先在求职意向中恢复评估。', 409)


def check_decision_unique(app, db, data, record_id=None):
    from .app import AppError
    duplicates = [record for record in company_decisions(app, db, data['company_id'], confirmed_only=False) if record['id'] != record_id]
    if duplicates:
        raise AppError('decision_exists', '该公司已有当前决定，请修改已有决定。', 409)


def markdown(result):
    """Export only resume content; IDs, provenance, defaults and review stay out."""
    def text(value):
        return str(value).replace('<', '&lt;').replace('>', '&gt;')
    basic = (result.get('basic') or {}).get('data', {})
    lines = ['# ' + text(basic.get('name') or result['variant']['name'])]
    contacts = [basic.get(key) for key in ('email', 'phone', 'location') if basic.get(key)]
    if contacts: lines += [' | '.join(text(v) for v in contacts)]
    for section in result['sections']:
        lines += ['', '## ' + section['label']]
        for item in section['items']:
            data = item['data']
            if section['key'] != 'summary': lines += ['', '### ' + text(item['title'])]
            if section['key'] == 'education':
                lines += [' · '.join(text(data[k]) for k in ('degree', 'major', 'department') if data.get(k))]
                if data.get('start_date'): lines += ['入学：' + text(data['start_date'])]
                for key, label in [('graduation_date', '毕业'), ('expected_graduation_date', '预计毕业'), ('degree_awarded_date', '学位授予')]:
                    if data.get(key): lines += [label + '：' + text(data[key])]
            elif section['key'] == 'publications':
                lines += ['，'.join(text(v) for v in data.get('authors') or [])]
                lines += [' · '.join(text(data[k]) for k in ('venue', 'track', 'year', 'volume', 'issue', 'article_number', 'pages', 'doi') if data.get(k) is not None)]
            else:
                if data.get('role'): lines += [text(data['role'])]
                if section['key'] in {'projects', 'research', 'employment'}:
                    if data.get('organization'): lines += [text(data['organization'])]
                    if data.get('start_date'): lines += ['开始：' + text(data['start_date'])]
                    if data.get('end_date'): lines += ['结束：' + text(data['end_date'])]
                    if data.get('project_code'): lines += ['项目编号：' + text(data['project_code'])]
                for key in ('summary', 'description', 'responsibilities', 'results', 'abstract'):
                    if data.get(key): lines += ['', text(data[key])]
                if data.get('technologies'): lines += ['技术：' + '、'.join(text(v) for v in data['technologies'])]
    return '\n'.join(lines).strip() + '\n'
