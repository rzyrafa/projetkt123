#!/usr/bin/env bash
#
# Skrypt instalacyjny dla kamery termowizyjnej (Raspberry Pi 3B, system Headless)
# =============================================================================
# Co robi:
#   1. Aktualizuje system.
#   2. Włącza interfejsy I2C i SPI.
#   3. Ustawia szybkie I2C (1 MHz) dla MLX90640.
#   4. Instaluje pakiety systemowe (picamera2, OpenCV, narzędzia I2C, itp.).
#   5. Tworzy środowisko wirtualne (venv) z dostępem do pakietów systemowych.
#   6. Instaluje biblioteki Pythona z requirements.txt.
#
# Uruchomienie:
#   chmod +x install.sh
#   ./install.sh
#
# Po instalacji wykonaj RESTART (sudo reboot), aby I2C/SPI i baudrate zadziałały.
# -----------------------------------------------------------------------------

set -e  # przerwij przy pierwszym błędzie

echo ">>> [1/6] Aktualizacja systemu..."
sudo apt update
sudo apt full-upgrade -y

echo ">>> [2/6] Włączanie interfejsów I2C i SPI..."
sudo raspi-config nonint do_i2c 0   # 0 = włącz
sudo raspi-config nonint do_spi 0   # 0 = włącz

echo ">>> [3/6] Ustawianie szybkiego I2C (1 MHz) dla MLX90640..."
# Plik konfiguracyjny różni się między Bookworm a Bullseye:
if [ -f /boot/firmware/config.txt ]; then
    CONFIG_TXT=/boot/firmware/config.txt   # Raspberry Pi OS Bookworm
else
    CONFIG_TXT=/boot/config.txt            # Raspberry Pi OS Bullseye i starsze
fi
if ! grep -q "^dtparam=i2c_arm_baudrate=1000000" "$CONFIG_TXT"; then
    echo "dtparam=i2c_arm_baudrate=1000000" | sudo tee -a "$CONFIG_TXT"
    echo "    Dodano szybkie I2C do $CONFIG_TXT"
else
    echo "    Szybkie I2C już ustawione w $CONFIG_TXT"
fi

echo ">>> [4/6] Instalacja pakietów systemowych (APT)..."
# Pakiety wymagane (bez nich testy nie ruszą):
sudo apt install -y \
    python3-pip \
    python3-venv \
    python3-picamera2 \
    python3-opencv \
    python3-numpy \
    python3-pil \
    python3-libgpiod \
    i2c-tools

# Pakiet opcjonalny: libatlas (przyspiesza numpy). W nowszych wydaniach RPi OS
# bywa niedostępny ("no installation candidate") — wtedy go po prostu pomijamy,
# bo numpy i tak instalujemy z APT. Dlatego instalujemy go osobno z "|| true".
echo ">>> (opcjonalnie) Próba instalacji biblioteki libatlas (przyspiesza numpy)..."
sudo apt install -y libatlas3-base 2>/dev/null \
    || sudo apt install -y libatlas-base-dev 2>/dev/null \
    || echo "    libatlas niedostępny w tym wydaniu — pomijam (nie jest wymagany)."

echo ">>> [5/6] Tworzenie środowiska wirtualnego (z dostępem do pakietów systemowych)..."
# --system-site-packages pozwala venv widzieć picamera2 i OpenCV z APT
python3 -m venv --system-site-packages venv
# shellcheck disable=SC1091
source venv/bin/activate

echo ">>> [6/6] Instalacja bibliotek Pythona (pip)..."
pip install --upgrade pip
pip install -r requirements.txt

echo ""
echo "============================================================"
echo " INSTALACJA ZAKOŃCZONA!"
echo ""
echo " 1) Wykonaj RESTART, aby aktywować I2C/SPI i szybkie I2C:"
echo "      sudo reboot"
echo ""
echo " 2) Po restarcie sprawdź czujnik termowizyjny (adres 0x33):"
echo "      i2cdetect -y 1"
echo ""
echo " 3) Aktywuj środowisko przed uruchomieniem skryptów:"
echo "      source venv/bin/activate"
echo ""
echo " 4) Uruchom testy:"
echo "      python3 test1_kamera_rgb.py     # -> http://<IP>:8000/"
echo "      python3 test2_mlx90640.py       # -> http://<IP>:8001/"
echo "      python3 test3_tft_lcd.py        # animacja na ekranie TFT"
echo "============================================================"
