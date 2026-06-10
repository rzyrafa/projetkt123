#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST 3 — EKRAN TFT LCD (SPI, 240x320) — TEST PŁYNNOŚCI + KALIBRACJA
==================================================================

Cel:
    Uruchomić wyświetlacz przez SPI i rysować zaawansowaną, animowaną falę
    (suma kilku sinusoid o zmiennej amplitudzie i fazie) z tęczowym kolorem,
    aby sprawdzić, czy odświeżanie SPI jest szybkie i płynne (z licznikiem FPS).

    Skrypt ma też TRYB KALIBRACJI (patrz niżej), który pomaga ustalić, dlaczego
    część ekranu może nie być rysowana (np. „pasek śniegu” na dole).

Dobór bibliotek:
    * `adafruit-circuitpython-rgb-display` — szybki sterownik (ILI9341 / ST7789)
      ze sprzętowym SPI.
    * `Pillow (PIL)` — rysujemy całą klatkę w pamięci i wysyłamy „hurtem”.
    * `adafruit-blinka` — udostępnia `board`, `digitalio`, `busio` na RPi.

Pinout (zgodnie z Twoim podłączeniem):
    CS    = Pin 24 (GPIO 8  / SPI CE0)
    RESET = Pin 22 (GPIO 25)
    DC    = Pin 18 (GPIO 24)
    MOSI  = Pin 19 (GPIO 10)  -> sprzętowe SPI0
    SCK   = Pin 23 (GPIO 11)  -> sprzętowe SPI0
    MISO  = Pin 21 (GPIO 9)   -> sprzętowe SPI0 (dla ekranu zwykle nieużywane)

Uruchomienie:
    python3 test3_tft_lcd.py              # animacja (test płynności)
    python3 test3_tft_lcd.py kalibracja   # statyczna plansza diagnostyczna
    (zatrzymanie: Ctrl + C)

DIAGNOZA „PASKA ŚNIEGU” NA DOLE / OBRAZU POZA EKRANEM:
    Taki pasek to obszar pamięci ekranu, do którego nigdy nie piszemy — znaczy,
    że FIZYCZNY panel jest większy niż adresowany obszar sterownika, albo trzeba
    podać OFFSET. Uruchom `kalibracja` i sprawdź na planszy:
      * Czy biała RAMKA dotyka wszystkich 4 krawędzi szkła?
      * Czy kolory pasów to kolejno CZERWONY / ZIELONY / NIEBIESKI?
      * Gdzie dokładnie zaczyna się „śnieg”?
    Następnie dostrój poniżej: STEROWNIK, SZER_PANELU/WYS_PANELU oraz X/Y_OFFSET.
