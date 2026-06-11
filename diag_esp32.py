#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DIAGNOSTYKA LINKU ESP32 (USB) — szybkie wykrycie, gdzie pęka komunikacja
========================================================================

Uruchom to, gdy `test_termo_esp32.py` nie pokazuje obrazu. Skrypt:
  1. otwiera port szeregowy,
  2. mierzy, ILE bajtów przychodzi z ESP32 (czy w ogóle coś wysyła),
  3. próbuje sparsować ramki i sprawdza sumy kontrolne,
  4. wypisuje czytelną diagnozę i co dalej zrobić.

Uruchomienie:
    python3 diag_esp32.py                      # /dev/ttyUSB0, 921600
    python3 diag_esp32.py /dev/ttyACM0          # inny port
    python3 diag_esp32.py /dev/ttyUSB0 115200   # inny baud (do testów)
"""

import sys
import time

import numpy as np

LICZBA_PIKSELI = 32 * 24
ROZMIAR_PAYLOAD = LICZBA_PIKSELI * 4   # 3072
HDR0, HDR1 = 0xAA, 0x55


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else "/dev/ttyUSB0"
    baud = int(sys.argv[2]) if len(sys.argv) > 2 else 921600

    try:
        import serial
    except ImportError:
        print("BŁĄD: brak modułu 'pyserial'. Zainstaluj:  pip install pyserial")
        sys.exit(1)

    print(f"Otwieram port {port} @ {baud} ...")
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except Exception as e:
        print(f"BŁĄD: nie mogę otworzyć portu {port}: {e}")
        print("  - sprawdź nazwę portu:  ls /dev/ttyUSB* /dev/ttyACM*")
        print("  - uprawnienia:  sudo usermod -aG dialout $USER  (potem wyloguj/restart)")
        print("  - czy port nie jest zajęty (np. otwarty Serial Monitor w Arduino)?")
        sys.exit(1)

    # ESP32 resetuje się przy otwarciu portu — daj mu chwilę na start
    print("Czekam 2 s (ESP32 restartuje się po otwarciu portu)...")
    time.sleep(2)

    # --- TEST 1: surowy throughput + próbka bajtów ---
    print("\n[TEST 1] Ile bajtów przychodzi w ciągu 3 s...")
    ser.reset_input_buffer()
    t0 = time.time()
    suma_bajtow = 0
    probka = bytearray()
    while time.time() - t0 < 3:
        n = ser.in_waiting
        if n:
            dane = ser.read(n)
            suma_bajtow += len(dane)
            if len(probka) < 96:
                probka.extend(dane[: 96 - len(probka)])
        else:
            time.sleep(0.01)
    print(f"  Odebrano {suma_bajtow} bajtów (~{suma_bajtow/3:.0f} B/s).")

    if probka:
        hex_str = " ".join(f"{b:02X}" for b in probka)
        ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in probka)
        print("  Próbka surowych bajtów (HEX):")
        print("   ", hex_str)
        print("  Ta sama próbka jako tekst (ASCII):")
        print("   ", ascii_str)
        if any(b in (HDR0,) for b in probka):
            print("  (W próbce jest bajt 0xAA — to dobrze, szukamy pary 0xAA 0x55.)")
        czytelne = sum(1 for b in probka if 32 <= b < 127)
        if czytelne > len(probka) * 0.7:
            print("  >>> UWAGA: dane wyglądają na TEKST (czytelne znaki). To znaczy, że")
            print("      na ESP32 działa INNY szkic (np. przykład wypisujący liczby),")
            print("      a NIE nasz binarny firmware esp32_mlx90640.ino. Wgraj nasz szkic.")

    if suma_bajtow == 0:
        print("\n  >>> DIAGNOZA: ZERO bajtów — ESP32 nic nie wysyła.")
        print("      Najczęstsze przyczyny:")
        print("       * firmware nie wgrany / nie ten szkic,")
        print("       * zły BAUD (firmware używa 921600),")
        print("       * MLX90640 NIE wykryty przez ESP32 (firmware tkwi w begin()),")
        print("         -> sprawdź zasilanie 3V3 i piny SDA=GPIO21, SCL=GPIO22 na ESP32,")
        print("       * zły port USB.")
        return

    # --- TEST 2: parsowanie ramek ---
    print("\n[TEST 2] Parsowanie ramek (do 20 lub 10 s)...")
    ok = 0
    bad = 0
    start = time.time()
    while ok + bad < 20 and time.time() - start < 10:
        b = ser.read(1)
        if not b or b[0] != HDR0:
            continue
        b2 = ser.read(1)
        if not b2 or b2[0] != HDR1:
            continue
        payload = ser.read(ROZMIAR_PAYLOAD)
        if len(payload) != ROZMIAR_PAYLOAD:
            continue
        chk = ser.read(2)
        if len(chk) != 2:
            continue
        if (sum(payload) & 0xFFFF) != (chk[0] | (chk[1] << 8)):
            bad += 1
            continue
        d = np.frombuffer(payload, dtype="<f4")
        ok += 1
        if ok <= 3 or ok % 5 == 0:
            print(f"  ramka OK #{ok}: min={d.min():5.1f}  max={d.max():5.1f}  "
                  f"środek={d[LICZBA_PIKSELI // 2]:5.1f} °C")

    print(f"\nWynik: poprawne ramki = {ok}, błędne (suma kontrolna) = {bad}")
    if ok == 0 and bad > 0:
        print("  >>> DIAGNOZA: dane przychodzą, ale sumy kontrolne się NIE zgadzają.")
        print("      Zwykle: zły/niestabilny BAUD albo kiepski kabel USB.")
        print("      Spróbuj innego kabla; ew. obniż baud w firmware i tutaj (np. 460800).")
    elif ok == 0:
        print("  >>> DIAGNOZA: nie znaleziono nagłówka 0xAA 0x55.")
        print("      Zły BAUD lub inny firmware niż esp32_mlx90640.ino.")
    else:
        print("  >>> LINK DZIAŁA POPRAWNIE. Jeśli strona nie pokazuje obrazu,")
        print("      problem jest po stronie podglądu — uruchom ponownie test_termo_esp32.py.")


if __name__ == "__main__":
    main()
