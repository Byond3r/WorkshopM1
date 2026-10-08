#!/bin/bash
# Installe tshark (capture réseau) et ufw (pare-feu) en passant par le partage de connexion du téléphone.
# La session SSH via Ultron va être coupée pendant l'installation : on confie donc le script à systemd,
# qui le fait tourner indépendamment de la session (le mot de passe sudo est demandé AVANT de partir) :
#   sudo systemd-run --unit=installation-sentinel bash /home/user/sentinel/installer.sh
# Après 5 minutes, se reconnecter à Ultron puis lire le résultat :
#   journalctl -u installation-sentinel --no-pager | tail -20

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
