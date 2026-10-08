#!/bin/bash
# Durcissement minimal du Pi pour la démo. À lancer UNE fois, après installer.sh :
#   sudo bash /home/user/sentinel/durcir.sh
# Retour arrière en cas de problème : sudo ufw disable
set -e

echo "== Pare-feu ufw : tout est refusé en entrée, sauf ce dont Sentinel-X a besoin"
ufw --force reset
ufw default deny incoming
ufw default allow outgoing
ufw limit 22/tcp comment "SSH (limite anti force brute)"
ufw allow from 10.42.0.0/24 to any port 5000 proto tcp comment "Flask : dashboard + API, reseau Ultron seulement"
# Sans ces deux règles, le hotspot ne distribue plus d'IP : l'ESP32 ne pourrait plus se connecter
ufw allow in on wlan0 to any port 67 proto udp comment "DHCP du hotspot Ultron"
ufw allow in on wlan0 to any port 53 comment "DNS du hotspot Ultron"
ufw --force enable
ufw status verbose

echo "== SSH : connexion directe en root interdite"
echo "PermitRootLogin no" > /etc/ssh/sshd_config.d/sentinel.conf
sshd -t                     # vérifie la configuration AVANT de recharger (évite de se bloquer dehors)
systemctl reload ssh

echo "== Terminé. Pense à changer le mot de passe de 'user' : passwd"
