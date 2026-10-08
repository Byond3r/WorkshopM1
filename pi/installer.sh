#!/bin/bash
# Installe tshark (capture réseau) et ufw (pare-feu) en passant par le partage de connexion du téléphone.
# La session SSH via Ultron va être coupée pendant l'installation : on lance donc le script en arrière-plan :
#   sudo nohup bash /home/user/sentinel/installer.sh > /tmp/installation.log 2>&1 &
# Après 5 minutes, se reconnecter à Ultron puis lire le résultat : cat /tmp/installation.log

CONNEXION_INTERNET="Xiaomi 17"
CONNEXION_HOTSPOT="Ultron"

echo "== $(date '+%H:%M:%S') Passage sur $CONNEXION_INTERNET"
nmcli con up "$CONNEXION_INTERNET"

echo "== $(date '+%H:%M:%S') Installation de tshark et ufw"
apt-get update
# noninteractive : pas de question bloquante pendant l'installation de wireshark-common
DEBIAN_FRONTEND=noninteractive apt-get install -y tshark ufw

# Pas de "set -e" : on revient sur Ultron MÊME si l'installation a échoué
echo "== $(date '+%H:%M:%S') Retour sur $CONNEXION_HOTSPOT"
nmcli con up "$CONNEXION_HOTSPOT"

echo "== Résultat"
tshark --version | head -1
ufw version | head -1
