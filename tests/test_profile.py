"""Synthetic acceptance for shared profile reads, patching and migration previews."""
from pathlib import Path
import tempfile
import unittest

from service.app import App, AppError, FIELDS
from service import profile
from service.mcp import OPERATIONS, tool_definitions


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-profile-test-')
        self.addCleanup(self.tmp.cleanup)
        self.app = App(data_dir=Path(self.tmp.name) / 'private')

    def save(self, kind, data, **metadata):
        return self.app.execute('record.save', {'kind': kind, 'data': data, **metadata})

    def test_schema_and_validator_share_exact_current_fields_and_reject_unknown_fields(self):
        catalog = self.app.execute('profile.schema')
        for kind, definition in catalog['records'].items():
            self.assertEqual(FIELDS[kind], {f['key'] for f in definition['fields']})
        catalog['sections'].clear()
        self.assertTrue(self.app.execute('profile.schema')['sections'])
        row = self.save('education', {'school': 'Synthetic University'})
        changed = self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'changes': {'supervisor_name': 'Synthetic Tutor'}})
        self.assertEqual('Synthetic Tutor', changed['data']['supervisor_name'])
        with self.assertRaises(AppError):
            self.app.execute('profile.patch', {'id': row['id'], 'revision': 2, 'changes': {'unknown_extension': 'Value'}})

    def test_patch_preserves_omitted_fields_scope_and_history_and_rejects_stale_updates(self):
        row = self.save('education', {'school': 'Synthetic University', 'major': 'Synthetic Major', 'description': 'Original exact text'}, scope='company:synthetic', status='confirmed')
        updated = self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'changes': {'major': 'Changed Major'}})
        self.assertEqual('Original exact text', updated['data']['description'])
        self.assertEqual('company:synthetic', updated['scope'])
        self.assertEqual(2, updated['revision'])
        with self.assertRaises(AppError) as error:
            self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'clear': ['school']})
        self.assertEqual('revision_conflict', error.exception.code)
        cleared = self.app.execute('profile.patch', {'id': row['id'], 'revision': 2, 'clear': ['description']})
        self.assertIsNone(cleared['data']['description'])
        history = self.app.execute('history.list', {'record_id': row['id']})['items']
        self.assertEqual(3, len(history))
        self.assertEqual('Original exact text', history[1]['before']['data']['description'])

    def test_patch_cannot_cross_accounts_change_workflow_or_promote_ai_edits(self):
        row = self.save('experience', {'title': 'Synthetic Project', 'category': 'project'}, status='confirmed')
        account = self.app.execute('account.create', {'name': 'Synthetic Other'})
        with self.assertRaises(AppError):
            self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'changes': {'title': 'Wrong Account'}}, user_id=account['id'])
        result = self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'changes': {'role': 'Proposed role'}}, actor='codex')
        self.assertEqual('unconfirmed', result['status'])
        application = self.save('application', {'company': 'Synthetic', 'title': 'Role', 'stage': 'candidate'})
        with self.assertRaises(AppError):
            self.app.execute('profile.patch', {'id': application['id'], 'revision': 1, 'changes': {'stage': 'submitted'}})
        for payload in [{'changes': {'title': 'A'}, 'clear': ['title']}, {'clear': ['title', 'title']}, {'changes': {}}, {'clear': ['not_a_field']}]:
            with self.assertRaises(AppError):
                self.app.execute('profile.patch', {'id': row['id'], 'revision': 2, **payload})

    def test_section_filtering_precedes_pagination_and_unknown_categories_remain_visible(self):
        self.save('experience', {'title': 'Synthetic Paper', 'category': 'publication'})
        self.save('experience', {'title': 'Unclassified'})
        for i in range(3):
            self.save('experience', {'title': 'Project ' + str(i), 'category': 'project'})
        first = self.app.execute('profile.get', {'section': 'projects', 'limit': 2})
        second = self.app.execute('profile.get', {'section': 'projects', 'limit': 2, 'offset': first['next_offset']})
        self.assertEqual(3, first['total'])
        self.assertEqual(1, len(second['items']))
        self.assertIsNone(second['next_offset'])
        self.assertEqual(3, len({r['id'] for r in first['items'] + second['items']}))
        self.assertEqual(1, self.app.execute('profile.get', {'section': 'uncategorized'})['total'])

    def test_preview_never_mutates_or_invents_actual_dates_and_duplicate_keys_block_extraction(self):
        row = self.save('education', {'school': 'Synthetic University', 'end_date': '2026-09', 'description': 'end_month_is_expected：True\nlaboratory：Synthetic Lab\nsupervisor：{"name":"Synthetic Tutor"}'})
        before = self.app.execute('record.list', {'kind': 'education'})
        result = self.app.execute('profile.migration.preview', {'section': 'education'})
        proposal = result['items'][0]
        self.assertEqual('proposed', proposal['status'])
        self.assertEqual('2026-09', proposal['proposed_fields']['expected_graduation_date'])
        self.assertNotIn('graduation_date', proposal['proposed_fields'])
        self.assertEqual(before, self.app.execute('record.list', {'kind': 'education'}))
        self.assertEqual({}, profile.embedded('laboratory：A\nlaboratory：B'))
        self.assertEqual(1, len(self.app.execute('history.list', {'record_id': row['id']})['items']))

    def test_intention_uses_single_settings_revision_and_retains_legacy_without_ai_write_access(self):
        self.save('preference', {'category': 'cities', 'value': '["Synthetic City"]', 'notes': 'preferred_cities'})
        old = self.app.execute('intention.get')
        self.assertEqual(1, old['legacy_records_pending_review'])
        new = self.app.execute('intention.patch', {'revision': old['revision'], 'changes': {'cities': ['Reviewed City']}})
        self.assertEqual(['Reviewed City'], self.app.execute('intention.get')['data']['cities'])
        self.assertEqual(new['_revision'], self.app.execute('intention.get')['revision'])
        with self.assertRaises(AppError):
            self.app.execute('intention.patch', {'revision': new['_revision'], 'changes': {'cities': ['AI Change']}}, actor='codex')
        self.assertEqual(1, self.app.execute('record.list', {'kind': 'preference'})['total'])
        self.assertIn('profile.patch', OPERATIONS['career_save'])
        self.assertNotIn('intention.patch', OPERATIONS['career_save'])
        self.assertIn('profile.migration.preview', OPERATIONS['career_get'])
        self.assertTrue(next(t for t in tool_definitions() if t['name'] == 'career_get')['annotations']['readOnlyHint'])
