#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST 1 — KAMERA RGB (Raspberry Pi Camera Module 2)
==================================================

Cel:
    Uruchomić kamerę RGB i udostępnić obraz NA ŻYWO przez przeglądarkę
    (strumień MJPEG po HTTP). Wystarczy wejść z laptopa pod adres:

        http://<IP_MALINKI>:8000/

Dobór biblioteki:
    Używamy `picamera2` — to oficjalna, nowoczesna biblioteka oparta o
    libcamera (stara `picamera` NIE działa na Bullseye/Bookworm). Strumień
    MJPEG generujemy sprzętowym/optymalnym enkoderem `MJPEGEncoder`, a klatki
    serwujemy lekkim serwerem z biblioteki standardowej (http.server) —
    dzięki temu NIE potrzebujemy żadnego okna/pulpitu (idealne dla headless).

Uruchomienie:
    python3 test1_kamera_rgb.py
    (zatrzymanie: Ctrl + C)
"""

import io
import logging
import socketserver
from http import server
from threading import Condition

from picamera2 import Picamera2
from picamera2.encoders import MJPEGEncoder
from picamera2.outputs import FileOutput

# --- KONFIGURACJA -----------------------------------------------------------
ROZDZIELCZOSC = (640, 480)   # rozdzielczość podglądu (możesz podnieść, np. 1280x720)
FPS = 30                     # docelowa liczba klatek na sekundę
PORT = 8000                  # port serwera WWW
JAKOSC_JPEG = 80             # jakość MJPEG (im wyżej, tym ostrzej i więcej danych)

# --- PROSTA STRONA HTML Z PODGLĄDEM ----------------------------------------
STRONA = """\
<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8">
  <title>Test 1 - Kamera RGB (Pi Camera 2)</title>
  <style>
    body { background:#111; color:#eee; font-family:sans-serif; text-align:center; margin:0; padding:20px; }
    h1 { font-weight:300; }
    img { max-width:100%; height:auto; border:2px solid #444; border-radius:8px; }
  </style>
</head>
<body>
  <h1>Kamera RGB — podgląd na żywo (MJPEG)</h1>
  <img src="stream.mjpg" alt="Strumień z kamery RGB">
  <p>Strumień MJPEG &mdash; Raspberry Pi Camera Module 2</p>
</body>
</html>
"""


class WyjscieStrumienia(io.BufferedIOBase):
    """Bufor jednej, najnowszej klatki JPEG współdzielony między wątkami.

    Enkoder zapisuje tu gotowe klatki, a wątki obsługujące przeglądarki
    czekają na sygnał (Condition) i wysyłają najświeższą klatkę.
    """

    def __init__(self):
        self.klatka = None
        self.warunek = Condition()

    def write(self, buf):
        with self.warunek:
            self.klatka = buf
            self.warunek.notify_all()   # obudź wszystkich oglądających


class ObslugaZadan(server.BaseHTTPRequestHandler):
    """Obsługa zapytań HTTP: strona główna oraz strumień MJPEG."""

    def do_GET(self):
        if self.path == '/':
            # Przekierowanie na stronę z podglądem
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
            # Nagłówki strumienia multipart/x-mixed-replace — klasyczny MJPEG
            self.send_response(200)
            self.send_header('Age', 0)
            self.send_header('Cache-Control', 'no-cache, private')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Content-Type',
                             'multipart/x-mixed-replace; boundary=KLATKA')
            self.end_headers()
            try:
                while True:
                    # Czekamy aż enkoder przygotuje nową klatkę
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
                logging.debug('Klient %s zamknął połączenie.', self.client_address)
            except Exception as e:
                # Najczęściej: przeglądarka zamknęła połączenie — to normalne
                logging.debug('Klient %s rozłączony: %s', self.client_address, str(e))

        else:
            self.send_error(404)
            self.end_headers()


class SerwerStrumienia(socketserver.ThreadingMixIn, server.HTTPServer):
    """Wielowątkowy serwer HTTP — obsługuje wielu widzów jednocześnie."""
    allow_reuse_address = True
    daemon_threads = True


def main():
    global wyjscie

    logging.basicConfig(level=logging.INFO)

    # 1. Konfiguracja kamery do trybu wideo
    picam2 = Picamera2()
    konfiguracja = picam2.create_video_configuration(
        main={"size": ROZDZIELCZOSC},
        controls={"FrameRate": FPS},
    )
    picam2.configure(konfiguracja)

    # 2. Start nagrywania do bufora przez enkoder MJPEG
    wyjscie = WyjscieStrumienia()
    enkoder = MJPEGEncoder()
    enkoder.quality = JAKOSC_JPEG
    picam2.start_recording(enkoder, FileOutput(wyjscie))

    # 3. Uruchomienie serwera WWW
    try:
        adres = ('', PORT)
        serwer = SerwerStrumienia(adres, ObslugaZadan)
        print("=" * 60)
        print(" Kamera RGB gotowa!")
        print(f" Otwórz w przeglądarce:  http://<IP_MALINKI>:{PORT}/")
        print(" Zatrzymanie: Ctrl + C")
        print("=" * 60)
        serwer.serve_forever()
    except KeyboardInterrupt:
        print("\nZatrzymywanie...")
    finally:
        picam2.stop_recording()


if __name__ == '__main__':
    main()
