import json
import os
import subprocess
import threading
import time
from collections import Counter, deque
from datetime import datetime
from pathlib import Path

# --- Configuration ---
INTERFACE = "wlan0"                                     # interface du hotspot Ultron
# /run est en RAM (pas d'usure de la carte SD) et seul root peut y écrire (pas de faux fichier)
FICHIER_SORTIE = Path("/run/sentinel/reseau.json")
DUREE_CAPTURE = 300       # tshark est relancé toutes les 5 min : sa mémoire repart de zéro (Pi 2 Go)
NB_DERNIERES_TRAMES = 40
FENETRE_DEBIT = 10        # secondes prises en compte pour calculer les paquets/s
PREFIXE_RESEAU = "10.42.0."
# Le hotspot redonne toujours la même IP à un même appareil : on peut les nommer
NOMS_CONNUS = {
    "10.42.0.1": "Raspberry Pi (serveur)",
    "10.42.0.189": "ESP32",
    "10.42.0.91": "PC portable (caméra YOLO)",
}
ADRESSES_NON_APPAREILS = {"10.42.0.0", "10.42.0.255"}   # adresse du réseau et broadcast
PORTS_SENTINEL = {"5000", "5443", "8000"}   # Flask HTTP, Flask HTTPS, flux vidéo
PROTOCOLES_EN_CLAIR = ["HTTP", "MQTT", "TELNET", "FTP"]   # contenu lisible par n'importe qui
PROTOCOLES_CHIFFRES = ["TLS", "SSH"]                       # contenu illisible sans la clé
# Du plus précis au plus général : le premier présent dans la pile donne le nom affiché
PRIORITE_PROTOCOLES = ["http", "tls", "ssh", "mqtt", "dns", "mdns", "dhcp", "arp", "icmp", "tcp", "udp"]

CHAMPS = [
    "frame.time_epoch", "ip.src", "ip.dst", "frame.protocols", "frame.len",
    "tcp.dstport", "udp.dstport",
    "http.request.method", "http.request.uri", "http.response.code", "dns.qry.name",
    "tcp.srcport",
]


def heure_actuelle():
    return datetime.now().strftime("%H:%M:%S")


class StatistiquesReseau:
    """Compteurs partagés entre le thread de capture et l'écriture du fichier."""

    def __init__(self):
        self.verrou = threading.Lock()
        self.total_paquets = 0
        self.paquets_en_clair = 0
        self.protocoles = Counter()
        self.appareils = {}
        self.dernieres_trames = deque(maxlen=NB_DERNIERES_TRAMES)
        # Trafic de Sentinel-X seulement : sinon noyé dans le bruit (DNS, mises à jour Windows...)
        self.trames_sentinel = deque(maxlen=NB_DERNIERES_TRAMES)
        self.instants_recents = deque()   # moment de chaque paquet des FENETRE_DEBIT dernières secondes

    def ajouter(self, trame):
        with self.verrou:
            self.total_paquets += 1
            self.protocoles[trame["protocole"]] += 1
            if trame["en_clair"]:
                self.paquets_en_clair += 1
            self.dernieres_trames.appendleft(trame)
            if trame["sentinel"]:
                self.trames_sentinel.appendleft(trame)
            self.instants_recents.append(time.time())
            for ip in (trame["source"], trame["destination"]):
                if ip.startswith(PREFIXE_RESEAU) and ip not in ADRESSES_NON_APPAREILS:
                    self._compter_appareil(ip, trame["taille"])

    def _compter_appareil(self, ip, taille):
        appareil = self.appareils.setdefault(ip, {
            "ip": ip, "nom": NOMS_CONNUS.get(ip, "Appareil inconnu"),
            "paquets": 0, "octets": 0, "derniere_vue": "",
        })
        appareil["paquets"] += 1
        appareil["octets"] += taille
        appareil["derniere_vue"] = heure_actuelle()

    def instantane(self):
        """Copie des statistiques à un instant donné, prête à être écrite en JSON."""
        with self.verrou:
            limite = time.time() - FENETRE_DEBIT
            while self.instants_recents and self.instants_recents[0] < limite:
                self.instants_recents.popleft()
            appareils = sorted(self.appareils.values(), key=lambda a: a["paquets"], reverse=True)
            return {
                "mis_a_jour": datetime.now().isoformat(timespec="seconds"),
                "interface": INTERFACE,
                "total_paquets": self.total_paquets,
                "paquets_par_seconde": round(len(self.instants_recents) / FENETRE_DEBIT, 1),
                "paquets_en_clair": self.paquets_en_clair,
                "protocoles": dict(self.protocoles.most_common()),
                "protocoles_en_clair": PROTOCOLES_EN_CLAIR,
                "protocoles_chiffres": PROTOCOLES_CHIFFRES,
                "appareils": [dict(appareil) for appareil in appareils],
                "dernieres_trames": list(self.dernieres_trames),
                "trames_sentinel": list(self.trames_sentinel),
            }


