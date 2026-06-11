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
 *   MLX SDA  -> GPIO21  (zmień SDA_PIN/SCL_PIN poniżej, jeśli masz inną płytkę)
 *   MLX SCL  -> GPIO22
 *   ESP32    -> USB     -> Raspberry Pi
 *
 * DIAGNOSTYKA: jeśli dioda miga SZYBKO, czujnik NIE jest wykryty. Otwórz wtedy
 * Serial Monitor (921600) w Arduino IDE — firmware wypisze skan magistrali I2C
 * (powinien znaleźć adres 0x33). Po podejrzeniu obrazu ZAMKNIJ Serial Monitor,
 * bo zajmuje port i blokuje odczyt na Raspberry Pi.
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
#define I2C_CLOCK_DETEKCJA 100000  // wolniej przy wykrywaniu = pewniej
#define I2C_CLOCK_PRACA    800000  // szybciej do odczytu ramek
#define SDA_PIN         21         // <- zmień, jeśli Twoja płytka ma inne piny I2C
#define SCL_PIN         22
#define LICZBA_PIKSELI  (32 * 24)  // 768
#define ROZMIAR_PAYLOAD (LICZBA_PIKSELI * 4)  // 3072 bajtów (float32)
#define LED_PIN         2          // wbudowana dioda na wielu ESP32 DevKit (GPIO2)

const uint8_t HDR0 = 0xAA;
const uint8_t HDR1 = 0x55;

Adafruit_MLX90640 mlx;
float ramka[LICZBA_PIKSELI];

// Skan magistrali I2C — wypisuje znalezione adresy (pomoc przy diagnostyce)
void skanujI2C() {
  Serial.println("Skan I2C...");
  int znalezione = 0;
  for (uint8_t adres = 1; adres < 127; adres++) {
    Wire.beginTransmission(adres);
    if (Wire.endTransmission() == 0) {
      Serial.print("  znaleziono urzadzenie pod adresem 0x");
      Serial.println(adres, HEX);
      znalezione++;
    }
  }
  if (znalezione == 0) {
    Serial.println("  BRAK urzadzen I2C! Sprawdz: zasilanie 3V3 (NIE 5V), GND,");
    Serial.print("  oraz piny SDA=GPIO"); Serial.print(SDA_PIN);
    Serial.print(", SCL=GPIO"); Serial.println(SCL_PIN);
  } else {
    Serial.println("  (MLX90640 powinien byc pod adresem 0x33)");
  }
}

void setup() {
  Serial.begin(BAUD);
  pinMode(LED_PIN, OUTPUT);
  delay(100);

  Wire.begin(SDA_PIN, SCL_PIN);     // jawne piny I2C
  Wire.setClock(I2C_CLOCK_DETEKCJA);

  // Inicjalizacja czujnika — w razie braku ponawiaj.
  // SYGNALIZACJA: gdy czujnik NIE jest wykryty, dioda miga SZYBKO, a do Serial
  // (921600) leci skan I2C — otwórz Serial Monitor, by zobaczyć, co jest na magistrali.
  unsigned long ostatniSkan = 0;
  while (!mlx.begin(MLX90640_I2CADDR_DEFAULT, &Wire)) {
    if (millis() - ostatniSkan > 1500) {
      Serial.println("MLX90640 nie wykryty.");
      skanujI2C();
      ostatniSkan = millis();
    }
    digitalWrite(LED_PIN, !digitalRead(LED_PIN));  // szybkie miganie
    delay(100);
  }
  digitalWrite(LED_PIN, LOW);

  Wire.setClock(I2C_CLOCK_PRACA);

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

  // SYGNALIZACJA: dioda zmienia stan przy każdej wysłanej ramce (powolne
  // "mruganie" = wszystko działa, czujnik wykryty i dane lecą do Pi).
  digitalWrite(LED_PIN, !digitalRead(LED_PIN));
}
