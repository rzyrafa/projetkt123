#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST 2 — KAMERA TERMOWIZYJNA MLX90640 (I2C)
===========================================

Cel:
    Odczytać dane z czujnika MLX90640 (matryca 32x24 = 768 pikseli temperatury),
    zamienić je na kolorową mapę cieplną i udostępnić jako płynny strumień
    MJPEG w przeglądarce (tak jak w Teście 1):

        http://<IP_MALINKI>:8001/

Dobór bibliotek:
    * `adafruit-circuitpython-mlx90640` — wygodny, sprawdzony sterownik czujnika.
    * `numpy`   — szybkie operacje na macierzy temperatur.
    * `opencv`  — bardzo szybkie skalowanie (interpolacja), nakładanie palety
                  kolorów (applyColorMap) i kodowanie do JPEG. Dużo wydajniejsze
                  niż matplotlib, więc strumień jest płynniejszy.
    * serwer MJPEG z biblioteki standardowej — bez okien/pulpitu (headless OK).

WAŻNE (płynność / I2C):
    MLX90640 przy wyższych odświeżaniach potrzebuje SZYBKIEJ magistrali I2C.
    W pliku /boot/firmware/config.txt (Bookworm) lub /boot/config.txt (Bullseye)
    ustaw:  dtparam=i2c_arm_baudrate=1000000   i zrestartuj malinkę.
    Tutaj prosimy o I2C = 800 kHz, co dla RPi 3B jest dobrym kompromisem.

Pinout (zgodnie z Twoim podłączeniem):
    SDA = Pin 3 (GPIO 2),  SCL = Pin 5 (GPIO 3)

Uruchomienie:
    python3 test2_mlx90640.py
    (zatrzymanie: Ctrl + C)
