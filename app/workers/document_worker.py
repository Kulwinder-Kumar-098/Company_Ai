"""Poll and process durable document-ingestion jobs."""

import logging
import signal
import threading

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.core.config import settings
from app.core.database import engine
from app.services.document_job_queue import process_one_document_job

logger = logging.getLogger(__name__)


def ensure_document_job_schema(database_engine: Engine) -> None:
    if not inspect(database_engine).has_table("document_processing_jobs"):
        raise RuntimeError(
            "The document processing queue table is missing. "
            "Run `alembic upgrade head` before starting the document worker."
        )


def run_worker(stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        try:
            has_work = process_one_document_job()
        except Exception:
            logger.exception("Document worker failed while polling the job queue.")
            has_work = False

        if not has_work:
            stop_event.wait(settings.document_worker_poll_seconds)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    stop_event = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop_event.set())
    signal.signal(signal.SIGINT, lambda *_: stop_event.set())
    ensure_document_job_schema(engine)
    logger.info("Starting document processing worker.")
    run_worker(stop_event)
    logger.info("Document processing worker stopped.")


if __name__ == "__main__":
    main()
