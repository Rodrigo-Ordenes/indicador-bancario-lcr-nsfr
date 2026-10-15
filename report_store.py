"""
report_store.py
===============
Trazabilidad de la reportería (requerimientos 3.5 y 5):
- rpt_stress_log      : cada escenario de tensión registrado, con fecha de corte,
                        shocks por línea (JSON), resultado y usuario.
- rpt_observaciones   : observaciones y acciones de mitigación de la alta
                        administración, con responsable, plazo y estado.
Ambas tablas son append-only: un cambio de estado se registra como nueva fila.
"""

import getpass
import json
import sqlite3
from datetime import datetime
from typing import Dict, Optional

import pandas as pd

import db_layer

DDL = """
CREATE TABLE IF NOT EXISTS rpt_stress_log (
    run_id         INTEGER PRIMARY KEY,
    registrado_en  TEXT NOT NULL,
    usuario        TEXT NOT NULL,
    indicador      TEXT NOT NULL,
    escenario      TEXT NOT NULL,
    fecha_corte    TEXT NOT NULL,
    ratio_base     REAL NOT NULL,
    ratio_estresado REAL NOT NULL,
    shocks_json    TEXT NOT NULL,
    comentario     TEXT
);
CREATE TABLE IF NOT EXISTS rpt_observaciones (
    obs_id         INTEGER PRIMARY KEY,
    registrado_en  TEXT NOT NULL,
    usuario        TEXT NOT NULL,
    fecha_corte    TEXT NOT NULL,
    indicador      TEXT NOT NULL,
    observacion    TEXT NOT NULL,
    accion         TEXT NOT NULL,
    responsable    TEXT NOT NULL,
    plazo          TEXT NOT NULL,
    estado         TEXT NOT NULL CHECK (estado IN ('Abierta','En curso','Cerrada'))
);
CREATE TRIGGER IF NOT EXISTS trg_rpt_stress_no_update BEFORE UPDATE ON rpt_stress_log
BEGIN SELECT RAISE(ABORT, 'rpt_stress_log es append-only'); END;
CREATE TRIGGER IF NOT EXISTS trg_rpt_obs_no_update BEFORE UPDATE ON rpt_observaciones
BEGIN SELECT RAISE(ABORT, 'rpt_observaciones es append-only; registre una nueva fila'); END;
"""


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(db_layer.DB_FILE, timeout=30)
    conn.executescript(DDL)
    return conn


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return "usuario"


def log_stress_run(indicador: str, escenario: str, fecha_corte, ratio_base: float, ratio_estresado: float,
                   shocks: Dict[str, float], comentario: Optional[str] = None) -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO rpt_stress_log (registrado_en, usuario, indicador, escenario, fecha_corte, ratio_base,
                   ratio_estresado, shocks_json, comentario) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _user(), indicador, escenario,
             pd.Timestamp(fecha_corte).strftime("%Y-%m-%d"), float(ratio_base), float(ratio_estresado),
             json.dumps(shocks, ensure_ascii=False), comentario))
        return cur.lastrowid


def stress_log(indicador: Optional[str] = None) -> pd.DataFrame:
    with _conn() as conn:
        q = "SELECT * FROM rpt_stress_log" + (" WHERE indicador = ?" if indicador else "") + " ORDER BY run_id DESC;"
        return pd.read_sql_query(q, conn, params=(indicador,) if indicador else ())


def add_observation(fecha_corte, indicador: str, observacion: str, accion: str, responsable: str,
                    plazo, estado: str = "Abierta") -> int:
    with _conn() as conn:
        cur = conn.execute(
            """INSERT INTO rpt_observaciones (registrado_en, usuario, fecha_corte, indicador, observacion, accion,
                   responsable, plazo, estado) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);""",
            (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), _user(), pd.Timestamp(fecha_corte).strftime("%Y-%m-%d"),
             indicador, observacion, accion, responsable, pd.Timestamp(plazo).strftime("%Y-%m-%d"), estado))
        return cur.lastrowid


def observations() -> pd.DataFrame:
    with _conn() as conn:
        return pd.read_sql_query("SELECT * FROM rpt_observaciones ORDER BY obs_id DESC;", conn)
