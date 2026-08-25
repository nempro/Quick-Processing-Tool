from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from threading import Event
from typing import Callable

from .errors import UpscaleCancelledError, UpscaleError
from .models import UpscaleOptions, UpscaleResult
from .service import UpscaleService


LOGGER = logging.getLogger(__name__)


class QueueStatus(str, Enum):
    WAITING = "waiting"
    PROCESSING = "processing"
    SAVING = "saving"
    DONE = "done"
    FAILED = "failed"
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
) -> BatchOutcome:
    """Process one item at a time and keep already verified outputs on cancellation."""
    started = time.perf_counter()
    succeeded = 0
    failed = 0
    cancelled = 0
    processed = 0
    total = len(jobs)
    LOGGER.info(
        "Batch upscale start: total=%d scale=%sx mode=%s",
        total,
        options.scale,
        options.mode.value,
    )

    for position, job in enumerate(jobs, start=1):
        if cancel_event.is_set():
            for remaining in jobs[position - 1 :]:
                callbacks.status(remaining.queue_index, QueueStatus.CANCELLED, "未処理")
                cancelled += 1
            break

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
            callbacks.status(job.queue_index, QueueStatus.FAILED, str(exc))
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
    outcome = BatchOutcome(total, succeeded, failed, cancelled, duration)
    LOGGER.info(
        "Upscale batch finished: total=%d succeeded=%d failed=%d cancelled=%d duration=%.3fs",
        total,
        succeeded,
        failed,
        cancelled,
        duration,
    )
    return outcome