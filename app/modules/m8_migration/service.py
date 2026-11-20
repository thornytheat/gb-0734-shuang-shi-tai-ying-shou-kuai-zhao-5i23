import sqlite3
from datetime import datetime, timezone
from time import perf_counter_ns

from app.clock import period_query, utc_text
from app.config import RULE_VERSION
from app.errors import Problem
from app.evidence import canonical, digest
from app.modules.m1_intake.service import load
from app.modules.m6_bridge.service import period_book
from app.modules.m8_migration import repository
from app.modules.m8_migration.records import DUE_BASIS, migration_records, unexplained

KIND = "aging-migration-reconciliation"


def publish_id_for(source_id, period):
    inputs = {"kind": KIND, "source_id": source_id, "rule_version": RULE_VERSION, "period": period.public()}
    return digest(canonical(inputs))


def reconciliation(data, period):
    book = period_book(data, period)
    bridge = book["bridge"]
    return {"period": period.public(), "status": bridge["status"],
            "publishable": bridge["status"] == "MATCHED", "due_basis": DUE_BASIS,
            "unexplained": unexplained(bridge), "buckets": bridge["buckets"],
            "migrations": migration_records(book)}


def preview(store, source_id, period):
    result = reconciliation(load(store, source_id), period)
    publish_id = publish_id_for(source_id, period)
    with store.connect() as connection:
        published = repository.find(connection, publish_id)
    return {**result, "publish_id": publish_id, "published": published}


def read_run(store, publish_id):
    with store.connect() as connection:
        return repository.get(connection, publish_id)


def publish(store, source_id, period):
    start = perf_counter_ns()
    publish_id = publish_id_for(source_id, period)
    result = reconciliation(load(store, source_id), period)
    if not result["publishable"]:
        raise Problem("UNEXPLAINED_DIFFERENCE", "账龄迁移存在未解释差额，不能发布", 409)
    payload = canonical(result)
    manifest = {"publish_id": publish_id, "source_id": source_id, "rule_version": RULE_VERSION,
                "elapsed_ms": (perf_counter_ns() - start) // 1_000_000,
                "generated_at": utc_text(datetime.now(timezone.utc)), "result_digest": digest(payload)}
    try:
        with store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            created = repository.insert(connection, manifest, payload)
            stored = repository.get(connection, publish_id)
            if (stored["manifest"]["result_digest"] != manifest["result_digest"]
                    or digest(canonical(stored["result"])) != manifest["result_digest"]):
                raise Problem("MIGRATION_MISMATCH", "重算摘要与发布记录不符", 409)
    except sqlite3.Error as exc:
        raise Problem("MIGRATION_WRITE_FAILED", "迁移对账写入失败，请重跑", 503) from exc
    return {"created": created, "manifest": stored["manifest"], "period": period.public(),
            "status": result["status"], "affected": len(result["migrations"])}


def rerun(store, publish_id):
    stored = read_run(store, publish_id)
    if stored["manifest"]["rule_version"] != RULE_VERSION:
        raise Problem("RULE_VERSION_CHANGED", "发布使用的规则版本已变更", 409)
    period = stored["result"]["period"]
    opening, closing = period["opening"], period["closing"]
    cuts = period_query(opening["effective"], opening["known"], closing["effective"], closing["known"])
    if publish_id_for(stored["manifest"]["source_id"], cuts) != publish_id:
        raise Problem("MIGRATION_MISMATCH", "发布参数与批次编号不符", 409)
    return publish(store, stored["manifest"]["source_id"], cuts)
