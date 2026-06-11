#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ŹRÓDŁO DANYCH TERMOWIZYJNYCH (MLX90640) — wspólny moduł
=======================================================

Udostępnia dwie wymienne klasy czujnika o IDENTYCZNYM interfejsie:
    * CzujnikESP32 — odczyt z ESP32 podłączonego po USB (port szeregowy),
                     ESP32 czyta MLX90640 i wysyła ramki (firmware: esp32_mlx90640/).
    * CzujnikI2C   — odczyt bezpośrednio z MLX90640 po I2C na Raspberry Pi.

Obie klasy:
    .start()    -> uruchamia odczyt w wątku w tle,
    .pobierz()  -> zwraca najnowszą macierz temperatur 24x32 (numpy float32)
                   albo None, jeśli nie ma jeszcze danych.

Dane są wygładzane w czasie (EMA) i mogą być odbite (orientacja czujnika).
"""

import time
import logging
from threading import Thread, Lock

import numpy as np

SZER_CZUJNIKA = 32
WYS_CZUJNIKA = 24
LICZBA_PIKSELI = SZER_CZUJNIKA * WYS_CZUJNIKA   # 768
ROZMIAR_PAYLOAD = LICZBA_PIKSELI * 4            # 3072 bajtów (float32)


class _BazaCzujnika:
    """Wspólna logika: bufor, blokada, wygładzanie EMA, orientacja."""

    def __init__(self, lustro_x=True, lustro_y=False, wygladzanie=0.5):
        self.lustro_x = lustro_x
        self.lustro_y = lustro_y
        self.wygladzanie = wygladzanie
        self._lock = Lock()
        self._dane = None
        self._srednia = None

    def _aktualizuj(self, d):
        """Przyjmuje surową macierz 24x32 (°C), nakłada orientację + EMA, zapisuje."""
        if self.lustro_x:
            d = np.fliplr(d)
        if self.lustro_y:
            d = np.flipud(d)
        if self._srednia is None:
            self._srednia = d.astype(np.float32)
        else:
            self._srednia = (self.wygladzanie * self._srednia
                             + (1.0 - self.wygladzanie) * d)
        with self._lock:
            self._dane = self._srednia.copy()

    def pobierz(self):
        with self._lock:
            return None if self._dane is None else self._dane.copy()

    def start(self):
        Thread(target=self._petla, daemon=True).start()

    def _petla(self):
        raise NotImplementedError


class CzujnikESP32(_BazaCzujnika):
    """Odczyt termowizji z ESP32 podłączonego przez USB (port szeregowy).

    Protokół ramki (zgodny z firmware esp32_mlx90640.ino):
        [0xAA][0x55] + 768 x float32 (LE) + suma kontrolna uint16 (LE)
    """

    HDR0 = 0xAA
    HDR1 = 0x55

    def __init__(self, port="/dev/ttyUSB0", baud=921600, **kwargs):
        super().__init__(**kwargs)
        self.port = port
        self.baud = baud

    def _zsynchronizuj(self, ser):
        """Szuka nagłówka 0xAA 0x55 w strumieniu. Zwraca True, gdy znaleziono."""
        b = ser.read(1)
        if not b or b[0] != self.HDR0:
            return False
        b2 = ser.read(1)
        if not b2 or b2[0] != self.HDR1:
            return False
        return True

    def _petla(self):
        import serial  # pyserial (instalowane przez pip: pyserial)

        while True:
            try:
                ser = serial.Serial(self.port, self.baud, timeout=2)
            except Exception as e:  # port jeszcze niedostępny / brak uprawnień
                logging.warning("Nie mogę otworzyć portu %s (%s). Ponawiam...",
                                self.port, e)
                time.sleep(2)
                continue

            logging.info("Połączono z ESP32 na %s @ %d", self.port, self.baud)
            try:
                while True:
                    if not self._zsynchronizuj(ser):
                        continue
                    payload = ser.read(ROZMIAR_PAYLOAD)
                    if len(payload) != ROZMIAR_PAYLOAD:
                        continue
                    chk = ser.read(2)
                    if len(chk) != 2:
                        continue
                    suma_obl = sum(payload) & 0xFFFF
                    suma_odb = chk[0] | (chk[1] << 8)
                    if suma_obl != suma_odb:
                        continue   # uszkodzona ramka — pomiń i resynchronizuj
                    d = np.frombuffer(payload, dtype="<f4").reshape(
                        (WYS_CZUJNIKA, SZER_CZUJNIKA)).astype(np.float32)
                    self._aktualizuj(d)
            except Exception as e:
                logging.warning("Błąd portu szeregowego: %s. Ponawiam połączenie...", e)
                try:
                    ser.close()
                except Exception:
                    pass
                time.sleep(1)


class CzujnikI2C(_BazaCzujnika):
    """Odczyt bezpośrednio z MLX90640 po I2C na Raspberry Pi (wariant zapasowy)."""

    def __init__(self, czestotliwosc=800000, odswiezanie=None, **kwargs):
        super().__init__(**kwargs)
        import board
        import busio
        import adafruit_mlx90640

        i2c = busio.I2C(board.SCL, board.SDA, frequency=czestotliwosc)
        self.mlx = adafruit_mlx90640.MLX90640(i2c)
        if odswiezanie is None:
            odswiezanie = adafruit_mlx90640.RefreshRate.REFRESH_8_HZ
        self.mlx.refresh_rate = odswiezanie
        self._surowa = [0.0] * LICZBA_PIKSELI

    def _petla(self):
        bledy = 0
        while True:
            try:
                self.mlx.getFrame(self._surowa)
                bledy = 0
            except ValueError:
                continue
            except (OSError, RuntimeError) as e:
                bledy += 1
                if bledy <= 3 or bledy % 30 == 0:
                    logging.warning("Błąd I2C MLX90640 (%d): %s", bledy, e)
                time.sleep(0.1)
                continue
            d = np.array(self._surowa, dtype=np.float32).reshape(
                (WYS_CZUJNIKA, SZER_CZUJNIKA))
            self._aktualizuj(d)
