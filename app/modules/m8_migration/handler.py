from fastapi import APIRouter, Depends

from app.clock import ArchiveRequest, PeriodQuery
from app.modules.m8_migration import repository, service
from app.store import Store, get_store

router = APIRouter(prefix="/api/migration", tags=["账龄迁移对账"])


@router.get("")
def preview(source_id: str, period: PeriodQuery, store: Store = Depends(get_store)):
    return service.preview(store, source_id, period)


@router.get("/runs")
def runs(source_id: str | None = None, store: Store = Depends(get_store)):
    with store.connect() as connection:
        return {"runs": repository.history(connection, source_id)}


@router.post("")
def publish(request: ArchiveRequest, store: Store = Depends(get_store)):
    return service.publish(store, request.source_id, request.period())


@router.get("/{publish_id}")
def detail(publish_id: str, store: Store = Depends(get_store)):
    return service.read_run(store, publish_id)


@router.post("/{publish_id}/rerun")
def rerun(publish_id: str, store: Store = Depends(get_store)):
    return service.rerun(store, publish_id)
