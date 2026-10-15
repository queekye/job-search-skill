"""Synthetic acceptance for absence defaults and company-scoped declarations."""
from pathlib import Path
import tempfile
import unittest

from service.app import App, AppError
from service import family


class FamilyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-family-test-')
        self.addCleanup(self.tmp.cleanup)
        self.app = App(data_dir=Path(self.tmp.name) / 'private')

    def save(self, data, **metadata):
        return self.app.execute('record.save', {'kind': 'family', 'data': data, **metadata})

    def evaluate(self, **args):
        return family.execute(self.app, 'family.evaluate', args, 'user')

    def test_absence_defaults_to_none_without_per_company_records(self):
        for company in (None, 'Synthetic Alpha', 'Synthetic Beta'):
            result = self.evaluate(**({'company': company} if company else {}))
            self.assertEqual('none', result['declaration'])
            self.assertEqual('default', result['source'])
            self.assertEqual([], result['matches'])
            self.assertFalse(result['requires_review'])
        self.assertEqual(0, self.app.execute('record.list', {'kind': 'family'})['total'])

    def test_relation_policy_notes_and_unknown_status_are_placeholders(self):
        self.save({'relation': '已有亲属任职声明', 'employment_status': 'unknown',
                   'notes': '没填就是默认无任职情况'})
        self.save({'relation': 'Synthetic relation', 'employment_status': 'none'})
        self.save({'notes': 'Synthetic historical policy', 'employment_status': 'unconfirmed'})
        self.save({'name': '  ', 'organization': None, 'role': '\t'})
        result = self.evaluate(company='Synthetic Alpha')
        self.assertEqual('none', result['declaration'])
        self.assertEqual('default', result['source'])
        self.assertEqual(4, result['ignored_placeholder_count'])
        self.assertEqual([], result['unresolved'])

    def test_known_employer_is_matched_exactly_in_company_scope(self):
        row = self.save({'relation': 'Synthetic relation', 'name': 'Synthetic Person',
                         'organization': 'Synthetic Alpha Holdings', 'employment_status': 'current'},
                        source='synthetic_import', status='confirmed')
        result = self.evaluate(company='Synthetic Alpha', organizations=['Synthetic Alpha Holdings'])
        self.assertEqual('present', result['declaration'])
        self.assertEqual('records', result['source'])
        self.assertEqual([row], result['matches'])
        self.assertFalse(result['requires_review'])
        self.assertEqual('none', self.evaluate(company='Synthetic Alpha')['declaration'])
        self.assertEqual('none', self.evaluate(company='Synthetic Beta')['declaration'])
        self.assertEqual('present', self.evaluate()['declaration'])

    def test_partial_real_information_is_reviewed_instead_of_defaulting_none(self):
        for data, issue in [({'name': 'Synthetic Person'}, 'employment_status_unknown'),
                            ({'organization': 'Synthetic Alpha'}, 'employment_status_unknown'),
                            ({'role': 'Synthetic Role', 'employment_status': 'unknown'}, 'employment_status_unknown'),
                            ({'employment_status': 'current'}, 'organization_missing'),
                            ({'employment_status': 'former'}, 'organization_missing'),
                            ({'organization': '未知', 'employment_status': 'current'}, 'organization_missing'),
                            ({'organization': 'unconfirmed', 'employment_status': 'former'}, 'organization_missing'),
                            ({'organization': 'Synthetic Alpha', 'employment_status': 'retired'}, 'employment_status_unknown')]:
            with self.subTest(data=data):
                row = self.save(data, status='confirmed')
                result = self.evaluate(company='Synthetic Alpha')
                self.assertEqual('review', result['declaration'])
                self.assertTrue(result['requires_review'])
                self.assertEqual('records', result['source'])
                self.assertIn(issue, result['unresolved'][-1]['issues'])
                self.app.execute('record.archive', {'id': row['id'], 'revision': row['revision']})

    def test_employment_status_filter_preserves_form_question_meaning(self):
        row = self.save({'organization': 'Synthetic Alpha', 'employment_status': 'former'}, status='confirmed')
        self.assertEqual([row], self.evaluate(company='Synthetic Alpha')['matches'])
        result = self.evaluate(company='Synthetic Alpha', employment_statuses=['current'])
        self.assertEqual('none', result['declaration'])
        self.assertEqual('records', result['source'])
        self.assertFalse(result['requires_review'])
        self.save({'name': 'Synthetic Person', 'employment_status': 'none'}, status='confirmed')
        self.assertEqual('none', self.evaluate(company='Synthetic Beta')['declaration'])

    def test_company_scoped_information_cannot_be_used_for_another_company_or_global(self):
        row = self.save({'organization': 'Synthetic Alpha', 'employment_status': 'current'},
                        scope='company:alpha', status='confirmed')
        result = self.evaluate(company='alpha', organizations=['Synthetic Alpha'])
        self.assertEqual([row], result['matches'])
        result = self.evaluate(company='Synthetic Beta')
        self.assertEqual('none', result['declaration'])
        self.assertEqual('default', result['source'])
        self.assertEqual(1, result['excluded_by_scope_count'])
        result = self.evaluate()
        self.assertEqual('review', result['declaration'])
        self.assertIn('company_scope_required', result['unresolved'][0]['issues'])

    def test_positive_match_does_not_hide_other_incomplete_details(self):
        matched = self.save({'organization': 'Synthetic Alpha', 'employment_status': 'current'}, status='confirmed')
        partial = self.save({'name': 'Synthetic Other Person'})
        result = self.evaluate(company='Synthetic Alpha')
        self.assertEqual('present', result['declaration'])
        self.assertTrue(result['requires_review'])
        self.assertEqual([matched], result['matches'])
        self.assertEqual(partial, result['unresolved'][0]['record'])

    def test_real_unconfirmed_or_observed_information_requires_review_even_when_it_matches_or_says_none(self):
        for status in ('unconfirmed', 'unknown', 'observed'):
            for data in ({'organization': 'Synthetic Alpha', 'employment_status': 'current'},
                         {'organization': 'Synthetic Beta', 'employment_status': 'current'},
                         {'organization': 'Synthetic Alpha', 'employment_status': 'former'},
                         {'name': 'Synthetic Person', 'employment_status': 'none'}):
                with self.subTest(status=status, data=data):
                    row = self.save(data, status=status, source='ai_draft' if status == 'unconfirmed' else 'synthetic_observation')
                    result = self.evaluate(company='Synthetic Alpha')
                    self.assertEqual('review', result['declaration'])
                    self.assertTrue(result['requires_review'])
                    self.assertEqual([], result['matches'])
                    self.assertIn('record_unconfirmed', result['unresolved'][0]['issues'])
                    self.assertEqual(row, result['unresolved'][0]['record'])
                    self.app.execute('record.archive', {'id': row['id'], 'revision': row['revision']})

    def test_confirmed_positive_match_is_preserved_but_draft_match_still_requires_review(self):
        confirmed = self.save({'organization': 'Synthetic Alpha', 'employment_status': 'current'}, status='confirmed')
        draft = self.save({'organization': 'Synthetic Alpha', 'employment_status': 'current'}, source='ai_draft')
        result = self.evaluate(company='Synthetic Alpha')
        self.assertEqual('present', result['declaration'])
        self.assertEqual([confirmed], result['matches'])
        self.assertEqual(draft, result['unresolved'][0]['record'])
        self.assertTrue(result['requires_review'])

    def test_evaluation_uses_only_current_account_records_and_never_changes_sources_or_history(self):
        kept = self.save({'organization': 'Synthetic Alpha', 'employment_status': 'current'},
                         source='synthetic_source', status='confirmed')
        archived = self.save({'name': 'Archived Synthetic Person'})
        self.app.execute('record.archive', {'id': archived['id'], 'revision': archived['revision']})
        account = self.app.execute('account.create', {'name': 'Synthetic Other Account'})
        other = self.app.for_user(account['id'])
        other.execute('record.save', {'kind': 'family', 'data': {'name': 'Other Account Person'}})
        before = self.app.execute('record.list', {'kind': 'family'})
        history = self.app.execute('history.list', {'record_id': kept['id']})
        self.assertEqual([kept], self.evaluate(company='Synthetic Alpha')['matches'])
        self.assertEqual('review', family.evaluate(other, {})['declaration'])
        self.assertEqual(before, self.app.execute('record.list', {'kind': 'family'}))
        self.assertEqual(history, self.app.execute('history.list', {'record_id': kept['id']}))
        self.assertEqual(1, len(history['items']))

    def test_invalid_arguments_and_unsupported_scopes_do_not_guess(self):
        for args in ({'unsupported': True}, {'company': ''}, {'company': 12},
                     {'organizations': ['Synthetic Alpha']}, {'company': 'Synthetic Alpha', 'organizations': 'Synthetic Alpha'},
                     {'company': 'Synthetic Alpha', 'organizations': ['']},
                     {'employment_statuses': []}, {'employment_statuses': ['unknown']},
                     {'employment_statuses': ['current', 'current']}, {'employment_statuses': [None]}):
            with self.subTest(args=args), self.assertRaises(AppError):
                family.evaluate(self.app, args)
        row = self.save({'organization': 'Synthetic Alpha', 'employment_status': 'current'}, scope='legacy:synthetic', status='confirmed')
        result = self.evaluate(company='Synthetic Alpha')
        self.assertEqual('review', result['declaration'])
        self.assertEqual(row, result['unresolved'][0]['record'])
        self.assertIn('unsupported_scope', result['unresolved'][0]['issues'])
        with self.assertRaises(AppError):
            family.execute(self.app, 'family.save', {}, 'user')
