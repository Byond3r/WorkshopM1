import json
import time
import urllib.request

from gpiozero import DigitalInputDevice

# --- Configuration ---
# Numéros GPIO (BCM). Broche PHYSIQUE (1 à 40 sur le connecteur) entre parenthèses.
PIN_MOUVEMENT_1 = 17 # mini PIR zone 1 (broche 11) : 1 = mouvement   | VCC -> 5 V (broche 2), GND -> broche 6
PIN_MOUVEMENT_2 = 23 # mini PIR zone 2 (broche 16) : 1 = mouvement   | VCC -> 5 V (broche 4), GND -> broche 14
PIN_PROXIMITE = 27   # module IR_01 (broche 13) : 0 = objet devant    | V+ -> 3,3 V (broche 1), G -> broche 9
PIN_SON       = 22   # LM393 son (broche 15) : bascule à chaque bruit | VCC -> 3,3 V (broche 17), GND -> broche 20
# Le module son est marqué "+5V" mais doit rester en 3,3 V : sa sortie suit l'alimentation,
# et le Pi ne supporte pas 5 V sur une broche GPIO.
# Le mini PIR a son propre régulateur : alimenté en 5 V, sa sortie reste en 3,3 V.
# Attention, ses broches sont dans l'ordre VCC / GND / OUT (pas le même ordre que le HC-SR501).

SERVEUR_URL = "http://127.0.0.1:5000/mesure"   # le serveur tourne sur ce même Pi
NOM_NOEUD = "pi-1"
INTERVALLE = 5          # envoi périodique (s) : le nœud reste "en ligne" sur le dashboard
MAINTIEN_SON = 2        # un bruit dure quelques ms : on le garde visible 2 s
PAUSE_BOUCLE = 0.05     # lecture des capteurs 20 fois par seconde
CHAUFFE_PIR = 30        # le PIR peut déclencher au hasard juste après la mise sous tension

capteur_mouvement_1 = DigitalInputDevice(PIN_MOUVEMENT_1)
capteur_mouvement_2 = DigitalInputDevice(PIN_MOUVEMENT_2)
capteur_proximite = DigitalInputDevice(PIN_PROXIMITE)
capteur_son = DigitalInputDevice(PIN_SON)

demarrage = time.time()
dernier_son = 0.0       # moment du dernier bruit entendu


def noter_son():
    global dernier_son
    dernier_son = time.time()


# Un bruit est trop bref pour être vu par la boucle : gpiozero appelle noter_son()
# dès que la sortie bascule, dans un sens ou dans l'autre (marche quelle que soit la polarité)
capteur_son.when_activated = noter_son
capteur_son.when_deactivated = noter_son


def lire_mouvement(capteur_pir):
    if time.time() - demarrage < CHAUFFE_PIR:
        return 0
    return capteur_pir.value


def lire_etats():
    """Lit les capteurs et renvoie 1 = événement détecté, 0 = rien."""
    return {
        "mouvement_1": lire_mouvement(capteur_mouvement_1),
        "mouvement_2": lire_mouvement(capteur_mouvement_2),
        "proximite": 1 if capteur_proximite.value == 0 else 0,   # logique inversée du module
        "son": 1 if time.time() - dernier_son < MAINTIEN_SON else 0,
    }


def envoyer(etats):
    mesure = {"capteur": NOM_NOEUD, **etats}
    requete = urllib.request.Request(
        SERVEUR_URL,
        data=json.dumps(mesure).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(requete, timeout=2) as reponse:
            print(f"{etats} -> HTTP {reponse.status}")
    except Exception as erreur:
        # Le script continue même si le serveur est arrêté
        print(f"{etats} -> serveur injoignable ({erreur})")


def main():
    derniers_etats = None
    dernier_envoi = 0
    print("Capteurs du Pi en écoute (Ctrl+C pour arrêter)")

    while True:
        etats = lire_etats()
        maintenant = time.time()

        # Envoi immédiat si un capteur change, sinon toutes les INTERVALLE secondes
        if etats != derniers_etats or maintenant - dernier_envoi >= INTERVALLE:
            envoyer(etats)
            derniers_etats = etats
            dernier_envoi = maintenant

        time.sleep(PAUSE_BOUCLE)


if __name__ == "__main__":
    main()
