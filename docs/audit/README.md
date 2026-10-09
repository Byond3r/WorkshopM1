# Audit de sécurité — Sentinel-X

Audit réalisé le 2026-10-08 sur le serveur Raspberry Pi 4 (`10.42.0.1`) et les flux du réseau Ultron.
Chaque affirmation de ce document renvoie à une preuve du dossier `docs/audit/`.

## 1. Périmètre

| Élément | Rôle | Exposition réseau |
|---|---|---|
| Raspberry Pi 4 (Debian 13) | serveur Flask, base SQLite, hotspot Wi-Fi Ultron | SSH 22, HTTP 5000, HTTPS 5443 |
| ESP32 WROOM 32 | nœud capteurs sans fil | client uniquement |
| PC portable (Windows) | caméra + YOLOv8n | flux vidéo MJPEG sur le port 8000 |
| Wi-Fi Ultron | réseau dédié `10.42.0.0/24`, WPA2 | — |

## 2. Méthode

1. **État initial** : relevé des ports en écoute et du pare-feu sur le Pi (`ss -ltnup`, `ufw status`).
2. **Durcissement** : script reproductible [`pi/durcir.sh`](../../pi/durcir.sh), appliqué avec un minuteur de secours
   (`ufw disable` automatique au bout de 15 min si la connexion était perdue).
3. **Vérification depuis l'extérieur** : connexions TCP depuis un poste **hors** du réseau Ultron.
4. **Chiffrement** : capture réseau sur le Pi (`tshark`), analysée dans Wireshark.

## 3. Constat initial ([`ports_avant.txt`](ports_avant.txt))

- **Pare-feu inactif** : tout service en écoute est joignable par n'importe quel appareil.
- **5 services exposés**, dont **rpcbind** (port 111, partages NFS), inutile ici et cible classique de reconnaissance.
- **Tous les flux en HTTP, en clair** : mesures, alertes et dashboard lisibles par tout appareil du Wi-Fi.

## 4. Mesures appliquées

