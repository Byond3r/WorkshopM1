import time

import cv2
import requests
from ultralytics import YOLO

# --- Configuration ---
PI_URL = "http://10.105.175.134:5000/alerte"   # adresse du serveur Flask sur le Pi
SEUIL_CONFIANCE = 0.5                      # on ignore les détections en dessous
DELAI_ALERTES = 3                          # secondes minimum entre deux alertes

model = YOLO("yolov8n.pt")
cam = cv2.VideoCapture(0, cv2.CAP_DSHOW)
derniere_alerte = 0                        # moment (en secondes) de la dernière alerte envoyée

while True:
    ok, frame = cam.read()
    if not ok:
        print("Impossible de lire la webcam")
        break

    results = model(frame, verbose=False)

    personne_detectee = False
    meilleure_confiance = 0.0

    # Une "box" = un objet détecté dans l'image
    for box in results[0].boxes:
        classe = model.names[int(box.cls)]   # numéro de classe -> nom ("person", "car"...)
        confiance = float(box.conf)

        if classe == "person" and confiance > SEUIL_CONFIANCE:
            personne_detectee = True
            meilleure_confiance = max(meilleure_confiance, confiance)

            # Dessiner un rectangle rouge autour de la personne
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
            cv2.putText(frame, f"INTRUS {confiance:.2f}", (x1, y1 - 10),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

    # Envoyer une alerte au Pi, mais pas plus d'une toutes les DELAI_ALERTES secondes
    maintenant = time.time()
    if personne_detectee and maintenant - derniere_alerte >= DELAI_ALERTES:
        alerte = {"alerte": "intrus", "confiance": round(meilleure_confiance, 2)}
        try:
            requests.post(PI_URL, json=alerte, timeout=1)
            print(f"Alerte envoyée : {alerte}")
        except requests.RequestException:
            print("Pi injoignable (es-tu bien connecté à Ultron ? serveur.py lancé ?)")
        derniere_alerte = maintenant

    cv2.imshow("Sentinel", frame)
    if cv2.waitKey(1) == ord("q"):
        break

cam.release()
cv2.destroyAllWindows()