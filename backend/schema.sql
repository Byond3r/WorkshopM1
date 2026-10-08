-- Schéma SQLite du projet capteurs.
-- L'API crée automatiquement le fichier et applique ce schéma au démarrage.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS devices (
    device_id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name TEXT NOT NULL,
    location TEXT
);

CREATE TABLE IF NOT EXISTS measurements (
    measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id INTEGER NOT NULL,
    metric TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT,
    measured_at TEXT NOT NULL,
    FOREIGN KEY (device_id) REFERENCES devices(device_id)
);

CREATE INDEX IF NOT EXISTS idx_measurements_measured_at
    ON measurements (measured_at DESC);
