import json

from app.errors import Problem

FIELDS = "publish_id, source_id, rule_version, generated_at, elapsed_ms, result_digest"


def find(connection, publish_id):
    row = connection.execute(f"SELECT {FIELDS} FROM migration_runs WHERE publish_id=?", (publish_id,)).fetchone()
    return dict(row) if row else None


def get(connection, publish_id):
    row = connection.execute(f"SELECT {FIELDS}, payload FROM migration_runs WHERE publish_id=?", (publish_id,)).fetchone()
    if row is None:
        raise Problem("RUN_NOT_FOUND", "迁移对账批次不存在", 404)
    return {"manifest": {key: row[key] for key in row.keys() if key != "payload"}, "result": json.loads(row["payload"])}


def insert(connection, manifest, payload):
    values = tuple(manifest[field.strip()] for field in FIELDS.split(","))
    cursor = connection.execute(f"INSERT INTO migration_runs ({FIELDS}, payload) VALUES (?, ?, ?, ?, ?, ?, ?) "
                                "ON CONFLICT(publish_id) DO NOTHING", (*values, payload))
    return cursor.rowcount == 1


def history(connection, source_id):
    rows = connection.execute(f"SELECT {FIELDS} FROM migration_runs WHERE (? IS NULL OR source_id=?) "
                              "ORDER BY generated_at DESC, publish_id LIMIT 100", (source_id, source_id))
    return [dict(row) for row in rows]
