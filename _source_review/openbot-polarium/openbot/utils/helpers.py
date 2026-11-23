"""Config loading and helpers."""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


def load_config(config_path: str = "config.yaml", env_path: str = ".env") -> dict:
    load_dotenv(env_path)
    cfg: dict[str, Any] = {}
    p = Path(config_path)
    if p.exists():
        with open(p) as f:
            cfg = yaml.safe_load(f) or {}
    return cfg


def setup_logging(level: str = "INFO", log_file: str | None = None) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=handlers,
        force=True,
    )


def env(key, default=None): return os.getenv(key, default)
def env_int(key, d=0):
    try: return int(os.getenv(key, d))
    except (TypeError, ValueError): return d
def env_float(key, d=0.0):
    try: return float(os.getenv(key, d))
    except (TypeError, ValueError): return d
def env_bool(key, d=False):
    return os.getenv(key, str(d)).lower().strip() in ("1", "true", "yes", "y", "on")
