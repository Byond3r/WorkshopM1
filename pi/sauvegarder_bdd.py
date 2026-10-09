"""Sauvegarde de la base SQLite, possible même pendant que le serveur écrit dedans.

Lancer sur le Pi :  python3 /home/user/sentinel/sauvegarder_bdd.py
"""
import sqlite3
from datetime import datetime
from pathlib import Path

DOSSIER = Path(__file__).resolve().parent
FICHIER_BDD = DOSSIER / "sentinel.db"
DOSSIER_SAUVEGARDES = DOSSIER / "sauvegardes"


def sauvegarder():
    DOSSIER_SAUVEGARDES.mkdir(mode=0o700, exist_ok=True)
    destination = DOSSIER_SAUVEGARDES / f"sentinel_{datetime.now():%Y%m%d_%H%M%S}.db"
    source = sqlite3.connect(FICHIER_BDD)
    copie = sqlite3.connect(destination)
    # backup() copie la base de façon cohérente ; un simple "cp" pendant une écriture
    # pourrait copier un fichier à moitié modifié, donc corrompu
    source.backup(copie)
    copie.close()
    source.close()
    destination.chmod(0o600)   # la sauvegarde contient les mêmes données : même protection
    return destination


if __name__ == "__main__":
    print(f"Sauvegarde créée : {sauvegarder()}")
