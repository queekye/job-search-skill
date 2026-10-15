"""Synthetic tests for typed intentions and reviewed legacy consolidation."""
import copy
from pathlib import Path
import tempfile
import unittest

from service.app import App, AppError
from service import intention


class IntentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-intention-test-')
        self.addCleanup(self.tmp.cleanup)
        self.app = App(data_dir=Path(self.tmp.name) / 'private')

    def save(self, label, value, **metadata):
        import json
        return self.app.execute('record.save', {'kind': 'preference', 'data': {
            'category': 'other', 'value': json.dumps(value, ensure_ascii=False), 'notes': label},
            'status': 'confirmed', **metadata})

    def invoke(self, operation, args=None, actor='user'):
        return self.app.execute(operation, args or {}, actor)

    def test_schema_detached_from_defaults_and_unknown_values_remain_unknown(self):
        schema = self.invoke('intention.schema')
        schema['defaults']['other_cities_allowed'] = False
        self.assertIsNone(self.invoke('intention.schema')['defaults']['other_cities_allowed'])
        read = self.invoke('intention.get')
        self.assertEqual(['campus'], read['data']['recruitment_types'])
        self.assertIsNone(read['data']['salary'])
        self.assertIsNone(read['data']['earliest_start_date'])
        self.assertIsNone(read['data']['other_cities_allowed'])
        self.assertEqual('settings.preferences', read['authority'])

    def test_typed_validate_preserves_omitted_legacy_fields_and_null_false_zero(self):
        current = dict(copy.deepcopy(intention.DEFAULTS), cities=['Old City'], allowed_cities=['Old City'], notes='Original note', roles=['Old Role'])
        updated = intention.validate({'city_tiers': [[' City A ', 'City B'], ['City C']], 'other_cities_allowed': False,
            'salary': {'expected_amount': 0, 'currency': 'CNY', 'period': 'annual'}}, current)
        self.assertEqual([['City A', 'City B'], ['City C']], updated['city_tiers'])
        self.assertFalse(updated['other_cities_allowed'])
        self.assertEqual(0, updated['salary']['expected_amount'])
        self.assertIsNone(updated['salary']['amount_min'])
        self.assertIsNone(updated['salary']['amount_max'])
        self.assertEqual('unknown', updated['salary']['tax_basis'])
        self.assertEqual(['Old City'], updated['cities'])
        self.assertEqual(['Old Role'], updated['roles'])
        self.assertEqual('Original note', updated['notes'])
        self.assertEqual(['Old City'], current['cities'])
        changed = intention.validate({'salary': {'tax_basis': 'net'}, 'other_cities_allowed': None}, updated)
        self.assertEqual(0, changed['salary']['expected_amount'])
        self.assertEqual('CNY', changed['salary']['currency'])
        self.assertIsNone(changed['other_cities_allowed'])
        self.assertIsNone(intention.validate({'salary': None}, changed)['salary'])

    def test_cities_alias_has_one_meaning_preserves_clear_and_old_raw_fallback(self):
        self.assertEqual(['Old City'], intention.effective({'cities': ['Old City']})['allowed_cities'])
        self.assertEqual([], intention.effective({'cities': ['Old City'], 'allowed_cities': []})['cities'])
        old = intention.validate({'cities': ['City A']}, intention.DEFAULTS)
        self.assertEqual(['City A'], old['allowed_cities'])
        new = intention.validate({'allowed_cities': ['City B']}, old)
        self.assertEqual(['City B'], new['cities'])
        cleared = intention.validate({'allowed_cities': []}, new)
        self.assertEqual([], cleared['cities'])
        self.assertEqual([], cleared['allowed_cities'])
        with self.assertRaises(AppError):
            intention.validate({'cities': ['City A'], 'allowed_cities': ['City B']}, intention.DEFAULTS)
        same = intention.validate({'cities': [' City A '], 'allowed_cities': ['City A']}, intention.DEFAULTS)
        self.assertEqual(same['cities'], same['allowed_cities'])

    def test_stored_explicit_empty_and_null_do_not_revive_old_values(self):
        self.invoke('intention.patch', {'revision': 0, 'changes': {'allowed_cities': [], 'earliest_start_date': None, 'city_tiers': []}})
        self.save('allowed_cities', ['Old City'])
        self.save('earliest_start_date', '2027-01')
        self.save('preferred_city_tiers', [['Old City']])
        preview = self.invoke('legacy.preview')
        self.assertEqual({}, preview['proposed_changes'])
        self.assertEqual({'allowed_cities', 'earliest_start_date', 'city_tiers'}, {conflict['field'] for conflict in preview['conflicts']})
        self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']})
        current = self.invoke('intention.get')['data']
        self.assertEqual([], current['allowed_cities'])
        self.assertIsNone(current['earliest_start_date'])
        self.assertEqual(3, self.invoke('intention.get')['legacy_count'])

    def test_old_raw_city_settings_are_effective_before_any_write(self):
        from service.app import _json
        with self.app._connect() as db:
            db.execute('INSERT INTO settings(user_id,data,revision) VALUES(?,?,?)', (self.app.user_id, _json({'preferences': {'cities': ['Old City']}}), 1))
        self.assertEqual(['Old City'], self.invoke('intention.get')['data']['allowed_cities'])
        self.save('preferred_cities', ['Old City'])
        preview = self.invoke('legacy.preview')
        self.assertEqual({}, preview['proposed_changes'])
        self.assertEqual('archive_duplicate', preview['items'][0]['action'])

    def test_invalid_types_lengths_duplicate_tiers_and_nonfinite_salary_rejected(self):
        invalid = [
            {'unknown': 'value'}, {'primary_directions': 'Topic'}, {'primary_directions': [False]},
            {'allowed_cities': ['x'] * 101}, {'primary_directions': ['']}, {'other_cities_allowed': 'false'},
            {'city_tiers': [[], ['City']]}, {'city_tiers': [['City'], ['City']]}, {'city_tiers': ['City']},
            {'city_tiers': [['City']] * 21}, {'recruitment_types': []}, {'recruitment_types': ['unknown']},
            {'notes': None}, {'salary': {'expected_amount': 30}},
            {'salary': {'expected_amount': True, 'currency': 'CNY', 'period': 'annual'}},
            {'salary': {'expected_amount': float('nan'), 'currency': 'CNY', 'period': 'annual'}},
            {'salary': {'expected_amount': float('inf'), 'currency': 'CNY', 'period': 'annual'}},
            {'salary': {'expected_amount': 10**1000, 'currency': 'CNY', 'period': 'annual'}},
            {'salary': {'amount_min': 3, 'amount_max': 2, 'currency': 'CNY', 'period': 'annual'}},
            {'salary': {'currency': 'CNY', 'period': 'weekly'}}, {'salary': {'currency': 'CNY', 'period': []}},
            {'salary': {'currency': 'CNY', 'period': 'annual', 'tax_basis': {}}},
            {'salary': {'currency': 'CNY', 'period': 'annual', 'tax_basis': []}}, {'salary': []},
        ]
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(AppError):
                    intention.validate(value, intention.DEFAULTS)

    def test_partial_dates_preserve_precision_and_reject_impossible_values(self):
        for value in ('2026', '2026-10', '2024-02-29', None):
            self.assertEqual(value, intention.validate({'earliest_start_date': value}, intention.DEFAULTS)['earliest_start_date'])
        for value in ('2026-02-29', '2026-00', '2026-13', '26', '2026-1', '', False, '2026-10-01T00:00Z'):
            with self.subTest(value=value):
                with self.assertRaises(AppError):
                    intention.validate({'earliest_start_date': value}, intention.DEFAULTS)

    def test_settings_patch_has_shared_revision_account_boundary_and_no_ai_write(self):
        before = self.invoke('intention.get')
        changed = self.invoke('intention.patch', {'revision': before['revision'], 'changes': {'primary_directions': ['Synthetic Topic'], 'other_cities_allowed': False}})
        self.assertEqual(changed['_revision'], self.invoke('intention.get')['revision'])
        with self.assertRaises(AppError) as stale:
            self.invoke('intention.patch', {'revision': before['revision'], 'changes': {'primary_directions': []}})
        self.assertEqual('revision_conflict', stale.exception.code)
        with self.assertRaises(AppError) as blocked:
            self.invoke('intention.patch', {'revision': changed['_revision'], 'changes': {'notes': 'Wrong'}}, 'codex')
        self.assertEqual('user_action_required', blocked.exception.code)
        other = self.app.execute('account.create', {'name': 'Synthetic Other'})
        scoped = self.app.for_user(other['id'])
        self.assertEqual([], intention.execute(scoped, 'intention.get', {}, 'user')['data']['primary_directions'])
        self.assertFalse(self.invoke('intention.get')['data']['other_cities_allowed'])

    def test_preview_paginated_read_only_with_safe_classification_and_no_authorization(self):
        self.save('preferred_city_tiers', [['City A'], ['City B']])
        self.save('company_specific_graduation_policy', 'Synthetic rule')
        self.save('excluded_research', ['Synthetic project'])
        auth = self.save('application_preparation_policy', 'Old authorization')
        self.save('unknown_private_key', 'Unclassified')
        self.save('expected_graduation_month', '2026-09')
        records = self.app.execute('record.list', {'kind': 'preference'})
        first = self.invoke('legacy.preview', {'limit': 2})
        second = self.invoke('legacy.preview', {'limit': 2, 'offset': first['next_offset']})
        self.assertEqual(6, first['total'])
        self.assertEqual(first['preview_digest'], second['preview_digest'])
        self.assertEqual(records, self.app.execute('record.list', {'kind': 'preference'}))
        self.assertEqual({'city_tiers': [['City A'], ['City B']]}, first['proposed_changes'])
        all_items = self.invoke('legacy.preview', {'limit': 100})['items']
        self.assertEqual('review', next(item for item in all_items if item['id'] == auth['id'])['action'])
        self.assertEqual(1, first['summary']['education'])
        self.assertEqual(1, first['summary']['resume_selection'])
        self.assertEqual(1, first['summary']['company_answer'])

    def test_apply_missing_only_archives_migrated_and_instructions_preserving_history(self):
        migrated = self.save('preferred_city_tiers', [['City A'], ['City B']])
        duplicate = self.save('preferred_city_tiers', [['City A'], ['City B']])
        instruction = self.save('developer_note', 'Synthetic editor instruction')
        auth = self.save('application_preparation_policy', 'Old authorization')
        preview = self.invoke('legacy.preview')
        applied = self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']})
        self.assertEqual(['city_tiers'], applied['applied_fields'])
        self.assertEqual(3, applied['archived_count'])
        self.assertEqual({migrated['id'], duplicate['id'], instruction['id']}, set(applied['archived_ids']))
        self.assertEqual([['City A'], ['City B']], self.invoke('intention.get')['data']['city_tiers'])
        pending = self.app.execute('record.list', {'kind': 'preference'})['items']
        self.assertEqual([auth['id']], [row['id'] for row in pending])
        history = self.app.execute('history.list', {'record_id': migrated['id']})['items']
        self.assertEqual(migrated['data'], history[0]['before']['data'])
        self.assertEqual('legacy.consolidate:migrate', history[0]['action'])
        self.assertTrue(self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']})['already_applied'])
        self.assertEqual(2, len(self.app.execute('history.list', {'record_id': migrated['id']})['items']))

    def test_conflicting_rows_and_current_values_never_auto_overwrite_or_archive(self):
        first = self.save('allowed_cities', ['City A'])
        second = self.save('allowed_cities', ['City B'])
        preview = self.invoke('legacy.preview')
        self.assertEqual({}, preview['proposed_changes'])
        self.assertEqual('allowed_cities', preview['conflicts'][0]['field'])
        applied = self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']})
        self.assertEqual(0, applied['archived_count'])
        self.assertEqual(2, self.app.execute('record.list', {'kind': 'preference'})['total'])
        revision = self.invoke('intention.get')['revision']
        self.invoke('intention.patch', {'revision': revision, 'changes': {'allowed_cities': ['Current City']}})
        preview = self.invoke('legacy.preview')
        self.assertEqual(['Current City'], preview['conflicts'][0]['current_value'])
        self.assertEqual({first['id'], second['id']}, set(preview['conflicts'][0]['record_ids']))

    def test_existing_equal_value_archives_duplicate_without_incrementing_settings_revision(self):
        revision = self.invoke('intention.get')['revision']
        self.invoke('intention.patch', {'revision': revision, 'changes': {'other_cities_allowed': False}})
        self.save('other_cities_allowed', False)
        preview = self.invoke('legacy.preview')
        self.assertEqual('archive_duplicate', preview['items'][0]['action'])
        old_revision = self.invoke('intention.get')['revision']
        result = self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']})
        self.assertEqual(1, result['archived_count'])
        self.assertEqual(old_revision, self.invoke('intention.get')['revision'])

    def test_salary_migration_requires_explicit_typed_units_and_keeps_expected_distinct(self):
        self.save('expected_annual_salary', '30万年薪')
        self.save('expected_annual_salary', {'expected_amount': 300000, 'currency': 'CNY', 'period': 'annual'})
        preview = self.invoke('legacy.preview')
        salary = preview['proposed_changes']['salary']
        self.assertEqual(300000, salary['expected_amount'])
        self.assertIsNone(salary['amount_min'])
        self.assertIsNone(salary['amount_max'])
        self.assertEqual('review', preview['items'][0]['action'])
        self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']})
        self.assertEqual(1, self.invoke('intention.get')['legacy_count'])

    def test_unconfirmed_and_company_scoped_intentions_remain_pending(self):
        self.save('allowed_cities', ['City A'], status='unconfirmed')
        self.save('preferred_city_tiers', [['City B']], scope='company:synthetic')
        preview = self.invoke('legacy.preview')
        self.assertEqual({}, preview['proposed_changes'])
        self.assertTrue(all(item['action'] == 'review' for item in preview['items']))

    def test_digest_pins_education_settings_and_record_revision_and_no_ai_apply(self):
        self.save('allowed_cities', ['City A'])
        row = self.app.execute('record.save', {'kind': 'education', 'data': {'school': 'Synthetic University', 'expected_graduation_date': '2026-09'}})
        preview = self.invoke('legacy.preview')
        with self.assertRaises(AppError) as ai:
            self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']}, 'codex')
        self.assertEqual('user_action_required', ai.exception.code)
        self.app.execute('record.save', {'id': row['id'], 'revision': row['revision'], 'kind': 'education', 'data': dict(row['data'], expected_graduation_date='2026-10')})
        with self.assertRaises(AppError) as stale:
            self.invoke('legacy.apply', {'preview_digest': preview['preview_digest']})
        self.assertEqual('revision_conflict', stale.exception.code)
        fresh = self.invoke('legacy.preview')
        self.invoke('intention.patch', {'revision': fresh['settings_revision'], 'changes': {'notes': 'Fresh current preference'}})
        with self.assertRaises(AppError):
            self.invoke('legacy.apply', {'preview_digest': fresh['preview_digest']})
        fresh = self.invoke('legacy.preview')
        pref = self.app.execute('record.list', {'kind': 'preference'})['items'][0]
        self.app.execute('record.save', {'id': pref['id'], 'revision': pref['revision'], 'kind': 'preference', 'data': dict(pref['data'], value='["City B"]')})
        with self.assertRaises(AppError):
            self.invoke('legacy.apply', {'preview_digest': fresh['preview_digest']})
        other = self.app.execute('account.create', {'name': 'Synthetic Other'})
        with self.assertRaises(AppError):
            intention.execute(self.app.for_user(other['id']), 'legacy.apply', {'preview_digest': fresh['preview_digest']}, 'user')


if __name__ == '__main__':
    unittest.main()
