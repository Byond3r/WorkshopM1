import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import requests
from ultralytics import YOLO

# --- Configuration ---
# Chemin absolu : le modèle est trouvé quel que soit le dossier d'où on lance le script
FICHIER_MODELE = Path(__file__).resolve().parent / "yolov8n.pt"
# Numéro de la caméra : 0 = webcam intégrée, 1 = webcam USB (en général).
# Changeable au lancement sans modifier le code : py pc/detection.py 2
INDEX_CAMERA = int(sys.argv[1]) if len(sys.argv) > 1 else 1
# HTTPS : l'alerte est chiffrée (TLS) et on vérifie que c'est bien NOTRE Pi qui répond,
# grâce à une copie de son certificat (scp user@10.42.0.1:/home/user/sentinel/tls/certificat.pem pc/certificat_pi.pem)
PI_URL = "https://10.42.0.1:5443/alerte"
FICHIER_CERTIFICAT_PI = Path(__file__).resolve().parent / "certificat_pi.pem"
SEUIL_CONFIANCE = 0.5                      # on ignore les détections en dessous
DELAI_ALERTES = 3                          # secondes minimum entre deux alertes
PORT_VIDEO = 8000                          # flux visible sur http://<IP_DU_PC>:8000/video
QUALITE_JPEG = 70                          # 0-100 : plus bas = plus léger sur le Wi-Fi
IMAGES_PAR_SECONDE_FLUX = 15               # limite le débit envoyé au navigateur

# Dernière image annotée, partagée entre la boucle YOLO et le serveur vidéo
derniere_image_jpeg = None
verrou_image = threading.Lock()


class FluxVideoHandler(BaseHTTPRequestHandler):
    """Sert la dernière image en boucle au format MJPEG (une suite de JPEG)."""

    def do_GET(self):
        # self.path contient aussi les paramètres ("/video?t=123") : on ne garde que le chemin
        if self.path.split("?")[0] != "/video":
            self.send_error(404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        try:
            while True:
                with verrou_image:
                    image_jpeg = derniere_image_jpeg
                if image_jpeg is not None:
                    self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n")
                    self.wfile.write(f"Content-Length: {len(image_jpeg)}\r\n\r\n".encode())
                    self.wfile.write(image_jpeg + b"\r\n")
                time.sleep(1 / IMAGES_PAR_SECONDE_FLUX)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # le navigateur a fermé l'onglet : c'est normal

    def log_message(self, format, *args):
        pass  # évite d'afficher une ligne dans la console à chaque connexion


def demarrer_serveur_video():
    serveur = ThreadingHTTPServer(("0.0.0.0", PORT_VIDEO), FluxVideoHandler)
    # daemon=True : le thread s'arrête tout seul quand on quitte avec "q"
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    print(f"Flux vidéo disponible sur http://<IP_DU_PC>:{PORT_VIDEO}/video")


def trouver_personnes(model, frame):
    """Renvoie la liste des personnes détectées : [(x1, y1, x2, y2, confiance), ...]."""
    personnes = []
    for box in model(frame, verbose=False)[0].boxes:
        classe = model.names[int(box.cls)]
        confiance = float(box.conf)
        if classe == "person" and confiance > SEUIL_CONFIANCE:
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            personnes.append((x1, y1, x2, y2, confiance))
    return personnes


def dessiner_personnes(frame, personnes):
    for x1, y1, x2, y2, confiance in personnes:
        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
        cv2.putText(frame, f"INTRUS {confiance:.2f}", (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)


def publier_image(frame):
    """Encode l'image en JPEG et la rend disponible pour le flux vidéo."""
    global derniere_image_jpeg
    encodage_ok, image_encodee = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, QUALITE_JPEG])
    if encodage_ok:
        with verrou_image:
            derniere_image_jpeg = image_encodee.tobytes()


def envoyer_alerte(confiance):
    alerte = {"alerte": "intrus", "confiance": round(confiance, 2)}
    try:
        requests.post(PI_URL, json=alerte, timeout=2, verify=str(FICHIER_CERTIFICAT_PI))
        print(f"Alerte envoyée (chiffrée) : {alerte}")
    except requests.exceptions.SSLError:
        print("Certificat refusé : ce n'est pas le bon Pi, ou pc/certificat_pi.pem n'est plus à jour")
    except requests.RequestException:
        print("Pi injoignable (es-tu bien connecté à Ultron ? serveur.py lancé ?)")


def main():
    if not FICHIER_CERTIFICAT_PI.exists():
        # Sans ce fichier, requests planterait à chaque alerte : on prévient dès le lancement
        raise SystemExit(f"Certificat du Pi introuvable : {FICHIER_CERTIFICAT_PI}\n"
                         "Copie-le avec : scp user@10.42.0.1:/home/user/sentinel/tls/certificat.pem pc/certificat_pi.pem")
    model = YOLO(FICHIER_MODELE)
    cam = cv2.VideoCapture(INDEX_CAMERA, cv2.CAP_DSHOW)
    demarrer_serveur_video()
    derniere_alerte = 0                    # moment (en secondes) de la dernière alerte envoyée

    while True:
        ok, frame = cam.read()
        if not ok:
            print(f"Impossible de lire la caméra n° {INDEX_CAMERA} : essaie py pc/detection.py 0 (ou 2)")
            break

        personnes = trouver_personnes(model, frame)
        dessiner_personnes(frame, personnes)
        publier_image(frame)

        # Pas plus d'une alerte toutes les DELAI_ALERTES secondes
        maintenant = time.time()
        if personnes and maintenant - derniere_alerte >= DELAI_ALERTES:
            meilleure_confiance = max(confiance for *_, confiance in personnes)
            envoyer_alerte(meilleure_confiance)
            derniere_alerte = maintenant

        cv2.imshow("Sentinel", frame)
        if cv2.waitKey(1) == ord("q"):
            break

    cam.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
