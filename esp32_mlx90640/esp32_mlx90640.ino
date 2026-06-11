/*
 * FIRMWARE ESP32 — CZYTNIK KAMERY TERMOWIZYJNEJ MLX90640 -> USB
 * ============================================================
 *
 * Rola:
 *   ESP32 czyta czujnik MLX90640 po I2C i wysyła gotowe ramki temperatur
 *   przez USB (port szeregowy) do Raspberry Pi. Dzięki temu malinka nie musi
 *   walczyć ze swoim wadliwym sprzętowym I2C — dostaje dane „na gotowo”.
 *
 * PODŁĄCZENIE MLX90640 do ESP32 (klasyczny ESP32 DevKit):
 *   MLX VIN  -> 3V3   (UWAGA: 3.3 V, NIE 5 V!)
 *   MLX GND  -> GND
 *   MLX SDA  -> GPIO21  (domyślne SDA na ESP32)
 *   MLX SCL  -> GPIO22  (domyślne SCL na ESP32)
 *   ESP32    -> USB     -> Raspberry Pi
 *
 * BIBLIOTEKI (Arduino IDE / arduino-cli):
 *   - "Adafruit MLX90640"  (zainstaluje też "Adafruit BusIO")
 *   Płytka: "ESP32 Dev Module" (pakiet "esp32" by Espressif).
 *
 * PROTOKÓŁ (binarny, odporny na desynchronizację):
 *   [0xAA][0x55] + 768 x float32 (little-endian, 3072 B) + suma kontrolna uint16 LE
 *   768 = 32 x 24 pikseli; float = temperatura w °C.
 *
 * Po stronie Raspberry Pi odbiera to moduł `termo_zrodlo.py` (klasa CzujnikESP32).
 */

#include <Wire.h>
#include <Adafruit_MLX90640.h>

// --- KONFIGURACJA -----------------------------------------------------------
#define BAUD            921600     // szybki port USB (musi zgadzać się z Pi)
#define I2C_CLOCK       800000     // 800 kHz na magistrali I2C czujnika
#define LICZBA_PIKSELI  (32 * 24)  // 768
#define ROZMIAR_PAYLOAD (LICZBA_PIKSELI * 4)  // 3072 bajtów (float32)

const uint8_t HDR0 = 0xAA;
const uint8_t HDR1 = 0x55;

Adafruit_MLX90640 mlx;
float ramka[LICZBA_PIKSELI];

void setup() {
  Serial.begin(BAUD);
  delay(100);

  Wire.begin();                 // SDA=GPIO21, SCL=GPIO22 (domyślne)
  Wire.setClock(I2C_CLOCK);

  // Inicjalizacja czujnika — w razie braku ponawiaj (czujnik mógł nie wstać)
  while (!mlx.begin(MLX90640_I2CADDR_DEFAULT, &Wire)) {
    delay(500);
  }

  // Tryb i parametry: chess (mniej pasków) + 18-bit + 8 Hz (stabilne przez USB)
  mlx.setMode(MLX90640_CHESS);
  mlx.setResolution(MLX90640_ADC_18BIT);
  mlx.setRefreshRate(MLX90640_8_HZ);
}

void loop() {
  // Odczyt pełnej ramki (768 wartości w °C). 0 = sukces.
  if (mlx.getFrame(ramka) != 0) {
    return;  // błąd odczytu — pomiń tę iterację
  }

  // Policz prostą sumę kontrolną z bajtów ramki
  uint8_t* bajty = (uint8_t*)ramka;
  uint16_t suma = 0;
  for (int i = 0; i < ROZMIAR_PAYLOAD; i++) {
    suma += bajty[i];
  }

  // Wyślij: nagłówek + dane + suma kontrolna
  Serial.write(HDR0);
  Serial.write(HDR1);
  Serial.write(bajty, ROZMIAR_PAYLOAD);
  Serial.write((uint8_t)(suma & 0xFF));
  Serial.write((uint8_t)((suma >> 8) & 0xFF));
}
