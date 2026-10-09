#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <MFRC522.h>   // bibliothèque "MFRC522" (GithubCommunity), à installer dans l'Arduino IDE

// --- Configuration ---
const char* WIFI_SSID     = "Ultron";
const char* WIFI_PASSWORD = "12345678";
// HTTPS (port 5443) : la mesure est chiffrée en TLS, illisible pour qui écoute le Wi-Fi.
// Plan B si le HTTPS pose problème en démo : "http://10.42.0.1:5000/mesure" (en clair).
const char* SERVEUR_URL   = "https://10.42.0.1:5443/mesure";
const char* BADGE_URL     = "https://10.42.0.1:5443/badge";
// Secret partagé avec le serveur (fichier cle_api.txt sur le Pi) : sans lui, /badge refuse.
// Limite connue : en production, il irait dans un fichier secrets.h non versionné.
const char* CLE_API       = "1cad872faa2d8e8acc21e80acf6efbab";
const unsigned long INTERVALLE_MS = 5000;           // un envoi de mesure toutes les 5 s
const unsigned long DELAI_ENTRE_BADGES_MS = 2000;   // évite d'envoyer 10 fois le même badge

// --- Lecteur RFID RC522 (bus SPI) ---
// SDA -> G5, SCK -> G18, MOSI -> G23, MISO -> G19, RST -> G22, 3.3V -> 3V3, GND -> GND, IRQ : rien
const int PIN_RFID_SDA = 5;
const int PIN_RFID_RST = 22;
MFRC522 lecteurRfid(PIN_RFID_SDA, PIN_RFID_RST);

// Client TLS réutilisé à chaque envoi
WiFiClientSecure clientTls;

unsigned long derniereMesure = 0;
unsigned long dernierBadge = 0;

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

// UID du badge en texte hexadécimal, ex. "A1B2C3D4"
String lireUidBadge() {
  String uid = "";
  for (byte i = 0; i < lecteurRfid.uid.size; i++) {
    if (lecteurRfid.uid.uidByte[i] < 0x10) {
      uid += "0";
    }
    uid += String(lecteurRfid.uid.uidByte[i], HEX);
  }
  uid.toUpperCase();
  return uid;
}

// L'ESP32 ne décide rien : il transmet l'UID, c'est le serveur qui sait quels badges sont autorisés
void envoyerBadge(const String& uid) {
  char json[80];
  snprintf(json, sizeof(json), "{\"capteur\":\"esp32-1\",\"uid\":\"%s\"}", uid.c_str());

  HTTPClient http;
  http.begin(clientTls, BADGE_URL);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Cle-Api", CLE_API);
  http.setTimeout(5000);

  int code = http.POST(json);
  // 200 = badge autorisé (armement/désarmement), 403 = badge inconnu, 401 = mauvaise clé API
  Serial.printf("Badge %s -> code HTTP %d %s\n", uid.c_str(), code, http.getString().c_str());

  http.end();
}

void verifierBadge() {
  if (millis() - dernierBadge < DELAI_ENTRE_BADGES_MS) {
    return;
  }
  if (!lecteurRfid.PICC_IsNewCardPresent()) {
    return;   // aucun badge à portée
  }
  dernierBadge = millis();
  if (!lecteurRfid.PICC_ReadCardSerial()) {
    // Le lecteur voit un badge mais n'arrive pas à lire son UID : badge trop loin, ou de type non géré
    Serial.println("Badge detecte mais illisible : rapproche-le, a plat sur l'antenne");
    return;
  }
  envoyerBadge(lireUidBadge());
  lecteurRfid.PICC_HaltA();   // met le badge en veille : il ne sera pas relu en boucle
}

void setup() {
  Serial.begin(115200);
  // Chiffre la connexion SANS vérifier le certificat (auto-signé) du Pi.
  // Limite connue : protège contre l'écoute, pas contre un faux serveur (man-in-the-middle).
  clientTls.setInsecure();

  SPI.begin();
  lecteurRfid.PCD_Init();
  // Puissance de réception au maximum : certains RC522 ne lisent qu'à quelques mm sinon
  lecteurRfid.PCD_SetAntennaGain(lecteurRfid.RxGain_max);
  // Affiche la version du lecteur : "0x92" ou "0x91" = bien câblé, "0x00" ou "0xFF" = problème de fils
  lecteurRfid.PCD_DumpVersionToSerial();

  connecterWifi();
}

void loop() {
  // Reconnexion si le Pi a redémarré
  if (WiFi.status() != WL_CONNECTED) {
    connecterWifi();
  }

  verifierBadge();   // à chaque tour : un badge doit réagir tout de suite

  // millis() au lieu de delay() : la boucle n'est jamais bloquée
  if (millis() - derniereMesure < INTERVALLE_MS) {
    return;
  }
  derniereMesure = millis();
  envoyerMesure();
}
