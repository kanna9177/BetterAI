"""Warmup Sisal via Playwright: apre sisal in chromium headless, lascia che
akamai generi i cookie, intercetta l'XHR del palinsesto e scrive un file
curl.txt pronto per sisal-sync.

Richiede:
    pip install playwright
    playwright install chromium

Uso:
    python -m betterai sisal-warmup              # scrive curl.txt
    python -m betterai sisal-warmup --out x.txt
    python -m betterai sisal-warmup --debug      # browser visibile per debug
"""
import asyncio
import sys


PAGE_URL = "https://www.sisal.it/scommesse-matchpoint/palinsesto/calcio?day=today"
TARGET_HOST = "betting.sisal.it"
TARGET_PATH_HINT = "schedaDisciplina"

DROP_HEADERS = {
    "host", "content-length", "content-type", "connection",
    "accept-encoding",   # curl lo imposta solo, non deve essere forzato
    ":authority", ":method", ":path", ":scheme",
}


async def warmup(out_path="curl.txt", timeout_s=90, headless=True):
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise RuntimeError(
            "playwright non installato. Esegui:\n"
            "  pip install playwright\n"
            "  playwright install chromium")

    captured = {}
    future = asyncio.get_event_loop().create_future()

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=headless,
            args=["--disable-blink-features=AutomationControlled"],
        )
        ctx = await browser.new_context(
            locale="it-IT",
            timezone_id="Europe/Rome",
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/129.0.0.0 Safari/537.36",
            viewport={"width": 1366, "height": 768},
        )
        page = await ctx.new_page()

        async def on_request(req):
            if (TARGET_HOST in req.url and TARGET_PATH_HINT in req.url
                    and not future.done()):
                try:
                    hdrs = await req.all_headers()
                except Exception:
                    hdrs = req.headers
                captured["url"] = req.url
                captured["headers"] = hdrs
                captured["method"] = req.method
                future.set_result(True)

        page.on("request", lambda r: asyncio.create_task(on_request(r)))

        print(f"[warmup] apro {PAGE_URL}", file=sys.stderr)
        try:
            await page.goto(PAGE_URL, wait_until="domcontentloaded",
                            timeout=timeout_s * 1000)
        except Exception as e:
            print(f"[warmup] errore nav: {e}", file=sys.stderr)

        # Aspetta che l'XHR giusta parta (o timeout)
        try:
            await asyncio.wait_for(future, timeout=timeout_s)
        except asyncio.TimeoutError:
            await browser.close()
            raise RuntimeError(
                f"non ho visto l'XHR {TARGET_PATH_HINT} entro {timeout_s}s.\n"
                "Prova --debug per vedere cosa succede nel browser.")

        # Lascia che la risposta arrivi (cookies freschi persistono)
        await page.wait_for_timeout(1500)
        await browser.close()

    # Scrivi curl.txt
    lines = [f"curl --url '{captured['url']}' \\"]
    for k, v in captured["headers"].items():
        if k.lower() in DROP_HEADERS or k.startswith(":"):
            continue
        # Escaping minimo: niente apici singoli nei valori (improbabile)
        v = str(v).replace("'", "'\\''")
        lines.append(f"  -H '{k}: {v}' \\")
    # Rimuove il backslash dall'ultima riga
    if lines[-1].endswith(" \\"):
        lines[-1] = lines[-1][:-2]
    text = "\n".join(lines) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[warmup] OK, scritto {out_path} ({len(text)} byte, "
          f"{len(captured['headers'])} header)", file=sys.stderr)
    return out_path


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="curl.txt",
                    help="file di destinazione (default curl.txt)")
    ap.add_argument("--timeout", type=int, default=90,
                    help="timeout secondi (default 90)")
    ap.add_argument("--debug", action="store_true",
                    help="browser visibile per debug")
    args = ap.parse_args(argv)
    try:
        asyncio.run(warmup(args.out, args.timeout, headless=not args.debug))
    except Exception as e:
        print(f"errore warmup: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
