#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <PubSubClient.h>

// ===== WiFi =====
const char* WIFI_SSID = "YOUR_WIFI_NAME";
const char* WIFI_PASSWORD = "YOUR_WIFI_PASSWORD";

// ===== HiveMQ Cloud =====
const char* MQTT_HOST = "5b44b6c6c4ef4b65a63422825cfa4e4d.s1.eu.hivemq.cloud";
const int MQTT_PORT = 8883;
const char* MQTT_USERNAME = "obecject detection";
const char* MQTT_PASSWORD = "obecject123";

// ===== MQTT Topic =====
// ต้องตรงกับหน้าเว็บ YOLO / MQTT Dashboard
const char* LED_CONTROL_TOPIC = "led/control";

// ===== Hardware =====
// LED สีแดง
#define RED_LED_PIN 2

// LED สีเหลือง
// ถ้าไม่มี LED เหลือง ให้ใส่ -1
#define YELLOW_LED_PIN 5

// ความเร็วกะพริบ
const unsigned long BLINK_INTERVAL_MS = 500;

WiFiClientSecure wifiClient;
PubSubClient mqttClient(wifiClient);

// ===== สถานะไฟ =====
enum LedState {
  LED_STATE_OFF,
  LED_STATE_RED,
  LED_STATE_YELLOW
};

LedState ledState = LED_STATE_OFF;

bool blinkOn = false;
unsigned long lastBlinkTime = 0;

// ตรวจว่ามี LED เหลืองหรือไม่
bool hasYellowLed() {
  return YELLOW_LED_PIN >= 0;
}

// เปิด/ปิด GPIO
void setPin(int pin, bool on) {
  if (pin >= 0) {
    digitalWrite(pin, on ? HIGH : LOW);
  }
}

// ===== ควบคุมสถานะ LED =====
void setLedState(LedState state) {
  ledState = state;
  blinkOn = false;
  lastBlinkTime = millis();

  switch (state) {

    case LED_STATE_OFF:
      setPin(RED_LED_PIN, false);
      setPin(YELLOW_LED_PIN, false);

      Serial.println("LED OFF");
      break;

    case LED_STATE_RED:
      setPin(RED_LED_PIN, true);
      setPin(YELLOW_LED_PIN, false);

      Serial.println("LED RED (รถอยู่ใกล้)");
      break;

    case LED_STATE_YELLOW:

      if (hasYellowLed()) {

        // มี LED เหลือง
        setPin(RED_LED_PIN, false);
        setPin(YELLOW_LED_PIN, true);

      } else {

        // มี LED ดวงเดียว
        // ใช้การกระพริบแทนสีเหลือง
        setPin(RED_LED_PIN, true);
        blinkOn = true;
      }

      Serial.println("LED YELLOW (รถอยู่ไกล)");
      break;
  }
}

// ===== Update LED แบบไม่ใช้ delay =====
void updateLed() {

  if (ledState == LED_STATE_YELLOW && !hasYellowLed()) {

    if (millis() - lastBlinkTime >= BLINK_INTERVAL_MS) {

      lastBlinkTime = millis();

      blinkOn = !blinkOn;

      setPin(RED_LED_PIN, blinkOn);
    }
  }
}

// ===== Connect WiFi =====
void connectWiFi() {

  Serial.print("Connecting WiFi");

  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  while (WiFi.status() != WL_CONNECTED) {

    delay(500);

    Serial.print(".");
  }

  Serial.println();

  Serial.print("WiFi connected, IP: ");
  Serial.println(WiFi.localIP());
}

// ===== MQTT Message =====
void handleMqttMessage(
  char* topic,
  byte* payload,
  unsigned int length
) {

  String message = "";

  for (unsigned int i = 0; i < length; i++) {
    message += (char)payload[i];
  }

  message.trim();
  message.toUpperCase();

  Serial.print("Message arrived [");
  Serial.print(topic);
  Serial.print("]: ");
  Serial.println(message);

  // ตรวจ Topic
  if (strcmp(topic, LED_CONTROL_TOPIC) == 0) {

    if (message == "RED" || message == "ON") {

      setLedState(LED_STATE_RED);

    } else if (message == "YELLOW") {

      setLedState(LED_STATE_YELLOW);

    } else if (message == "OFF") {

      setLedState(LED_STATE_OFF);

    } else {

      Serial.println(
        "Unknown command "
        "(ใช้ได้: RED, YELLOW, OFF, ON)"
      );
    }
  }
}

// ===== Connect MQTT =====
void connectMQTT() {

  while (!mqttClient.connected()) {

    String clientId =
      "ESP32-LED-" +
      String((uint32_t)ESP.getEfuseMac(), HEX);

    Serial.print("Connecting MQTT...");

    if (
      mqttClient.connect(
        clientId.c_str(),
        MQTT_USERNAME,
        MQTT_PASSWORD
      )
    ) {

      Serial.println("connected");

      // Subscribe LED control
      mqttClient.subscribe(LED_CONTROL_TOPIC);

      Serial.print("Subscribed: ");
      Serial.println(LED_CONTROL_TOPIC);

    } else {

      Serial.print("failed, rc=");
      Serial.print(mqttClient.state());

      Serial.println(
        " try again in 3 seconds"
      );

      // รอ 3 วินาที
      // แต่ยังให้ LED กระพริบได้
      for (int i = 0; i < 60; i++) {

        updateLed();

        delay(50);
      }
    }
  }
}

// ===== Setup =====
void setup() {

  Serial.begin(115200);

  // ตั้งค่า LED
  pinMode(RED_LED_PIN, OUTPUT);

  if (hasYellowLed()) {
    pinMode(YELLOW_LED_PIN, OUTPUT);
  }

  // เริ่มต้นปิดไฟ
  setLedState(LED_STATE_OFF);

  // WiFi
  connectWiFi();

  // HiveMQ Cloud TLS
  // ใช้สำหรับทดสอบโดยไม่ตรวจ certificate
  wifiClient.setInsecure();

  // MQTT
  mqttClient.setServer(
    MQTT_HOST,
    MQTT_PORT
  );

  mqttClient.setCallback(
    handleMqttMessage
  );
}

// ===== Loop =====
void loop() {

  // Update LED
  updateLed();

  // ตรวจ WiFi
  if (WiFi.status() != WL_CONNECTED) {
    connectWiFi();
  }

  // ตรวจ MQTT
  if (!mqttClient.connected()) {
    connectMQTT();
  }

  // MQTT loop
  mqttClient.loop();
}