# API Python avec SQLite

Cette version utilise SQLite à la place de PostgreSQL. SQLite enregistre la base dans le fichier data/capteurs.sqlite ; Python inclut déjà le module sqlite3, donc aucun pilote de base de données supplémentaire n'est nécessaire. Les routes HTTP restent les mêmes pour le Raspberry et le Dashboard.

## Configurer

1. Copier .env.example vers .env.
2. Remplacer API_KEY par une clé secrète. La même clé sera configurée sur le Raspberry.
3. Ne pas partager .env ni le fichier data/capteurs.sqlite sur GitHub.

Le fichier SQLite et ses tables sont créés automatiquement au premier démarrage. Pour enregistrer une mesure, son device_id doit correspondre à un appareil existant.

## Installer sous Windows

Dans PowerShell, exécuter :

    Set-Location "C:\Users\Teliau\Documents\Codex\2026-10-07\je-souhaite-cr-er-une-base\outputs\api-sqlite"
    py -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt

## Démarrer

La version PostgreSQL écoute déjà sur le port 8000. Cette version SQLite utilise le port 8001 afin de pouvoir les garder côte à côte :

    .\.venv\Scripts\python.exe -m uvicorn main:app --host 0.0.0.0 --port 8001 --reload

Laisser le terminal ouvert. Vérifier la connexion à SQLite sur http://localhost:8001/api/health. La documentation interactive est sur http://localhost:8001/docs.

## Routes

- GET /api/health vérifie l'accès au fichier SQLite.
- POST /api/devices crée un appareil ; il faut l'en-tête X-API-Key.
- GET /api/devices liste les appareils.
- POST /api/measurements enregistre une mesure ; il faut l'en-tête X-API-Key.
- GET /api/measurements renvoie les dernières mesures pour le Dashboard.
- GET /api/measurements?metric=temperature filtre par type de mesure.

Créer un appareil dans POST /api/devices avec ce JSON :

    {
      "device_name": "Capteur salon",
      "location": "Salon"
    }

La réponse contient le device_id à réutiliser lors de l'envoi des mesures.

Exemple de mesure :

    {
      "device_id": 1,
      "metric": "temperature",
      "value": 21.4,
      "unit": "°C"
    }

Si measured_at est absent, l'API utilise la date et l'heure UTC actuelles.

## Raspberry Pi sur le même Wi-Fi

Trouver l'adresse IPv4 Wi-Fi du PC avec ipconfig. Sur le Raspberry, envoyer les mesures vers http://ADRESSE_IPV4_DU_PC:8001/api/measurements avec l'en-tête X-API-Key. Autoriser Python sur le port 8001 pour le réseau privé dans le pare-feu Windows.

Ne pas exposer le fichier SQLite ni ouvrir un partage réseau vers le fichier. Le Raspberry et React passent par l'API. Ne pas exposer PostgreSQL sur le port 5432.

## Transfert de données depuis PostgreSQL

Ce fichier SQLite est une base séparée et vide au départ. Le schéma est recréé automatiquement, mais les anciennes données PostgreSQL ne sont pas copiées automatiquement.
