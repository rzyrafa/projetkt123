#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST LINKU ESP32 — TERMOWIZJA PRZEZ USB (Podgląd na żywo w przeglądarce)
=======================================================================

Cel:
    Sprawdzić SAM tor: MLX90640 -> ESP32 -> USB -> Raspberry Pi, niezależnie
    od ekranu i kamery. Skrypt odbiera ramki termowizji z ESP32 (port szeregowy),
    robi mapę cieplną i udostępnia ją w przeglądarce:

        http://<IP_MALINKI>:8001/

    To odpowiednik Testu 2, ale dane idą z ESP32 po USB, a nie z I2C malinki.

Wymagania:
    * Wgrany firmware na ESP32: katalog esp32_mlx90640/
    * pip install pyserial  (jest w requirements.txt)
    * Znajdź port USB:  ls /dev/ttyUSB* /dev/ttyACM*

Uruchomienie:
    python3 test_termo_esp32.py                 # port domyślny /dev/ttyUSB0
    python3 test_termo_esp32.py /dev/ttyACM0     # inny port
    (zatrzymanie: Ctrl + C)
"""

import io
import sys
import time
import logging
import socketserver
from http import server
from threading import Condition

import numpy as np
import cv2

from termo_zrodlo import CzujnikESP32, SZER_CZUJNIKA, WYS_CZUJNIKA

# --- KONFIGURACJA -----------------------------------------------------------
PORT_ESP32 = "/dev/ttyUSB0"   # nadpisywany pierwszym argumentem wiersza poleceń
BAUD_ESP32 = 230400           # musi zgadzać się z firmware ESP32
PORT_WWW = 8001
ROZMIAR_PODGLADU = (640, 480)
PALETA = cv2.COLORMAP_INFERNO
JAKOSC_JPEG = 80


STRONA = """\
<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8">
<title>Test ESP32 - Termowizja przez USB</title>
<style>body{background:#111;color:#eee;font-family:sans-serif;text-align:center;margin:0;padding:20px}
img{width:640px;max-width:100%;height:auto;border:2px solid #444;border-radius:8px}</style>
</head><body><h1>Termowizja z ESP32 (USB) — podgląd na żywo</h1>
<img src="stream.mjpg"><p>MLX90640 -> ESP32 -> USB -> Raspberry Pi</p>
</body></html>
"""


class WyjscieStrumienia(io.BufferedIOBase):
    def __init__(self):
        self.klatka = None
        self.warunek = Condition()

    def aktualizuj(self, buf):
        with self.warunek:
            self.klatka = buf
            self.warunek.notify_all()


def main():
    logging.basicConfig(level=logging.INFO)

    port = sys.argv[1] if len(sys.argv) > 1 else PORT_ESP32

    # 1. Źródło termowizji z ESP32 (USB)
    print(f"Łączenie z ESP32 na {port} @ {BAUD_ESP32}...")
    czujnik = CzujnikESP32(port=port, baud=BAUD_ESP32)
    czujnik.start()

    wyjscie = WyjscieStrumienia()

    # 2. Serwer WWW
    class Obsluga(server.BaseHTTPRequestHandler):
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
                self.send_header('Cache-Control', 'no-cache, private')
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
                    pass
            else:
                self.send_error(404)
                self.end_headers()

    class Serwer(socketserver.ThreadingMixIn, server.HTTPServer):
        allow_reuse_address = True
        daemon_threads = True

    from threading import Thread
    serwer = Serwer(('', PORT_WWW), Obsluga)
    Thread(target=serwer.serve_forever, daemon=True).start()
    print("=" * 60)
    print(f" Podgląd: http://<IP_MALINKI>:{PORT_WWW}/   (Ctrl+C aby zakończyć)")
    print("=" * 60)

    # 3. Pętla: dane z ESP32 -> mapa cieplna -> JPEG -> przeglądarka
    fps = 0.0
    licznik = 0
    czas_start = time.monotonic()
    try:
        while True:
            dane = czujnik.pobierz()
            if dane is None:
                time.sleep(0.05)
                continue

            t_min, t_max = float(dane.min()), float(dane.max())
            t_srodek = float(dane[WYS_CZUJNIKA // 2, SZER_CZUJNIKA // 2])
            zakres = max(t_max - t_min, 1e-3)
            znorm = np.clip((dane - t_min) / zakres * 255.0, 0, 255).astype(np.uint8)
            duzy = cv2.resize(znorm, ROZMIAR_PODGLADU, interpolation=cv2.INTER_CUBIC)
            kolor = cv2.applyColorMap(duzy, PALETA)

            def napis(txt, poz):
                cv2.putText(kolor, txt, poz, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (0, 0, 0), 3, cv2.LINE_AA)
                cv2.putText(kolor, txt, poz, cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                            (255, 255, 255), 1, cv2.LINE_AA)

            napis(f"MIN: {t_min:5.1f} C", (10, 25))
            napis(f"MAX: {t_max:5.1f} C", (10, 50))
            napis(f"SRODEK: {t_srodek:5.1f} C", (10, 75))
            napis(f"FPS: {fps:4.1f}", (10, ROZMIAR_PODGLADU[1] - 15))

            ok, buf = cv2.imencode('.jpg', kolor,
                                   [int(cv2.IMWRITE_JPEG_QUALITY), JAKOSC_JPEG])
            if ok:
                wyjscie.aktualizuj(buf.tobytes())

            licznik += 1
            if licznik >= 10:
                teraz = time.monotonic()
                fps = licznik / (teraz - czas_start)
                czas_start = teraz
                licznik = 0
    except KeyboardInterrupt:
        print("\nZatrzymywanie...")


if __name__ == '__main__':
    main()
