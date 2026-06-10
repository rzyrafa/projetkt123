#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TEST 3 — EKRAN TFT LCD (SPI, ILI9341 240x320) — TEST PŁYNNOŚCI
==============================================================

Cel:
    Uruchomić wyświetlacz przez SPI i rysować zaawansowaną, animowaną falę
    (suma kilku sinusoid o zmiennej amplitudzie i fazie) z tęczowym kolorem,
    aby sprawdzić, czy odświeżanie SPI jest szybkie i płynne. Na ekranie
    pokazujemy też licznik FPS.

Dobór bibliotek:
    * `adafruit-circuitpython-rgb-display` — szybki sterownik m.in. ILI9341
      z obsługą SPRZĘTOWEGO SPI (wysokie taktowanie = duża płynność).
    * `Pillow (PIL)` — rysujemy całą klatkę w pamięci (Image/ImageDraw),
      a potem jednym wywołaniem `disp.image()` wysyłamy ją do ekranu.
    * `adafruit-blinka` — udostępnia `board`, `digitalio`, `busio` na RPi.

    Rysowanie całej klatki w buforze PIL i wysyłka "hurtem" to najszybsza
    i najpłynniejsza metoda dla tych wyświetlaczy (brak migotania).

Pinout (zgodnie z Twoim podłączeniem):
    CS    = Pin 24 (GPIO 8  / SPI CE0)
    RESET = Pin 22 (GPIO 25)
    DC    = Pin 18 (GPIO 24)
    MOSI  = Pin 19 (GPIO 10)  -> sprzętowe SPI0
    SCK   = Pin 23 (GPIO 11)  -> sprzętowe SPI0
    MISO  = Pin 21 (GPIO 9)   -> sprzętowe SPI0 (dla ekranu zwykle nieużywane)

Uruchomienie:
    python3 test3_tft_lcd.py
    (zatrzymanie: Ctrl + C)
"""

import math
import time
import colorsys

import board
import digitalio
from PIL import Image, ImageDraw
from adafruit_rgb_display import ili9341

# --- KONFIGURACJA -----------------------------------------------------------
# Taktowanie SPI. To NAJCZĘSTSZA przyczyna szumu/„śniegu” (zwłaszcza na dole
# ekranu) przy połączeniu przewodami dupont. Jeśli widzisz szum:
#   - zmniejsz tę wartość: 24 MHz -> 16 MHz -> 12 MHz,
#   - skróć przewody SPI (MOSI/SCK), najlepiej kilka cm.
# Jeśli obraz jest idealny, możesz spróbować podnieść z powrotem do 32 MHz.
BAUDRATE = 24000000
ROTACJA = 90          # 0/180 = pionowo (240x320), 90/270 = poziomo (320x240).
                      # Jeśli obraz jest „przesunięty”, przetestuj 0/90/180/270.


def main():
    # 1. Konfiguracja pinów sterujących (Blinka)
    cs_pin = digitalio.DigitalInOut(board.CE0)   # CS  = GPIO8  / SPI CE0
    dc_pin = digitalio.DigitalInOut(board.D24)   # DC  = GPIO24
    reset_pin = digitalio.DigitalInOut(board.D25)  # RST = GPIO25

    # 2. Sprzętowe SPI0 (MOSI=GPIO10, MISO=GPIO9, SCK=GPIO11)
    spi = board.SPI()

    # 3. Inicjalizacja wyświetlacza ILI9341 (natywnie 240x320)
    disp = ili9341.ILI9341(
        spi,
        rotation=ROTACJA,
        cs=cs_pin,
        dc=dc_pin,
        rst=reset_pin,
        baudrate=BAUDRATE,
    )

    # Przy rotacji 90/270 zamieniamy szerokość z wysokością
    if disp.rotation % 180 == 90:
        szer = disp.height
        wys = disp.width
    else:
        szer = disp.width
        wys = disp.height

    print("=" * 60)
    print(" Ekran ILI9341 zainicjalizowany.")
    print(f" Rozmiar roboczy: {szer} x {wys}, SPI: {BAUDRATE/1_000_000:.0f} MHz")
    print(" Zatrzymanie: Ctrl + C")
    print("=" * 60)

    # Jednorazowe wyczyszczenie CAŁEJ pamięci ekranu na czarno. Dzięki temu,
    # gdyby kontroler miał drobny offset/nadwyżkę pikseli, niezapisany obszar
    # będzie czarny zamiast pokazywać losowy „śnieg”.
    disp.fill(0)

    # 4. Bufor klatki w pamięci (rysujemy tu, potem wysyłamy całość)
    obraz = Image.new("RGB", (szer, wys))
    rys = ImageDraw.Draw(obraz)

    srodek_y = wys / 2
    amplituda = wys / 2 - 6     # margines od krawędzi
    krok = 2                    # co ile pikseli liczymy punkt (mniej = gładziej, wolniej)

    t = 0.0
    licznik = 0
    fps = 0.0
    czas_start = time.monotonic()

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
        # Wyczyść ekran na koniec (na czarno)
        rys.rectangle((0, 0, szer, wys), fill=(0, 0, 0))
        disp.image(obraz)


if __name__ == '__main__':
    main()
