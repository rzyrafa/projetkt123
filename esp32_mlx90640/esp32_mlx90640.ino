/*
 * FIRMWARE ESP32 — CZYTNIK KAMERY TERMOWIZYJNEJ MLX90640 -> USB
 * ============================================================
 *
 * Rola:
 *   ESP32 czyta czujnik MLX90640 po I2C i wysyła gotowe ramki temperatur
 *   przez USB (port szeregowy) do Raspberry Pi. Dzięki temu malinka nie musi
 *   walczyć ze swoim wadliwym sprzętowym I2C — dostaje dane „na gotowo”.
 *
 * PODŁĄCZENIE MLX90640 do ESP32 (klasyczny ESP32 DevKit v1 / WROOM-32):
 *   MLX VIN  -> 3V3   (UWAGA: 3.3 V, NIE 5 V!)
 *   MLX GND  -> GND
 *   MLX SDA  -> GPIO21  (zmień SDA_PIN/SCL_PIN poniżej, jeśli masz inną płytkę)
 *   MLX SCL  -> GPIO22
 *
 * POŁĄCZENIE Z RASPBERRY PI — DWA WARIANTY:
 *   A) Przez USB: po prostu wepnij ESP32 kablem USB do malinki. Dane lecą wtedy
 *      portem USB (Serial). Wymaga DOBREGO zasilania Pi (inaczej ESP32 się resetuje).
 *   B) Po pinach GPIO (UART) — gdy ESP32 ma OSOBNE zasilanie (np. powerbank),
 *      a Pi własny zasilacz. Dane lecą przez Serial2 (GPIO17). Podłącz:
 *        ESP32 GPIO17 (TX2)  -> Raspberry Pi pin 10 (GPIO15 / RXD)
 *        ESP32 GND           -> Raspberry Pi pin 6  (GND)   <-- WSPÓLNA MASA, KONIECZNIE!
 *        (opcjonalnie ESP32 GPIO16 (RX2) -> Pi pin 8 / GPIO14 / TXD)
 *      Oba układy mają logikę 3,3 V, więc łączymy wprost (bez konwertera poziomów).
 *      Na Pi włącz UART (patrz README) i używaj portu /dev/serial0.
 *
 * Firmware wysyła ramki danych przez OBA wyjścia (USB Serial i Serial2), więc
 * działa w obu wariantach. Diagnostyka tekstowa (skan I2C) idzie na USB (Serial).
 *
 * DIAGNOSTYKA: jeśli dioda miga SZYBKO, czujnik NIE jest wykryty. Podłącz ESP32
 * przez USB i otwórz Serial Monitor (230400) — firmware wypisze skan I2C
 * (powinien znaleźć adres 0x33). Po diagnozie ZAMKNIJ Serial Monitor.
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
// Prędkość portu USB. 230400 jest PEWNE na Raspberry Pi (USB + CH340/CP2102).
// 921600 bywa niestabilne na malince (mimo że działa w Serial Monitorze na PC) —
// objawia się stałym, powtarzającym się „śmieciem” i brakiem nagłówków ramek.
// 230400 z zapasem wystarcza na ~4 kl./s. Musi być TAKIE SAMO jak na Raspberry Pi.
#define BAUD            230400
#define I2C_CLOCK_DETEKCJA 100000  // wolniej przy wykrywaniu = pewniej
#define I2C_CLOCK_PRACA    800000  // szybciej do odczytu ramek
#define SDA_PIN         21         // <- zmień, jeśli Twoja płytka ma inne piny I2C
#define SCL_PIN         22
// UART2 do połączenia po pinach z Raspberry Pi (wariant B — osobne zasilanie):
#define TX2_PIN         17         // ESP32 TX2 -> Raspberry Pi pin 10 (GPIO15/RXD)
#define RX2_PIN         16         // ESP32 RX2 (opcjonalnie) <- Pi pin 8 (GPIO14/TXD)
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

// Wysyła jedną ramkę przez OBA wyjścia: USB (Serial) i UART2 (Serial2).
// Dzięki temu działa zarówno wariant USB, jak i połączenie po pinach GPIO.
void wyslijRamke(uint8_t* bajty, uint16_t suma) {
  Serial.write(HDR0);
  Serial.write(HDR1);
  Serial.write(bajty, ROZMIAR_PAYLOAD);
  Serial.write((uint8_t)(suma & 0xFF));
  Serial.write((uint8_t)((suma >> 8) & 0xFF));

  Serial2.write(HDR0);
  Serial2.write(HDR1);
  Serial2.write(bajty, ROZMIAR_PAYLOAD);
  Serial2.write((uint8_t)(suma & 0xFF));
  Serial2.write((uint8_t)((suma >> 8) & 0xFF));
}

void setup() {
  Serial.begin(BAUD);                                  // USB (wariant A + diagnostyka)
  Serial2.begin(BAUD, SERIAL_8N1, RX2_PIN, TX2_PIN);   // GPIO UART (wariant B)
  pinMode(LED_PIN, OUTPUT);
  delay(100);

  // Włącz wewnętrzne podciąganie linii I2C (pomaga, gdy moduł nie ma własnych
  // rezystorów pull-up). UWAGA: to słabe (~45 kΩ) — pewniejsze są ZEWNĘTRZNE
  // rezystory 4,7 kΩ z SDA do 3V3 i z SCL do 3V3.
  pinMode(SDA_PIN, INPUT_PULLUP);
  pinMode(SCL_PIN, INPUT_PULLUP);

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

  // Wyślij ramkę (przez USB i UART2 jednocześnie)
  wyslijRamke(bajty, suma);

  // SYGNALIZACJA: dioda zmienia stan przy każdej wysłanej ramce (powolne
  // "mruganie" = wszystko działa, czujnik wykryty i dane lecą do Pi).
  digitalWrite(LED_PIN, !digitalRead(LED_PIN));
}
