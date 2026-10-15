"""Read-only family declarations derived from the applicant's current records.

An empty declaration is a product default, not a verified website observation.
Company questions use exact supplied employer names, never notes or fuzzy matching.
"""
from __future__ import annotations

import re
import unicodedata


EMPLOYMENT_STATUSES = frozenset({'current', 'former'})
DETAIL_FIELDS = ('name', 'organization', 'department', 'role')
EMPTY_STATUSES = frozenset({'', 'unknown', 'unconfirmed', 'none'})
UNKNOWN_ORGANIZATIONS = frozenset({'unknown', 'unconfirmed', 'tbd', 'n/a', 'na',
                                 'none', 'null', '-', '—', '未知', '不详', '不了解',
                                 '未填写', '未填', '不清楚', '待确认', '待核实', '无'})


def _key(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', value).strip()).casefold()


def has_details(data):
    """A relation label, timestamp or policy note does not assert employment."""
    detail = any(isinstance(data.get(key), str) and data[key].strip() for key in DETAIL_FIELDS)
    status = data.get('employment_status')
    status = status.strip().casefold() if isinstance(status, str) else ''
    return bool(detail or status not in EMPTY_STATUSES)


def evaluate(app, args):
    from .app import AppError, _text

    if not isinstance(args, dict) or set(args) - {'company', 'organizations', 'employment_statuses'}:
        raise AppError('invalid_request', 'Family evaluation accepts company, organizations and employment_statuses only.')
    company = args.get('company')
    if company is not None:
        _text(company, 'Company', 500, False)
    organizations = args.get('organizations', [])
    if not isinstance(organizations, list) or len(organizations) > 100 or (organizations and company is None):
        raise AppError('invalid_request', 'Organization aliases require a company and a list of at most 100 exact names.')
    for organization in organizations:
        _text(organization, 'Organization', 500, False)
    statuses = args.get('employment_statuses', ['current', 'former'])
    if not isinstance(statuses, list) or not statuses or any(not isinstance(status, str) or status not in EMPLOYMENT_STATUSES for status in statuses) or len(set(statuses)) != len(statuses):
        raise AppError('invalid_request', 'Employment statuses must be distinct current or former values.')
    names = {_key(name) for name in ([company] if company else []) + organizations}

    with app._connect() as db:
        db.execute('BEGIN')
        records = [app._record(row) for row in db.execute(
            "SELECT * FROM records WHERE user_id=? AND kind='family' AND archived=0 ORDER BY created_at,id",
            (app.user_id,))]

    matches, unresolved = [], []
    ignored_placeholders, excluded_by_scope, evaluated = 0, 0, 0
    for record in records:
        data = record['data']
        if not has_details(data):
            ignored_placeholders += 1
            continue
        scope = record['scope']
        if company is not None and scope.startswith('company:') and _key(scope[len('company:'):]) not in names:
            excluded_by_scope += 1
            continue
        evaluated += 1
        issues = []
        if scope != 'global' and not scope.startswith('company:'):
            issues.append('unsupported_scope')
        elif company is None and scope != 'global':
            # A company-specific answer cannot become a universal declaration.
            issues.append('company_scope_required')
        if record['status'] != 'confirmed':
            issues.append('record_unconfirmed')
        status = data.get('employment_status')
        status = status.strip().casefold() if isinstance(status, str) else ''
        organization = data.get('organization')
        organization = organization.strip() if isinstance(organization, str) else ''
        if status not in EMPLOYMENT_STATUSES | {'none'}:
            issues.append('employment_status_unknown')
        elif status != 'none' and (not organization or _key(organization) in UNKNOWN_ORGANIZATIONS):
            issues.append('organization_missing')
        if issues:
            unresolved.append({'record': record, 'issues': issues})
        elif status in statuses and (company is None or _key(organization) in names):
            matches.append(record)

    declaration = 'present' if matches else 'review' if unresolved else 'none'
    default = not evaluated
    basis = 'default_no_employment_information' if default else 'matching_employment' if matches else 'incomplete_employment_information' if unresolved else 'no_matching_employment'
    return {'user_id': app.user_id, 'company': company, 'employment_statuses': statuses,
            'declaration': declaration, 'source': 'default' if default else 'records',
            'basis': basis, 'requires_review': bool(unresolved), 'matches': matches,
            'unresolved': unresolved, 'ignored_placeholder_count': ignored_placeholders,
            'excluded_by_scope_count': excluded_by_scope, 'mode': 'read_only'}


def execute(app, operation, args, actor):
    from .app import AppError

    if operation != 'family.evaluate':
        raise AppError('unknown_operation', 'Unknown family operation.', 404)
    return evaluate(app, args)
