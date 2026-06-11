# Kamera termowizyjna — testy komponentów (Raspberry Pi 3B, Headless)

Zestaw **3 niezależnych skryptów testowych** do sprawdzenia każdego komponentu
zaawansowanej kamery termowizyjnej **osobno**, zanim powstanie program główny.

Wszystko działa **bez pulpitu / środowiska graficznego** — sterujesz przez SSH,
a obraz oglądasz w przeglądarce na laptopie (Testy 1 i 2) lub bezpośrednio na
ekranie TFT (Test 3).

| Test | Komponent | Co robi | Jak oglądać |
|------|-----------|---------|-------------|
| 1 | Kamera RGB (Pi Camera Module 2) | Strumień wideo na żywo | `http://<IP_MALINKI>:8000/` |
| 2 | Termowizja MLX90640 (I2C) | Mapa cieplna na żywo | `http://<IP_MALINKI>:8001/` |
| 3 | Ekran TFT LCD (SPI, ST7789) | Płynna animowana fala + FPS | bezpośrednio na ekranie |

Dodatkowo:
- **`kamera_termowizyjna.py`** — program główny: fuzja RGB + termowizja na ekranie.
- **Wariant ESP32** — termowizja czytana przez ESP32 i przesyłana po USB do Pi
  (firmware w `esp32_mlx90640/`, test linku: `test_termo_esp32.py`). Patrz sekcja
  „Wariant: termowizja przez ESP32 (USB)” niżej.

---

## Dobór bibliotek (i dlaczego)

- **Kamera RGB → `picamera2`** + sprzętowy enkoder `MJPEGEncoder`.
  Oficjalna, nowoczesna biblioteka (libcamera). Stara `picamera` **nie działa**
  na Bullseye/Bookworm. Strumień MJPEG serwujemy lekkim serwerem z biblioteki
  standardowej (`http.server`) — zero okien/pulpitu.
- **MLX90640 → `adafruit-circuitpython-mlx90640`** (odczyt) + **`OpenCV`**
  (szybkie skalowanie, paleta kolorów, kodowanie JPEG) + **`numpy`**.
  OpenCV jest znacznie wydajniejszy od matplotlib → płynniejszy podgląd.
- **Ekran TFT → `adafruit-circuitpython-rgb-display`** (sterownik **ST7789** ze
  **sprzętowym SPI**) + **`Pillow`** (rysowanie całej klatki w buforze).
  Rysowanie „hurtem” i jedno `disp.image()` = maksymalna płynność, brak migotania.
- **Wariant ESP32 → `pyserial`** (odczyt ramek termowizji z ESP32 po USB) +
  firmware Arduino z biblioteką **Adafruit MLX90640**.

Wszystkie skrypty unikają funkcji otwierających okna (`imshow`, `plt.show()` itd.).

---

## Pinout (Twoje podłączenie)

**Kamera RGB:** dedykowany slot taśmowy CSI.

**MLX90640 (I2C):**
- SDA = Pin 3 (GPIO 2)
- SCL = Pin 5 (GPIO 3)

**Ekran TFT ILI9341 (SPI):**
- CS = Pin 24 (GPIO 8 / SPI CE0)
- RESET = Pin 22 (GPIO 25)
- DC = Pin 18 (GPIO 24)
- SDI/MOSI = Pin 19 (GPIO 10)
- SCK = Pin 23 (GPIO 11)
- SDO/MISO = Pin 21 (GPIO 9)

---

## Instalacja (komendy bash)

Najprościej — uruchom gotowy skrypt:

```bash
chmod +x install.sh
./install.sh
sudo reboot          # restart konieczny dla I2C/SPI i szybkiego I2C
```

### …albo krok po kroku (jeśli wolisz ręcznie)

