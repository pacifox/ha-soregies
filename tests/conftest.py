"""Chargement de la couche API sans le runtime Home Assistant.

`custom_components/soregies/api.py` ne dépend que d'`aiohttp` : c'est une
propriété délibérée, qui permet de tester le dialogue avec le portail sans
installer Home Assistant. Mais le module utilise des imports relatifs
(`from .const import …`), qui exigent un paquet parent.

On fabrique donc un paquet parent minimal dont on fixe seulement le
`__path__`, sans exécuter le véritable `__init__.py` — celui-ci importe
Home Assistant en entier et masquerait toute dépendance introduite par
mégarde dans la couche API.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

PACKAGE = "soregies_under_test"
SOURCE = Path(__file__).resolve().parents[1] / "custom_components" / "soregies"


def _load(module_name: str) -> types.ModuleType:
    full_name = f"{PACKAGE}.{module_name}"
    if full_name in sys.modules:
        return sys.modules[full_name]
    spec = importlib.util.spec_from_file_location(full_name, SOURCE / f"{module_name}.py")
    if spec is None or spec.loader is None:  # pragma: no cover - défense
        raise ImportError(f"{module_name} introuvable dans {SOURCE}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    spec.loader.exec_module(module)
    return module


def _install_parent() -> None:
    if PACKAGE in sys.modules:
        return
    parent = types.ModuleType(PACKAGE)
    parent.__path__ = [str(SOURCE)]
    sys.modules[PACKAGE] = parent


_install_parent()

const = _load("const")
api = _load("api")
