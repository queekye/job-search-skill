"""Regenerate portable profile fields from the shared editable-field catalog."""
import argparse
import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generate(schema, catalog):
    result = copy.deepcopy(schema)
    result['title'] = 'Job Search Skill portable private profile v3 (legacy v1 and v2 imports)'
    result['properties']['schema_version'] = {'enum': [1, 2, 3]}
    for kind, record in catalog['records'].items():
        props = {}
        for field in record['fields']:
            if 'json_schema' in field:
                props[field['key']] = copy.deepcopy(field['json_schema'])
                continue
            typ = {'string_array': 'array'}.get(field['value_type'], field['value_type'])
            definition = {'type': [typ, 'null']}
            for src, target in [('max_length', 'maxLength'), ('minimum', 'minimum'), ('maximum', 'maximum')]:
                if src in field:
                    definition[target] = field[src]
            if typ == 'array':
                definition.update(maxItems=field['max_items'], items={'type': 'string', 'minLength': 1, 'maxLength': field['item_max_length']})
            if 'options' in field and (kind, field['key']) != ('family', 'employment_status'):
                if typ == 'array':
                    definition['items']['enum'] = list(field['options'])
                else:
                    definition['enum'] = list(field['options']) + [None]
            if field.get('format') == 'partial_date':
                definition['pattern'] = r'^(?:\d{4}(?:-\d{2})?(?:-\d{2})?)?$'
            props[field['key']] = definition
        result['$defs'][kind] = {'type': 'object', 'minProperties': 1, 'additionalProperties': False, 'properties': props}
    kinds = list(catalog['records']) + ['application']
    result['$defs']['record']['properties']['kind'] = {'enum': kinds}
    result['$defs']['record']['allOf'] = [
        {'if': {'properties': {'kind': {'const': kind}}},
         'then': {'properties': {'data': {'$ref': f'#/$defs/{kind}'}}}}
        for kind in kinds
    ]
    status = {'enum': ['unconfirmed', 'confirmed', 'observed', 'unknown']}
    short = {'type': 'string', 'minLength': 1, 'maxLength': 500}
    identifier = {'type': 'string', 'minLength': 1, 'maxLength': 100}
    result['$defs']['profile_source'] = {'type': 'object', 'additionalProperties': False,
        'required': ['id', 'record_id', 'field_path', 'revision', 'source', 'scope', 'status', 'value', 'created_at'],
        'properties': {'id': identifier, 'record_id': identifier, 'field_path': short,
                       'revision': {'type': 'integer', 'minimum': 1}, 'source': short, 'scope': short, 'status': status,
                       'value': {'type': ['string', 'number', 'boolean', 'array', 'object', 'null']}, 'created_at': short}}
    result['$defs']['profile_relation'] = {'type': 'object', 'additionalProperties': False,
        'required': ['id', 'from_id', 'to_id', 'relation_type', 'revision', 'source', 'status', 'created_at', 'updated_at', 'archived'],
        'properties': {'id': identifier, 'from_id': identifier, 'to_id': identifier,
                       'relation_type': {'enum': list(catalog['relations']['types'])}, 'revision': {'type': 'integer', 'minimum': 1},
                       'source': short, 'status': status, 'created_at': short, 'updated_at': short, 'archived': {'const': False}}}
    result['properties']['profile_sources'] = {'type': 'array', 'maxItems': 50000, 'items': {'$ref': '#/$defs/profile_source'}}
    result['properties']['profile_relations'] = {'type': 'array', 'maxItems': 10000, 'items': {'$ref': '#/$defs/profile_relation'}}
    result['allOf'] = [{'if': {'properties': {'schema_version': {'const': 1}}},
                        'then': {'not': {'anyOf': [{'required': ['profile_sources']}, {'required': ['profile_relations']}]}}},
                       {'if': {'properties': {'schema_version': {'enum': [1, 2]}}},
                        'then': {'properties': {'records': {'items': {
                            'properties': {'kind': {'not': {'enum': catalog['domain_kinds']}}}
                        }}}}}]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    path = ROOT / 'schemas' / 'profile.schema.json'
    current = json.loads(path.read_text(encoding='utf-8'))
    catalog = json.loads((ROOT / 'schemas' / 'profile-fields.json').read_text(encoding='utf-8'))
    generated = generate(current, catalog)
    if args.check:
        if current != generated:
            raise SystemExit('Portable profile schema is stale. Run scripts/generate_profile_schema.py.')
    else:
        path.write_text(json.dumps(generated, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
