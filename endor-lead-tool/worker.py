"""Arka plan iş kuyruğu (threading tabanlı, Celery/Redis gereksiz).

v1.1'de Impressum çekimi, v1.2'de Clutch crawl bu kuyruğa iş olarak eklenecek.
Her kayıt işlendiği anda DB'ye yazılır (sonda toplu değil), iş kesilirse kaldığı
yerden devam eder, bir kaydın hatası kuyruğu durdurmaz.
"""
from __future__ import annotations
import threading
import queue
import traceback

_q: "queue.Queue[tuple]" = queue.Queue()
_status = {"running": False, "done": 0, "total": 0, "errors": 0, "current": None, "job_name": None}
_lock = threading.Lock()


def enqueue(func, *args, job_name=None, **kwargs):
    with _lock:
        if not _status["running"] and _q.empty():
            _status["done"] = 0
            _status["total"] = 0
            _status["errors"] = 0
        _status["total"] += 1
        if job_name:
            _status["job_name"] = job_name
    _q.put((func, args, kwargs, job_name))
    _ensure_worker()


def status() -> dict:
    with _lock:
        return dict(_status)


def cancel():
    """Kuyruktaki tüm bekleyen işleri temizler ve durumu sıfırlar."""
    with _lock:
        while not _q.empty():
            try:
                _q.get_nowait()
                _q.task_done()
            except Exception:
                break
        _status["running"] = False
        _status["current"] = None
        _status["done"] = 0
        _status["total"] = 0
        _status["errors"] = 0
        _status["job_name"] = None


def _ensure_worker():
    with _lock:
        if _status["running"]:
            return
        _status["running"] = True
    t = threading.Thread(target=_loop, daemon=True)
    t.start()


def _loop():
    while True:
        try:
            item = _q.get(timeout=1)
            func, args, kwargs, job_name = item[0], item[1], item[2], item[3]
            with _lock:
                if job_name:
                    _status["job_name"] = job_name
        except queue.Empty:
            with _lock:
                _status["running"] = False
                _status["current"] = None
            return
        try:
            func(*args, **kwargs)
        except Exception:
            with _lock:
                _status["errors"] += 1
            traceback.print_exc()
        finally:
            with _lock:
                _status["done"] += 1
            _q.task_done()

