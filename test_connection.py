"""
RailController – Z21 Verbindungstest
Führe dieses Skript lokal aus (ohne Docker) um die Z21-Verbindung zu testen.

Voraussetzung: pip install asyncio
Aufruf:        python test_connection.py 192.168.1.111
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "app"))

from z21.client import Z21Client


async def main(host: str):
    print(f"\nRailController – Z21 Verbindungstest")
    print(f"Verbinde mit {host}:21105 ...\n")

    received_events = []

    def on_event(event: dict):
        received_events.append(event)
        t = event.get("type", "unknown")

        if t == "serial_number":
            print(f"  Seriennummer: {event['serial']}")
        elif t == "systemstate":
            power = "AN" if event["track_power"] else "AUS"
            print(f"  Gleisspannung:  {power}")
            print(f"  Temperatur:     {event['temperature_c']:.1f} °C")
            print(f"  Versorgung:     {event['supply_voltage_mv']} mV")
        elif t == "track_power_on":
            print(f"  >> Gleis EIN")
        elif t == "track_power_off":
            print(f"  >> Gleis AUS")
        elif t == "loco_info":
            print(f"  Lok {event['address']}: Speed={event['speed']}, Vorwärts={event['forward']}")
        else:
            print(f"  Event: {event}")

    client = Z21Client(host)

    try:
        await client.connect()
        print("Verbindung hergestellt. Warte auf Antwort...\n")

        # 2 Sekunden auf Events warten
        await asyncio.sleep(2)

        if not received_events:
            print("FEHLER: Keine Antwort von der Z21 erhalten.")
            print("Prüfe: IP-Adresse korrekt? Gleicher Subnet? Z21 eingeschaltet?")
        else:
            print(f"\nVerbindungstest erfolgreich ({len(received_events)} Events empfangen).")

        # Kurzer Gleisspannungstest
        print("\nGleisspannung AN...")
        await client.track_power_on()
        await asyncio.sleep(1)

        print("Gleisspannung AUS...")
        await client.track_power_off()
        await asyncio.sleep(0.5)

    except Exception as e:
        print(f"Fehler: {e}")
    finally:
        await client.disconnect()
        print("\nVerbindung getrennt.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Verwendung: python test_connection.py <Z21-IP>")
        print("Beispiel:   python test_connection.py 192.168.1.111")
        sys.exit(1)

    asyncio.run(main(sys.argv[1]))
