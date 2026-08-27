from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Callable

from .errors import UpscaleCancelledError, UpscaleError
from ..image_workspace import MissingSourceError
from .models import UpscaleOptions, UpscaleResult
from .service import UpscaleService


LOGGER = logging.getLogger(__name__)


class QueueStatus(str, Enum):
    WAITING = "waiting"
    WARNING = "warning"
    SKIPPED = "skipped"
    PROCESSING = "processing"
    SAVING = "saving"
    DONE = "done"
    FAILED = "failed"
    MISSING = "missing"
    CANCELLED = "cancelled"


@dataclass
class UpscaleQueueItem:
    source_path: Path
    width: int
    height: int
    source_format: str
    size_bytes: int
    has_alpha: bool
    status: QueueStatus = QueueStatus.WAITING
    detail: str = ""
    result: UpscaleResult | None = None


@dataclass(frozen=True)
class BatchJob:
    queue_index: int
    source_path: Path
    output_folder: Path


@dataclass(frozen=True)
class BatchOutcome:
    total: int
    succeeded: int
    failed: int
    cancelled: int
    duration_seconds: float
    skipped: int = 0


@dataclass(frozen=True)
class BatchCallbacks:
    status: Callable[[int, QueueStatus, str], None]
    current: Callable[[int, int, int, str], None]
    result: Callable[[int, UpscaleResult], None]
    progress: Callable[[int, int], None]


def run_sequential_batch(
    service: UpscaleService,
    jobs: list[BatchJob],
    options: UpscaleOptions,
    cancel_event: Event,
    callbacks: BatchCallbacks,
    skipped_jobs: list[BatchJob] | None = None,
) -> BatchOutcome:
    """Process one item at a time and keep already verified outputs on cancellation."""
    started = time.perf_counter()
    succeeded = 0
    failed = 0
    cancelled = 0
    skipped = 0
    processed = 0
    skipped_jobs = skipped_jobs or []
    total = len(jobs) + len(skipped_jobs)
    LOGGER.info(
        "Batch upscale start: total=%d scale=%sx mode=%s",
        total,
        options.scale,
        options.mode.value,
    )

    for skipped_job in skipped_jobs:
        callbacks.status(
            skipped_job.queue_index,
            QueueStatus.SKIPPED,
            "大きすぎる可能性があるためスキップ",
        )
        skipped += 1
        processed += 1
        callbacks.progress(processed, total)

    for position, job in enumerate(jobs, start=1):
        if cancel_event.is_set():
            for remaining in jobs[position - 1 :]:
                callbacks.status(remaining.queue_index, QueueStatus.CANCELLED, "未処理")
                cancelled += 1
            break

        if not job.source_path.is_file():
            LOGGER.warning("Upscale batch source missing: %s", job.source_path)
            callbacks.status(job.queue_index, QueueStatus.MISSING, "元画像なし")
            failed += 1
            processed += 1
            callbacks.progress(processed, total)
            continue

        LOGGER.info("[%d/%d] start %s", position, total, job.source_path)
        callbacks.current(job.queue_index, position, total, job.source_path.name)
        callbacks.status(job.queue_index, QueueStatus.PROCESSING, "高画質化中")

        def stage_changed(stage: str) -> None:
            if stage == "saving":
                callbacks.status(job.queue_index, QueueStatus.SAVING, "保存・確認中")

        try:
            result = service.run(
                job.source_path,
                job.output_folder,
                options,
                lambda _value: None,
                cancel_event,
                stage=stage_changed,
            )
        except MissingSourceError:
            callbacks.status(job.queue_index, QueueStatus.MISSING, "元画像なし")
            failed += 1
        except UpscaleCancelledError:
            callbacks.status(job.queue_index, QueueStatus.CANCELLED, "キャンセル")
            cancelled += 1
            for remaining in jobs[position:]:
                callbacks.status(remaining.queue_index, QueueStatus.CANCELLED, "未処理")
                cancelled += 1
            break
        except UpscaleError as exc:
            LOGGER.exception("Upscale batch item failed: %s", job.source_path)
            callbacks.status(job.queue_index, QueueStatus.FAILED, str(exc))
            failed += 1
        except Exception as exc:
            LOGGER.exception("Unexpected upscale batch item failure: %s", job.source_path)
            status = QueueStatus.MISSING if not job.source_path.is_file() else QueueStatus.FAILED
            detail = "元画像なし" if status is QueueStatus.MISSING else str(exc)
            callbacks.status(job.queue_index, status, detail)
            failed += 1
        else:
            callbacks.result(job.queue_index, result)
            callbacks.status(job.queue_index, QueueStatus.DONE, result.output_path.name)
            LOGGER.info(
                "[%d/%d] saved %s duration=%.3fs",
                position,
                total,
                result.output_path,
                result.duration_seconds,
            )
            succeeded += 1

        processed += 1
        callbacks.progress(processed, total)

    duration = time.perf_counter() - started
    outcome = BatchOutcome(total, succeeded, failed, cancelled, duration, skipped)
    LOGGER.info(
        "Batch upscale finished: total=%d succeeded=%d failed=%d cancelled=%d skipped=%d duration=%.3fs",
        total,
        succeeded,
        failed,
        cancelled,
        skipped,
        duration,
    )
    return outcome
