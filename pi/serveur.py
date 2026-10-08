import json
import logging
import secrets
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, request, send_from_directory
from werkzeug.serving import make_server

# Chemins ABSOLUS : sous systemd, le dossier courant n'est pas celui du script
DOSSIER = Path(__file__).resolve().parent
FICHIER_BDD = DOSSIER / "sentinel.db"
FICHIER_LOG = DOSSIER / "serveur.log"
DELAI_HORS_LIGNE = 15  # secondes sans mesure -> nœud considéré hors ligne
FICHIER_RESEAU = Path("/run/sentinel/reseau.json")  # écrit chaque seconde par capture_reseau.py
DELAI_CAPTURE_ARRETEE = 10  # fichier plus vieux que ça -> la capture est considérée arrêtée
PORT_HTTP = 5000            # dashboard (et anciens envois en clair)
PORT_HTTPS = 5443           # envois chiffrés (TLS) de l'ESP32 et de detection.py
CERTIFICAT_TLS = DOSSIER / "tls" / "certificat.pem"   # créés par generer_certificat.sh
CLE_TLS = DOSSIER / "tls" / "cle.pem"
# Badges RFID : fichiers propres au Pi, jamais versionnés dans git
FICHIER_BADGES = DOSSIER / "badges_autorises.txt"   # une ligne par badge : UID;nom
FICHIER_CLE_API = DOSSIER / "cle_api.txt"          # secret partagé avec l'ESP32

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(FICHIER_LOG), logging.StreamHandler()],
)
log = logging.getLogger("sentinel")

app = Flask(__name__)
demarrage = datetime.now()
systeme_arme = True   # au démarrage, le système est armé (choix le plus sûr)


# ---------- IA : détection d'anomalies (Isolation Forest) ----------
# Principe : le modèle apprend à quoi ressemblent des mesures "normales",
# puis signale celles qui s'en écartent. Pas de seuil fixe écrit à la main.
try:
    from sklearn.ensemble import IsolationForest
    IA_DISPONIBLE = True
except ImportError:          # le serveur tourne quand même sans scikit-learn
    IA_DISPONIBLE = False

CARACTERISTIQUES = ["temperature", "gaz"]   # colonnes utilisées par le modèle
MIN_ENTRAINEMENT = 30                       # mesures nécessaires avant de juger
REENTRAINER_TOUS = 50                       # ré-apprentissage régulier
modele = None
mesures_depuis_entrainement = 0


def vecteur(data):
    """Extrait [temperature, gaz] si toutes les valeurs sont présentes, sinon None."""
    try:
        return [float(data[c]) for c in CARACTERISTIQUES]
    except (KeyError, TypeError, ValueError):
        return None


def entrainer():
    """Entraîne le modèle sur les 500 dernières mesures en base."""
    global modele, mesures_depuis_entrainement
    if not IA_DISPONIBLE:
        return
    with connexion() as conn:
        lignes = conn.execute(
            "SELECT donnees FROM mesures ORDER BY id DESC LIMIT 500"
        ).fetchall()
    X = [v for v in (vecteur(json.loads(l["donnees"])) for l in lignes) if v]
    if len(X) < MIN_ENTRAINEMENT:
        return
    # contamination = proportion d'anomalies attendue dans les données (5 %)
    modele = IsolationForest(n_estimators=100, contamination=0.05, random_state=42)
    modele.fit(X)
    mesures_depuis_entrainement = 0
    log.info("Isolation Forest entraîné sur %d mesures", len(X))


def analyser(data):
    """Ajoute 'anomalie' (bool) et 'score_anomalie' à la mesure si le modèle est prêt."""
    global mesures_depuis_entrainement
    v = vecteur(data)
    if v is None or not IA_DISPONIBLE:
        return data
    mesures_depuis_entrainement += 1
    if modele is None or mesures_depuis_entrainement >= REENTRAINER_TOUS:
        entrainer()
    if modele is not None:
        # predict : 1 = normal, -1 = anomalie ; score : plus c'est bas, plus c'est anormal
        data["anomalie"] = bool(modele.predict([v])[0] == -1)
        data["score_anomalie"] = round(float(modele.score_samples([v])[0]), 3)
    return data


