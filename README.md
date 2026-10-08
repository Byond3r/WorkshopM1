# Sentinel-X — Workshop M1 2026

Tourelle de surveillance autonome : capteurs IoT, détection d'intrus par vision (YOLOv8n),
détection d'anomalies (Isolation Forest) et dashboard web temps réel.

## Arborescence

Un dossier par machine sur laquelle le code tourne.

```
pc/       PC portable (Windows) : webcam + YOLOv8n, alertes au Pi, flux vidéo MJPEG
pi/       Raspberry Pi 4 : serveur Flask, base SQLite, dashboard
esp32/    ESP32 WROOM 32 : nœud capteurs (température, gaz, présence)
docs/     Sujet du workshop, rapport, soutenance
```

## Réseau (Wi-Fi Ultron)

| Appareil | IP |
|---|---|
| Raspberry Pi 4 (serveur, hotspot) | 10.42.0.1 |
| ESP32 | 10.42.0.189 (DHCP) |
| PC portable (YOLO + flux vidéo) | 10.42.0.x (DHCP) |

## Lancer

**PC** (venv activé) :
```powershell
py pc/detection.py          # flux vidéo sur http://<IP_DU_PC>:8000/video
```
`yolov8n.pt` doit être dans `pc/`.

**Pi** : les fichiers de `pi/` vont dans `/home/user/sentinel/`, servis par `sentinel.service`.
```powershell
scp pi/serveur.py pi/dashboard.html user@10.42.0.1:/home/user/sentinel/
ssh user@10.42.0.1 "sudo systemctl restart sentinel"
```
Dashboard : http://10.42.0.1:5000

**Capture réseau (Pi)** : `capture_reseau.py` lance tshark sur `wlan0` et alimente l'onglet « Réseau / Cyber ».
```bash
sudo cp /home/user/sentinel/sentinel-reseau.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now sentinel-reseau
```

**Chiffrement (HTTPS, port 5443)** : l'ESP32 et `detection.py` envoient leurs données en TLS.
Le dashboard reste sur HTTP:5000 (consultation locale).
```bash
bash /home/user/sentinel/generer_certificat.sh     # sur le Pi, une fois (certificat auto-signé, 1 an)
```
```powershell
scp user@10.42.0.1:/home/user/sentinel/tls/certificat.pem pc/certificat_pi.pem   # le PC vérifie le Pi
```

**ESP32** : ouvrir `esp32/esp32_sentinel.ino` dans l'Arduino IDE (COM4, « ESP32 Dev Module »).
