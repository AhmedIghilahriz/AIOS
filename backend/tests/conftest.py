"""Permet de lancer `pytest` depuis n'importe où en ajoutant backend/ au PYTHONPATH."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