```bash
# 1. System
sudo apt update && sudo apt full-upgrade -y

# 2. Włącz I2C i SPI
sudo raspi-config nonint do_i2c 0
sudo raspi-config nonint do_spi 0

# 3. Szybkie I2C (1 MHz) dla MLX90640
#    Bookworm: /boot/firmware/config.txt   |   Bullseye: /boot/config.txt
echo "dtparam=i2c_arm_baudrate=1000000" | sudo tee -a /boot/firmware/config.txt

# 4. Pakiety systemowe (picamera2 i OpenCV najlepiej z APT)
sudo apt install -y python3-pip python3-venv python3-picamera2 \
    python3-opencv python3-numpy python3-pil python3-libgpiod i2c-tools
# (opcjonalnie, przyspiesza numpy — pomiń, jeśli "no installation candidate")
sudo apt install -y libatlas3-base || true

# 5. Środowisko wirtualne z dostępem do pakietów systemowych
python3 -m venv --system-site-packages venv
source venv/bin/activate

# 6. Biblioteki Pythona
pip install --upgrade pip
pip install -r requirements.txt

# 7. Restart
sudo reboot
```

---

## Sprawdzenie sprzętu po instalacji

```bash
# Czujnik termowizyjny powinien zgłosić się pod adresem 0x33:
i2cdetect -y 1

# Kamera RGB powinna być wykryta:
libcamera-hello --list-cameras
```

---

## Uruchamianie testów

> Pamiętaj, by najpierw aktywować środowisko: `source venv/bin/activate`

### Test 1 — Kamera RGB
```bash
python3 test1_kamera_rgb.py
```
Otwórz na laptopie: `http://<IP_MALINKI>:8000/`

### Test 2 — Termowizja MLX90640
```bash
python3 test2_mlx90640.py
```
Otwórz na laptopie: `http://<IP_MALINKI>:8001/`
(Test 1 i 2 mają różne porty, więc mogą działać równolegle.)

### Test 3 — Ekran TFT LCD
```bash
python3 test3_tft_lcd.py              # animacja (test płynności)
python3 test3_tft_lcd.py kalibracja   # plansza diagnostyczna (gdy obraz nie pasuje)
```
Animowana, tęczowa fala pojawi się na ekranie; w terminalu i na ekranie
zobaczysz licznik **FPS**. Zatrzymanie: `Ctrl + C`.

Domyślnie ustawiony jest sterownik **ST7789** (taki kontroler ma ten moduł 2.4"
240×320). Gdyby kolory wyszły złe, w `test3_tft_lcd.py` masz „bezpieczniki”:
`INWERSJA` (None/True/False) oraz `ZAMIEN_RB` (zamiana czerwony↔niebieski).
Diagnostyka: `python3 test3_tft_lcd.py kalibracja` — biała ramka po obrysie i
pasy czerwony/zielony/niebieski pokażą adresowany obszar i kolejność kolorów.

Adres IP malinki sprawdzisz poleceniem `hostname -I`.

---

## Program główny — fuzja RGB + termowizja na ekranie (`kamera_termowizyjna.py`)

Gdy wszystkie 3 testy działają, ten skrypt spina je w całość: czyta obraz z
kamery RGB i temperatury z MLX90640, **nakłada mapę cieplną na obraz RGB (fuzja)**
i wyświetla wynik na ekranie **ST7789**. Opcjonalnie udostępnia ten sam obraz w
przeglądarce (MJPEG).

```bash
source venv/bin/activate
python3 kamera_termowizyjna.py            # start w trybie "fuzja"
python3 kamera_termowizyjna.py gorace      # start w trybie "gorące punkty"
```

**Tryby obrazu** (przełączasz na żywo klawiszami w terminalu SSH):
- `1` — tylko kamera RGB,
- `2` — tylko mapa cieplna,
- `3` — **fuzja** (półprzezroczyste nałożenie termowizji na RGB),
- `4` — **gorące punkty** (RGB, a ciepło widoczne tylko tam, gdzie gorąco).

Dodatkowo: `+` / `-` reguluje przezroczystość nałożenia, `q` kończy program.

Podgląd w przeglądarce (gdy `WEB_PODGLAD = True`): `http://<IP_MALINKI>:8080/`.

