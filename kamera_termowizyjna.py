#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PROGRAM GŁÓWNY — FUZJA KAMERY RGB + TERMOWIZJI NA EKRANIE ST7789
===============================================================

Spina w całość wszystkie 3 przetestowane komponenty:
    * Kamera RGB (Raspberry Pi Camera Module 2, picamera2),
    * Termowizja MLX90640 (I2C),
    * Wyświetlacz TFT ST7789 240x320 (SPI),
oraz dokłada (opcjonalnie) podgląd w przeglądarce (strumień MJPEG).

Co robi:
    - czyta obraz z kamery RGB,
    - czyta temperatury z MLX90640 i robi z nich kolorową mapę cieplną,
    - NAKŁADA termowizję na obraz RGB (fuzja) i pokazuje wynik na ekranie ST7789.

TRYBY OBRAZU (przełączasz klawiszami w terminalu SSH albo argumentem startowym):
    1 / "rgb"     -> tylko obraz z kamery RGB
    2 / "termo"   -> tylko mapa cieplna
    3 / "fuzja"   -> półprzezroczyste nałożenie termowizji na RGB  (domyślny)
    4 / "gorace"  -> RGB, a termowizja widoczna TYLKO w gorących miejscach

Sterowanie klawiszami (gdy uruchomione w terminalu):
    1/2/3/4 = zmiana trybu,  + / - = przezroczystość,  q = wyjście.

Uruchomienie:
    python3 kamera_termowizyjna.py            # tryb fuzja
    python3 kamera_termowizyjna.py gorace      # start od trybu "gorące punkty"
    (zatrzymanie: q albo Ctrl + C)

Podgląd w przeglądarce (jeśli WEB_PODGLAD = True):
    http://<IP_MALINKI>:8080/
