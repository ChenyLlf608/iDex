#!/usr/bin/env python3
"""Fill the Simplified Chinese catalogue text PokeAPI does not carry.

PokeAPI stores flavor text per game version, and official Simplified Chinese
only begins with Sun/Moon (2016). It is also well behind on Scarlet/Violet, so
for 1..1025 it is missing a Chinese dex entry for several hundred species and a
Chinese genus for the newest ones -- not because none was written, but because
it has not ingested the games they appear in.

52poke carries both: dex entries per version in its `{{图鉴}}` template, and
the genus as the `species` field of the infobox (stored as a stem, so `种子`
becomes `种子宝可梦`). Entries from games that shipped an official Chinese
localization are preferred; where only pre-2016 games list the species, the
text is a community translation and is recorded as such so the app can say so
rather than pass it off as official.

English and Japanese gaps are left alone on purpose: English is complete, and
filling Japanese would mean a second scraper against a different wiki.

Output is checked in: it is upstream text, it changes rarely, and committing it
keeps the catalogue build offline and reviewable.

    python scripts/catalog/fetch_wiki_descriptions.py --max-species 1025
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


# The infobox stores the genus as a stem; PokeAPI stores it whole, and the two
# have to agree. 妙蛙种子 is `species=种子` there and `种子宝可梦` in PokeAPI.
GENUS_SUFFIX = "宝可梦"
INFOBOX = "寶可夢信息框"


def infobox_genus(text: str) -> str | None:
    body = template_body(text, INFOBOX)
    if not body:
        return None
    match = re.search(r"^\|\s*species\s*=\s*(.*)$", body, re.MULTILINE)
    if not match:
        return None
    stem = clean(match.group(1))
    if not stem:
        return None
    return stem if stem.endswith(GENUS_SUFFIX) else stem + GENUS_SUFFIX


def pokeapi_gaps(maximum: int) -> tuple[set[int], set[int]]:
    """Species ids with no Simplified Chinese dex entry, and none with a genus."""
    import csv
    import io
    flavor_path = CACHE / "pokemon_species_flavor_text.csv"
    if flavor_path.exists():
        flavor_data = flavor_path.read_bytes()
    else:
        flavor_data = get(CSV_BASE + "pokemon_species_flavor_text.csv")
        flavor_path.parent.mkdir(parents=True, exist_ok=True)
        flavor_path.write_bytes(flavor_data)
    described = set()
    for row in csv.DictReader(io.StringIO(flavor_data.decode("utf-8"))):
        if row["language_id"] == "12" and row["flavor_text"].strip():
            described.add(int(row["species_id"]))

    names_path = CACHE / "pokemon_species_names.csv"
    names_data = names_path.read_bytes()
    classified = set()
    for row in csv.DictReader(io.StringIO(names_data.decode("utf-8"))):
        if row["local_language_id"] == "12" and row.get("genus", "").strip():
            classified.add(int(row["pokemon_species_id"]))

    span = range(1, maximum + 1)
    return ({i for i in span if i not in described}, {i for i in span if i not in classified})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-species", type=int, default=1025)
    parser.add_argument("--ids", help="Comma-separated species ids; default is every id whose "
                                      "Simplified Chinese dex entry or genus PokeAPI lacks")
    parser.add_argument("--delay", type=float, default=0.6, help="Pause between requests")
    parser.add_argument("--output", type=pathlib.Path, default=OUTPUT)
    arguments = parser.parse_args()

    names = chinese_names(arguments.max_species)
    missing_description, missing_genus = pokeapi_gaps(arguments.max_species)
    if arguments.ids:
        wanted = [int(value) for value in arguments.ids.split(",")]
        missing_description |= set(wanted)
    else:
        wanted = sorted(missing_description | missing_genus)

    print(f"Fetching {len(wanted)} species from 52poke "
          f"({len(missing_description)} need text, {len(missing_genus)} need a genus)",
          file=sys.stderr)
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
        if not text:
            failures.append({"id": identifier, "title": title, "resolved": resolved,
                             "reason": "page missing"})
            continue

        entry: dict[str, object] = {
            "source": f"https://wiki.52poke.com/wiki/{urllib.parse.quote(resolved)}"
        }
        if identifier in missing_description:
            body = template_body(text, "图鉴")
            chosen = choose(entries(body)) if body else None
            if chosen:
                entry["chineseDescription"], entry["version"], entry["official"] = chosen
        if identifier in missing_genus:
            genus = infobox_genus(text)
            if genus:
                entry["chineseGenus"] = genus

        if len(entry) == 1:  # nothing beyond the source URL
            failures.append({"id": identifier, "title": title, "resolved": resolved,
                             "reason": "no usable dex entry or genus"})
            continue
        if identifier in missing_description and "chineseDescription" not in entry:
            failures.append({"id": identifier, "title": title, "resolved": resolved,
                             "reason": "genus found but no usable dex entry"})
        if identifier in missing_genus and "chineseGenus" not in entry:
            failures.append({"id": identifier, "title": title, "resolved": resolved,
                             "reason": "dex entry found but no usable genus"})
        results[str(identifier)] = entry
        if position % 25 == 0:
            print(f"  {position}/{len(wanted)}", file=sys.stderr)
        time.sleep(arguments.delay)

    described = [e for e in results.values() if "chineseDescription" in e]
    official_count = sum(1 for e in described if e.get("official"))
    payload = {
        "source": "https://wiki.52poke.com",
        "license": "CC BY-NC-SA 3.0",
        "note": ("Simplified Chinese catalogue text PokeAPI does not carry. `official` marks a dex "
                 "entry from a game that shipped an official Chinese localization; the rest are "
                 "52poke community translations of games that never had one. English and Japanese "
                 "gaps are deliberately not filled."),
        "requested": len(wanted),
        "resolved": len(results),
        "descriptionsResolved": len(described),
        "descriptionsRequested": len(missing_description),
        "genusResolved": sum(1 for e in results.values() if "chineseGenus" in e),
        "genusRequested": len(missing_genus),
        "officialCount": official_count,
        "communityCount": len(described) - official_count,
        "failures": failures,
        "entries": dict(sorted(results.items(), key=lambda item: int(item[0]))),
    }
    arguments.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
    print(json.dumps({key: value for key, value in payload.items() if key != "entries"},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