"""

import sys
import math
import time
import colorsys

import board
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341, st7789

# --- KONFIGURACJA SPRZĘTU ---------------------------------------------------
# Wybór sterownika. Wiele tanich modułów „240x320” to NIE ILI9341, lecz ST7789
# (wtedy często pojawia się pasek/offset, jeśli użyjemy złego sterownika).
#   "ili9341" — klasyczny ILI9341 (240x320)
#   "st7789"  — ST7789 (240x320) — spróbuj, jeśli ILI9341 zostawia pasek
STEROWNIK = "ili9341"

# Rozmiar PANELU (w orientacji pionowej, natywnej). Dla większości modułów
# 2.0–2.8" to 240 x 320. Jeśli kalibracja pokaże, że panel jest większy/mniejszy,
# zmień te wartości.
SZER_PANELU = 240
WYS_PANELU = 320

# Offsety pamięci (w pikselach). Dla czystego ILI9341 zwykle 0/0. Dla ST7789 i
# klonów bywa potrzebne przesunięcie (np. 0/0, 0/80, 0/20). Jeśli obraz jest
# „przesunięty”, a na przeciwległej krawędzi widać śnieg — dobierz tutaj.
X_OFFSET = 0
Y_OFFSET = 0

# Taktowanie SPI. Jeśli widzisz losowy szum w CAŁYM obrazie — zmniejszaj:
# 24 -> 16 -> 12 MHz i skróć przewody MOSI/SCK. (Uwaga: szum tylko na dole,
# niezależny od baudrate i rotacji, to NIE jest problem SPI — to rozmiar/offset.)
BAUDRATE = 24000000

ROTACJA = 90          # 0/180 = pionowo, 90/270 = poziomo.


def zbuduj_wyswietlacz():
    """Tworzy obiekt wyświetlacza wg ustawień i zwraca (disp, szer, wys)."""
    cs_pin = digitalio.DigitalInOut(board.CE0)     # CS  = GPIO8  / SPI CE0
    dc_pin = digitalio.DigitalInOut(board.D24)     # DC  = GPIO24
    reset_pin = digitalio.DigitalInOut(board.D25)  # RST = GPIO25
    spi = board.SPI()                              # MOSI=GPIO10, SCK=GPIO11

    wspolne = dict(
        rotation=ROTACJA,
        cs=cs_pin,
        dc=dc_pin,
        rst=reset_pin,
        baudrate=BAUDRATE,
        width=SZER_PANELU,
        height=WYS_PANELU,
    )

    if STEROWNIK == "st7789":
        # ST7789 obsługuje offsety pamięci (przydatne dla klonów)
        disp = st7789.ST7789(spi, x_offset=X_OFFSET, y_offset=Y_OFFSET, **wspolne)
    else:
        # ILI9341 NIE przyjmuje x_offset/y_offset — używa stałego mapowania
        disp = ili9341.ILI9341(spi, **wspolne)

    # Wymiary „robocze” obrazu (po uwzględnieniu rotacji)
    if disp.rotation % 180 == 90:
        szer, wys = disp.height, disp.width
    else:
        szer, wys = disp.width, disp.height

    print("=" * 60)
    print(f" Sterownik: {STEROWNIK}, panel {SZER_PANELU}x{WYS_PANELU}, "
          f"offset {X_OFFSET}/{Y_OFFSET}")
    print(f" Rozmiar roboczy: {szer} x {wys}, rotacja {ROTACJA}, "
          f"SPI: {BAUDRATE/1_000_000:.0f} MHz")
    print("=" * 60)
    return disp, szer, wys


def tryb_kalibracji(disp, szer, wys):
    """Rysuje statyczną planszę diagnostyczną i czeka (Ctrl+C, by wyjść).

    Plansza pokazuje DOKŁADNIE adresowany obszar:
      * gruba biała ramka po obrysie,
      * pasy R/G/B (sprawdzenie kolejności kolorów),
      * krzyżyki w rogach i napisy GORA/DOL/LEWO/PRAWO.
    """
    obraz = Image.new("RGB", (szer, wys))
    rys = ImageDraw.Draw(obraz)

    # Tło: 3 pionowe pasy R/G/B (do weryfikacji kolejności kolorów)
    rys.rectangle((0, 0, szer // 3, wys), fill=(255, 0, 0))
    rys.rectangle((szer // 3, 0, 2 * szer // 3, wys), fill=(0, 255, 0))
    rys.rectangle((2 * szer // 3, 0, szer, wys), fill=(0, 0, 255))

    # Gruba biała ramka po samym obrysie adresowanego obszaru
    for i in range(3):
        rys.rectangle((i, i, szer - 1 - i, wys - 1 - i), outline=(255, 255, 255))

    # Krzyżyki w rogach
    d = 12
    for (cx, cy) in [(0, 0), (szer - 1, 0), (0, wys - 1), (szer - 1, wys - 1)]:
        rys.line((cx - d, cy, cx + d, cy), fill=(255, 255, 0), width=2)
        rys.line((cx, cy - d, cx, cy + d), fill=(255, 255, 0), width=2)

    # Napisy orientacyjne
    rys.text((szer // 2 - 12, 4), "GORA", fill=(0, 0, 0))
    rys.text((szer // 2 - 8, wys - 14), "DOL", fill=(0, 0, 0))
    rys.text((4, wys // 2), "LEWO", fill=(0, 0, 0))
    rys.text((szer - 36, wys // 2), "PRAWO", fill=(0, 0, 0))

    disp.image(obraz)
    print("Plansza kalibracyjna wyświetlona. Zrób zdjęcie i przeanalizuj:")
    print("  - czy biała ramka dotyka wszystkich 4 krawędzi szkła?")
    print("  - czy pasy to kolejno: CZERWONY / ZIELONY / NIEBIESKI?")
    print("  - gdzie zaczyna się 'śnieg' (jeśli jest)?")
    print("Ctrl+C, aby zakończyć.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nKoniec kalibracji.")


def tryb_animacji(disp, szer, wys):
    """Animowana fala (test płynności SPI) z licznikiem FPS."""
    obraz = Image.new("RGB", (szer, wys))
    rys = ImageDraw.Draw(obraz)

    srodek_y = wys / 2
    amplituda = wys / 2 - 6     # margines od krawędzi
    krok = 2                    # co ile pikseli liczymy punkt (mniej = gładziej)

    t = 0.0
    licznik = 0
    fps = 0.0
    czas_start = time.monotonic()

    print(" Test płynności uruchomiony. Zatrzymanie: Ctrl + C")
    try:
        while True:
            # --- czyszczenie tła ---
            rys.rectangle((0, 0, szer, wys), fill=(0, 0, 0))

            # --- oś pozioma (delikatna linia odniesienia) ---
            rys.line((0, srodek_y, szer, srodek_y), fill=(40, 40, 40))

            # --- ZAAWANSOWANA FALA: suma 3 sinusoid o zmiennej amplitudzie ---
            # Kształt "oddycha" dzięki modulacji amplitud funkcjami czasu.
            a1 = 0.6 + 0.4 * math.sin(t * 0.7)
            a2 = 0.3 + 0.2 * math.cos(t * 1.3)
            a3 = 0.2 + 0.2 * math.sin(t * 0.5)

            poprzedni = None
            for x in range(0, szer + krok, krok):
                faza = (x / szer) * 2.0 * math.pi
                y = (
                    a1 * math.sin(faza * 2.0 + t)
                    + a2 * math.sin(faza * 5.0 - t * 2.0)
                    + a3 * math.sin(faza * 9.0 + t * 0.5)
                )
                py = int(srodek_y - y * amplituda)

                if poprzedni is not None:
                    # Tęczowy kolor zależny od pozycji X i czasu (HSV -> RGB)
                    odcien = ((x / szer) + t * 0.05) % 1.0
                    r, g, b = colorsys.hsv_to_rgb(odcien, 1.0, 1.0)
                    kolor = (int(r * 255), int(g * 255), int(b * 255))
                    rys.line((poprzedni[0], poprzedni[1], x, py),
                             fill=kolor, width=2)
                poprzedni = (x, py)

            # --- licznik FPS na ekranie ---
            rys.text((4, 2), f"FPS: {fps:4.1f}", fill=(255, 255, 255))

            # --- WYSYŁKA CAŁEJ KLATKI DO EKRANU PRZEZ SPI ---
            disp.image(obraz)

            # --- animacja w czasie ---
            t += 0.15

            # --- pomiar FPS ---
            licznik += 1
            if licznik >= 15:
                teraz = time.monotonic()
                fps = licznik / (teraz - czas_start)
                czas_start = teraz
                licznik = 0
                print(f"FPS: {fps:4.1f}")

    except KeyboardInterrupt:
        print("\nZatrzymywanie...")
        rys.rectangle((0, 0, szer, wys), fill=(0, 0, 0))
        disp.image(obraz)


def main():
    disp, szer, wys = zbuduj_wyswietlacz()

    # Wyczyść cały adresowany obszar na czarno (na starcie)
    disp.fill(0)

    if len(sys.argv) > 1 and sys.argv[1].lower().startswith("kalib"):
        tryb_kalibracji(disp, szer, wys)
    else:
        tryb_animacji(disp, szer, wys)


if __name__ == '__main__':
    main()
