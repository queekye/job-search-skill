"""Migration safety and relation semantics with synthetic private records."""
from pathlib import Path
import tempfile
import unittest

from service.app import App, AppError
from service.backup import logical_digest
from scripts.generate_profile_schema import generate
from service import profile
import json


class ProfileMigrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-typed-migration-')
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name) / 'private')

    def save(self, kind, data, **metadata):
        return self.app.execute('record.save', {'kind': kind, 'data': data, **metadata})

    def test_shared_portable_schema_stays_generated(self):
        path = Path(__file__).resolve().parents[1] / 'schemas' / 'profile.schema.json'
        schema = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(schema, generate(schema, profile.CATALOG))

    def test_reviewed_migration_preserves_id_source_scope_history_and_is_idempotent(self):
        row = self.save('education', {'school': 'Synthetic University', 'description': 'laboratory：Synthetic Lab\nsupervisor：{"name":"Synthetic Tutor"}\nresearch_fields：["Synthetic Topic"]\ndouble_degree：False\nstudy_mode：全日制'}, source='synthetic_document', scope='company:synthetic', status='confirmed')
        before_digest = logical_digest(self.app.base_data_dir)
        preview = self.app.execute('profile.migration.preview', {'limit': 1})
        self.assertEqual(before_digest, logical_digest(self.app.base_data_dir))
        self.assertEqual({'research_directions': ['Synthetic Topic'], 'laboratory': 'Synthetic Lab', 'double_degree': False, 'study_mode': 'full_time', 'supervisor_name': 'Synthetic Tutor', 'description': None}, preview['items'][0]['changes'])
        result = self.app.execute('profile.migration.apply', {'preview_digest': preview['preview_digest']})
        self.assertEqual(1, result['applied_count'])
        current = self.app.execute('profile.get')['items'][0]
        self.assertEqual(row['id'], current['id'])
        self.assertEqual('company:synthetic', current['scope'])
        self.assertEqual('confirmed', current['status'])
        self.assertEqual('synthetic_document', current['source'])
        self.assertFalse(current['data']['double_degree'])
        self.assertIsNone(current['data']['description'])
        history = self.app.execute('history.list', {'record_id': row['id']})['items']
        self.assertEqual(row['data']['description'], history[0]['before']['data']['description'])
        self.assertEqual('profile.migration', history[0]['action'])
        self.app.execute('profile.patch', {'id': row['id'], 'revision': current['revision'], 'changes': {'laboratory': 'Applicant Revised Lab'}})
        repeated = self.app.execute('profile.migration.apply', {'preview_digest': preview['preview_digest']})
        self.assertTrue(repeated['already_applied'])
        self.assertEqual('Applicant Revised Lab', self.app.execute('profile.get')['items'][0]['data']['laboratory'])

    def test_preview_pins_whole_account_even_when_paginated_and_apply_is_atomic(self):
        first = self.save('education', {'school': 'Synthetic A', 'description': 'laboratory：Synthetic Lab'})
        second = self.save('education', {'school': 'Synthetic B'})
        preview = self.app.execute('profile.migration.preview', {'limit': 1})
        self.assertEqual({first['id'], second['id']}, set(preview['source_revisions']))
        self.app.execute('profile.patch', {'id': second['id'], 'revision': 1, 'changes': {'major': 'Applicant Change'}})
        with self.assertRaises(AppError) as error:
            self.app.execute('profile.migration.apply', {'preview_digest': preview['preview_digest']})
        self.assertEqual('revision_conflict', error.exception.code)
        self.assertNotIn('laboratory', self.app.execute('profile.get')['items'][0]['data'])
        other = self.app.execute('account.create', {'name': 'Synthetic Other'})
        with self.assertRaises(AppError):
            self.app.execute('profile.migration.apply', {'preview_digest': preview['preview_digest']}, user_id=other['id'])

    def test_ambiguous_dates_conflicts_and_duplicates_remain_for_review(self):
        self.save('education', {'school': 'Synthetic A', 'end_date': '2026-09', 'laboratory': 'Reviewed Lab', 'description': 'laboratory：Old Lab'})
        self.save('education', {'school': 'Synthetic B', 'description': 'laboratory：A\nlaboratory：B'})
        result = self.app.execute('profile.migration.preview')
        self.assertTrue(result['items'][0]['review_required'])
        self.assertEqual({}, result['items'][0]['changes'])
        self.assertEqual({}, result['items'][1]['changes'])
        applied = self.app.execute('profile.migration.apply', {'preview_digest': result['preview_digest']})
        self.assertEqual(0, applied['applied_count'])
        self.assertNotIn('graduation_date', self.app.execute('profile.get')['items'][0]['data'])

    def test_unmapped_nested_supervisor_details_are_not_cleared(self):
        for index, value in enumerate(['{"email":"synthetic@example.com"}', 'Synthetic Tutor', '["Synthetic Tutor"]']):
            self.save('education', {'school': 'Synthetic University ' + str(index), 'description': 'supervisor：' + value})
        for result in self.app.execute('profile.migration.preview')['items']:
            self.assertTrue(result['review_required'])
            self.assertNotIn('description', result['changes'])
        row = self.save('education', {'school': 'Synthetic Unknown Date', 'description': 'end_month_is_expected："yes"'})
        result = next(p for p in self.app.execute('profile.migration.preview')['items'] if p['id'] == row['id'])
        self.assertTrue(result['review_required'])
        self.assertNotIn('description', result['changes'])

    def test_historical_field_sources_survive_category_changes_and_malformed_reads_fail_cleanly(self):
        row = self.save('experience', {'title': 'Synthetic', 'category': 'research'})
        self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'changes': {'category': 'publication', 'authors': ['Synthetic Author']}})
        exported = self.app.execute('profile.export')
        other = App(Path(self.tmp.name) / 'other-private')
        self.assertEqual(1, other.execute('profile.import', {'profile': exported})['imported'])
        with self.assertRaises(AppError):
            self.app.execute('profile.get', {'section': []})
        for key in ('field_path', 'status'):
            bad = json.loads(json.dumps(exported))
            bad['profile_sources'][0][key] = []
            with self.assertRaises(AppError):
                other.execute('profile.import', {'profile': bad})

    def test_explicit_project_blocks_become_distinct_fields_with_full_original_in_history(self):
        row = self.save('experience', {'title': 'Synthetic Project', 'category': 'project', 'description': '项目描述：Synthetic Background\n个人职责：Synthetic Responsibility\n项目成果：Synthetic Result'})
        preview = self.app.execute('profile.migration.preview')
        self.app.execute('profile.migration.apply', {'preview_digest': preview['preview_digest']})
        data = self.app.execute('profile.get')['items'][0]['data']
        self.assertEqual('Synthetic Background', data['description'])
        self.assertEqual('Synthetic Responsibility', data['responsibilities'])
        self.assertEqual('Synthetic Result', data['results'])
        self.assertEqual(row['data']['description'], self.app.execute('history.list')['items'][0]['before']['data']['description'])

    def test_publication_fields_preserve_types_and_do_not_guess_from_title(self):
        self.save('experience', {'title': 'Synthetic Paper 2025', 'category': 'publication', 'description': 'authors：["Synthetic A","Synthetic B"]\nyear：2024\npublication_status：published'})
        preview = self.app.execute('profile.migration.preview')
        self.app.execute('profile.migration.apply', {'preview_digest': preview['preview_digest']})
        data = self.app.execute('profile.get')['items'][0]['data']
        self.assertEqual(['Synthetic A', 'Synthetic B'], data['authors'])
        self.assertEqual(2024, data['year'])
        self.assertIsNone(data['description'])
        self.assertNotIn('author_position', data)

    def test_relations_require_explicit_matching_endpoints_and_preserve_account_revision_history(self):
        project = self.save('experience', {'title': 'Same Title', 'category': 'project'})
        paper = self.save('experience', {'title': 'Same Title', 'category': 'publication'})
        self.assertEqual([], self.app.execute('profile.relation.list')['items'])
        relation = self.app.execute('profile.relation.save', {'from_id': project['id'], 'to_id': paper['id'], 'relation_type': 'project_publication', 'status': 'confirmed'})
        duplicate = self.app.execute('profile.relation.save', {'from_id': project['id'], 'to_id': paper['id'], 'relation_type': 'project_publication'}, actor='codex')
        self.assertEqual(relation, duplicate)
        with self.assertRaises(AppError) as error:
            self.app.execute('profile.patch', {'id': project['id'], 'revision': 1, 'changes': {'category': 'research'}})
        self.assertEqual('linked_record_type', error.exception.code)
        with self.assertRaises(AppError):
            self.app.execute('profile.relation.save', {'from_id': paper['id'], 'to_id': project['id'], 'relation_type': 'project_publication'})
        other = self.app.execute('account.create', {'name': 'Synthetic Other'})
        with self.assertRaises(AppError):
            self.app.execute('profile.relation.archive', {'id': relation['id'], 'revision': 1}, user_id=other['id'])
        self.app.execute('profile.relation.archive', {'id': relation['id'], 'revision': 1})
        with self.assertRaises(AppError):
            self.app.execute('profile.relation.archive', {'id': relation['id'], 'revision': 1})
        self.assertEqual([], self.app.execute('profile.relation.list')['items'])
        with self.app._connect() as db:
            self.assertEqual(2, db.execute('SELECT COUNT(*) FROM profile_relation_events').fetchone()[0])

    def test_evidence_is_separate_and_typed_limits_keep_unknown_false_and_empty_lists_distinct(self):
        row = self.save('education', {'school': 'Synthetic University', 'double_degree': False, 'research_directions': [], 'rank_percent': 0, 'graduation_date': None})
        current = self.app.execute('profile.get')['items'][0]
        self.assertTrue(current['field_sources'])
        self.assertNotIn('field_sources', current['data'])
        for changes in ({'double_degree': 'false'}, {'rank_percent': True}, {'rank_percent': float('inf')}, {'gpa': 10**1000}, {'research_directions': [True]}, {'graduation_date': '2026-02-30'}):
            with self.assertRaises(AppError):
                self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'changes': changes})
        self.app.execute('profile.patch', {'id': row['id'], 'revision': 1, 'changes': {'graduation_date': '2026'}})
        self.assertEqual('2026', self.app.execute('profile.get')['items'][0]['data']['graduation_date'])

    def test_portable2_duplicate_source_record_ids_are_rejected_atomically(self):
        payload = {'schema_version': 2, 'records': [{'id': 'shared:record', 'kind': 'education', 'data': {'school': 'Synthetic University'}}, {'id': 'shared:record', 'kind': 'experience', 'data': {'title': 'Synthetic Project', 'category': 'project'}}]}
        with self.assertRaises(AppError):
            self.app.execute('profile.import', {'profile': payload})
        self.assertEqual(0, self.app.execute('record.list')['total'])


if __name__ == '__main__':
    unittest.main()
