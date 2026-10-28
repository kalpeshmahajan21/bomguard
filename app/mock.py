"""MOCK=1: replays a realistic run with no API keys. For UI work and video takes only.
Real runs always go through Nemotron on Token Factory + Tavily. The UI shows a DEMO DATA banner in this mode."""
import asyncio
import random
from . import agent, config, llm, research

CATEGORIES = {
    "ATMEGA328P-AU": "MCU", "MPU-6050": "IMU", "HMC5883L": "Magnetometer", "BMP180": "Barometer",
    "CP2102-GMR": "USB-UART bridge", "AMS1117-3.3": "LDO regulator", "ESP32-WROOM-32E": "Wi-Fi module",
    "SN74HC595N": "Logic", "TPS63020DSJR": "Buck-boost", "W25Q32JVSSIQ": "SPI flash", "LM358DR": "Op-amp",
    "DS3231SN#": "RTC",
}
CANON = {"TI": "Texas Instruments", "AMS": "Advanced Monolithic Systems"}

FAKE = {
    "MPU-6050": dict(lifecycle="EOL", availability="Limited", rohs="Compliant", confidence="high",
                     summary="Discontinued by TDK InvenSense; remaining stock is broker-only. Redesign around a current IMU.",
                     successor="ICM-42688-P",
                     alternatives=[
                         dict(mpn="ICM-42688-P", manufacturer="TDK InvenSense", fit="Functional",
                              differences="LGA-14 2.5x3mm package vs QFN-24; new register map; lower noise gyro."),
                         dict(mpn="LSM6DSO", manufacturer="STMicroelectronics", fit="Functional",
                              differences="LGA-14 package; different driver; built-in FIFO and pedometer."),
                     ], recommendation="Move to ICM-42688-P: same vendor, better noise, well supported in flight stacks."),
    "HMC5883L": dict(lifecycle="Obsolete", availability="Out of stock", rohs="Compliant", confidence="high",
                     summary="Honeywell discontinued the HMC5883L; genuine parts are no longer available.",
                     successor=None,
                     alternatives=[
                         dict(mpn="QMC5883L", manufacturer="QST", fit="Pin-compatible",
                              differences="Same LGA-16 footprint; different I2C address (0x0D) and register map."),
                         dict(mpn="MMC5603NJ", manufacturer="MEMSIC", fit="Functional",
                              differences="0.8mm WLP package; higher resolution; new driver."),
                     ], recommendation="QMC5883L keeps the PCB; budget a day for the driver change."),
    "BMP180": dict(lifecycle="Obsolete", availability="Out of stock", rohs="Compliant", confidence="high",
                   summary="Bosch replaced the BMP180 with the BMP280/BMP390 family.",
                   successor="BMP280",
                   alternatives=[
                       dict(mpn="BMP390", manufacturer="Bosch Sensortec", fit="Functional",
                            differences="2x2mm LGA vs 3.6x3.8mm; SPI or I2C; better relative accuracy."),
                       dict(mpn="BMP280", manufacturer="Bosch Sensortec", fit="Functional",
                            differences="2x2.5mm LGA; register map differs; widely stocked."),
                   ], recommendation="Use BMP390 for altitude hold; it is the current Bosch recommendation."),
    "CP2102-GMR": dict(lifecycle="NRND", availability="In stock", rohs="Compliant", confidence="medium",
                       summary="Still sold but not recommended for new designs; Silicon Labs points to CP2102N.",
                       successor="CP2102N-A02-GQFN28",
                       alternatives=[
                           dict(mpn="CP2102N-A02-GQFN28", manufacturer="Silicon Labs", fit="Pin-compatible",
                                differences="QFN-28 pin-compatible with CP2102; check VDD/VREGIN strapping."),
                       ], recommendation="Swap to CP2102N-A02-GQFN28 at the next board spin."),
    "AMS1117-3.3": dict(lifecycle="Active", availability="In stock", rohs="Compliant", confidence="medium",
                        summary="Widely second-sourced LDO; low supply risk.", successor=None),
}

DOMAINS = {
    "Microchip": "microchip.com", "TDK InvenSense": "invensense.tdk.com", "Honeywell": "honeywell.com",
    "Bosch Sensortec": "bosch-sensortec.com", "Silicon Labs": "silabs.com", "Espressif": "espressif.com",
    "Texas Instruments": "ti.com", "Winbond": "winbond.com", "Analog Devices": "analog.com",
    "Advanced Monolithic Systems": "advanced-monolithic.com",
}


