import ipaddress
import json
import logging
import secrets
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

from flask import Flask, Response, request, send_from_directory
from werkzeug.security import check_password_hash
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
RESEAU_ULTRON = ipaddress.ip_network("10.42.0.0/24")
PORT_CAMERA = 8000   # flux MJPEG servi par pc/detection.py
# Badges RFID : fichiers propres au Pi, jamais versionnés dans git
FICHIER_BADGES = DOSSIER / "badges_autorises.txt"   # une ligne par badge : UID;nom
FICHIER_CLE_API = DOSSIER / "cle_api.txt"          # secret partagé avec l'ESP32
# Login du dashboard : une ligne "utilisateur:empreinte du mot de passe" (jamais le mot de passe en clair)
FICHIER_IDENTIFIANTS = DOSSIER / "identifiants_dashboard.txt"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.FileHandler(FICHIER_LOG), logging.StreamHandler()],
)
log = logging.getLogger("sentinel")

app = Flask(__name__)
# Au-delà, Flask refuse la requête (erreur 413) : empêche de remplir la carte SD avec d'énormes JSON
app.config["MAX_CONTENT_LENGTH"] = 4 * 1024
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


# ---------- Login du dashboard (authentification HTTP Basic) ----------
# Seules les routes de CONSULTATION sont protégées : les envois des capteurs (POST) ne sont pas concernés.
ROUTES_CONSULTATION = {"dashboard", "lister_mesures", "lister_alertes", "status", "reseau", "relayer_camera"}


def identifiants_valides(autorisation):
    if autorisation is None or not FICHIER_IDENTIFIANTS.exists():
        return False   # sans fichier d'identifiants, personne n'entre (on ne laisse pas ouvert par défaut)
    utilisateur, _, empreinte = FICHIER_IDENTIFIANTS.read_text(encoding="utf-8").strip().partition(":")
    # Les deux vérifications sont TOUJOURS faites : si un mauvais nom d'utilisateur répondait plus vite
    # qu'un mauvais mot de passe, un attaquant pourrait deviner les noms valides en chronométrant
    utilisateur_ok = secrets.compare_digest(autorisation.username or "", utilisateur)
    mot_de_passe_ok = check_password_hash(empreinte, autorisation.password or "")
    return utilisateur_ok and mot_de_passe_ok


@app.before_request
def proteger_consultation():
    if request.endpoint in ROUTES_CONSULTATION and not identifiants_valides(request.authorization):
        # WWW-Authenticate demande au navigateur d'afficher sa fenêtre de connexion
        return Response("Authentification requise", 401, {"WWW-Authenticate": 'Basic realm="Sentinel-X"'})
    return None


# ---------- Validation des données reçues ----------
# Liste blanche : seuls ces champs sont acceptés. Sans ça, un JSON contenant "horodatage" ou "source"
# écraserait ces colonnes à l'affichage (ligne_vers_dict), et n'importe quoi finirait dans la base.
CHAMPS_NUMERIQUES_MESURE = {"temperature": (-40, 125), "gaz": (0, 10000)}   # plages physiques plausibles
CHAMPS_BINAIRES_MESURE = {"mouvement_1", "mouvement_2", "proximite", "son"}  # 0 ou 1
LONGUEUR_MAX_TEXTE = 32
LONGUEUR_MAX_UID = 20


def est_nombre(valeur):
    # En Python, True est aussi un int : on l'exclut explicitement
    return isinstance(valeur, (int, float)) and not isinstance(valeur, bool)


def erreur_texte(data, champ):
    """Message d'erreur si le champ n'est pas un texte court, sinon None."""
    valeur = data.get(champ)
    if not isinstance(valeur, str) or not 0 < len(valeur) <= LONGUEUR_MAX_TEXTE:
        return f"'{champ}' doit être un texte de 1 à {LONGUEUR_MAX_TEXTE} caractères"
    return None


def erreur_champs_inconnus(data, champs_autorises):
    inconnus = set(data) - champs_autorises
    return f"champ(s) non autorisé(s) : {', '.join(sorted(inconnus))}" if inconnus else None


