#!/usr/bin/env python3
"""Fill the Simplified Chinese dex entries PokeAPI does not carry.

PokeAPI stores flavor text per game version, and official Simplified Chinese
only begins with Sun/Moon (2016). For 1..649 that leaves 157 species with no
Chinese entry at all -- not because none was ever written, but because PokeAPI
has not ingested the newer games where those species reappeared.

52poke's `{{图鉴}}` template does carry them, per version, in
`-{zh-hans:...;zh-hant:...}-` form. Entries from games that shipped an official
Chinese localization are preferred; where only pre-2016 games list the species,
the text is a community translation and is recorded as such so the app can say
so rather than pass it off as official.

Output is checked in: it is upstream text, it changes rarely, and committing it
keeps the catalogue build offline and reviewable.

    python scripts/catalog/fetch_wiki_descriptions.py --max-species 649
"""

import argparse
import json
import pathlib
import re
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
CACHE = ROOT / ".catalog-cache"
OUTPUT = pathlib.Path(__file__).resolve().parent / "wiki-descriptions.json"
API = "https://wiki.52poke.com/api.php"
CSV_BASE = "https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/"
USER_AGENT = "iDex-development-catalog/1.0 (non-commercial fan project)"

# Games that shipped an official Simplified Chinese localization, newest first.
# PokeAPI's own convention is to keep the latest text, so this follows it.
OFFICIAL_VERSIONS = [
    "zadex", "videx", "scdex", "ladex",
    "bdspdex", "spdex", "bddex",
    "Shdex", "Swdex", "letsgodex",
    "ultramoondex", "ultrasundex", "moondex", "sundex",
]

# No official Chinese ever existed for these; 52poke's text is a community
# translation. Newest first, same as above.
COMMUNITY_VERSIONS = [
    "orasdex", "alphasapphiredex", "omegarubydex", "ydex", "xdex",
    "b2w2dex", "bwb2w2dex", "bwdex",
    "hgssdex", "soulsilverdex", "heartgolddex",
    "dpptdex", "platinumdex", "pearldex", "diamonddex",
    "frlgdex", "leafgreendex", "firereddex",
    "rsedex", "emeralddex", "sapphiredex", "rubydex",
    "crystaldex", "silverdex", "golddex",
    "yellowdex", "bluedex", "redgreendex",
]


def get(url: str, attempts: int = 4) -> bytes:
    for attempt in range(attempts):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            return urllib.request.urlopen(request, timeout=45).read()
        except Exception:
            if attempt == attempts - 1:
                raise
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError("unreachable")


def chinese_names(maximum: int) -> dict[int, str]:
    """Page titles are the species' Chinese names, which PokeAPI does have."""
    import csv
    import io
    path = CACHE / "pokemon_species_names.csv"
    if path.exists():
        data = path.read_bytes()
    else:
        data = get(CSV_BASE + "pokemon_species_names.csv")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    names = {}
    for row in csv.DictReader(io.StringIO(data.decode("utf-8"))):
        identifier = int(row["pokemon_species_id"])
        if row["local_language_id"] == "12" and 1 <= identifier <= maximum:
            names[identifier] = row["name"]
    return names


def wikitext(title: str) -> tuple[str | None, str]:
    """Returns the page source and the title it actually resolved to.

    Two lookups have to be relaxed. Redirects are followed, and titles are run
    through the wiki's own Simplified Chinese variant conversion: PokeAPI's
    zh-Hans names are not always written the way the wiki files them. Minun's
    is 負电拍拍, whose first character is the traditional form, and no page or
    redirect exists under that spelling -- converttitles maps it to 负电拍拍.
    Doing this through the wiki rather than a local character table keeps the
    two in agreement for any name, not just the one that happened to break.
    """
    url = (f"{API}?action=query&titles={urllib.parse.quote(title)}"
           "&redirects=1&converttitles=1&variant=zh-hans"
           "&prop=revisions&rvprop=content&rvslots=main&format=json&formatversion=2")
    payload = json.loads(get(url).decode("utf-8"))["query"]
    page = payload["pages"][0]
    resolved = page.get("title", title)
    if page.get("missing"):
        return None, resolved
    return page["revisions"][0]["slots"]["main"]["content"], resolved


def template_body(text: str, name: str) -> str | None:
    """Brace-balanced, because the body contains nested template calls."""
    start = text.find("{{" + name)
    if start < 0:
        return None
    depth = 0
    index = start
    while index < len(text) - 1:
        pair = text[index:index + 2]
        if pair == "{{":
            depth += 1
            index += 2
        elif pair == "}}":
            depth -= 1
            index += 2
            if depth == 0:
                return text[start:index]
        else:
            index += 1
    return None