"""

import io
import logging
import socketserver
import time
from http import server
from threading import Condition, Thread

import numpy as np
import cv2

import board
import busio
import adafruit_mlx90640

# --- KONFIGURACJA -----------------------------------------------------------
PORT = 8001                       # inny port niż Test 1, by móc działać równolegle
ROZMIAR_PODGLADU = (640, 480)     # docelowy rozmiar obrazu w przeglądarce (4:3)
PALETA = cv2.COLORMAP_INFERNO     # paleta cieplna (INFERNO/JET/HOT/TURBO...)
JAKOSC_JPEG = 80                  # jakość kodowania JPEG
CZESTOTLIWOSC_I2C = 800000        # 800 kHz na magistrali I2C
# Odświeżanie czujnika: 8 Hz to dobry, stabilny wybór dla RPi 3B.
ODSWIEZANIE = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ

# Wymiary natywne matrycy MLX90640
SZER_CZUJNIKA = 32
WYS_CZUJNIKA = 24


# --- SERWER STRUMIENIA (identyczny wzorzec jak w Teście 1) ------------------
STRONA = """\
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>Test 2 - Termowizja MLX90640</title>
  <style>
    body { background:#111; color:#eee; font-family:sans-serif; text-align:center; margin:0; padding:20px; }
    h1 { font-weight:300; }
    img { width:640px; max-width:100%; height:auto; border:2px solid #444; border-radius:8px; image-rendering:auto; }
  </style>
</head>
<body>
  <h1>Termowizja MLX90640 — mapa cieplna na żywo</h1>
  <img src="stream.mjpg" alt="Strumień termowizyjny">
  <p>Paleta INFERNO &mdash; wartości w &deg;C nałożone na obraz</p>
</body>
</html>
"""


class WyjscieStrumienia(io.BufferedIOBase):
    """Bufor najnowszej klatki JPEG współdzielony między wątkami."""

    def __init__(self):
        self.klatka = None
        self.warunek = Condition()

    def aktualizuj(self, buf):
        with self.warunek:
            self.klatka = buf
            self.warunek.notify_all()


class ObslugaZadan(server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/':
            self.send_response(301)
            self.send_header('Location', '/index.html')
            self.end_headers()
        elif self.path == '/index.html':
            tresc = STRONA.encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', len(tresc))
            self.end_headers()
            self.wfile.write(tresc)
        elif self.path == '/stream.mjpg':
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type',
                             'multipart/x-mixed-replace; boundary=KLATKA')
            self.end_headers()
            try:
                while True:
                    with wyjscie.warunek:
                        wyjscie.warunek.wait()
                        klatka = wyjscie.klatka
                    self.wfile.write(b'--KLATKA\r\n')
                    self.send_header('Content-Type', 'image/jpeg')
                    self.send_header('Content-Length', len(klatka))
                    self.end_headers()
                    self.wfile.write(klatka)
                    self.wfile.write(b'\r\n')
            except Exception as e:
                logging.info('Klient %s rozłączony: %s', self.client_address, str(e))
        else:
            self.send_error(404)
            self.end_headers()


class SerwerStrumienia(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# --- LOGIKA TERMOWIZJI ------------------------------------------------------
def watek_czujnika(mlx, wyjscie):
    """Pętla w tle: odczyt z czujnika -> mapa cieplna -> JPEG -> bufor."""
    surowa_klatka = [0.0] * (SZER_CZUJNIKA * WYS_CZUJNIKA)
    licznik = 0
    czas_start = time.monotonic()
    fps = 0.0

    while True:
        try:
            # Odczyt 768 wartości temperatury (w stopniach Celsjusza)
            mlx.getFrame(surowa_klatka)
        except ValueError:
            # Sporadyczne błędy I2C/CRC są normalne — po prostu pomijamy klatkę
            continue
        except RuntimeError as e:
            logging.warning("Błąd odczytu MLX90640: %s", e)
            time.sleep(0.05)
            continue

        # 1. Z listy do macierzy 24x32
        dane = np.array(surowa_klatka, dtype=np.float32).reshape(
            (WYS_CZUJNIKA, SZER_CZUJNIKA))

        # Czujnik bywa montowany "do góry nogami" / lustrzanie — korekta obrazu.
        # (Dostosuj flip/rotację jeśli obraz jest odwrócony u Ciebie.)
        dane = np.fliplr(dane)

        # 2. Statystyki temperatury
        t_min = float(dane.min())
        t_max = float(dane.max())
        t_srodek = float(dane[WYS_CZUJNIKA // 2, SZER_CZUJNIKA // 2])

        # 3. Normalizacja do zakresu 0..255 (dynamiczny zakres min-max)
        zakres = max(t_max - t_min, 1e-3)
        znorm = ((dane - t_min) / zakres * 255.0).astype(np.uint8)

        # 4. Powiększenie z ładną interpolacją (gładka mapa zamiast "kratki")
        powiekszony = cv2.resize(znorm, ROZMIAR_PODGLADU,
                                 interpolation=cv2.INTER_CUBIC)

        # 5. Nałożenie palety kolorów (mapa cieplna)
        kolor = cv2.applyColorMap(powiekszony, PALETA)

        # 6. Krzyżyk w centrum + opisy temperatur
        cx, cy = ROZMIAR_PODGLADU[0] // 2, ROZMIAR_PODGLADU[1] // 2
        cv2.drawMarker(kolor, (cx, cy), (255, 255, 255),
                       markerType=cv2.MARKER_CROSS, markerSize=20, thickness=1)

        def napis(txt, poz, kolor_txt):
            cv2.putText(kolor, txt, poz, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 0, 0), 3, cv2.LINE_AA)        # czarny kontur
            cv2.putText(kolor, txt, poz, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        kolor_txt, 1, cv2.LINE_AA)        # właściwy tekst

        napis(f"MIN: {t_min:5.1f} C", (10, 25), (255, 255, 255))
        napis(f"MAX: {t_max:5.1f} C", (10, 50), (255, 255, 255))
        napis(f"SRODEK: {t_srodek:5.1f} C", (10, 75), (255, 255, 255))
        napis(f"FPS: {fps:4.1f}", (10, ROZMIAR_PODGLADU[1] - 15), (255, 255, 255))

        # 7. Kodowanie do JPEG i przekazanie do serwera
        ok, bufor = cv2.imencode('.jpg', kolor,
                                 [int(cv2.IMWRITE_JPEG_QUALITY), JAKOSC_JPEG])
        if ok:
            wyjscie.aktualizuj(bufor.tobytes())

        # 8. Pomiar FPS (co ~10 klatek)
        licznik += 1
        if licznik >= 10:
            teraz = time.monotonic()
            fps = licznik / (teraz - czas_start)
            czas_start = teraz
            licznik = 0


def main():
    global wyjscie

    logging.basicConfig(level=logging.INFO)

    # 1. Inicjalizacja I2C i czujnika
    print("Inicjalizacja magistrali I2C i czujnika MLX90640...")
    i2c = busio.I2C(board.SCL, board.SDA, frequency=CZESTOTLIWOSC_I2C)
    mlx = adafruit_mlx90640.MLX90640(i2c)
    print("Wykryto MLX90640, ID:",
          [hex(x) for x in mlx.serial_number])
    mlx.refresh_rate = ODSWIEZANIE

    # 2. Wątek odczytu/przetwarzania w tle
    wyjscie = WyjscieStrumienia()
    Thread(target=watek_czujnika, args=(mlx, wyjscie), daemon=True).start()

    # 3. Serwer WWW
    try:
        serwer = SerwerStrumienia(('', PORT), ObslugaZadan)
        print("=" * 60)
        print(" Termowizja gotowa!")
        print(f" Otwórz w przeglądarce:  http://<IP_MALINKI>:{PORT}/")
        print(" Zatrzymanie: Ctrl + C")
        print("=" * 60)
        serwer.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymywanie...")


if __name__ == '__main__':
    main()