| Mesure | Détail | Preuve |
|---|---|---|
| Pare-feu ufw | entrée refusée par défaut ; SSH limité (anti force brute) ; HTTP 5000 et HTTPS 5443 réservés à `10.42.0.0/24` ; DHCP et DNS autorisés sur `wlan0` (sinon l'ESP32 n'obtient plus d'IP) | [`ufw_status_final.txt`](ufw_status_final.txt) |
| Services inutiles | rpcbind désactivé | [`durcissement.txt`](durcissement.txt) |
| SSH | connexion directe en root interdite (`PermitRootLogin no`, configuration vérifiée par `sshd -t` avant rechargement) | [`durcissement.txt`](durcissement.txt) |
| Comptes | mot de passe de `user` changé | — |
| Chiffrement | HTTPS (TLS) sur le port 5443 pour l'ESP32 et la caméra, certificat auto-signé valable 1 an | [`preuve_wireshark.pcap`](preuve_wireshark.pcap) |
| Moindre privilège | serveur Flask et capteurs en utilisateur simple ; clé privée TLS en `600` et hors de git | [`pi/sentinel.service`](../../pi/sentinel.service) |
| Application | requêtes SQL paramétrées (pas d'injection SQL) ; dashboard protégé contre le XSS (tout texte reçu du réseau est échappé) ; route `/badge` protégée par clé API comparée en temps constant | [`pi/serveur.py`](../../pi/serveur.py), [`pi/dashboard.html`](../../pi/dashboard.html) |

## 5. Résultats : avant / après

| Critère | Avant | Après |
|---|---|---|
| Pare-feu | inactif | actif, refus par défaut |
| Ports TCP en écoute | 22, 111, 5000 | 22, 5000, 5443 ([`ports_apres.txt`](ports_apres.txt)) |
| Flask (5000) depuis un autre réseau | ouvert | **filtré** ([`test_pare_feu_depuis_xiaomi.txt`](test_pare_feu_depuis_xiaomi.txt)) |
| rpcbind (111) | ouvert | **filtré**, service désactivé |
| Envois des capteurs | HTTP en clair | **TLS** (port 5443) |

« Filtré » signifie que le Pi ne répond même pas : un scanner ne peut pas savoir si un service existe derrière.

## 6. Preuve du chiffrement ([`preuve_wireshark.pcap`](preuve_wireshark.pcap))

Ouvrir le fichier dans Wireshark, puis **Analyser → Décoder comme… → TCP port 5443 → TLS** (port non standard).

| Trames | Lecture |
|---|---|
| 1 à 3 | ouverture TCP de l'ESP32 (`10.42.0.189`) vers le Pi, port 5443 |
| 4 | *Client Hello* TLS 1.2 : l'ESP32 propose de chiffrer |
| 6 | *Server Hello, Certificate, Server Key Exchange* : le Pi présente son certificat, échange de clé de session |
| 10 à 14 | *Change Cipher Spec* : tout est chiffré à partir d'ici |
| 16, 17, 19, 21 | *Application Data* : la mesure, **illisible** (*Suivre → Flux TCP*) |
| 22 | *Encrypted Alert* : fermeture normale de la session (*close_notify*), pas une erreur |
| 28 et 36 | requête du dashboard en HTTP : son JSON est **lisible en clair** (*Suivre → Flux HTTP*), pour comparaison |

## 7. Risques résiduels et recommandations

Classés du plus au moins prioritaire.

| # | Risque | Impact | Recommandation |
|---|---|---|---|
| 1 | `/mesure` et `/alerte` acceptent des données **sans authentification** | un appareil sur Ultron peut injecter de fausses mesures ou alertes, et **empoisonner l'Isolation Forest** | clé API sur toutes les routes d'écriture, comme pour `/badge` |
| 2 | Mot de passe Wi-Fi faible (`12345678`), présent dans le dépôt git ; clé API écrite dans le firmware versionné | accès au réseau Ultron, puis au point 1 | mot de passe WPA2 robuste, dépôt privé, secrets dans un `secrets.h` non versionné, rotation de la clé |
| 3 | L'ESP32 chiffre sans vérifier le certificat (`setInsecure`) | attaque de l'homme du milieu possible sur Ultron | épingler le certificat du Pi dans le firmware (`setCACert`) ; `detection.py` le fait déjà |
| 4 | Dashboard en HTTP (5000) et flux vidéo MJPEG du PC (8000) sans authentification ni chiffrement | données et images lisibles sur Ultron | HTTPS pour le dashboard **et** le flux vidéo (sinon le navigateur bloque le contenu mixte), authentification |
| 5 | Serveur de développement Flask (Werkzeug) | non prévu pour la production | serveur WSGI (Gunicorn) derrière un proxy |
| 6 | avahi-daemon (mDNS) actif ; ufw laisse passer le mDNS multicast par défaut | annonce le Pi sur le réseau | le désactiver s'il n'est pas utilisé |
| 7 | Capture tshark exécutée en root | une faille dans l'analyse d'un paquet donnerait les droits root | capture via `dumpcap` avec capacités réseau, en utilisateur dédié |
| 8 | Badges RFID identifiés par leur seul UID | un UID MIFARE se clone facilement | badges à authentification chiffrée (MIFARE DESFire) |

## 8. Limite de cet audit

Le scan **Nmap** n'a pas pu être réalisé : la version installée par `winget` (Nmap 7.80, 2019) plantait au démarrage
sur le PC. La vérification externe a été faite par des connexions TCP directes (section 5). À compléter avec une version
récente de Nmap, depuis un poste du réseau Ultron :
```powershell
nmap -sV 10.42.0.1 -oN docs/audit/nmap_apres.txt
```

## 9. Index des preuves

| Fichier | Contenu |
|---|---|
| [`ports_avant.txt`](ports_avant.txt) | ports en écoute et pare-feu **avant** durcissement |
| [`durcissement.txt`](durcissement.txt) | journal d'exécution de `durcir.sh` (avant l'ajout de la règle HTTPS 5443) |
| [`ports_apres.txt`](ports_apres.txt) | ports en écoute **après** durcissement et activation du HTTPS |
| [`ufw_status_final.txt`](ufw_status_final.txt) | règles du pare-feu, état final |
| [`test_pare_feu_depuis_xiaomi.txt`](test_pare_feu_depuis_xiaomi.txt) | test des ports depuis un réseau extérieur |
| [`preuve_wireshark.pcap`](preuve_wireshark.pcap) | capture réseau : TLS de l'ESP32 et HTTP du dashboard |
