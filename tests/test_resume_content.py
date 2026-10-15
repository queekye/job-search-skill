"""Resume facts and audit comments must not share fillable text fields."""
from pathlib import Path
import json
import tempfile
import unittest

from service.app import App, AppError
from service import forms, resume_content


class ResumeContentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-content-test-')
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name) / 'private')

    def test_factual_limits_track_and_experiment_conditions_remain_valid(self):
        text = 'Grand Challenge赛道；第一作者，已发表。在指定GPU与Token预算下达到2.0倍加速；不支持未验证模型。'
        row = self.app.execute('record.save', {'kind': 'experience', 'data': {'category': 'research', 'title': 'Synthetic research', 'description': text}, 'status': 'confirmed'})
        self.assertEqual(text, row['data']['description'])
        self.assertEqual([], resume_content.editorial_fields('experience', row['data']))

    def test_new_saves_reject_editorial_instructions_with_a_fixed_message(self):
        for text in ['已发表。论文题名不添加方法缩写以外的后缀。',
                     '原简历列中科院2区，此为源文档描述，不作为当前分区的独立核验。',
                     '主简历项目1，后续表单可复用：',
                     'academic_ranking：{"value":"其他","source_refs":["synthetic"]}']:
            with self.subTest(text=text):
                with self.assertRaises(AppError) as error:
                    self.app.execute('record.save', {'kind': 'experience', 'data': {'title': 'Synthetic Entry', 'description': text}})
                self.assertEqual('resume_editorial_notes', error.exception.code)
                self.assertNotIn(text, error.exception.message)

    def legacy_record(self):
        row = self.app.execute('record.save', {'kind': 'experience', 'data': {'title': 'Synthetic Paper', 'category': 'publication', 'description': 'Synthetic Venue 2026，已发表。'}, 'status': 'confirmed'})
        # Existing versions could store comments; test the upgrade boundary.
        with self.app._connect() as db:
            data = dict(row['data'], description=row['data']['description'] + '论文题名不添加方法缩写以外的后缀。')
            db.execute('UPDATE records SET data=? WHERE id=?', (json.dumps(data, ensure_ascii=False), row['id']))
        return row, data

    def test_cleanup_preserves_title_and_full_original_in_history(self):
        row, old = self.legacy_record()
        clean = self.app.execute('profile.patch', {'id': row['id'], 'revision': row['revision'], 'changes': {'description': 'Synthetic Venue 2026，已发表。'}})
        self.assertEqual(row['data']['title'], clean['data']['title'])
        latest = self.app.execute('history.list', {'record_id': row['id']})['items'][0]
        self.assertEqual(old, latest['before']['data'])

    def test_confirmed_legacy_commentary_is_blocked_before_mapping_into_a_form(self):
        row, _ = self.legacy_record()
        package = {'id': 'synthetic-company', 'form': {'input_schema': {'type': 'object', 'properties': {
            'papers': {'type': 'array', 'x-source': 'experience:publication', 'items': {
                'type': 'object', 'properties': {'description': {'type': 'string', 'x-source': 'description'}}}}}}}}
        with self.assertRaises(AppError) as error:
            forms._source_input(self.app, package, {'experience': [row['id']]})
        self.assertEqual('resume_editorial_notes', error.exception.code)