def erreur_mesure(data):
    """Raison du refus d'une mesure, ou None si elle est valide."""
    if not isinstance(data, dict):
        return "objet JSON attendu"
    champs_autorises = {"capteur"} | set(CHAMPS_NUMERIQUES_MESURE) | CHAMPS_BINAIRES_MESURE
    erreur = erreur_champs_inconnus(data, champs_autorises) or erreur_texte(data, "capteur")
    if erreur:
        return erreur
    for champ, (minimum, maximum) in CHAMPS_NUMERIQUES_MESURE.items():
        if champ in data and not (est_nombre(data[champ]) and minimum <= data[champ] <= maximum):
            return f"'{champ}' doit être un nombre entre {minimum} et {maximum}"
    for champ in CHAMPS_BINAIRES_MESURE:
        if champ in data and data[champ] not in (0, 1):
            return f"'{champ}' doit valoir 0 ou 1"
    return None


def erreur_alerte(data):
    """Raison du refus d'une alerte, ou None si elle est valide."""
    if not isinstance(data, dict):
        return "objet JSON attendu"
    erreur = erreur_champs_inconnus(data, {"alerte", "confiance"}) or erreur_texte(data, "alerte")
    if erreur:
        return erreur
    if "confiance" in data and not (est_nombre(data["confiance"]) and 0 <= data["confiance"] <= 1):
        return "'confiance' doit être un nombre entre 0 et 1"
    return None


def uid_valide(uid):
    """Un UID de badge est un court texte hexadécimal (ex. A1B2C3D4)."""
    return 0 < len(uid) <= LONGUEUR_MAX_UID and all(c in "0123456789ABCDEF" for c in uid)


# ---------- Réception ----------

@app.route("/mesure", methods=["POST"])
def recevoir_mesure():
    data = request.get_json(silent=True)
    erreur = erreur_mesure(data)
    if erreur:
        log.warning("Mesure refusée de %s : %s", request.remote_addr, erreur)
        return {"status": "erreur", "message": erreur}, 400

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
    erreur = erreur_alerte(data)
    if erreur:
        log.warning("Alerte refusée de %s : %s", request.remote_addr, erreur)
        return {"status": "erreur", "message": erreur}, 400
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

    data = request.get_json(silent=True)
    uid = str(data.get("uid", "")).upper() if isinstance(data, dict) else ""
    if not uid_valide(uid):
        log.warning("Badge refusé de %s : UID invalide", request.remote_addr)
        return {"status": "erreur", "message": "UID de badge invalide"}, 400
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


@app.route("/camera", methods=["GET"])
def relayer_camera():
    """Relaie le flux vidéo du PC : le dashboard en HTTPS l'affiche sans contenu mixte (HTTP dans HTTPS)."""
    try:
        adresse = ipaddress.ip_address(request.args.get("ip", ""))
    except ValueError:
        return {"status": "erreur", "message": "IP invalide"}, 400
    # Seulement vers une machine d'Ultron, port et chemin fixes : sinon le Pi pourrait servir
    # de relais vers n'importe quelle adresse (attaque SSRF)
    if adresse not in RESEAU_ULTRON:
        return {"status": "erreur", "message": "IP hors du réseau Ultron"}, 403
    try:
        flux = urllib.request.urlopen(f"http://{adresse}:{PORT_CAMERA}/video", timeout=5)
    except OSError:
        return {"status": "erreur", "message": "caméra injoignable"}, 502

    def relayer():
        try:
            # read1 renvoie ce qui est déjà arrivé, sans attendre 16 Ko : l'image s'affiche sans retard
            while bloc := flux.read1(16384):
                yield bloc
        finally:
            flux.close()   # le navigateur a fermé l'onglet : on coupe aussi la connexion vers le PC

    return Response(relayer(), content_type=flux.headers.get("Content-Type"))


# ---------- Dashboard ----------

@app.route("/", methods=["GET"])
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


def proteger_fichiers():
    """Base et journal lisibles par le seul utilisateur du service (en plus du dossier déjà en 700)."""
    for fichier in (FICHIER_BDD, FICHIER_LOG):
        fichier.chmod(0o600)


init_bdd()
proteger_fichiers()
entrainer()   # si la base contient déjà assez de mesures, le modèle est prêt dès le démarrage

if __name__ == "__main__":
    log.info("Serveur Sentinel démarré (base : %s)", FICHIER_BDD)
    demarrer_https()
    app.run(host="0.0.0.0", port=PORT_HTTP, threaded=True)
