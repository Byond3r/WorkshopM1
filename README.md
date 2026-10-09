# Sentinel-X — Workshop M1 2026

Tourelle de surveillance autonome pour les micro-centrales d'AetherCorp : capteurs IoT, détection d'intrus
par vision (YOLOv8n), détection d'anomalies (Isolation Forest), dashboard web temps réel, flux chiffrés et serveur durci.

**Audit de sécurité** : [`docs/audit/README.md`](docs/audit/README.md)

## Architecture

```
                         Wi-Fi Ultron (hotspot du Pi, WPA2, 10.42.0.0/24)
 ┌──────────────────────┐                                  ┌──────────────────────────────────┐
 │ ESP32  10.42.0.189   │── HTTPS 5443 : mesures ─────────▶│ Raspberry Pi 4  10.42.0.1        │
 │ (temp. / gaz simulés)│                                  │  sentinel          Flask + SQLite │
 └──────────────────────┘                                  │                    + Isolation    │
 ┌──────────────────────┐                                  │                      Forest       │
 │ PC  10.42.0.91       │── HTTPS 5443 : alertes YOLO ────▶│  sentinel-capteurs proximité, son │
 │ webcam + YOLOv8n     │                                  │  sentinel-reseau   capture tshark │
 │ flux MJPEG :8000 ◀───┼── le navigateur lit la vidéo     │  ufw               pare-feu       │
 └──────────────────────┘                                  └───────────────┬──────────────────┘
                                                                           │ HTTPS 5443 (login)
                                                          Dashboard : https://10.42.0.1:5443
                                                          (plan B en HTTP : http://10.42.0.1:5000)
```

Choix volontaire : YOLO tourne sur le PC, car le Pi 4 n'a que 2 Go de RAM. Le Pi ne reçoit que des alertes JSON,
et la vidéo va directement du PC au navigateur.

## Arborescence

Un dossier par machine sur laquelle le code tourne.

```
pc/        PC portable (Windows)
  detection.py            webcam + YOLOv8n, alertes HTTPS au Pi, flux vidéo MJPEG (port 8000)
  certificat_pi.pem       certificat public du Pi : detection.py vérifie qu'il parle au bon serveur
pi/        Raspberry Pi 4 (tout est copié dans /home/user/sentinel/)
  serveur.py              API Flask (HTTP 5000 + HTTPS 5443), SQLite, Isolation Forest, badges RFID
  dashboard.html          dashboard : vue d'ensemble, caméra, réseau / cyber (sans ressource externe)
  capteurs_pi.py          lecture des capteurs du Pi (mouvement PIR, proximité IR, son)
  capture_reseau.py       capture tshark agrégée pour l'onglet Réseau / Cyber
  *.service               services systemd (démarrage automatique)
  generer_certificat.sh   certificat TLS auto-signé
  installer.sh            installation de tshark et ufw (bascule temporaire sur le partage de connexion)
  durcir.sh               durcissement : pare-feu, services inutiles, SSH
  sauvegarder_bdd.py      sauvegarde cohérente de la base SQLite (dans sauvegardes/, droits 600)
  durcissement-systemd.conf  durcissement des services serveur et capteurs (NoNewPrivileges, etc.)
esp32/     ESP32 WROOM 32
  esp32_sentinel.ino      nœud sans fil : mesures HTTPS, lecteur RFID RC522
docs/      sujet, déroulé de la démo, audit de sécurité et preuves
backend/   API FastAPI proposée par l'équipe, non utilisée (évolution possible)
deployer.ps1              copie pi/ sur le Pi et redémarre les services
```

## Plan d'adressage (Wi-Fi Ultron)

| Appareil | IP | Ports ouverts |
|---|---|---|
| Raspberry Pi 4 (serveur, hotspot) | 10.42.0.1 (fixe) | 22 SSH, 5000 HTTP, 5443 HTTPS |
| ESP32 | 10.42.0.189 (DHCP) | aucun (client) |
| PC portable | 10.42.0.91 (DHCP) | 8000 flux vidéo |

Le hotspot redonne la même IP à un même appareil. Connexion NetworkManager du Pi : `Ultron` (WPA2 pur, canal 6).

## API du serveur