**Strojenie** (góra pliku `kamera_termowizyjna.py`):
- `ALFA` — siła nałożenia termowizji w trybie fuzji,
- `PROG_GORACE` — próg [°C] dla trybu „gorące punkty”,
- `TERMO_LUSTRO_X` / `TERMO_LUSTRO_Y` — dopasowanie orientacji termowizji do kamery,
- `INWERSJA` / `ZAMIEN_RB` — korekta kolorów ekranu (jak w Teście 3),
- `WEB_PODGLAD` — włącz/wyłącz podgląd w przeglądarce.

> Uwaga o dopasowaniu obrazów: kamera RGB i MLX90640 mają inne pole widzenia i są
> fizycznie przesunięte, więc nałożenie jest przybliżone. Dla idealnego pokrycia
> trzeba by skalibrować przesunięcie/skalę termowizji względem RGB — w razie
> potrzeby można to dodać.

---

## Wariant: termowizja przez ESP32 (USB)

Zamiast podłączać MLX90640 do I2C malinki, czujnik wpinasz w **ESP32**, a ESP32
łączysz **kablem USB** z Raspberry Pi. ESP32 czyta czujnik i wysyła gotowe ramki
temperatur — malinka nie walczy ze swoim sprzętowym I2C, a odczyt jest stabilny.

### 1. Podłączenie MLX90640 do ESP32 (ESP32 DevKit)
| MLX90640 | ESP32 |
|----------|-------|
| VIN | 3V3 (NIE 5 V!) |
| GND | GND |
| SDA | GPIO21 (domyślne SDA) |
| SCL | GPIO22 (domyślne SCL) |

ESP32 → kabel USB → Raspberry Pi.

### 2. Wgranie firmware na ESP32
Plik: `esp32_mlx90640/esp32_mlx90640.ino`. W Arduino IDE (lub `arduino-cli`):
- Zainstaluj bibliotekę **„Adafruit MLX90640”** (pociągnie też „Adafruit BusIO”).
- Wybierz płytkę **„ESP32 Dev Module”** (pakiet „esp32” by Espressif).
- Wgraj szkic. Prędkość portu w firmware: **230400** (`BAUD`), zgodna z kodem na
  Pi. (230400 jest pewne na Raspberry Pi; wyższe baudy jak 921600 bywają na
  malince niestabilne, mimo że działają w Serial Monitorze na PC.)

### 3. Znalezienie portu USB i uprawnienia (na Raspberry Pi)
```bash
ls /dev/ttyUSB* /dev/ttyACM*          # zwykle /dev/ttyUSB0 (CP2102/CH340) lub /dev/ttyACM0
sudo usermod -aG dialout $USER && echo "Wyloguj się/zrestartuj, by zadziałało"
```

### 4. Test samego linku ESP32 (w przeglądarce)
```bash
source venv/bin/activate
python3 test_termo_esp32.py                 # port /dev/ttyUSB0
python3 test_termo_esp32.py /dev/ttyACM0     # inny port
```
Podgląd: `http://<IP_MALINKI>:8001/`. To odpowiednik Testu 2, ale dane idą z ESP32.

### 5. Użycie w programie głównym
W `kamera_termowizyjna.py` (góra pliku) ustaw:
```python
ZRODLO_TERMO = "esp32"          # albo "i2c" dla bezpośredniego podłączenia
PORT_ESP32   = "/dev/ttyUSB0"    # Twój port USB
BAUD_ESP32   = 230400
```
Reszta (tryby, fuzja, ekran) działa tak samo. Przełączenie z powrotem na I2C =
ustawienie `ZRODLO_TERMO = "i2c"`.

### Protokół (dla ciekawych / diagnostyki)
ESP32 wysyła ramki binarnie: `[0xAA][0x55]` + 768 × `float32` (LE, 3072 B) +
suma kontrolna `uint16` (LE). Odbiór i resynchronizacja: `termo_zrodlo.py`
(klasa `CzujnikESP32`). Uszkodzone ramki są odrzucane po sumie kontrolnej.