def nom_protocole(pile):
    """'eth:ethertype:ip:tcp:http:json' -> 'HTTP'"""
    couches = pile.split(":")
    for protocole in PRIORITE_PROTOCOLES:
        if protocole in couches:
            return protocole.upper()
    return couches[-1].upper() or "?"


def decrire(champs):
    if champs["http.request.method"]:
        return f'{champs["http.request.method"]} {champs["http.request.uri"]}'
    if champs["http.response.code"]:
        return f'Réponse HTTP {champs["http.response.code"]}'
    if champs["dns.qry.name"]:
        return f'Requête DNS {champs["dns.qry.name"]}'
    port = champs["tcp.dstport"] or champs["udp.dstport"]
    return f"port {port}" if port else ""


def analyser_ligne(ligne):
    """Transforme une ligne de tshark (champs séparés par des tabulations) en trame."""
    valeurs = ligne.rstrip("\n").split("\t")
    valeurs += [""] * (len(CHAMPS) - len(valeurs))   # tshark omet les derniers champs vides
    champs = dict(zip(CHAMPS, valeurs))
    protocole = nom_protocole(champs["frame.protocols"])
    ports = {champs["tcp.srcport"], champs["tcp.dstport"]}
    return {
        "heure": datetime.fromtimestamp(float(champs["frame.time_epoch"])).strftime("%H:%M:%S"),
        "source": champs["ip.src"] or "-",
        "destination": champs["ip.dst"] or "-",
        "protocole": protocole,
        "taille": int(champs["frame.len"] or 0),
        "info": decrire(champs),
        "en_clair": protocole in PROTOCOLES_EN_CLAIR,
        "chiffre": protocole in PROTOCOLES_CHIFFRES,
        "sentinel": bool(ports & PORTS_SENTINEL),   # échange avec un service de Sentinel-X
    }


def commande_tshark():
    commande = [
        "tshark", "-i", INTERFACE,
        "-n",                                  # pas de résolution de noms : plus rapide
        "-l",                                  # une ligne envoyée par paquet, sans attendre
        "-a", f"duration:{DUREE_CAPTURE}",
        # Flask (5000) et le flux vidéo (8000) parlent HTTP sur des ports non standard
        "-d", "tcp.port==5000,http",
        "-d", "tcp.port==8000,http",
        "-d", "tcp.port==5443,tls",            # HTTPS des capteurs, sur un port non standard
        "-T", "fields", "-E", "separator=/t", "-E", "occurrence=f",
    ]
    for champ in CHAMPS:
        commande += ["-e", champ]
    return commande


def capturer_en_continu(statistiques):
    while True:
        processus = subprocess.Popen(
            commande_tshark(), stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace"
        )
        for ligne in processus.stdout:
            try:
                statistiques.ajouter(analyser_ligne(ligne))
            except ValueError:
                continue   # ligne incomplète : on l'ignore
        processus.wait()
        time.sleep(3)      # fin des 5 min ou interface absente : on relance tshark


def ecrire_statistiques(statistiques):
    fichier_temporaire = FICHIER_SORTIE.with_suffix(".tmp")
    fichier_temporaire.write_text(json.dumps(statistiques.instantane(), ensure_ascii=False), encoding="utf-8")
    # Remplacement atomique : Flask ne lit jamais un fichier à moitié écrit
    os.replace(fichier_temporaire, FICHIER_SORTIE)


def main():
    FICHIER_SORTIE.parent.mkdir(exist_ok=True)   # lisible par tous, modifiable par root seulement
    statistiques = StatistiquesReseau()
    threading.Thread(target=capturer_en_continu, args=(statistiques,), daemon=True).start()
    print(f"Capture réseau sur {INTERFACE} -> {FICHIER_SORTIE}")
    while True:
        ecrire_statistiques(statistiques)
        time.sleep(1)


if __name__ == "__main__":
    main()
