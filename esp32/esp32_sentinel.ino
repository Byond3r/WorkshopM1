#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>

// --- Configuration ---
const char* WIFI_SSID     = "Ultron";
const char* WIFI_PASSWORD = "12345678";
// HTTPS (port 5443) : la mesure est chiffrée en TLS, illisible pour qui écoute le Wi-Fi.
// Plan B si le HTTPS pose problème en démo : "http://10.42.0.1:5000/mesure" (en clair).
const char* SERVEUR_URL   = "https://10.42.0.1:5443/mesure";
const unsigned long INTERVALLE_MS = 5000;   // un envoi toutes les 5 s

// Client TLS réutilisé à chaque envoi
WiFiClientSecure clientTls;

// Aucun capteur n'est branché sur l'ESP32 : les capteurs physiques sont sur le
// Raspberry Pi (pi/capteurs_pi.py). L'ESP32 est le nœud sans fil du réseau Ultron.

unsigned long derniereMesure = 0;

void connecterWifi() {
  WiFi.mode(WIFI_STA);
  Serial.print("Connexion a ");
  Serial.println(WIFI_SSID);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(WiFi.status());            // 6 = en cours/échec, 3 = connecté
    Serial.print(" ");
  }
  Serial.print("\nConnecte, IP : ");
  Serial.println(WiFi.localIP());
}

void envoyerMesure() {
  // SIMULÉ : aucun capteur de température ni de gaz n'est branché.
  // Gardé pour alimenter le graphe et l'Isolation Forest.
  float temperature = 20.0 + random(0, 50) / 10.0;
  int   gaz         = random(100, 300);

  char json[120];
  snprintf(json, sizeof(json),
           "{\"capteur\":\"esp32-1\",\"temperature\":%.1f,\"gaz\":%d}",
           temperature, gaz);

  HTTPClient http;
  http.begin(clientTls, SERVEUR_URL);
  http.addHeader("Content-Type", "application/json");
  http.setTimeout(5000);   // la poignée de main TLS prend plus de temps que du HTTP simple

  int code = http.POST(json);
  Serial.printf("Envoi %s -> code HTTP %d\n", json, code);

  http.end();
}

void setup() {
  Serial.begin(115200);
  // Chiffre la connexion SANS vérifier le certificat (auto-signé) du Pi.
  // Limite connue : protège contre l'écoute, pas contre un faux serveur (man-in-the-middle).
  clientTls.setInsecure();
  connecterWifi();
}

void loop() {
  // Reconnexion si le Pi a redémarré
  if (WiFi.status() != WL_CONNECTED) {
    connecterWifi();
  }

  // millis() au lieu de delay() : la boucle n'est jamais bloquée
  if (millis() - derniereMesure < INTERVALLE_MS) {
    return;
  }
  derniereMesure = millis();
  envoyerMesure();
}
