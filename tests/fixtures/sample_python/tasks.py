"""Sample Celery tasks module for PythonAdapter test fixture.

Exercises:
- Third-party import (celery)
- Decorator-based registration (@celery.task)
"""
from celery import Celery

celery = Celery(__name__)


@celery.task
def process(data):
    """Background processing task."""
    return {"processed": data}


@celery.task
def cleanup():
    """Cleanup task."""
    pass
