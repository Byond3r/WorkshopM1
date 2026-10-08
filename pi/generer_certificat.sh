#!/bin/bash
# Génère le certificat TLS auto-signé du serveur (valable 1 an). À lancer une fois, SANS sudo :
#   bash /home/user/sentinel/generer_certificat.sh
# certificat.pem est public (copié sur le PC pour vérifier le serveur) ; cle.pem est SECRÈTE et reste sur le Pi.
set -e
DOSSIER_TLS="/home/user/sentinel/tls"
mkdir -p "$DOSSIER_TLS"

openssl req -x509 -newkey rsa:2048 -nodes -days 365 \
    -keyout "$DOSSIER_TLS/cle.pem" -out "$DOSSIER_TLS/certificat.pem" \
    -subj "/CN=10.42.0.1/O=Sentinel-X" \
    -addext "subjectAltName=IP:10.42.0.1,IP:127.0.0.1"

chmod 600 "$DOSSIER_TLS/cle.pem"   # clé privée : lisible par l'utilisateur 'user' seulement
openssl x509 -in "$DOSSIER_TLS/certificat.pem" -noout -subject -enddate -fingerprint -sha256
