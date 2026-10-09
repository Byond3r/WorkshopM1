# Audit de sécurité — Sentinel-X

Audit réalisé le 2026-10-08 sur le serveur Raspberry Pi 4 (`10.42.0.1`) et les flux du réseau Ultron,
complété le 2026-10-09 : sécurité de la base de données, login et HTTPS du dashboard, durcissement systemd, scan Nmap.
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
5. **Validation des entrées** : 14 envois légitimes et malveillants rejoués contre le serveur, dans un dossier
   de test séparé (la base réelle n'est pas touchée).
6. **Scans Nmap 7.92** depuis un poste **hors** du réseau Ultron (vue d'un attaquant extérieur), puis depuis un poste
   **connecté à Ultron** (vue d'un attaquant qui aurait obtenu le mot de passe Wi-Fi).
7. **Note d'exposition des services** avant et après durcissement : `systemd-analyze security`.

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
| Durcissement systemd | `NoNewPrivileges`, aucune capacité root, `ProtectSystem=full`, `/tmp` privé, 400 Mo maximum (équivalent de `cap_drop: ALL` et `no-new-privileges` de Docker) | [`durcissement_systemd.txt`](durcissement_systemd.txt) |
| Login du dashboard | authentification HTTP Basic sur les routes de consultation ; mot de passe stocké sous forme d'empreinte scrypt (fichier `600`, hors de git) ; durée de vérification identique quel que soit le champ faux | [`durcissement_systemd.txt`](durcissement_systemd.txt) |
| HTTPS du dashboard | dashboard servi sur `https://10.42.0.1:5443` : identifiants et données chiffrés. Le flux vidéo du PC (HTTP) est **relayé par le Pi** (`/camera`) pour éviter le blocage du contenu mixte ; le relais n'accepte que les IP d'Ultron, sur le port 8000 et le chemin `/video` (**protection SSRF**), et seulement après login | [`tests_validation.txt`](tests_validation.txt) |
| Application | dashboard protégé contre le XSS (tout texte reçu du réseau est échappé) ; route `/badge` protégée par clé API comparée en temps constant | [`pi/serveur.py`](../../pi/serveur.py), [`pi/dashboard.html`](../../pi/dashboard.html) |
| Base de données | voir section 5 | [`tests_validation.txt`](tests_validation.txt) |

## 5. Sécurité de la base de données

La base est un fichier SQLite sur le Pi (`/home/user/sentinel/sentinel.db`, tables `mesures` et `alertes`).

| Mesure | Ce qu'elle empêche |
|---|---|
| **Aucun port réseau** : SQLite est un fichier, pas un serveur | connexion directe à la base depuis le réseau ; seule l'API Flask y accède |
| **Requêtes SQL paramétrées** (`?`), jamais de concaténation | l'injection SQL |
| **Validation par liste blanche** : champs autorisés, types et plages physiques (température −40 à 125 °C, gaz 0 à 10 000 ppm, capteurs binaires 0 ou 1, textes de 32 caractères maximum) | les valeurs absurdes ; la **falsification d'horodatage ou de source** (un champ `horodatage` envoyé dans le JSON écrasait la vraie colonne à l'affichage) ; le forçage de `systeme_arme` |
| **Taille maximale de 4 Ko par requête** (erreur 413 au-delà) | la saturation de la carte SD par d'énormes requêtes |
| **Droits `600`** sur la base et le journal, appliqués à chaque démarrage, dans un dossier en `700` | la lecture par un autre utilisateur du Pi |
| **Sauvegarde cohérente** ([`pi/sauvegarder_bdd.py`](../../pi/sauvegarder_bdd.py)), via l'API `backup()` de SQLite, copies en `600` | la perte des données si la carte SD lâche ; un simple `cp` pendant une écriture pourrait copier une base corrompue |
| Base, clés et sauvegardes **exclues de git** | la fuite des données dans le dépôt |

Résultats des tests ([`tests_validation.txt`](tests_validation.txt)) : les 4 envois légitimes passent (y compris
l'injection d'anomalie de la démo, 85 °C), les 10 envois malveillants ou absurdes sont refusés (400 ou 413).

## 6. Résultats : avant / après

| Critère | Avant | Après |
|---|---|---|
| Pare-feu | inactif | actif, refus par défaut |
| Ports TCP en écoute | 22, 111, 5000 | 22, 5000, 5443 ([`ports_apres.txt`](ports_apres.txt)) |
| Flask (5000) depuis un autre réseau | ouvert | **filtré** ([`test_pare_feu_depuis_xiaomi.txt`](test_pare_feu_depuis_xiaomi.txt)) |
| rpcbind (111) | ouvert | **filtré**, service désactivé |
| Envois des capteurs | HTTP en clair | **TLS** (port 5443) |
| Mesure avec un faux `horodatage` | enregistrée, affichée avec la fausse date | **refusée** (400) |
| Requête de 5 Ko | enregistrée telle quelle | **refusée** (413) |
| Droits de la base | `644` | `600` |
| Accès au dashboard | libre depuis Ultron, en HTTP | **identifiant obligatoire** (401 sans), en **HTTPS** |
| Note d'exposition `systemd-analyze` (serveur, capteurs) | 9,2 UNSAFE | **6,1 MEDIUM** ([`durcissement_systemd.txt`](durcissement_systemd.txt)) |
| Scan Nmap depuis l'extérieur d'Ultron | — | **1 port visible sur 1 028** (SSH, limité) ([`nmap_depuis_exterieur.txt`](nmap_depuis_exterieur.txt)) |
| Scan Nmap depuis Ultron | — | **4 ports visibles, tous attendus** : 22 (SSH), 53 (DNS du hotspot), 5000 et 5443 (dashboard, qui répond **401** sans identifiant) ; tout le reste filtré ([`nmap_depuis_ultron.txt`](nmap_depuis_ultron.txt)) |

« Filtré » signifie que le Pi ne répond même pas : un scanner ne peut pas savoir si un service existe derrière.

## 7. Preuve du chiffrement ([`preuve_wireshark.pcap`](preuve_wireshark.pcap))

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

## 8. Risques résiduels et recommandations

Classés du plus au moins prioritaire.

| # | Risque | Impact | Recommandation |
|---|---|---|---|
| 1 | `/mesure` et `/alerte` acceptent des données **sans authentification** | un appareil sur Ultron peut injecter de fausses mesures ou alertes **plausibles** (la validation bloque déjà les valeurs absurdes), et **empoisonner l'Isolation Forest** | clé API sur toutes les routes d'écriture, comme pour `/badge` |
| 2 | Mot de passe Wi-Fi faible (`12345678`), présent dans le dépôt git ; clé API écrite dans le firmware versionné | accès au réseau Ultron, puis au point 1 | mot de passe WPA2 robuste, dépôt privé, secrets dans un `secrets.h` non versionné, rotation de la clé |
| 3 | L'ESP32 chiffre sans vérifier le certificat (`setInsecure`) | attaque de l'homme du milieu possible sur Ultron | épingler le certificat du Pi dans le firmware (`setCACert`) ; `detection.py` le fait déjà |
| 4 | Le dashboard reste **aussi** accessible en HTTP (port 5000, plan B de la démo), où les identifiants circulent en base64 non chiffré ; le flux vidéo va du PC au Pi en HTTP, et le port 8000 du PC n'a pas d'authentification | sur Ultron, un appareil qui écoute peut lire les identifiants si le HTTP est utilisé, et les images | une fois le HTTPS éprouvé : réserver le port 5000 au Pi lui-même (ufw) ; servir le flux vidéo du PC en HTTPS |
| 5 | Serveur de développement Flask (Werkzeug) | non prévu pour la production | serveur WSGI (Gunicorn) derrière un proxy |
| 6 | avahi-daemon (mDNS) actif ; ufw laisse passer le mDNS multicast par défaut | annonce le Pi sur le réseau | le désactiver s'il n'est pas utilisé |
| 7 | Capture tshark exécutée en root, non durcie (note 9,6) | une faille dans l'analyse d'un paquet donnerait les droits root | capture via `dumpcap` avec les seules capacités réseau, en utilisateur dédié |
| 8 | Badges RFID identifiés par leur seul UID | un UID MIFARE se clone facilement | badges à authentification chiffrée (MIFARE DESFire) |
| 9 | Pas d'horloge sur pile sur le Pi, ni d'internet sur Ultron | horodatages en base décalés après un redémarrage (le dashboard, lui, compense : il calcule les durées avec l'heure du Pi, reçue dans l'en-tête HTTP `Date`) | module RTC, ou serveur de temps local sur le réseau Ultron |
| 10 | Base non chiffrée sur la carte SD ; sauvegardes stockées sur la même carte | vol de la carte = lecture des données ; carte détruite = sauvegardes perdues | copier les sauvegardes hors du Pi ; chiffrement du disque si les données deviennent sensibles |
| 11 | **Versions annoncées** (trouvé par le scan depuis Ultron) : en-tête `Server: Werkzeug/3.1.3 Python/3.13.5` sur chaque réponse HTTP, bannières d'OpenSSH 10.0p2 et de dnsmasq 2.91 | un attaquant cherche directement les failles connues de ces versions précises | masquer l'en-tête `Server` (serveur de production derrière un proxy, voir n° 5) ; maintenir le système à jour (`apt upgrade`) |

## 9. Correspondance avec le plan de sécurisation du workshop

Le plan de l'école suppose une architecture MQTT + Docker. Sentinel-X utilise une API **HTTPS** et des services
**systemd**, ce que le sujet autorise (« API REST/WebSocket », « TLS/MQTTS ») : chaque exigence est rapprochée
de son équivalent. ✅ fait · 🔄 équivalent · ⚠️ partiel · ❌ non fait.

| Niveau | Exigence du plan | État | Dans Sentinel-X |
|---|---|---|---|
| 1 | MQTT authentifié | ⚠️ | clé API sur `/badge` ; `/mesure` et `/alerte` protégées par le pare-feu et la validation, sans authentification (risque n° 1) |
| 1 | Mots de passe hors du code | ⚠️ | clé TLS, clé API, identifiants du dashboard et badges hors de git ; mot de passe Wi-Fi et clé API dans le firmware (risque n° 2) |
| 1 | Pas de `--privileged` | 🔄 | serveur et capteurs en utilisateur simple, durcis par systemd |
| 1 | Pare-feu hôte | ✅ | ufw, refus par défaut |
| 1 | Wi-Fi WPA2 avec mot de passe fort | ⚠️ | WPA2 pur (CCMP) ; mot de passe faible (risque n° 2) |
| 1 | Login sur le dashboard | ✅ | authentification HTTP Basic, empreinte scrypt |
| 1 | Versionnement | ✅ | Git + GitHub, branches, Pull Requests |
| 1 | Plan d'adressage | ✅ | README |
| 1 | Redémarrage automatique | 🔄 | systemd `Restart=always`, testé par un redémarrage complet du Pi |
| 1 | Débranchement de l'ESP | ✅ | reconnexion automatique (testée), délais d'attente sur tous les envois, nœud affiché HORS LIGNE après 15 s |
| 2 | TLS | 🔄 | HTTPS 5443 pour l'ESP32 et la caméra ; la caméra vérifie le certificat |
| 2 | HTTPS sur le dashboard | ✅ | `https://10.42.0.1:5443`, flux vidéo relayé par le Pi ; HTTP gardé en plan B (risque n° 4) |
| 2 | Réseaux séparés | 🔄 | Wi-Fi dédié Ultron, pare-feu filtrant par réseau d'origine |
| 2 | Drop des capabilities | 🔄 | `CapabilityBoundingSet=` vide, `NoNewPrivileges` (section 4) |
| 2 | Preuve Wireshark | ✅ | section 7 |
| 2 | Authentification de l'API | ⚠️ | `/badge` uniquement |
| 2 | Supervision intégrée au dashboard | ⚠️ | capture réseau en direct, état des nœuds et de l'IA ; pas de métriques système (CPU, RAM) |
| 2 | Healthchecks | 🔄 | route `/status` interrogée toutes les 2 s ; relance automatique par systemd |
| 2 | Logs centralisés | 🔄 | journald pour tous les services |
| 3 | Non-root, lecture seule, limites de ressources | 🔄 | utilisateur simple, `ProtectSystem=full`, `MemoryMax=400M` (sauf capture réseau) |
| 3 | Scan Nmap | ✅ | depuis l'extérieur d'Ultron et depuis Ultron (section 6), avec une trouvaille (risque n° 11) |
| 3 | Au-delà du plan | ✅ | sécurité de la base (section 5), protection XSS, rpcbind désactivé, SSH sans root, risques résiduels identifiés (section 8) |

## 10. Limites de cet audit

- Les scans Nmap couvrent les ports TCP 1 à 1024, ainsi que 5000, 5443 et 8000 (ceux du projet) : pas de scan UDP,
  ni des 65 535 ports.
- Le flux vidéo du PC (port 8000) n'a pas été audité : il tourne sur le PC, pas sur le Pi.
- Pour rejouer le scan depuis Ultron :
  ```powershell
  C:\Users\GreGY\nmap-7.92\nmap.exe -sT -sV -Pn -p 1-1024,5000,5443,8000 10.42.0.1 -oN docs/audit/nmap_depuis_ultron.txt
  ```

## 11. Index des preuves

| Fichier | Contenu |
|---|---|
| [`ports_avant.txt`](ports_avant.txt) | ports en écoute et pare-feu **avant** durcissement |
| [`durcissement.txt`](durcissement.txt) | journal d'exécution de `durcir.sh` (avant l'ajout de la règle HTTPS 5443) |
| [`ports_apres.txt`](ports_apres.txt) | ports en écoute **après** durcissement et activation du HTTPS |
| [`ufw_status_final.txt`](ufw_status_final.txt) | règles du pare-feu, état final |
| [`test_pare_feu_depuis_xiaomi.txt`](test_pare_feu_depuis_xiaomi.txt) | test des ports depuis un réseau extérieur |
| [`preuve_wireshark.pcap`](preuve_wireshark.pcap) | capture réseau : TLS de l'ESP32 et HTTP du dashboard |
| [`tests_validation.txt`](tests_validation.txt) | 14 tests de validation des entrées, droits des fichiers, sauvegarde, tests du relais vidéo HTTPS (SSRF) |
| [`durcissement_systemd.txt`](durcissement_systemd.txt) | durcissement systemd (notes avant/après) et tests du login du dashboard |
| [`nmap_depuis_exterieur.txt`](nmap_depuis_exterieur.txt) | scan Nmap 7.92 depuis l'extérieur du réseau Ultron |
| [`nmap_depuis_ultron.txt`](nmap_depuis_ultron.txt) | scan Nmap 7.92 depuis le réseau Ultron (versions annoncées, login 401) |
