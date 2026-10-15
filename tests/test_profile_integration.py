"""Synthetic typed-profile API, portable and company-input integration acceptance."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from service.app import App, AppError, FIELDS
from service.forms import _source_input
from service.mcp import OPERATIONS, call_tool, tool_definitions
from service.profile import CATALOG


class TypedProfileIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='career-typed-profile-integration-')
        self.addCleanup(self.tmp.cleanup)
        self.app = App(Path(self.tmp.name) / 'private')

    def save(self, kind, data, **metadata):
        return self.app.execute('record.save', {'kind': kind, 'data': data, **metadata})

    def test_typed_values_roundtrip_patch_and_catalog_fields_agree(self):
        for kind, definition in CATALOG['records'].items():
            self.assertEqual(FIELDS[kind], {field['key'] for field in definition['fields']})
        row = self.save('education', {'school': 'Synthetic University', 'start_date': '2020',
                                     'double_degree': False, 'gpa': 3.75,
                                     'research_directions': ['Synthetic vision', 'Synthetic learning']})
        updated = call_tool(self.app, 'career_save', {'operation': 'profile.patch',
                           'args': {'id': row['id'], 'revision': row['revision'],
                                    'changes': {'double_degree': True, 'gpa': 4, 'graduation_date': '2026-09'}}})
        self.assertIs(True, updated['data']['double_degree'])
        self.assertEqual(4, updated['data']['gpa'])
        self.assertEqual('2020', updated['data']['start_date'])
        self.assertEqual(['Synthetic vision', 'Synthetic learning'], updated['data']['research_directions'])
        self.assertEqual('unconfirmed', updated['status'])
        self.assertEqual(updated['data'], self.app.execute('record.list', {'kind': 'education'})['items'][0]['data'])

    def test_invalid_typed_values_category_fields_and_identity_checksum_reject(self):
        for data in [{'double_degree': 'false'}, {'gpa': True}, {'gpa': float('inf')},
                     {'research_directions': '["Synthetic"]'}, {'research_directions': [4]},
                     {'start_date': '2026-13'}, {'start_date': '2026-02-30'}]:
            with self.assertRaises(AppError):
                self.save('education', {'school': 'Synthetic University', **data})
        with self.assertRaises(AppError):
            self.save('experience', {'title': 'Synthetic Project', 'category': 'project', 'authors': ['Synthetic Author']})
        with self.assertRaises(AppError) as error:
            self.save('basic', {'id_number': '110101' + '19900101' + '0010'})
        self.assertEqual('invalid_identifier', error.exception.code)

    def test_company_source_mapping_preserves_array_boolean_and_numeric_values(self):
        row = self.save('education', {'school': 'Synthetic University', 'double_degree': False,
                                     'gpa': 3.25, 'research_directions': ['Synthetic vision']}, status='confirmed')
        package = {'id': 'synthetic-company', 'form': {'input_schema': {'type': 'object', 'properties': {
            'directions': {'type': 'array', 'items': {'type': 'string'}, 'x-source': 'education.research_directions'},
            'double_degree': {'type': 'boolean', 'x-source': 'education.double_degree'},
            'gpa': {'type': 'number', 'x-source': 'education.gpa'},
            'educations': {'type': 'array', 'x-source': 'education', 'items': {'type': 'object', 'properties': {
                'school': {'type': 'string', 'x-source': 'school'},
                'directions': {'type': 'array', 'items': {'type': 'string'}, 'x-source': 'research_directions'}}}}}}}}
        inputs, pins, issues = _source_input(self.app, package, {'education': [row['id']]})
        self.assertEqual([], issues)
        self.assertEqual(['Synthetic vision'], inputs['directions'])
        self.assertIs(False, inputs['double_degree'])
        self.assertEqual(3.25, inputs['gpa'])
        self.assertEqual(['Synthetic vision'], inputs['educations'][0]['directions'])
        self.assertEqual([{'id': row['id'], 'kind': 'education', 'revision': 1}], pins)
        self.assertNotIn('profile_sources', inputs)

    def test_mcp_declares_typed_patch_and_relation_operations_but_no_migration_apply(self):
        definitions = {tool['name']: tool for tool in tool_definitions()}
        self.assertIn('profile.relation.list', OPERATIONS['career_get'])
        self.assertIn('profile.relation.save', OPERATIONS['career_save'])
        self.assertIn('profile.relation.archive', OPERATIONS['career_save'])
        self.assertFalse(any('profile.migration.apply' in operations for operations in OPERATIONS.values()))
        branches = definitions['career_save']['inputSchema']['allOf']
        patch = next(branch['then']['properties']['args'] for branch in branches
                     if branch['if']['properties']['operation']['const'] == 'profile.patch')
        self.assertEqual(['boolean', 'null'], patch['properties']['changes']['properties']['double_degree']['type'])
        self.assertEqual(['array', 'null'], patch['properties']['changes']['properties']['authors']['type'])
        relation = next(branch['then']['properties']['args'] for branch in branches
                        if branch['if']['properties']['operation']['const'] == 'profile.relation.save')
        self.assertIn({'required': ['from_id', 'to_id', 'relation_type']}, relation['anyOf'])
        self.assertIn({'required': ['id', 'revision']}, relation['anyOf'])
        with self.assertRaises(AppError) as error:
            self.app.execute('profile.migration.apply', {}, actor='codex')
        self.assertEqual('user_action_required', error.exception.code)

    def test_field_evidence_and_history_undo_share_transaction(self):
        row = self.save('education', {'school': 'Synthetic University', 'gpa': 3.5}, source='synthetic_document', status='confirmed')
        updated = self.save('education', {'school': 'Synthetic University', 'gpa': 3.8}, id=row['id'], revision=1)
        event = self.app.execute('history.list', {'record_id': row['id']})['items'][0]
        undone = self.app.execute('history.undo', {'event_id': event['id'], 'revision': updated['revision']})
        self.assertEqual(3.5, undone['data']['gpa'])
        sources = self.app.execute('profile.export')['profile_sources']
        gpa_evidence = [source for source in sources if source['record_id'] == row['id'] and source['field_path'] == 'gpa']
        self.assertTrue(any(source['value'] == 3.8 for source in gpa_evidence))
        self.assertTrue(any(source['value'] == 3.5 and source['revision'] == 3 for source in gpa_evidence))

    def test_portable2_remaps_relations_preserves_evidence_and_reimport_user_edits(self):
        project = self.save('experience', {'title': 'Synthetic Project', 'category': 'project',
                                          'technologies': ['Synthetic toolkit'], 'is_current': False}, status='confirmed')
        paper = self.save('experience', {'title': 'Synthetic Paper', 'category': 'publication',
                                        'authors': ['Synthetic Author'], 'author_position': 1, 'year': 2025}, status='confirmed')
        self.app.execute('profile.relation.save', {'from_id': project['id'], 'to_id': paper['id'],
                         'relation_type': 'project_publication', 'status': 'confirmed'})
        portable = self.app.execute('profile.export')
        self.assertEqual(3, portable['schema_version'])
        self.assertEqual(1, len(portable['profile_relations']))
        other = App(Path(self.tmp.name) / 'other-private')
        self.assertEqual(2, other.execute('profile.import', {'profile': portable})['imported'])
        restored = other.execute('profile.export')
        ids = {record['data']['title']: record['id'] for record in restored['records']}
        link = restored['profile_relations'][0]
        self.assertEqual(ids['Synthetic Project'], link['from_id'])
        self.assertEqual(ids['Synthetic Paper'], link['to_id'])
        self.assertTrue(all(source['record_id'] in ids.values() for source in restored['profile_sources']))
        local_project = next(record for record in restored['records'] if record['data']['title'] == 'Synthetic Project')
        other.execute('profile.patch', {'id': local_project['id'], 'revision': local_project['revision'],
                      'changes': {'title': 'Applicant Revised Project'}})
        count = len(other.execute('profile.export')['profile_sources'])
        self.assertEqual(0, other.execute('profile.import', {'profile': portable})['imported'])
        reimported = other.execute('profile.export')
        self.assertEqual(count, len(reimported['profile_sources']))
        self.assertEqual(1, len(reimported['profile_relations']))
        self.assertTrue(any(record['data']['title'] == 'Applicant Revised Project' for record in reimported['records']))

    def test_portable2_unknown_keys_and_invalid_extra_relations_are_atomic(self):
        portable = {'schema_version': 2, 'records': [{'id': 'synthetic:education', 'kind': 'education',
                    'data': {'school': 'Synthetic University', 'double_degree': False}}],
                    'profile_sources': [], 'profile_relations': []}
        with self.assertRaises(AppError):
            self.app.execute('profile.import', {'profile': {**portable, 'vault': {'password': 'Synthetic secret'}}})
        invalid = copy.deepcopy(portable)
        invalid['profile_relations'] = [{'id': 'synthetic:relation', 'from_id': 'synthetic:education',
                                        'to_id': 'missing:record', 'relation_type': 'project_publication'}]
        with self.assertRaises(AppError):
            self.app.execute('profile.import', {'profile': invalid})
        self.assertEqual(0, self.app.execute('record.list')['total'])
        legacy = {'schema_version': 1, 'records': portable['records'], 'vault': {'password': 'Synthetic ignored'}}
        self.assertEqual(1, self.app.execute('profile.import', {'profile': legacy})['imported'])
        self.assertNotIn('vault', self.app.execute('profile.export'))


if __name__ == '__main__':
    unittest.main()
