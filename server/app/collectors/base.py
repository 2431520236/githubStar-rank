"""通用多线程执行器(所有采集器与 AI 批量任务复用)。

对应手册《05 · 采集器与多线程机制》1. base.py。
"""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Iterable, Optional, Sequence

log = logging.getLogger("ghradar.collector")


class Progress:
    """线程安全进度计数。"""

    def __init__(self, total: int = 0):
        self._lock = threading.Lock()
        self.total = total
        self.done = 0
        self.ok = 0
        self.failed = 0

    def tick(self, success: bool = True) -> None:
        with self._lock:
            self.done += 1
            if success:
                self.ok += 1
            else:
                self.failed += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {"total": self.total, "done": self.done, "ok": self.ok, "failed": self.failed}


def run_threaded(items: Sequence[Any], worker: Callable[[Any], Any], concurrency: int = 6,
                 on_result: Optional[Callable[[Any], None]] = None,
                 stop_flag: Optional[threading.Event] = None) -> dict:
    """多线程执行。返回 {ok, failed, results, errors[:50], total}。"""
    items = list(items)
    workers = max(1, min(int(concurrency or 1), 32))
    results: list[Any] = []
    errors: list[str] = []
    ok = failed = 0
    lock = threading.Lock()

    skipped = 0

    def _is_stop(e: BaseException) -> bool:
        """额度耗尽/主动中止 → 记为「跳过」而不是「失败」。
        否则 5000 个任务里只有几百个真跑, 其余全被记成 failed, 统计完全失真。"""
        if stop_flag is not None and stop_flag.is_set():
            return True
        s = str(e)
        return ("额度已耗尽" in s) or ("额度不足" in s) or isinstance(e, StopIteration)

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="radar") as ex:
        futures = {}
        for it in items:
            if stop_flag is not None and stop_flag.is_set():
                break
            futures[ex.submit(_safe, worker, it)] = it
        for fut in as_completed(futures):
            item = futures[fut]
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                with lock:
                    if _is_stop(e):
                        skipped += 1
                    else:
                        failed += 1
                        if len(errors) < 50:
                            errors.append(f"{_name_of(item)}: {e}")
                continue
            with lock:
                ok += 1
                results.append(res)
            if on_result is not None:
                try:
                    on_result(res)
                except Exception as e:  # noqa: BLE001
                    with lock:
                        if len(errors) < 50:
                            errors.append(f"on_result({_name_of(item)}): {e}")
    return {"ok": ok, "failed": failed, "skipped": skipped,
            "results": results, "errors": errors[:50], "total": len(items)}


def _safe(worker: Callable[[Any], Any], item: Any) -> Any:
    return worker(item)


def _name_of(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("full_name") or item.get("repo_id") or item)
    return str(item)