# ---------- Base de données ----------

def connexion():
    """Une connexion par requête : SQLite n'aime pas partager une connexion entre threads."""
    conn = sqlite3.connect(FICHIER_BDD)
    conn.row_factory = sqlite3.Row  # accès aux colonnes par nom
    return conn


def init_bdd():
    with connexion() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS mesures (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                horodatage TEXT NOT NULL,
                source     TEXT,
                capteur    TEXT,
                donnees    TEXT NOT NULL      -- JSON brut envoyé par le nœud
            );
            CREATE TABLE IF NOT EXISTS alertes (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                horodatage TEXT NOT NULL,
                source     TEXT,
                type       TEXT,
                donnees    TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mesures_date ON mesures(horodatage);
            CREATE INDEX IF NOT EXISTS idx_alertes_date ON alertes(horodatage);
        """)


def maintenant():
    return datetime.now().isoformat(timespec="seconds")


def ligne_vers_dict(ligne):
    """Remet à plat une ligne SQL : colonnes + contenu du JSON."""
    return {
        "id": ligne["id"],
        "horodatage": ligne["horodatage"],
        "source": ligne["source"],
        **json.loads(ligne["donnees"]),
    }


# ---------- Réception ----------

@app.route("/mesure", methods=["POST"])
def recevoir_mesure():
    data = request.get_json(silent=True)
    if data is None:
        log.warning("Mesure invalide reçue de %s", request.remote_addr)
        return {"status": "erreur", "message": "JSON attendu"}, 400

    data = analyser(data)   # verdict de l'IA ajouté avant l'enregistrement

    with connexion() as conn:
        # Requête paramétrée (?) : jamais de concaténation -> pas d'injection SQL
        conn.execute(
            "INSERT INTO mesures (horodatage, source, capteur, donnees) VALUES (?, ?, ?, ?)",
            (maintenant(), request.remote_addr, data.get("capteur"), json.dumps(data)),
        )
    log.info("MESURE %s %s", request.remote_addr, data)
    return {"status": "ok"}


def enregistrer_alerte(source, data):
    """Ajoute une alerte en base, avec l'état du système au moment où elle arrive."""
    data["systeme_arme"] = systeme_arme
    with connexion() as conn:
        conn.execute(
            "INSERT INTO alertes (horodatage, source, type, donnees) VALUES (?, ?, ?, ?)",
            (maintenant(), source, data.get("alerte"), json.dumps(data)),
        )
    log.info("ALERTE %s %s", source, data)


@app.route("/alerte", methods=["POST"])
def recevoir_alerte():
    data = request.get_json(silent=True)
    if data is None:
        log.warning("Alerte invalide reçue de %s", request.remote_addr)
        return {"status": "erreur", "message": "JSON attendu"}, 400
    enregistrer_alerte(request.remote_addr, data)
    return {"status": "ok"}


# ---------- Badges RFID (armement / désarmement) ----------

def lire_badges_autorises():
    """{UID: nom}. Relu à chaque badge : en ajouter un ne demande pas de redémarrer le serveur."""
    if not FICHIER_BADGES.exists():
        return {}
    badges = {}
    for ligne in FICHIER_BADGES.read_text(encoding="utf-8").splitlines():
        if ";" in ligne and not ligne.startswith("#"):
            uid, nom = ligne.split(";", 1)
            badges[uid.strip().upper()] = nom.strip()
    return badges


def cle_api_valide(cle_recue):
    """compare_digest compare en temps constant : la durée ne trahit pas les caractères justes."""
    if cle_recue is None or not FICHIER_CLE_API.exists():
        return False
    return secrets.compare_digest(cle_recue, FICHIER_CLE_API.read_text(encoding="utf-8").strip())


@app.route("/badge", methods=["POST"])
def recevoir_badge():
    global systeme_arme
    # Sans la clé, n'importe qui sur le Wi-Fi pourrait envoyer un UID et désarmer le système
    if not cle_api_valide(request.headers.get("X-Cle-Api")):
        log.warning("Badge refusé : clé API invalide (%s)", request.remote_addr)
        return {"status": "erreur", "message": "clé API invalide"}, 401

    data = request.get_json(silent=True) or {}
    uid = str(data.get("uid", "")).upper()
    badges = lire_badges_autorises()

    if uid not in badges:
        enregistrer_alerte(request.remote_addr, {"alerte": "badge_inconnu", "uid": uid})
        return {"status": "refuse", "arme": systeme_arme}, 403

    systeme_arme = not systeme_arme
    evenement = "armement" if systeme_arme else "desarmement"
    enregistrer_alerte(request.remote_addr, {"alerte": evenement, "uid": uid, "nom": badges[uid]})
    return {"status": "ok", "arme": systeme_arme}


# ---------- Lecture (pour le dashboard) ----------

@app.route("/mesures", methods=["GET"])
def lister_mesures():
    n = min(request.args.get("n", default=50, type=int), 1000)
    with connexion() as conn:
        lignes = conn.execute(
            "SELECT * FROM mesures ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    return {"mesures": [ligne_vers_dict(l) for l in lignes]}


@app.route("/alertes", methods=["GET"])
def lister_alertes():
    n = min(request.args.get("n", default=20, type=int), 1000)
    with connexion() as conn:
        lignes = conn.execute(
            "SELECT * FROM alertes ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
    return {"alertes": [ligne_vers_dict(l) for l in lignes]}


@app.route("/status", methods=["GET"])
def status():
    """État du serveur + de chaque nœud capteur (en ligne si mesure récente)."""
    with connexion() as conn:
        noeuds = conn.execute("""
            SELECT capteur, source, MAX(horodatage) AS derniere, COUNT(*) AS total
            FROM mesures GROUP BY capteur
        """).fetchall()
        nb_alertes = conn.execute("SELECT COUNT(*) FROM alertes").fetchone()[0]

    etat_noeuds = []
    for n in noeuds:
        age = (datetime.now() - datetime.fromisoformat(n["derniere"])).total_seconds()
        etat_noeuds.append({
            "capteur": n["capteur"],
            "source": n["source"],
            "derniere_mesure": n["derniere"],
            "en_ligne": age < DELAI_HORS_LIGNE,
            "total_mesures": n["total"],
        })

    return {
        "status": "en ligne",
        "demarre_depuis": demarrage.isoformat(timespec="seconds"),
        "noeuds": etat_noeuds,
        "total_alertes": nb_alertes,
        "ia": {"disponible": IA_DISPONIBLE, "entraine": modele is not None},
        "systeme_arme": systeme_arme,
    }


@app.route("/reseau", methods=["GET"])
def reseau():
    """Statistiques réseau produites par capture_reseau.py (tshark, moteur de Wireshark)."""
    try:
        age = time.time() - FICHIER_RESEAU.stat().st_mtime
        statistiques = json.loads(FICHIER_RESEAU.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"actif": False}
    statistiques["actif"] = age < DELAI_CAPTURE_ARRETEE
    return statistiques


# ---------- Dashboard ----------

@app.route("/")
def dashboard():
    return send_from_directory(DOSSIER, "dashboard.html")


# ---------- Démarrage ----------

def demarrer_https():
    """Second accès, chiffré (TLS), à la même application. Sans certificat, le serveur tourne quand même."""
    if not (CERTIFICAT_TLS.exists() and CLE_TLS.exists()):
        log.warning("Certificat TLS absent (%s) : HTTPS désactivé", CERTIFICAT_TLS)
        return
    serveur_https = make_server(
        "0.0.0.0", PORT_HTTPS, app, threaded=True, ssl_context=(str(CERTIFICAT_TLS), str(CLE_TLS))
    )
    threading.Thread(target=serveur_https.serve_forever, daemon=True).start()
    log.info("HTTPS actif sur le port %d", PORT_HTTPS)


init_bdd()
entrainer()   # si la base contient déjà assez de mesures, le modèle est prêt dès le démarrage

if __name__ == "__main__":
    log.info("Serveur Sentinel démarré (base : %s)", FICHIER_BDD)
    demarrer_https()
    app.run(host="0.0.0.0", port=PORT_HTTP, threaded=True)
