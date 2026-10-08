import os
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

APP_DIR = Path(__file__).resolve().parent
load_dotenv(APP_DIR / ".env")

DATABASE_FILE = Path(os.getenv("DB_FILE", "data/capteurs.sqlite"))
if not DATABASE_FILE.is_absolute():
    DATABASE_FILE = APP_DIR / DATABASE_FILE
DATABASE_FILE.parent.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("API_KEY", "")
FRONTEND_ORIGINS = [
    origin.strip()
    for origin in os.getenv("FRONTEND_ORIGINS", "http://localhost:5173").split(",")
    if origin.strip()
]

if not API_KEY:
    raise RuntimeError("API_KEY doit être renseigné dans le fichier .env")


@contextmanager
def database_connection():
    connection = sqlite3.connect(DATABASE_FILE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    schema = (APP_DIR / "schema.sql").read_text(encoding="utf-8")
    with database_connection() as connection:
        connection.executescript(schema)


initialize_database()

app = FastAPI(title="API des capteurs (SQLite)", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"],
)


class DeviceInput(BaseModel):
    device_name: str = Field(min_length=1, max_length=150)
    location: str | None = Field(default=None, max_length=200)


class MeasurementInput(BaseModel):
    device_id: int = Field(gt=0, description="Identifiant d'un appareil enregistré")
    metric: str = Field(min_length=1, max_length=100)
    value: float = Field(allow_inf_nan=False)
    unit: str | None = Field(default=None, max_length=30)
    measured_at: datetime | None = None


def check_api_key(provided_key: str | None) -> None:
    if provided_key is None or not secrets.compare_digest(provided_key, API_KEY):
        raise HTTPException(status_code=401, detail="Clé API incorrecte")


def normalize_timestamp(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    elif value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


@app.get("/api/health")
def health():
    try:
        with database_connection() as connection:
            connection.execute("SELECT 1").fetchone()
        return {"status": "ok", "database": "connected", "engine": "SQLite"}
    except sqlite3.Error as error:
        print("Échec de connexion SQLite :", error)
        raise HTTPException(status_code=503, detail="Base SQLite indisponible") from error


@app.post("/api/devices", status_code=201)
def create_device(
    device: DeviceInput,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    check_api_key(x_api_key)
    try:
        with database_connection() as connection:
            cursor = connection.execute(
                "INSERT INTO devices (device_name, location) VALUES (?, ?)",
                (device.device_name, device.location),
            )
            device_id = cursor.lastrowid
            row = connection.execute(
                "SELECT device_id, device_name, location FROM devices WHERE device_id = ?",
                (device_id,),
            ).fetchone()
        return {"message": "Appareil enregistré", "data": dict(row)}
    except sqlite3.Error as error:
        print("Échec d'enregistrement de l'appareil :", error)
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer l'appareil") from error


@app.get("/api/devices")
def list_devices():
    try:
        with database_connection() as connection:
            rows = connection.execute(
                "SELECT device_id, device_name, location FROM devices ORDER BY device_id"
            ).fetchall()
        return {"data": [dict(row) for row in rows], "count": len(rows)}
    except sqlite3.Error as error:
        print("Échec de lecture des appareils :", error)
        raise HTTPException(status_code=500, detail="Impossible de lire les appareils") from error


@app.post("/api/measurements", status_code=201)
def create_measurement(
    measurement: MeasurementInput,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    check_api_key(x_api_key)
    measured_at = normalize_timestamp(measurement.measured_at)
    try:
        with database_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO measurements (device_id, metric, value, unit, measured_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    measurement.device_id,
                    measurement.metric,
                    measurement.value,
                    measurement.unit,
                    measured_at,
                ),
            )
            row = connection.execute(
                """
                SELECT measurement_id, device_id, metric, value, unit, measured_at
                FROM measurements
                WHERE measurement_id = ?
                """,
                (cursor.lastrowid,),
            ).fetchone()
        return {"message": "Mesure enregistrée", "data": dict(row)}
    except sqlite3.IntegrityError as error:
        if "FOREIGN KEY constraint failed" in str(error):
            raise HTTPException(status_code=404, detail="Appareil inconnu : vérifie device_id") from error
        raise HTTPException(status_code=400, detail="Données incompatibles avec la base") from error
    except sqlite3.Error as error:
        print("Échec d'enregistrement de la mesure :", error)
        raise HTTPException(status_code=500, detail="Impossible d'enregistrer la mesure") from error


@app.get("/api/measurements")
def list_measurements(
    limit: int = Query(default=100, ge=1, le=500),
    metric: str | None = None,
):
    try:
        with database_connection() as connection:
            rows = connection.execute(
                """
                SELECT m.measurement_id, d.device_name, d.location,
                       m.metric, m.value, m.unit, m.measured_at
                FROM measurements AS m
                JOIN devices AS d ON d.device_id = m.device_id
                WHERE (? IS NULL OR m.metric = ?)
                ORDER BY m.measured_at DESC
                LIMIT ?
                """,
                (metric, metric, limit),
            ).fetchall()
        return {"data": [dict(row) for row in rows], "count": len(rows)}
    except sqlite3.Error as error:
        print("Échec de lecture des mesures :", error)
        raise HTTPException(status_code=500, detail="Impossible de lire les mesures") from error