| Route | Méthode | Utilisée par | Rôle |
|---|---|---|---|
| `/mesure` | POST | ESP32, `capteurs_pi.py` | enregistre une mesure (+ verdict de l'Isolation Forest) |
| `/alerte` | POST | `detection.py` | enregistre une alerte d'intrusion |
| `/badge` | POST | ESP32 (RFID) | arme / désarme le système ; en-tête `X-Cle-Api` obligatoire |
| `/mesures?n=` `/alertes?n=` | GET | dashboard | dernières mesures / alertes |
| `/status` | GET | dashboard | nœuds en ligne, état de l'IA, système armé ou non |
| `/reseau` | GET | dashboard | statistiques de la capture réseau |
| `/camera?ip=` | GET | dashboard en HTTPS | relaie le flux vidéo du PC (IP d'Ultron uniquement, port 8000) |
| `/` | GET | navigateur | le dashboard |

Toutes les données reçues sont **validées** (liste blanche de champs, types, plages physiques) : une requête
invalide est refusée avec une erreur `400` qui en donne la raison, une requête de plus de 4 Ko avec une erreur `413`.

Les routes de **consultation** (GET) demandent un identifiant (authentification HTTP Basic, utilisateur `operateur`) ;
les **envois** des capteurs et de la caméra (POST) n'en demandent pas.

**Dashboard en HTTPS** : ouvrir `https://10.42.0.1:5443` (`https` va avec le port 5443, `http` avec le port 5000).
Le certificat est auto-signé : accepter l'avertissement du navigateur une fois (Firefox : *Avancé… → Accepter le risque
et poursuivre*). Dans l'onglet Caméra, saisir l'**IP du PC sur Ultron** (`10.42.0.91`), pas `localhost` : en HTTPS,
c'est le Pi qui va chercher la vidéo (une image HTTP dans une page HTTPS serait bloquée par le navigateur).

## Capteurs

| Capteur | Branché sur | État |
|---|---|---|
| Mini PIR zone 1 (mouvement) | Pi : OUT → broche 11 (GPIO17), VCC → broche 2 (5 V), GND → broche 6 | ✅ réel |
| Mini PIR zone 2 (mouvement) | Pi : OUT → broche 16 (GPIO23), VCC → broche 4 (5 V), GND → broche 14 | ✅ réel |
| IR_01 (proximité, anti-sabotage) | Pi : S → broche 13 (GPIO27), V+ → broche 1 (3,3 V), G → broche 9 | ✅ réel |
| LM393 (son) | Pi : OUT → broche 15 (GPIO22), +5V → broche 17 (**3,3 V**), GND → broche 20 | ✅ réel |
| Webcam USB + YOLOv8n | PC | ✅ réel |
| Température, gaz | ESP32 | ⚠️ **simulés** (aucun capteur reçu) |
| HC-SR501 (mouvement) | — | ❌ deux modules défectueux, remplacés par le mini PIR |
| RC522 (badge RFID) | ESP32 : SDA G5, SCK G18, MOSI G23, MISO G19, RST G22, 3.3V 3V3 | ❌ lecteur défectueux ; logiciel prêt |

Le module son est marqué « +5V » mais doit rester en 3,3 V : sa sortie suit l'alimentation, et les GPIO du Pi
ne supportent pas 5 V. Le mini PIR, lui, a son propre régulateur : alimenté en 5 V, sa sortie reste en 3,3 V.
Ses broches sont dans l'ordre **VCC / GND / OUT**, différent de celui du HC-SR501.

## Lancer

### Pi : services systemd (démarrage automatique)

| Service | Rôle | Utilisateur |
|---|---|---|
| `sentinel` | serveur Flask, HTTP 5000 + HTTPS 5443 | `user` |
| `sentinel-capteurs` | `capteurs_pi.py` | `user` (groupe `gpio`) |
| `sentinel-reseau` | `capture_reseau.py` (tshark) | `root` (capture réseau) |

```bash
systemctl is-active sentinel sentinel-reseau sentinel-capteurs ufw   # vérifier
journalctl -u sentinel -f                                            # journal du serveur
```

**Déployer depuis le PC** (connecté à Ultron, depuis la racine du projet ; le mot de passe sudo est demandé) :
```powershell
powershell -ExecutionPolicy Bypass -File deployer.ps1
```

**Première installation d'un service** (exemple avec `sentinel-capteurs`) :
```bash
sudo cp /home/user/sentinel/sentinel-capteurs.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sentinel-capteurs
```

**Durcissement des services** (une seule fois) : fichier « drop-in » qui complète les `.service` sans les modifier.
```bash
sudo mkdir -p /etc/systemd/system/sentinel.service.d /etc/systemd/system/sentinel-capteurs.service.d
sudo cp /home/user/sentinel/durcissement-systemd.conf /etc/systemd/system/sentinel.service.d/durcissement.conf
sudo cp /home/user/sentinel/durcissement-systemd.conf /etc/systemd/system/sentinel-capteurs.service.d/durcissement.conf
sudo systemctl daemon-reload && sudo systemctl restart sentinel sentinel-capteurs
```

**Fichiers propres au Pi, jamais versionnés** : `tls/cle.pem` (clé privée TLS), `cle_api.txt` (secret partagé
avec l'ESP32), `badges_autorises.txt` (une ligne par badge : `UID;nom`), `identifiants_dashboard.txt`
(`operateur:` + empreinte scrypt du mot de passe), `sentinel.db`, `serveur.log`, `sauvegardes/`.

**Changer le mot de passe du dashboard** (sur le Pi ; le mot de passe est lu au clavier, il n'apparaît nulle part) :
```bash
cd /home/user/sentinel && python3 -c "import getpass; from werkzeug.security import generate_password_hash as h; open('identifiants_dashboard.txt','w').write('operateur:' + h(getpass.getpass('Nouveau mot de passe : '), method='scrypt:16384:8:1') + '\n')" && chmod 600 identifiants_dashboard.txt
```

**Sauvegarder la base** (possible pendant que le serveur tourne) :
```bash
python3 /home/user/sentinel/sauvegarder_bdd.py
```

### PC : caméra

Avec le **Python global**, où sont installés OpenCV et YOLO (le `.venv` du projet est vide, ne pas l'activer) :
```powershell
py pc/detection.py        # webcam USB (caméra n° 1)
py pc/detection.py 0      # webcam intégrée
```
VS Code active automatiquement le `.venv` dans chaque nouveau terminal (erreur `No module named 'cv2'`).
Commande qui fonctionne dans tous les cas, avec le chemin complet du Python global :
```powershell
& "C:\Users\GreGY\AppData\Local\Python\pythoncore-3.14-64\python.exe" pc/detection.py
```
`yolov8n.pt` et `certificat_pi.pem` doivent être dans `pc/`. Flux vidéo : `http://10.42.0.91:8000/video`.
Si le Wi-Fi Ultron est en profil « Public », le pare-feu Windows bloque le port 8000 : le passer en « Privé ».

### ESP32

Arduino IDE, carte « ESP32 Dev Module », port COM4. Bibliothèque **MFRC522** (GithubCommunity) requise.
Si le téléversement bloque sur « Connecting… », maintenir le bouton **BOOT**.

### Chiffrement (une seule fois)

```bash
bash /home/user/sentinel/generer_certificat.sh     # sur le Pi : certificat auto-signé, 1 an
```
```powershell
scp user@10.42.0.1:/home/user/sentinel/tls/certificat.pem pc/certificat_pi.pem
```

## Sécurité

Résumé ; le détail et les preuves sont dans [`docs/audit/README.md`](docs/audit/README.md).
- Pare-feu ufw (refus par défaut), rpcbind désactivé, SSH sans root, services en utilisateur simple.
- Envois des capteurs et de la caméra en **HTTPS** ; `detection.py` vérifie le certificat du Pi.
- **Dashboard en HTTPS** : identifiants et données chiffrés ; flux vidéo relayé par le Pi (`/camera`), limité aux IP
  d'Ultron pour empêcher le Pi de servir de relais vers d'autres adresses (SSRF).
- **Login sur le dashboard** (empreinte scrypt, jamais le mot de passe en clair), protection contre le XSS,
  route `/badge` protégée par clé API.
- **Services durcis** par systemd (`NoNewPrivileges`, aucune capacité root, système en lecture seule, mémoire limitée) :
  note d'exposition `systemd-analyze` passée de 9,2 à 6,1.
- **Scans Nmap** : depuis l'extérieur du réseau Ultron, un seul port visible sur 1 028 (SSH, limité contre la force
  brute) ; depuis Ultron, 4 ports, tous attendus (SSH, DNS du hotspot, dashboard HTTP et HTTPS protégés par le login).
- **Base de données** : SQLite n'ouvre aucun port (seule l'API y accède), requêtes paramétrées (pas d'injection SQL),
  validation par liste blanche des données reçues, 4 Ko maximum par requête, fichiers en `600`, sauvegardes cohérentes.
- Le Pi n'a ni horloge sur pile ni internet sur Ultron : le dashboard calcule les durées avec l'heure du Pi
  (en-tête HTTP `Date`), pour que les alertes s'affichent même si cette heure est décalée.

## Limites connues et évolutions

- Température et gaz **simulés** : brancher de vrais capteurs (le DHT11 est disponible pour la température).
- `/mesure` et `/alerte` sans authentification (les valeurs absurdes sont refusées, pas les valeurs plausibles) :
  étendre la clé API de `/badge`.
- Sauvegardes stockées sur la même carte SD que la base : les copier régulièrement hors du Pi.
- L'ESP32 ne vérifie pas le certificat du Pi : l'épingler dans le firmware.
- Le dashboard reste aussi accessible en HTTP (port 5000, plan B de la démo), où les identifiants circulent en clair ;
  et le flux vidéo va du PC au Pi en HTTP sur Ultron. Une fois le HTTPS éprouvé : réserver le port 5000 au Pi
  lui-même et chiffrer le flux vidéo du PC.
- RFID : remplacer le lecteur RC522 défectueux ; le reste de la chaîne est prêt et testé.
- Docker Compose : prévu ; utiliser `network_mode: host`, sinon Docker contourne ufw.
