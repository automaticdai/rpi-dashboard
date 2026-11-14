"""Runs at boot: join Wi-Fi, serve /health on :8765, reconnect whenever the link drops."""
import asyncio
from machine import Pin

import server
import wifi
from wifi_config import COUNTRY, HOSTNAME, PASSWORD, SSID

led = Pin("LED", Pin.OUT)


async def join():
    delay = 2
    while True:
        try:
            ip = wifi.connect(SSID, PASSWORD, country=COUNTRY, hostname=HOSTNAME)
            print("Wi-Fi connected:", ip)
            led.on()
            return
        except OSError as e:
            print(e, "- retrying in", delay, "s")
            led.off()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def main():
    await join()
    await server.serve(HOSTNAME)
    print("serving http://%s:%d/health" % (wifi.ip(), server.PORT))
    while True:
        await asyncio.sleep(10)
        if not wifi.is_connected():
            print("Wi-Fi lost, reconnecting")
            led.off()
            await join()


asyncio.run(main())