### Diagnostyka, gdy strona nie pokazuje obrazu z ESP32
Najpierw uruchom skrypt diagnostyczny — jednoznacznie pokaże, gdzie jest problem:
```bash
source venv/bin/activate
python3 diag_esp32.py                 # /dev/ttyUSB0 @ 230400
python3 diag_esp32.py /dev/ttyACM0     # inny port
python3 diag_esp32.py /dev/ttyUSB0 skan  # przetestuj kilka baudów i wskaż działający
```
Interpretacja wyniku:
- **0 bajtów** → ESP32 nic nie wysyła. Sprawdź: czy firmware wgrany, dobry BAUD,
  oraz czy ESP32 wykrył czujnik (patrz dioda LED niżej).
- **dane są, ale STAŁY powtarzający się wzór bez nagłówków `0xAA 0x55`** →
  niezgodność prędkości (baud). Na Raspberry Pi używaj **230400** (po obu stronach).
  Wyższe baudy (921600) bywają tu niestabilne. Użyj `... skan`, by znaleźć działający.
- **bajty są, ale błędne sumy kontrolne** → kiepski kabel USB → zmień kabel/baud.
- **ramki OK + temperatury** → link działa; zrestartuj `test_termo_esp32.py`.

**Dioda LED na ESP32 (GPIO2) jako wskaźnik:**
- **szybkie miganie** = ESP32 NIE wykrył MLX90640 → problem ESP32↔czujnik,
- **powolne mruganie** = czujnik wykryty, ramki lecą do Pi (wszystko OK).

**Gdy dioda miga szybko (czujnik niewykryty):**
1. Otwórz **Serial Monitor** w Arduino IDE (prędkość **230400**) — firmware
   wypisuje skan magistrali I2C. Powinien znaleźć adres **0x33**.
   - **„BRAK urzadzen I2C”** → zasilanie/piny: VIN na **3V3 (NIE 5V)**, wspólny
     GND, SDA→GPIO21, SCL→GPIO22 (nie zamienione), pewne styki.
   - **dużo „widmowych” adresów (np. 0x60..0x7E) bez 0x33** → „pływająca”
     magistrala: zamień SDA↔SCL, sprawdź **wspólną masę** i **3V3**, oraz dodaj
     **rezystory pull-up 4,7 kΩ** z SDA→3V3 i SCL→3V3 (jeśli moduł ich nie ma).
   - **jest inny adres niż 0x33** → inny moduł/wariant — daj znać.
   - **inna płytka ESP32** (C3/S2/S3) ma inne piny I2C → zmień `SDA_PIN`/`SCL_PIN`
     na górze `esp32_mlx90640.ino` i wgraj ponownie.
2. Po diagnozie **zamknij Serial Monitor** (zajmuje port i blokuje odczyt na Pi).

Dodatkowo `test_termo_esp32.py` / program główny wypisują w terminalu co 3 s
statystyki (`ramki OK=.. błędne=..`) albo ostrzeżenie o braku danych.

### Najczęstsze problemy (ESP32)
- **`Nie mogę otworzyć portu`** — zły port (`ls /dev/ttyUSB* /dev/ttyACM*`) albo
  brak uprawnień (grupa `dialout` + wylogowanie/restart).
- **Port się otwiera, ale brak ramek** — firmware niewgrany / zły `BAUD_ESP32`
  (musi być 230400 po obu stronach) / zajęty port (zamknij Serial Monitor w
  Arduino) / ESP32 nie widzi czujnika (sprawdź diodę LED).
- **Obraz odwrócony** — dostrój `TERMO_LUSTRO_X` / `TERMO_LUSTRO_Y`.

---

## Najczęstsze problemy

- **MLX90640 niewidoczny w `i2cdetect`** — sprawdź podłączenie SDA/SCL i czy
  wykonałeś restart po włączeniu I2C.
