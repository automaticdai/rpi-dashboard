"""Runs at boot: join Wi-Fi, set the clock, take a reading every 10 s, serve the rpidash agent API."""
import asyncio
import gc
import time

import ntptime
from machine import Pin

import core1
import cpuload
import metrics
import wifi
from server import DEVICE_ID, Server
from wifi_config import COUNTRY, HOSTNAME, PASSWORD, SSID

VERSION = "pico-0.3.0"
NTP_EVERY_S = 6 * 3600  # the Pico's clock drifts a few seconds a day

led = Pin("LED", Pin.OUT)


def read_token():
    try:
        with open("token.txt") as f:
            return f.read().strip()
    except OSError:
        print("no token.txt: only /health will answer")
        return ""


class Agent:
    def __init__(self):
        self.hostname, self.version = HOSTNAME, VERSION
        self.history = metrics.History()
        self.latest = None
        self.boot_time = None  # Unix time of power-up, known once NTP has set the clock
        self.runner = None     # the application on core 1

    def uptime(self):
        return time.ticks_ms() // 1000 if self.boot_time is None else time.time() - self.boot_time

    def fresh_sample(self):
        return metrics.sample(time.time(), (self.latest or {}).get("cpu_cores"))

    def spec(self):
        return metrics.spec(DEVICE_ID, self.hostname, self.version, self.boot_time)


async def join():
    delay = 2
    while True:
        try:
            ip = await wifi.connect(SSID, PASSWORD, country=COUNTRY, hostname=HOSTNAME)
            print("Wi-Fi connected:", ip)
            led.on()
            return
        except OSError as e:
            print(e, "- retrying in", delay, "s")
            led.off()
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)


async def keep_connected():
    while True:
        await asyncio.sleep(10)
        if not wifi.is_connected():
            print("Wi-Fi lost, reconnecting")
            led.off()
            await join()


async def keep_time(agent):
    while True:
        try:
            ntptime.settime()  # one UDP round trip, ~1 s timeout: short enough to block
            if agent.boot_time is None:
                agent.boot_time = time.time() - time.ticks_ms() // 1000
            print("clock set:", time.gmtime())
            await asyncio.sleep(NTP_EVERY_S)
        except OSError as e:
            print("NTP:", e)
            await asyncio.sleep(30)


async def sample_loop(agent):
    reported = None
    while not metrics.epoch_ok():  # readings need real timestamps
        await asyncio.sleep(1)
    next_t = time.time()
    while True:
        cores = (cpuload.core0_pct(), agent.runner.cpu_pct() if agent.runner else 0.0)
        s = metrics.sample(time.time(), cores)
        agent.latest = s
        agent.history.add(s)
        err = agent.runner and agent.runner.error
        if err and err is not reported:  # printed here: core 1 must not write to USB serial
            print("app crashed:\n" + err)
        reported = err
        gc.collect()  # keep the heap reading steady and fragmentation low
        next_t += metrics.INTERVAL_S
        await asyncio.sleep(min(metrics.INTERVAL_S, max(1, next_t - time.time())))  # clock jumps (NTP) never stall it


def start_app(agent):
    try:
        import app
    except Exception as e:  # a broken app.py must not stop the agent: show it as crashed instead
        err = e

        class app:
            NAME, PERIOD_MS = "app.py", 1000

            @staticmethod
            def setup(values):
                raise err

            step = setup
    agent.runner = core1.Runner(app)
    agent.runner.start()


async def main(agent):
    cpuload.install()
    start_app(agent)
    await join()
    asyncio.create_task(keep_time(agent))
    asyncio.create_task(sample_loop(agent))
    await Server(agent, read_token()).serve()
    print("serving http://%s:8765" % wifi.ip())
    await keep_connected()


AGENT = Agent()
try:
    asyncio.run(main(AGENT))
finally:
    # Ctrl-C (mpremote connecting) or an error: stop core 1 before the REPL or a soft reset runs,
    # or the next reset can hang with the app still running there.
    if AGENT.runner and not AGENT.runner.close():
        print("the app on core 1 didn't stop; press reset if the board hangs")
