"""Checkout asset locations, independent of internal package nesting."""

from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parent
BACKEND_ROOT = PACKAGE_ROOT.parent
REPO_ROOT = BACKEND_ROOT.parent
