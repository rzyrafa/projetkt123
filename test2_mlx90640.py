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

WAŻNE (płynność / I2C) — przeczytaj, jeśli masz błąd "[Errno 5] I/O error":
    MLX90640 potrzebuje SZYBKIEJ magistrali I2C. Na Raspberry Pi prawdziwa
    prędkość I2C NIE jest ustawiana w kodzie (parametr `frequency` w busio jest
    tu IGNOROWANY), lecz w pliku konfiguracyjnym rozruchu:
        /boot/firmware/config.txt (Bookworm) lub /boot/config.txt (Bullseye)
    Dodaj linię i ZRESTARTUJ malinkę (sudo reboot):
        dtparam=i2c_arm_baudrate=1000000      # 1 MHz (zalecane)
    Jeśli przy 1 MHz nadal sypie błędami I/O, spróbuj wolniej i stabilniej:
        dtparam=i2c_arm_baudrate=400000       # 400 kHz
    Sprawdź też pewność połączeń SDA/SCL (krótkie przewody) — luźny styk = Errno 5.

    Uwaga: pojedyncze błędy odczytu są normalne; skrypt je łapie i próbuje dalej.

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
CZESTOTLIWOSC_I2C = 800000        # (ignorowane na RPi — prędkość ustawia config.txt)
# Odświeżanie czujnika. UWAGA: to częstotliwość POJEDYNCZEJ PODSTRONY, a pełna
# klatka składa się z DWÓCH podstron — więc realne FPS ≈ ODSWIEZANIE / 2.
#   REFRESH_8_HZ  -> ~4 kl./s   STABILNE, ZALECANE dla RPi 3B (mało pasków)
#   REFRESH_16_HZ -> ~8 kl./s   szybsze, ale częściej daje poziome PASKI
#   REFRESH_32_HZ -> ~16 kl./s  bardzo agresywne; zwykle paski/błędy na RPi 3B
# Paski biorą się z rozjeżdżania dwóch "podstron" przy zbyt szybkim odczycie —
# dlatego dla ładnego, stabilnego obrazu zostajemy przy 8 Hz.
ODSWIEZANIE = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ

# --- STABILIZACJA OBRAZU ----------------------------------------------------
# Zakres temperatur do kolorowania:
#   None            -> automatyczny (z czujnika), ale WYGŁADZANY w czasie,
#                      żeby kolory nie „pulsowały” klatka po klatce.
#   (min, max)      -> stały zakres w °C, np. (20.0, 40.0) — najstabilniejszy
#                      obraz, jeśli wiesz w jakim zakresie pracujesz.
ZAKRES_TEMP = None
# Siła wygładzania klatek w czasie (0.0 = brak, 0.9 = bardzo gładko/wolniej).
# Uśrednia kolejne odczyty -> mniej szumu i słabsze paski. 0.5 to dobry start.
WYGLADZANIE = 0.5
# Lekkie rozmycie przestrzenne (0 = brak; 3/5 = delikatne). Wygładza paski.
ROZMYCIE = 3

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
            except (BrokenPipeError, ConnectionResetError):
                # NORMALNE: przeglądarka zamknęła kartę / odświeżyła stronę.
                # To nie jest błąd — po prostu kończymy obsługę tego klienta.
                logging.debug('Klient %s zamknął połączenie.', self.client_address)
            except Exception as e:
                logging.debug('Klient %s rozłączony: %s', self.client_address, str(e))
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
    bledy_z_rzedu = 0   # licznik kolejnych błędów I2C (do diagnostyki)

    srednia_dane = None     # wygładzona w czasie macierz temperatur (EMA)
    zakres_min = None       # wygładzony dolny próg kolorowania (tryb auto)
    zakres_max = None       # wygładzony górny próg kolorowania (tryb auto)

    while True:
        try:
            # Odczyt 768 wartości temperatury (w stopniach Celsjusza)
            mlx.getFrame(surowa_klatka)
            bledy_z_rzedu = 0
        except ValueError:
            # Błędy CRC / "dane jeszcze niegotowe" są NORMALNE — pomijamy klatkę
            continue
        except (OSError, RuntimeError) as e:
            # [Errno 5] Input/output error itp. — typowy chwilowy błąd magistrali
            # I2C na Raspberry Pi. NIE przerywamy wątku, tylko próbujemy dalej.
            bledy_z_rzedu += 1
            if bledy_z_rzedu <= 3 or bledy_z_rzedu % 30 == 0:
                logging.warning(
                    "Błąd I2C przy odczycie MLX90640 (%d z rzędu): %s",
                    bledy_z_rzedu, e)
            # Po serii błędów spróbuj „odświeżyć” konfigurację czujnika
            if bledy_z_rzedu % 30 == 0:
                try:
                    mlx.refresh_rate = ODSWIEZANIE
                except Exception:
                    pass
            time.sleep(0.1)
            continue

        # 1. Z listy do macierzy 24x32
        dane = np.array(surowa_klatka, dtype=np.float32).reshape(
            (WYS_CZUJNIKA, SZER_CZUJNIKA))

        # 1a. Czyszczenie BŁĘDNYCH pikseli (np. -273°C przy zakłóceniach I2C),
        #     żeby nie rozwalały skali kolorów.
        poprawne = (dane > -40.0) & (dane < 300.0)
        if not poprawne.all():
            dane[~poprawne] = np.median(dane[poprawne]) if poprawne.any() else 25.0

        # Czujnik bywa montowany "do góry nogami" / lustrzanie — korekta obrazu.
        # (Dostosuj flip/rotację jeśli obraz jest odwrócony u Ciebie.)
        dane = np.fliplr(dane)

        # 1a. WYGŁADZANIE W CZASIE (EMA) — redukuje szum i osłabia paski,
        #     a także stabilizuje statystyki temperatury między klatkami.
        if srednia_dane is None:
            srednia_dane = dane
        else:
            srednia_dane = (WYGLADZANIE * srednia_dane
                            + (1.0 - WYGLADZANIE) * dane)
        dane = srednia_dane

        # 2. Statystyki temperatury (z wygładzonej macierzy)
        t_min = float(dane.min())
        t_max = float(dane.max())
        t_srodek = float(dane[WYS_CZUJNIKA // 2, SZER_CZUJNIKA // 2])

        # 3. Wyznaczenie zakresu kolorowania — STABILNEGO, żeby obraz nie pulsował
        if ZAKRES_TEMP is not None:
            # stały zakres zadany ręcznie
            dolny, gorny = float(ZAKRES_TEMP[0]), float(ZAKRES_TEMP[1])
        else:
            # tryb auto: powoli „goń” bieżące min/max (też EMA)
            if zakres_min is None:
                zakres_min, zakres_max = t_min, t_max
            else:
                zakres_min = 0.9 * zakres_min + 0.1 * t_min
                zakres_max = 0.9 * zakres_max + 0.1 * t_max
            dolny, gorny = zakres_min, zakres_max

        # 4. Normalizacja do 0..255 wg stabilnego zakresu
        zakres = max(gorny - dolny, 1e-3)
        znorm = np.clip((dane - dolny) / zakres * 255.0, 0, 255).astype(np.uint8)

        # 5. Powiększenie z ładną interpolacją (gładka mapa zamiast "kratki")
        powiekszony = cv2.resize(znorm, ROZMIAR_PODGLADU,
                                 interpolation=cv2.INTER_CUBIC)

        # 5a. Delikatne rozmycie — dodatkowo wygładza ewentualne paski
        if ROZMYCIE and ROZMYCIE >= 3:
            powiekszony = cv2.GaussianBlur(powiekszony, (ROZMYCIE, ROZMYCIE), 0)

        # 6. Nałożenie palety kolorów (mapa cieplna)
        kolor = cv2.applyColorMap(powiekszony, PALETA)

        # 7. Krzyżyk w centrum + opisy temperatur
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

        # 8. Kodowanie do JPEG i przekazanie do serwera
        ok, bufor = cv2.imencode('.jpg', kolor,
                                 [int(cv2.IMWRITE_JPEG_QUALITY), JAKOSC_JPEG])
        if ok:
            wyjscie.aktualizuj(bufor.tobytes())

        # 9. Pomiar FPS (co ~10 klatek)
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
    # Uwaga: na RPi argument `frequency` jest ignorowany — realną prędkość I2C
    # ustawia się w config.txt (patrz nagłówek pliku: dtparam=i2c_arm_baudrate).
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
