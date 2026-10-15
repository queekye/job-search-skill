"""Shared resume/answer workflows with synthetic private applicant facts."""
from pathlib import Path
import copy
import json
import tempfile
import unittest

from service.app import App, AppError
from service.mcp import OPERATIONS, call_tool, tool_definitions


class ResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-resume-test-')
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name) / 'private')
        self.basic = self.fact('basic', {'name': 'Synthetic Applicant', 'email': 'applicant@example.invalid', 'summary': 'Synthetic summary'})
        self.education = self.fact('education', {'school': 'Synthetic University', 'degree': 'Synthetic Degree', 'start_date': '2020', 'expected_graduation_date': '2200', 'education_status': 'studying'})
        self.project = self.fact('experience', {'title': 'Synthetic Project', 'category': 'project', 'description': 'Synthetic background', 'responsibilities': 'Synthetic responsibility', 'results': 'Synthetic result', 'start_date': '2020-11', 'end_date': '2023-10', 'project_code': 'Synthetic-Code'})
        self.paper = self.fact('experience', {'title': 'Synthetic Paper', 'category': 'publication', 'authors': ['Synthetic First', 'Synthetic Second'], 'year': 2025, 'venue': 'Synthetic Conference'})

    def fact(self, kind, data, **kwargs):
        return self.app.execute('record.save', {'kind': kind, 'data': data, 'status': 'confirmed', **kwargs})

    def variant(self, **data):
        return self.app.execute('resume.save', {'data': {'name': 'Synthetic Main Resume', 'project_ids': [self.project['id']], 'publication_ids': [self.paper['id']], **data}, 'status': 'confirmed'})

    def answer(self, value, **data):
        return self.app.execute('answer.save', {'data': {'company_id': 'synthetic-alpha', 'question_key': 'city', 'label': 'Preferred city', 'value_type': 'string', 'value': value, **data}, 'status': 'confirmed'})

    def test_composition_uses_explicit_order_and_keeps_review_sources_out_of_markdown(self):
        variant = self.variant(section_order=['publications', 'projects', 'summary', 'education'])
        self.app.execute('profile.relation.save', {'from_id': self.project['id'], 'to_id': self.paper['id'], 'relation_type': 'project_publication'})
        result = self.app.execute('resume.compose', {'id': variant['id']})
        self.assertEqual(['publications', 'projects', 'summary', 'education'], [s['key'] for s in result['sections']])
        self.assertEqual(1, len(next(s for s in result['sections'] if s['key'] == 'projects')['items']))
        self.assertEqual(5, len(result['pins']))
        self.assertEqual('none', result['family']['declaration'])
        self.assertIn('Synthetic background', result['markdown'])
        self.assertIn('开始：2020-11', result['markdown'])
        self.assertIn('结束：2023-10', result['markdown'])
        self.assertIn('项目编号：Synthetic-Code', result['markdown'])
        self.assertIn('Synthetic First，Synthetic Second', result['markdown'])
        self.assertNotIn('source_fingerprints', result['markdown'])
        self.assertNotIn('user_edit', result['markdown'])

    def test_wording_override_is_variant_local_and_stale_sources_require_explicit_review(self):
        variant = self.variant(text_overrides={self.project['id']: {'responsibilities': 'Tailored wording'}})
        self.assertEqual('Synthetic responsibility', self.app.execute('profile.get', {'section': 'projects'})['items'][0]['data']['responsibilities'])
        self.app.execute('profile.patch', {'id': self.project['id'], 'revision': 1, 'changes': {'responsibilities': 'Updated source fact'}})
        result = self.app.execute('resume.compose', {'id': variant['id']})
        self.assertIn('stale_wording', [i['code'] for i in result['issues']])
        review = self.app.execute('resume.get', {'id': variant['id']})['source_review']
        self.assertTrue(review['needs_review'])
        self.assertEqual('Updated source fact', next(r for r in review['current_sources'] if r['id'] == self.project['id'])['data']['responsibilities'])
        acknowledged = self.app.execute('resume.save', {'id': variant['id'], 'revision': 1, 'reviewed_sources': review['current_fingerprints']})
        self.assertFalse(self.app.execute('resume.get', {'id': acknowledged['id']})['source_review']['needs_review'])
        self.app.execute('profile.patch', {'id': self.project['id'], 'revision': 2, 'changes': {'results': 'Changed again'}})
        with self.assertRaises(AppError) as error:
            self.app.execute('resume.save', {'id': variant['id'], 'revision': 2, 'reviewed_sources': review['current_fingerprints']})
        self.assertEqual('revision_conflict', error.exception.code)

    def test_ai_edits_remain_unconfirmed_and_do_not_get_confirmation_from_old_variants(self):
        variant = self.variant()
        result = self.app.execute('resume.save', {'id': variant['id'], 'revision': 1, 'data': {'name': 'AI draft'}}, actor='codex')
        self.assertEqual('unconfirmed', result['status'])
        for operation, payload in [('resume.save', {'id': result['id'], 'revision': 2, 'status': 'confirmed'}), ('answer.save', {'data': {'company_id': 'alpha', 'question_key': 'q', 'label': 'Question', 'value_type': 'boolean', 'value': False}, 'status': 'confirmed'})]:
            with self.assertRaises(AppError) as error:
                self.app.execute(operation, payload, actor='codex')
            self.assertEqual('user_action_required', error.exception.code)

    def test_answers_respect_scope_expiry_linked_pins_and_conflicts(self):
        variant = self.variant()
        answer = self.answer('Synthetic City', linked_record_id=self.project['id'], linked_field='description', expires_at='2200')
        mismatch = self.app.execute('answer.save', {'data': {**answer['data'], 'question_key': 'other'}, 'scope': 'company:synthetic-beta', 'status': 'confirmed'})
        result = self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'synthetic-alpha'})
        self.assertTrue(next(a for a in result['answers'] if a['id'] == answer['id'])['usable'])
        self.assertFalse(next(a for a in result['answers'] if a['id'] == mismatch['id'])['usable'])
        self.assertIn(self.project['id'], {p['id'] for p in result['pins']})
        self.answer('Different City')
        result = self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'synthetic-alpha'})
        self.assertIn('answer_conflict', [i['code'] for i in result['issues']])
        self.assertTrue(all(not a['usable'] for a in result['answers']))
        self.answer('Old answer', question_key='old', expires_at='2000-01')
        self.assertIn('answer_expired', [i['code'] for i in self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'synthetic-alpha'})['issues']])

    def test_scope_and_unavailable_references_are_reported_without_body_leaks(self):
        scoped = self.fact('experience', {'title': 'Scoped Project', 'category': 'project', 'description': 'Scoped content'}, scope='company:synthetic-beta')
        variant = self.variant(project_ids=[scoped['id']], target_scope='company:synthetic-beta')
        result = self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'synthetic-alpha'})
        self.assertIn('variant_scope_mismatch', [i['code'] for i in result['issues']])
        self.assertNotIn('Scoped content', result['markdown'])
        answer = self.answer('Linked', linked_record_id=self.project['id'], linked_field='description')
        with self.app._connect() as db:
            db.execute('UPDATE records SET archived=1 WHERE id=?', (self.project['id'],))
        result = self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'synthetic-alpha'})
        self.assertFalse(next(a for a in result['answers'] if a['id'] == answer['id'])['usable'])
        self.assertIn('answer_source_unavailable', [i['code'] for i in result['issues']])

    def test_refs_and_account_changes_are_guarded_without_harming_existing_edits(self):
        variant = self.variant()
        other = self.app.execute('account.create', {'name': 'Synthetic Other'})
        with self.assertRaises(AppError): self.app.execute('resume.get', {'id': variant['id']}, user_id=other['id'])
        with self.assertRaises(AppError): self.app.execute('record.archive', {'id': self.project['id'], 'revision': 1})
        with self.assertRaises(AppError): self.app.execute('profile.patch', {'id': self.project['id'], 'revision': 1, 'changes': {'category': 'research'}})
        with self.assertRaises(AppError): self.app.execute('resume.save', {'data': {'name': 'Wrong selection', 'project_ids': [self.paper['id']]}})
        self.app.execute('resume.save', {'id': variant['id'], 'revision': 1, 'data': {'name': 'Applicant edit'}})
        with self.assertRaises(AppError): self.app.execute('resume.save', {'id': variant['id'], 'revision': 1, 'data': {'name': 'Stale edit'}})

    def test_portable3_remaps_embedded_references_and_preserves_user_reimport_edits(self):
        variant = self.variant(text_overrides={self.project['id']: {'results': 'Variant result'}})
        self.answer('City', linked_record_id=self.education['id'], linked_field='expected_graduation_date')
        exported = self.app.execute('profile.export')
        exported['records'].sort(key=lambda r: r['kind'] != 'resume_variant')
        other = App(Path(self.tmp.name) / 'other-private')
        other.execute('profile.import', {'profile': exported})
        imported = other.execute('resume.list')['items'][0]
        restored_project = other.execute('profile.get', {'section': 'projects'})['items'][0]
        self.assertEqual([restored_project['id']], imported['data']['project_ids'])
        self.assertIn(restored_project['id'], imported['data']['text_overrides'])
        self.assertIn('Variant result', other.execute('resume.compose', {'id': imported['id']})['markdown'])
        other.execute('resume.save', {'id': imported['id'], 'revision': imported['revision'], 'data': {'name': 'User revised'}})
        self.assertEqual(0, other.execute('profile.import', {'profile': exported})['imported'])
        self.assertEqual('User revised', other.execute('resume.list')['items'][0]['data']['name'])
        bad = copy.deepcopy(exported);bad['records'][0]['data']['project_ids'] = ['missing']
        fresh = App(Path(self.tmp.name) / 'failed-private')
        with self.assertRaises(AppError): fresh.execute('profile.import', {'profile': bad})
        self.assertEqual(0, fresh.execute('record.list')['total'])

    def test_task_context_separates_facts_from_history_and_drafts_and_mcp_exposes_same_operations(self):
        self.app.execute('record.save', {'kind': 'preference', 'data': {'category': 'other', 'value': 'Historical task instruction'}})
        variant = self.variant()
        self.answer('City')
        context = self.app.execute('task.context')
        self.assertTrue(all(r['kind'] in {'basic', 'education', 'experience', 'family'} for r in context['profile']['items']))
        self.assertEqual(1, context['legacy_pending'])
        self.assertEqual(1, context['company_answers']['total'])
        self.assertEqual(1, context['resume_variants']['total'])
        self.assertEqual('settings.preferences', context['intention']['authority'])
        self.assertIn('resume.compose', OPERATIONS['career_get'])
        self.assertIn('answer.save', OPERATIONS['career_save'])
        self.assertNotIn('legacy.apply', OPERATIONS['career_save'])
        response = call_tool(self.app, 'career_get', {'operation': 'resume.compose', 'args': {'id': variant['id']}})
        self.assertEqual('read_only', response['mode'])
        self.assertTrue(tool_definitions())

    def test_invalid_json_choices_and_editorial_wording_do_not_enter_business_content(self):
        for payload in [{'name': 'Bad sections', 'section_order': ['unknown']}, {'name': 'Duplicate', 'project_ids': [self.project['id'], self.project['id']]}, {'name': 'Bad wording', 'project_ids': [self.project['id']], 'text_overrides': {self.project['id']: {'results': '论文题名不添加方法缩写以外的后缀。'}}}]:
            with self.assertRaises(AppError): self.app.execute('resume.save', {'data': payload})
        for value, typ in [(True, 'number'), ({'nested': float('inf')}, 'object'), ('text', 'boolean')]:
            with self.assertRaises(AppError): self.answer(value, value_type=typ)

    def test_source_versions_and_stale_edits_do_not_acknowledge_unseen_updates(self):
        variant = self.variant(text_overrides={self.project['id']: {'results': 'Variant result'}})
        self.app.execute('profile.patch', {'id': self.project['id'], 'revision': 1, 'changes': {'results': 'New source'}})
        with self.assertRaises(AppError) as error:
            self.app.execute('resume.save', {'id': variant['id'], 'revision': 1, 'source_versions': {self.project['id']: 1}, 'data': {'text_overrides': {self.project['id']: {'results': 'Minor wording'}}}})
        self.assertEqual('revision_conflict', error.exception.code)
        changed = self.app.execute('resume.save', {'id': variant['id'], 'revision': 1, 'data': {'text_overrides': {self.project['id']: {'results': 'Minor wording'}}}})
        self.assertTrue(self.app.execute('resume.get', {'id': changed['id']})['source_review']['needs_review'])
        self.assertIn('stale_wording', [i['code'] for i in self.app.execute('resume.compose', {'id': changed['id']})['issues']])

    def test_removed_selection_prunes_fingerprints_so_archives_remain_portable(self):
        variant = self.variant(text_overrides={self.project['id']: {'results': 'Variant result'}})
        self.app.execute('resume.save', {'id': variant['id'], 'revision': 1, 'data': {'project_ids': [], 'text_overrides': {}}})
        self.app.execute('record.archive', {'id': self.project['id'], 'revision': 1})
        exported = self.app.execute('profile.export')
        other = App(Path(self.tmp.name) / 'no-project-private')
        other.execute('profile.import', {'profile': exported})
        self.assertNotIn(self.project['id'], other.execute('resume.list')['items'][0]['data']['source_fingerprints'])

    def test_linked_answer_updates_and_partial_expiry_require_precise_review(self):
        variant = self.variant(target_scope='Synthetic research roles')
        answer = self.answer('2200', linked_record_id=self.education['id'], linked_field='expected_graduation_date', expires_at='2200')
        self.app.execute('profile.patch', {'id': self.education['id'], 'revision': 1, 'changes': {'expected_graduation_date': '2199'}})
        result = self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'synthetic-alpha'})
        self.assertFalse(next(item for item in result['answers'] if item['id'] == answer['id'])['usable'])
        self.assertNotIn('variant_scope_mismatch', [i['code'] for i in result['issues']])
        review = self.app.execute('answer.get', {'id': answer['id']})['source_review']
        self.app.execute('answer.save', {'id': answer['id'], 'revision': 1, 'reviewed_sources': review['current_fingerprints']})
        self.assertTrue(self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'synthetic-alpha'})['answers'][0]['usable'])

    def test_declared_company_fields_preview_stays_read_only_and_reports_supported_scope(self):
        variant = self.variant()
        self.app.execute('profile.patch', {'id': self.basic['id'], 'revision': 1, 'changes': {'name': None}})
        before = self.app.execute('history.list')['total']
        result = self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'jd'})
        self.assertIsNotNone(result['company_form'])
        self.assertFalse(result['company_form']['scope_complete'])
        self.assertTrue(result['company_form']['missing'])
        self.assertEqual(before, self.app.execute('history.list')['total'])
        self.app.execute('record.save', {'id': self.project['id'], 'revision': 1, 'kind': 'experience', 'data': self.project['data']}, actor='codex')
        issues = self.app.execute('resume.compose', {'id': variant['id'], 'company_id': 'jd'})['issues']
        self.assertIn('company_form_needs_review', [i['code'] for i in issues])
        self.assertNotIn('company_form_unavailable', [i['code'] for i in issues])

    def test_company_decisions_are_applicant_owned_portable_and_stop_preparation(self):
        decision = self.app.execute('decision.save', {'data': {'company_id': 'jd', 'company_name': '京东', 'decision': 'pause', 'reason': 'Synthetic applicant pause'}})
        self.assertEqual(decision['id'], self.app.execute('decision.get', {'id': decision['id']})['id'])
        for operation, args in [('decision.save', {'id': decision['id'], 'revision': 1, 'data': {'decision': 'watch'}}), ('record.archive', {'id': decision['id'], 'revision': 1})]:
            with self.assertRaises(AppError): self.app.execute(operation, args, actor='codex')
        with self.assertRaises(AppError) as error: self.app.execute('fill.prepare', {'company_id': 'jd'})
        self.assertEqual('company_paused', error.exception.code)
        self.assertEqual(1, self.app.execute('task.context')['company_decisions']['total'])
        self.assertIn('company_paused', [i['code'] for i in self.app.execute('resume.compose', {'id': self.variant()['id'], 'company_id': 'jd'})['issues']])
        exported = self.app.execute('profile.export')
        other = App(Path(self.tmp.name) / 'decision-portable')
        other.execute('profile.import', {'profile': exported})
        self.assertEqual('pause', other.execute('decision.list')['items'][0]['data']['decision'])
        legacy = copy.deepcopy(exported); legacy['schema_version'] = 2
        with self.assertRaises(AppError): other.execute('profile.import', {'profile': legacy})
        self.app.execute('decision.save', {'id': decision['id'], 'revision': 1, 'data': {'decision': 'watch'}})
        self.assertEqual('pending', self.app.execute('fill.prepare', {'company_id': 'jd'})['status'])
        intention = self.app.execute('intention.get')
        self.app.execute('intention.patch', {'revision': intention['revision'], 'changes': {'excluded_companies': ['京东']}})
        with self.assertRaises(AppError): self.app.execute('fill.prepare', {'company_id': 'jd'})
        self.assertIn('company_excluded', [i['code'] for i in self.app.execute('resume.compose', {'id': self.app.execute('resume.list')['items'][0]['id'], 'company_id': 'jd'})['issues']])

    def test_restoring_an_old_decision_cannot_create_two_current_decisions(self):
        old = self.app.execute('decision.save', {'data': {'company_id': 'jd', 'company_name': '京东', 'decision': 'pause'}})
        self.app.execute('decision.archive', {'id': old['id'], 'revision': 1})
        event = next(e for e in self.app.execute('history.list', {'record_id': old['id']})['items'] if e['action'] == 'archive')
        self.app.execute('decision.save', {'data': {'company_id': 'jd', 'company_name': '京东', 'decision': 'watch'}})
        with self.assertRaises(AppError) as error:
            self.app.execute('history.undo', {'event_id': event['id'], 'revision': 2})
        self.assertEqual('decision_exists', error.exception.code)


if __name__ == '__main__': unittest.main()