CONVERSION = re.compile(r"-\{(?:[^{}]*?;)?\s*zh(?:-hans|-cn)\s*:\s*([^;}]*)[^{}]*\}-")
PLAIN_CONVERSION = re.compile(r"-\{([^{}:;]*)\}-")
PIPED_LINK = re.compile(r"\[\[[^\]|]*\|([^\]]*)\]\]")
LINK = re.compile(r"\[\[([^\]]*)\]\]")
TOOLTIP = re.compile(r"\{\{\s*tt\s*\|([^|}]*)\|[^}]*\}\}")
TEMPLATE = re.compile(r"\{\{[^{}]*\}\}")
TAG = re.compile(r"<[^>]+>")


def clean(value: str) -> str:
    value = CONVERSION.sub(r"\1", value)
    value = PLAIN_CONVERSION.sub(r"\1", value)
    value = TOOLTIP.sub(r"\1", value)
    for _ in range(3):  # nested templates unwrap from the inside out
        value = TEMPLATE.sub("", value)
    value = PIPED_LINK.sub(r"\1", value)
    value = LINK.sub(r"\1", value)
    value = TAG.sub("", value)
    value = value.replace("'''", "").replace("''", "")
    return " ".join(value.split()).strip()


def entries(body: str) -> dict[str, str]:
    found = {}
    for line in body.split("\n"):
        match = re.match(r"^\|\s*([A-Za-z0-9_]+dex)\s*=\s*(\S.*)$", line.strip())
        if not match:
            continue
        text = clean(match.group(2))
        if text:
            found[match.group(1)] = text
    return found


def choose(found: dict[str, str]) -> tuple[str, str, bool] | None:
    for version in OFFICIAL_VERSIONS:
        if version in found:
            return found[version], version, True
    for version in COMMUNITY_VERSIONS:
        if version in found:
            return found[version], version, False
    return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-species", type=int, default=649)
    parser.add_argument("--ids", help="Comma-separated species ids; default is every id without "
                                      "Simplified Chinese flavor text in PokeAPI")
    parser.add_argument("--delay", type=float, default=0.6, help="Pause between requests")
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    arguments = parser.parse_args()

    names = chinese_names(arguments.max_species)
    if arguments.ids:
        wanted = [int(value) for value in arguments.ids.split(",")]
    else:
        import csv
        import io
        path = CACHE / "pokemon_species_flavor_text.csv"
        if path.exists():
            data = path.read_bytes()
        else:
            data = get(CSV_BASE + "pokemon_species_flavor_text.csv")
            path.write_bytes(data)
        covered = set()
        for row in csv.DictReader(io.StringIO(data.decode("utf-8"))):
            if row["language_id"] == "12" and row["flavor_text"].strip():
                covered.add(int(row["species_id"]))
        wanted = [identifier for identifier in range(1, arguments.max_species + 1)
                  if identifier not in covered]

    print(f"Fetching {len(wanted)} species from 52poke", file=sys.stderr)
    results, failures = {}, []
    for position, identifier in enumerate(wanted, start=1):
        title = names.get(identifier)
        if not title:
            failures.append({"id": identifier, "reason": "no Chinese name in PokeAPI"})
            continue
        try:
            text, resolved = wikitext(title)
        except Exception as error:
            failures.append({"id": identifier, "title": title, "reason": f"fetch failed: {error}"})
            continue
        body = template_body(text, "图鉴") if text else None
        chosen = choose(entries(body)) if body else None
        if not chosen:
            failures.append({"id": identifier, "title": title, "resolved": resolved,
                             "reason": "page missing" if not text else "no usable dex entry"})
            continue
        description, version, official = chosen
        results[str(identifier)] = {
            "chineseDescription": description,
            "version": version,
            "official": official,
            "source": f"https://wiki.52poke.com/wiki/{urllib.parse.quote(resolved)}",
        }
        if position % 25 == 0:
            print(f"  {position}/{len(wanted)}", file=sys.stderr)
        time.sleep(arguments.delay)

    official_count = sum(1 for entry in results.values() if entry["official"])
    payload = {
        "source": "https://wiki.52poke.com",
        "license": "CC BY-NC-SA 3.0",
        "note": ("Simplified Chinese dex entries PokeAPI does not carry. `official` marks text "
                 "from a game that shipped an official Chinese localization; the rest are 52poke "
                 "community translations of games that never had one."),
        "requested": len(wanted),
        "resolved": len(results),
        "officialCount": official_count,
        "communityCount": len(results) - official_count,
        "failures": failures,
        "entries": dict(sorted(results.items(), key=lambda item: int(item[0]))),
    }
    arguments.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "entries"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
