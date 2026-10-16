"""Run the real UI against disposable synthetic mail notices (no IMAP access).

Usage: python -B tests/mailbox_acceptance.py --port 8877
All records live in an external temporary directory and disappear on exit.
"""
import argparse
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from service.app import App
from service.http import LocalServer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8877)
    options = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='career-mail-ui-') as temporary:
        app = App(Path(temporary) / 'private', ROOT)
        application = app.execute('record.save', {'kind': 'application', 'data': {
            'company': 'Synthetic Employer', 'title': 'Synthetic Engineer',
            'stage': 'interview', 'raw_status': 'Synthetic original status'}})
        app.execute('task.save', {'title': 'Synthetic interview preparation', 'kind': 'custom', 'data': {
            'workflow': 'interview_preparation', 'schema_version': 1,
            'application_id': application['id'], 'scheduled_at': '2026-11-20T15:00:00+08:00',
            'round': None, 'selected_record_ids': [],
            'source_versions': {application['id']: application['revision']},
            'plan': [], 'project_cards': [], 'practice': [], 'summary': 'Synthetic acceptance fixture'}})
        app.execute('task.save', {'title': 'Synthetic recruitment notice', 'kind': 'custom', 'data': {
            'workflow': 'recruitment_notice', 'schema_version': 1, 'source': 'mail',
            'mail_key': 'synthetic-key', 'message_id': '<synthetic.example.invalid>',
            'notice_type': 'interview', 'subject': 'Synthetic Employer interview invitation',
            'sender': 'Synthetic recruiter', 'received_at': 'Fri, 16 Oct 2026 12:00:00 +0800',
            'observed_at': '2026-10-16T04:00:00Z', 'summary': 'Synthetic interview instructions',
            'raw_text': 'Synthetic instructions: implement a program.\n<img src=x onerror="alert(1)">',
            'time_candidates': ['11月20日 15:00'], 'scheduled_at': None,
            'application_id': None, 'match_status': 'unmatched', 'requires_review': True}})
        server = LocalServer(app, port=options.port)
        print(f'Synthetic UI only: http://127.0.0.1:{server.server_port}/', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
