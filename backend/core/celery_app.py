from dotenv import load_dotenv
from pathlib import Path
from celery import Celery
from celery.schedules import crontab
import os

load_dotenv(dotenv_path=Path(__file__).resolve().parents[2] / ".env")

def _get_redis_url():
    url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    # Upstash rediss:// requiert ssl_cert_reqs pour Celery
    if url.startswith("rediss://") and "ssl_cert_reqs" not in url:
        sep = "&" if "?" in url else "?"
        url = url + sep + "ssl_cert_reqs=CERT_NONE"
    return url

REDIS_URL = _get_redis_url()

celery_app = Celery(
    "aios",
    broker=REDIS_URL,
    backend=REDIS_URL
)

celery_app.conf.beat_schedule = {
    # Vérifier les délais toutes les heures
    "verifier-deadlines": {
        "task": "workers.tasks.verifier_deadlines",
        "schedule": crontab(minute=0),
    },
    # Relances documents non reçus — chaque matin à 9h
    "relances-documents": {
        "task": "workers.tasks.relances_documents",
        "schedule": crontab(hour=9, minute=0),
    },
    # Relances impayés — chaque jour à 10h
    "relances-impayes": {
        "task": "workers.tasks.relances_impayes",
        "schedule": crontab(hour=10, minute=0),
    },
    # Sync emails toutes les 15 minutes
    "sync-emails": {
        "task": "workers.tasks.synchroniser_tous_les_emails",
        "schedule": crontab(minute="*/15"),
    },
    # Veille réglementaire — chaque matin à 7h
    "veille-reglementaire": {
        "task": "workers.tasks.scanner_veille_reglementaire",
        "schedule": crontab(hour=7, minute=0),
    },
    # AIOS-FIX: cas 13 — radar d'inactivité (dossiers qui stagnent) — chaque matin à 8h
    "radar-inactivite": {
        "task": "workers.tasks.radar_inactivite",
        "schedule": crontab(hour=8, minute=0),
    },
}

celery_app.conf.task_routes = {
    "workers.tasks.verifier_deadlines": {"queue": "high_priority"},
    "workers.tasks.relances_*": {"queue": "default"},
}