"""

import io
import sys
import time
import select
import socketserver
import logging
from http import server
from threading import Condition, Thread

import numpy as np
import cv2

import board
import digitalio
from adafruit_rgb_display import st7789, ili9341
from PIL import Image

from picamera2 import Picamera2

from termo_zrodlo import CzujnikESP32, CzujnikI2C

# ============================================================================
#  KONFIGURACJA
# ============================================================================

# --- Rozmiar renderowania = rozmiar ekranu w poziomie (rotacja 90) ----------
SZER = 320
WYS = 240

# --- WYŚWIETLACZ (ustawienia z Testu 3) -------------------------------------
STEROWNIK = "st7789"     # "st7789" (Twój panel) lub "ili9341"
SZER_PANELU = 240        # natywny (pionowy) rozmiar panelu
WYS_PANELU = 320
ROTACJA = 90             # 90 = poziomo (320x240)
X_OFFSET = 0
Y_OFFSET = 0
BAUDRATE = 24000000
# Inwersja: na Twoim panelu prawdziwe kolory wymagają inwersji WYŁĄCZONEJ
# (inaczej obraz wygląda jak "negatyw"). Jeśli RGB jest negatywem -> zmień na True.
INWERSJA = False         # False = INVOFF (0x20), True = INVON (0x21), None = nie ruszaj
ZAMIEN_RB = False        # True, jeśli czerwony i niebieski są zamienione (BGR)

# --- TERMOWIZJA -------------------------------------------------------------
# Źródło danych termowizji:
#   "esp32" -> MLX90640 podłączony do ESP32, a ESP32 przez USB do Raspberry Pi
#              (firmware: katalog esp32_mlx90640/). ZALECANE w Twoim zestawie.
#   "i2c"   -> MLX90640 podłączony bezpośrednio do pinów I2C malinki (wariant zapasowy)
ZRODLO_TERMO = "esp32"
PORT_ESP32 = "/dev/ttyUSB0"   # port USB ESP32 (sprawdź: ls /dev/ttyUSB* /dev/ttyACM*)
BAUD_ESP32 = 230400           # musi zgadzać się z firmware ESP32 (pewne na RPi)
CZESTOTLIWOSC_I2C = 800000    # używane tylko gdy ZRODLO_TERMO = "i2c"

PALETA = cv2.COLORMAP_INFERNO
WYGLADZANIE = 0.5        # wygładzanie termowizji w czasie (0..0.9)
# Dopasowanie orientacji termowizji do kamery (zależy od montażu czujnika):
TERMO_LUSTRO_X = True    # odbicie w poziomie
TERMO_LUSTRO_Y = False   # odbicie w pionie

# --- FUZJA ------------------------------------------------------------------
ALFA = 0.5               # siła nałożenia termowizji w trybie "fuzja" (0..1)
PROG_GORACE = 30.0       # próg [°C] dla trybu "gorace" (powyżej = pokaż ciepło)

# --- PODGLĄD W PRZEGLĄDARCE -------------------------------------------------
WEB_PODGLAD = True       # True = dodatkowo strumień MJPEG w przeglądarce
WEB_PORT = 8080
JAKOSC_JPEG = 80

# Wymiary natywne matrycy MLX90640
SZER_CZUJNIKA = 32
WYS_CZUJNIKA = 24

# Dostępne tryby
TRYBY = ["rgb", "termo", "fuzja", "gorace"]


# ============================================================================
#  PODGLĄD WEB (MJPEG) — opcjonalny
# ============================================================================
class WyjscieStrumienia(io.BufferedIOBase):
    def __init__(self):
        self.klatka = None
        self.warunek = Condition()

    def aktualizuj(self, buf):
        with self.warunek:
            self.klatka = buf
            self.warunek.notify_all()


STRONA = """\
<!DOCTYPE html><html lang="pl"><head><meta charset="utf-8">
<title>Kamera termowizyjna - podglad</title>
<style>body{background:#111;color:#eee;font-family:sans-serif;text-align:center;margin:0;padding:20px}
img{width:640px;max-width:100%;height:auto;border:2px solid #444;border-radius:8px;image-rendering:pixelated}</style>
</head><body><h1>Fuzja RGB + Termowizja</h1>
<img src="stream.mjpg"><p>Podglad na zywo (to samo, co widzisz na ekranie TFT)</p>
</body></html>
"""


def uruchom_serwer_web(wyjscie):
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

    serwer = Serwer(('', WEB_PORT), Obsluga)
    Thread(target=serwer.serve_forever, daemon=True).start()
    print(f" Podgląd web: http://<IP_MALINKI>:{WEB_PORT}/")


# ============================================================================
#  STEROWANIE KLAWIATURĄ (nieblokujące, działa przez SSH w terminalu)
# ============================================================================
class Klawiatura:
    def __init__(self):
        self.aktywna = sys.stdin.isatty()
        self._stare = None

    def __enter__(self):
        if self.aktywna:
            import termios
            import tty
            self._termios = termios
            self._stare = termios.tcgetattr(sys.stdin)
            tty.setcbreak(sys.stdin.fileno())
        return self

    def czytaj(self):
        """Zwraca pojedynczy wciśnięty klawisz lub None (bez blokowania)."""
        if not self.aktywna:
            return None
        if select.select([sys.stdin], [], [], 0)[0]:
            return sys.stdin.read(1)
        return None

    def __exit__(self, *a):
        if self.aktywna and self._stare is not None:
            self._termios.tcsetattr(sys.stdin, self._termios.TCSADRAIN, self._stare)


# ============================================================================
#  INICJALIZACJA SPRZĘTU
# ============================================================================
def zbuduj_wyswietlacz():
    cs_pin = digitalio.DigitalInOut(board.CE0)
    dc_pin = digitalio.DigitalInOut(board.D24)
    reset_pin = digitalio.DigitalInOut(board.D25)
    spi = board.SPI()

    wspolne = dict(rotation=ROTACJA, cs=cs_pin, dc=dc_pin, rst=reset_pin,
                   baudrate=BAUDRATE, width=SZER_PANELU, height=WYS_PANELU)
    if STEROWNIK == "st7789":
        disp = st7789.ST7789(spi, x_offset=X_OFFSET, y_offset=Y_OFFSET, **wspolne)
    else:
        disp = ili9341.ILI9341(spi, **wspolne)

    if INWERSJA is True:
        disp.write(0x21)
    elif INWERSJA is False:
        disp.write(0x20)
    disp.fill(0)
    return disp


def przeslij_na_ekran(disp, obraz_rgb_np):
    """obraz_rgb_np: numpy HxWx3 w kolejności RGB -> wysyła na ekran ST7789."""
    if ZAMIEN_RB:
        obraz_rgb_np = obraz_rgb_np[:, :, ::-1]
    disp.image(Image.fromarray(np.ascontiguousarray(obraz_rgb_np)))


# ============================================================================
#  PRZETWARZANIE OBRAZU
# ============================================================================
def termowizja_na_kolor(dane):
    """Macierz temperatur -> kolorowy obraz BGR w rozmiarze ekranu (+ min/max)."""
    t_min = float(dane.min())
    t_max = float(dane.max())
    zakres = max(t_max - t_min, 1e-3)
    znorm = np.clip((dane - t_min) / zakres * 255.0, 0, 255).astype(np.uint8)
    duzy = cv2.resize(znorm, (SZER, WYS), interpolation=cv2.INTER_CUBIC)
    kolor = cv2.applyColorMap(duzy, PALETA)        # BGR
    return kolor, t_min, t_max


def zloz_obraz(rgb_bgr, termo_bgr, dane, tryb, alfa):
    """Łączy obraz RGB (jako BGR) z termowizją wg wybranego trybu. Zwraca BGR."""
    if tryb == "rgb" or termo_bgr is None:
        return rgb_bgr
    if tryb == "termo":
        return termo_bgr
    if tryb == "fuzja":
        return cv2.addWeighted(rgb_bgr, 1.0 - alfa, termo_bgr, alfa, 0)
    if tryb == "gorace":
        # Termowizję pokazujemy tylko tam, gdzie jest goręcej niż próg
        temp_duzy = cv2.resize(dane, (SZER, WYS), interpolation=cv2.INTER_CUBIC)
        maska = temp_duzy >= PROG_GORACE
        wynik = rgb_bgr.copy()
        nakladka = cv2.addWeighted(rgb_bgr, 1.0 - alfa, termo_bgr, alfa, 0)
        wynik[maska] = nakladka[maska]
        return wynik
    return rgb_bgr


def opisz(obraz_bgr, tryb, alfa, fps, t_min, t_max):
    """Nakłada tekst informacyjny (z czarnym konturem dla czytelności)."""
    def napis(txt, poz):
        cv2.putText(obraz_bgr, txt, poz, cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (0, 0, 0), 3, cv2.LINE_AA)
        cv2.putText(obraz_bgr, txt, poz, cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                    (255, 255, 255), 1, cv2.LINE_AA)

    napis(f"TRYB: {tryb}", (6, 18))
    napis(f"FPS: {fps:4.1f}", (6, WYS - 10))
    if t_min is not None:
        napis(f"{t_min:4.1f}-{t_max:4.1f}C", (SZER - 120, 18))
    if tryb in ("fuzja", "gorace"):
        napis(f"alfa: {alfa:.2f}", (SZER - 110, WYS - 10))


# ============================================================================
#  GŁÓWNA PĘTLA
# ============================================================================
def main():
    logging.basicConfig(level=logging.INFO)

    # Tryb startowy z argumentu (np. "gorace")
    tryb = "fuzja"
    if len(sys.argv) > 1 and sys.argv[1].lower() in TRYBY:
        tryb = sys.argv[1].lower()
    alfa = ALFA

    # 1. Wyświetlacz
    print("Inicjalizacja wyświetlacza...")
    disp = zbuduj_wyswietlacz()

    # 2. Kamera RGB (picamera2) — niska rozdzielczość = większa płynność
    print("Inicjalizacja kamery RGB...")
    picam2 = Picamera2()
    konf = picam2.create_preview_configuration(
        main={"size": (SZER, WYS), "format": "RGB888"})
    picam2.configure(konf)
    picam2.start()
    time.sleep(1.0)   # czas na ustawienie ekspozycji

    # 3. Termowizja — źródło ESP32 (USB) albo bezpośrednie I2C
    if ZRODLO_TERMO == "esp32":
        print(f"Inicjalizacja termowizji z ESP32 ({PORT_ESP32})...")
        czujnik = CzujnikESP32(
            port=PORT_ESP32, baud=BAUD_ESP32,
            lustro_x=TERMO_LUSTRO_X, lustro_y=TERMO_LUSTRO_Y,
            wygladzanie=WYGLADZANIE)
    else:
        print("Inicjalizacja termowizji bezpośrednio z MLX90640 (I2C)...")
        czujnik = CzujnikI2C(
            czestotliwosc=CZESTOTLIWOSC_I2C,
            lustro_x=TERMO_LUSTRO_X, lustro_y=TERMO_LUSTRO_Y,
            wygladzanie=WYGLADZANIE)
    czujnik.start()

    # 4. Opcjonalny podgląd web
    wyjscie_web = None
    if WEB_PODGLAD:
        wyjscie_web = WyjscieStrumienia()
        uruchom_serwer_web(wyjscie_web)

    print("=" * 60)
    print(" Gotowe! Sterowanie: 1=RGB 2=Termo 3=Fuzja 4=Gorace")
    print("         + / - = przezroczystosc,  q = wyjscie")
    print("=" * 60)

    licznik = 0
    fps = 0.0
    czas_start = time.monotonic()

    with Klawiatura() as klawiatura:
        try:
            while True:
                # --- obraz z kamery (picamera2 'RGB888' = w numpy kolejność BGR) ---
                rgb_bgr = picam2.capture_array()
                if rgb_bgr.shape[2] == 4:          # czasem RGBA
                    rgb_bgr = rgb_bgr[:, :, :3]
                if rgb_bgr.shape[1] != SZER or rgb_bgr.shape[0] != WYS:
                    rgb_bgr = cv2.resize(rgb_bgr, (SZER, WYS))

                # --- termowizja ---
                dane = czujnik.pobierz()
                if dane is not None:
                    termo_bgr, t_min, t_max = termowizja_na_kolor(dane)
                else:
                    termo_bgr, t_min, t_max = None, None, None

                # --- fuzja wg trybu ---
                wynik_bgr = zloz_obraz(rgb_bgr, termo_bgr, dane, tryb, alfa)
                opisz(wynik_bgr, tryb, alfa, fps, t_min, t_max)

                # --- na ekran (ST7789 chce RGB) ---
                wynik_rgb = cv2.cvtColor(wynik_bgr, cv2.COLOR_BGR2RGB)
                przeslij_na_ekran(disp, wynik_rgb)

                # --- podgląd web (JPEG) ---
                if wyjscie_web is not None:
                    ok, buf = cv2.imencode('.jpg', wynik_bgr,
                                           [int(cv2.IMWRITE_JPEG_QUALITY), JAKOSC_JPEG])
                    if ok:
                        wyjscie_web.aktualizuj(buf.tobytes())

                # --- klawiatura ---
                k = klawiatura.czytaj()
                if k:
                    if k in ('1', '2', '3', '4'):
                        tryb = TRYBY[int(k) - 1]
                    elif k in ('+', '='):
                        alfa = min(1.0, alfa + 0.1)
                    elif k == '-':
                        alfa = max(0.0, alfa - 0.1)
                    elif k in ('q', 'Q'):
                        break

                # --- FPS ---
                licznik += 1
                if licznik >= 10:
                    teraz = time.monotonic()
                    fps = licznik / (teraz - czas_start)
                    czas_start = teraz
                    licznik = 0

        except KeyboardInterrupt:
            pass
        finally:
            print("\nZatrzymywanie...")
            try:
                picam2.stop()
            except Exception:
                pass
            disp.fill(0)


if __name__ == '__main__':
    main()
