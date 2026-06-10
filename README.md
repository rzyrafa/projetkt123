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
| 3 | Ekran TFT LCD (SPI, ILI9341) | Płynna animowana fala + FPS | bezpośrednio na ekranie |

---

## Dobór bibliotek (i dlaczego)

- **Kamera RGB → `picamera2`** + sprzętowy enkoder `MJPEGEncoder`.
  Oficjalna, nowoczesna biblioteka (libcamera). Stara `picamera` **nie działa**
  na Bullseye/Bookworm. Strumień MJPEG serwujemy lekkim serwerem z biblioteki
  standardowej (`http.server`) — zero okien/pulpitu.
- **MLX90640 → `adafruit-circuitpython-mlx90640`** (odczyt) + **`OpenCV`**
  (szybkie skalowanie, paleta kolorów, kodowanie JPEG) + **`numpy`**.
  OpenCV jest znacznie wydajniejszy od matplotlib → płynniejszy podgląd.
- **Ekran TFT → `adafruit-circuitpython-rgb-display`** (sterownik ILI9341 ze
  **sprzętowym SPI**) + **`Pillow`** (rysowanie całej klatki w buforze).
  Rysowanie „hurtem” i jedno `disp.image()` = maksymalna płynność, brak migotania.

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
    python3-opencv python3-numpy python3-pil python3-libgpiod \
    i2c-tools libatlas-base-dev

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
python3 test3_tft_lcd.py
```
Animowana, tęczowa fala pojawi się na ekranie; w terminalu i na ekranie
zobaczysz licznik **FPS**. Zatrzymanie: `Ctrl + C`.

Adres IP malinki sprawdzisz poleceniem `hostname -I`.

---

## Najczęstsze problemy

- **MLX90640 niewidoczny w `i2cdetect`** — sprawdź podłączenie SDA/SCL i czy
  wykonałeś restart po włączeniu I2C.
- **Termowizja „zacina się” / niskie FPS** — upewnij się, że ustawiłeś
  `dtparam=i2c_arm_baudrate=1000000` i zrestartowałeś. Możesz też zmniejszyć
  `ODSWIEZANIE` w `test2_mlx90640.py`.
- **Ekran biały/szumy** — zmniejsz `BAUDRATE` w `test3_tft_lcd.py`
  (np. do `24000000`), sprawdź piny DC/RESET/CS.
- **Obraz termowizji odwrócony** — zmień `np.fliplr` / dodaj `np.flipud`
  w `test2_mlx90640.py`.
- **`externally-managed-environment` przy pip** — instaluj zawsze wewnątrz
  `venv` (jak wyżej); nie używaj `sudo pip`.
