"""Sample main module for PythonAdapter test fixture.

Exercises:
- Standard stdlib import (os)
- Relative import with __init__.py re-export chain
- Dynamic imports (importlib.import_module and __import__)
- __name__ == "__main__" guard
"""
import importlib
import os

from .utils import helper_fn

dynamic_mod = importlib.import_module("dynamic_mod")
another = __import__("another")


def run():
    """Main entry point function."""
    result = helper_fn()
    return result


if __name__ == "__main__":
    run()