def normalize(parts: list[dict]) -> list[dict]:
    if config.SIMULATE_402 == "upload":
        raise llm.BillingPaused("Error code: 402 - Payment Required: You have exhausted your budget. (simulated)")
    for p in parts:
        p["manufacturer"] = CANON.get(p["manufacturer"], p["manufacturer"])
        p["category"] = CATEGORIES.get(p["mpn"], "Passive" if not p["mpn"] else "Component")
        p["is_generic"] = not p["mpn"]
    return parts


async def analyze_part(part: dict, usage: llm.Usage, stats: research.TavilyStats, emit) -> dict:
    if config.SIMULATE_402 == "scan" and part.get("mpn"):
        await asyncio.sleep(0.3)
        raise llm.BillingPaused("Error code: 402 - Payment Required: You have exhausted your budget. (simulated)")
    if part.get("is_generic") or not part["mpn"]:
        return await agent.analyze_part(part, usage, stats, emit)
    mpn, mfr = part["mpn"], part["manufacturer"]
    dom = DOMAINS.get(mfr, "example.com")
    sources = [
        {"title": f"{mpn} product page — {mfr}", "url": f"https://www.{dom}/", "content": ""},
        {"title": f"{mpn} distributor listing (demo)", "url": "https://www.digikey.com/", "content": ""},
    ]
    await emit("stage", {"row": part["row"], "stage": "research"})
    await asyncio.sleep(random.uniform(0.6, 1.4))
    stats.searches += 1; stats.credits += 2; stats.sources += 6
    await emit("stage", {"row": part["row"], "stage": "extract"})
    await asyncio.sleep(random.uniform(0.8, 1.6))
    usage.add("extract", random.randint(2500, 4200), 1.1)

    f = FAKE.get(mpn, dict(lifecycle="Active", availability="In stock", rohs="Compliant", confidence="high",
                           summary="In active production with healthy distributor stock.", successor=None))
    r = {**part, "lifecycle": f["lifecycle"], "availability": f["availability"], "rohs": f["rohs"],
         "confidence": f["confidence"], "summary": f["summary"], "successor": f.get("successor"),
         "sources": sources, "alternatives": [], "claims": [
             {"claim": f"Lifecycle listed as {f['lifecycle']} on the manufacturer page.", "source_index": 0,
              "url": sources[0]["url"], "source_title": sources[0]["title"]},
             {"claim": f"Availability: {f['availability']}.", "source_index": 1,
              "url": sources[1]["url"], "source_title": sources[1]["title"]},
         ]}
    r["risk"] = agent.score(r)
    r["band"] = agent.band(r["risk"])
    if r["risk"] >= 50 and f.get("alternatives"):
        await emit("stage", {"row": part["row"], "stage": "alternatives"})
        await asyncio.sleep(random.uniform(1.2, 2.0))
        stats.searches += 1; stats.credits += 2; stats.sources += 6
        usage.add("reason", random.randint(4000, 6500), 2.3)
        r["alternatives"] = [dict(a, url=f"https://www.{DOMAINS.get(a['manufacturer'], 'example.com')}/",
                                  source_title=f"{a['mpn']} — {a['manufacturer']}") for a in f["alternatives"]]
        r["recommendation"] = f["recommendation"]
    return r


async def brief(results: list[dict], usage: llm.Usage, stats: research.TavilyStats) -> dict:
    await asyncio.sleep(1.5)
    stats.searches += 3; stats.credits += 3; stats.sources += 9
    usage.add("reason", 5200, 3.1)
    return {
        "health": agent.health(results),
        "headline": "Not production-ready: all three motion and pressure sensors are discontinued, and two cannot be bought today.",
        "top_risks": [
            {"mpn": "HMC5883L", "why": "Obsolete and out of stock; no genuine supply.", "action": "Switch to QMC5883L (same footprint) and update the driver.", "urgency": "now"},
            {"mpn": "BMP180", "why": "Obsolete; Bosch moved to the BMP3xx family.", "action": "Respin the baro footprint for BMP390.", "urgency": "now"},
            {"mpn": "MPU-6050", "why": "EOL with broker-only stock and counterfeit risk.", "action": "Plan ICM-42688-P on the next revision.", "urgency": "this quarter"},
            {"mpn": "CP2102-GMR", "why": "NRND; still buyable but will not stay that way.", "action": "Drop in CP2102N-A02-GQFN28 at next spin.", "urgency": "monitor"},
        ],
        "market_notes": [],
        "next_steps": [
            "Freeze purchasing of HMC5883L and BMP180; do not build more boards on them.",
            "Do one board revision that replaces all three legacy sensors together.",
            "Re-run BOMGuard monthly on the released BOM.",
        ],
    }
