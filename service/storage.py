"""Shared local database schema; migrations never inspect applicant values."""
from __future__ import annotations

SCHEMA_VERSION = 2


def initialize(db, timestamp):
    """Atomically add account scope to legacy databases, retaining the default user."""
    db.execute("BEGIN IMMEDIATE")
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version > SCHEMA_VERSION:
        raise ValueError("Database schema is newer than this service.")
    db.execute("""CREATE TABLE IF NOT EXISTS accounts (
      id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL)""")
    db.execute("INSERT OR IGNORE INTO accounts VALUES ('default',?,?)", ("默认用户", timestamp))
    statements = [
        """CREATE TABLE IF NOT EXISTS records (
          id TEXT PRIMARY KEY, kind TEXT NOT NULL, data TEXT NOT NULL,
          revision INTEGER NOT NULL, source TEXT NOT NULL, scope TEXT NOT NULL,
          status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          archived INTEGER NOT NULL DEFAULT 0, user_id TEXT NOT NULL DEFAULT 'default')""",
        """CREATE TABLE IF NOT EXISTS events (
          id TEXT PRIMARY KEY, record_id TEXT NOT NULL, kind TEXT NOT NULL,
          action TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL,
          before_json TEXT, after_json TEXT, user_id TEXT NOT NULL DEFAULT 'default')""",
        """CREATE TABLE IF NOT EXISTS documents (
          id TEXT PRIMARY KEY, original_name TEXT NOT NULL, stored_name TEXT NOT NULL,
          sha256 TEXT NOT NULL, size INTEGER NOT NULL, mime_type TEXT NOT NULL,
          extracted_text TEXT NOT NULL, extraction_status TEXT NOT NULL,
          created_at TEXT NOT NULL, archived INTEGER NOT NULL DEFAULT 0,
          user_id TEXT NOT NULL DEFAULT 'default')""",
        """CREATE TABLE IF NOT EXISTS settings (
          user_id TEXT PRIMARY KEY, data TEXT NOT NULL, revision INTEGER NOT NULL)""",
        """CREATE TABLE IF NOT EXISTS imports (
          user_id TEXT NOT NULL, identity TEXT NOT NULL, record_id TEXT NOT NULL,
          imported_at TEXT NOT NULL, PRIMARY KEY(user_id,identity))""",
    ]
    for statement in statements:
        db.execute(statement)
    for table in ("records", "events", "documents"):
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if "user_id" not in columns:
            db.execute(f"ALTER TABLE {table} ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")
    settings_columns = {row[1] for row in db.execute("PRAGMA table_info(settings)")}
    if "user_id" not in settings_columns:
        db.execute("ALTER TABLE settings RENAME TO settings_legacy")
        db.execute("CREATE TABLE settings (user_id TEXT PRIMARY KEY,data TEXT NOT NULL,revision INTEGER NOT NULL)")
        db.execute("INSERT INTO settings SELECT 'default',data,revision FROM settings_legacy WHERE id=1")
        db.execute("DROP TABLE settings_legacy")
    import_columns = {row[1] for row in db.execute("PRAGMA table_info(imports)")}
    if "user_id" not in import_columns:
        db.execute("ALTER TABLE imports RENAME TO imports_legacy")
        db.execute("""CREATE TABLE imports (user_id TEXT NOT NULL,identity TEXT NOT NULL,
          record_id TEXT NOT NULL,imported_at TEXT NOT NULL,PRIMARY KEY(user_id,identity))""")
        db.execute("INSERT INTO imports SELECT 'default',identity,record_id,imported_at FROM imports_legacy")
        db.execute("DROP TABLE imports_legacy")
    for statement in (
        "CREATE INDEX IF NOT EXISTS records_user_kind ON records(user_id,kind,archived)",
        "CREATE INDEX IF NOT EXISTS events_user_record ON events(user_id,record_id,at)",
        "CREATE INDEX IF NOT EXISTS documents_user ON documents(user_id,archived)",
        """CREATE TABLE IF NOT EXISTS tasks (
          id TEXT PRIMARY KEY,user_id TEXT NOT NULL,title TEXT NOT NULL,kind TEXT NOT NULL,
          instructions TEXT NOT NULL,data TEXT NOT NULL,status TEXT NOT NULL,
          revision INTEGER NOT NULL,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
          created_by TEXT NOT NULL,updated_by TEXT NOT NULL)""",
        "CREATE INDEX IF NOT EXISTS tasks_user_status ON tasks(user_id,status,updated_at)",
        """CREATE TABLE IF NOT EXISTS task_events (
          id TEXT PRIMARY KEY,user_id TEXT NOT NULL,task_id TEXT NOT NULL,
          actor TEXT NOT NULL,at TEXT NOT NULL,before_json TEXT,after_json TEXT NOT NULL)""",
    ):
        db.execute(statement)
    db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
