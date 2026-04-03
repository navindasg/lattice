"""Sample Flask routes module for PythonAdapter test fixture.

Exercises:
- Third-party import (flask)
- Decorator-based registration (@app.route)
"""
from flask import Flask

app = Flask(__name__)


@app.route("/")
def index():
    """Root route handler."""
    return "Hello, World!"


@app.route("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok"}
