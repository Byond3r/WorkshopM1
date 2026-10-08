import json
import time
import urllib.request

from gpiozero import DigitalInputDevice

# --- Configuration ---
# Numéros GPIO (BCM). Broche PHYSIQUE (1 à 40 sur le connecteur) entre parenthèses.
PIN_MOUVEMENT = 17   # HC-SR501 (broche 11) : 1 = mouvement      | VCC -> 5 V (broche 2)
PIN_PROXIMITE = 27   # barrette IR (broche 13) : 0 = objet devant | VCC -> 3,3 V (broche 1)
PIN_SON       = 22   # LM393 son (broche 15) : bascule à chaque bruit | VCC -> 3,3 V (broche 1)
PIN_CONTACT   = 23   # contact (broche 16) : l'autre patte -> GND (broche 14)

SERVEUR_URL = "http://127.0.0.1:5000/mesure"   # le serveur tourne sur ce même Pi
NOM_NOEUD = "pi-1"
INTERVALLE = 5          # envoi périodique (s) : le nœud reste "en ligne" sur le dashboard
MAINTIEN_SON = 2        # un bruit dure quelques ms : on le garde visible 2 s
CHAUFFE_PIR = 60        # le HC-SR501 déclenche au hasard pendant ~1 min après la mise sous tension
PAUSE_BOUCLE = 0.05     # lecture des capteurs 20 fois par seconde

capteur_mouvement = DigitalInputDevice(PIN_MOUVEMENT)
capteur_proximite = DigitalInputDevice(PIN_PROXIMITE)
capteur_son = DigitalInputDevice(PIN_SON)
# pull_up=True : résistance interne vers 3,3 V -> contact fermé = actif
capteur_contact = DigitalInputDevice(PIN_CONTACT, pull_up=True)

demarrage = time.time()
dernier_son = 0.0       # moment du dernier bruit entendu


def noter_son():
    global dernier_son
    dernier_son = time.time()


# Un bruit est trop bref pour être vu par la boucle : gpiozero appelle noter_son()
# dès que la sortie bascule, dans un sens ou dans l'autre (marche quelle que soit la polarité)
capteur_son.when_activated = noter_son
capteur_son.when_deactivated = noter_son


def lire_mouvement():
    if time.time() - demarrage < CHAUFFE_PIR:
        return 0
    return capteur_mouvement.value


def lire_etats():
    """Lit tous les capteurs et renvoie 1 = événement détecté, 0 = rien."""
    return {
        "mouvement": lire_mouvement(),
        "proximite": 1 if capteur_proximite.value == 0 else 0,   # logique inversée du module
        "son": 1 if time.time() - dernier_son < MAINTIEN_SON else 0,
        "contact_ouvert": 0 if capteur_contact.is_active else 1,
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
    print(f"Capteurs du Pi en écoute (PIR prêt dans {CHAUFFE_PIR} s, Ctrl+C pour arrêter)")

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