- **Termowizja ma niskie FPS (np. ~1 fps)** — to prawie zawsze za wolne I2C.
  Pełna klatka MLX90640 składa się z DWÓCH podstron, więc realne FPS ≈
  `ODSWIEZANIE / 2`. Aby przyspieszyć:
  1. Ustaw `dtparam=i2c_arm_baudrate=1000000` w `config.txt` i **zrestartuj**
     (bez tego, na 100 kHz, dostaniesz ~1 fps).
  2. W `test2_mlx90640.py` ustaw `ODSWIEZANIE = ...REFRESH_16_HZ` (≈8 fps).
     Jeśli pojawią się błędy I/O — wróć do `REFRESH_8_HZ` (≈4 fps).
  MLX90640 fizycznie nie robi „60 fps" — to czujnik wolny z natury.
- **`OSError: [Errno 5] Input/output error` w Teście 2** — to błąd magistrali
  I2C (znany problem MLX90640 na RPi). Skrypt łapie pojedyncze takie błędy i
  próbuje dalej, ale jeśli sypią się non‑stop:
  1. Dodaj do `config.txt` prędkość I2C i **zrestartuj**:
     `dtparam=i2c_arm_baudrate=1000000` (a jeśli wciąż błędy — spróbuj `400000`).
     Na Pi prędkość ustawia się TYLKO tutaj, nie w kodzie.
  2. Sprawdź połączenia SDA (Pin 3) / SCL (Pin 5) — krótkie, pewne przewody;
     luźny styk to najczęstsza przyczyna Errno 5.
  3. Potwierdź, że czujnik jest widoczny: `i2cdetect -y 1` → adres `0x33`.
- **Ekran biały/szumy** — zmniejsz `BAUDRATE` w `test3_tft_lcd.py`
  (np. do `24000000`), sprawdź piny DC/RESET/CS.
- **Niepełny ekran / „pasek śniegu” + fioletowe (negatywowe) kolory (Test 3)** —
  to znak, że użyto złego sterownika. Te moduły 2.4" 240×320 to zwykle **ST7789**
  (mimo opisów „ILI9341”). W `test3_tft_lcd.py` ustaw `STEROWNIK = "st7789"`
  (już domyślnie). Sterownik ST7789 włącza inwersję i poprawnie adresuje panel —
  pasek i złe kolory znikają.
- **Kolory nadal złe po zmianie sterownika:**
  - „negatyw” (biel=czerń) → ustaw `INWERSJA = False`,
  - czerwony i niebieski zamienione → ustaw `ZAMIEN_RB = True`.
- **Losowy szum w CAŁYM obrazie reagujący na zegar SPI** — zmniejsz `BAUDRATE`
  (`24000000`→`16000000`→`12000000`) i skróć przewody MOSI/SCK.
- **Obraz lekko przesunięty (śnieg na innej krawędzi)** — dobierz `Y_OFFSET`
  (np. `0`, `20`, `80`) i ew. `X_OFFSET`.
- **Obraz termowizji odwrócony** — zmień `np.fliplr` / dodaj `np.flipud`
  w `test2_mlx90640.py`.
- **Poziome paski na termowizji** — to rozjeżdżanie się dwóch „podstron”
  czujnika przy zbyt szybkim odczycie. Ustaw `ODSWIEZANIE = ...REFRESH_8_HZ`
  (już domyślnie) i zostaw `ROZMYCIE = 3`. Paski znikają / mocno słabną.
- **Obraz „pulsuje” / migocze jasnością** — to efekt automatycznego skalowania
  kolorów. Zwiększ `WYGLADZANIE` (np. do `0.7`) albo ustaw stały zakres
  temperatur, np. `ZAKRES_TEMP = (20.0, 40.0)`, w `test2_mlx90640.py`.
- **`externally-managed-environment` przy pip** — instaluj zawsze wewnątrz
  `venv` (jak wyżej); nie używaj `sudo pip`.
- **`Package 'libatlas-base-dev' has no installation candidate`** — to tylko
  opcjonalny pakiet przyspieszający numpy. Zaktualizowany `install.sh` pomija go
  automatycznie. Ręcznie możesz po prostu kontynuować — testy działają bez niego.
