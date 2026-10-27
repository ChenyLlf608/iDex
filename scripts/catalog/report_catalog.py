"""Summarize the generated catalogue as markdown for a CI summary."""

import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
RESOURCES = ROOT / "iDex" / "Resources"
CATALOG = RESOURCES / "pokemon-catalog.json"
WIKI = pathlib.Path(__file__).resolve().parent / "wiki-descriptions.json"


def megabytes(byte_count: int) -> str:
    return f"{byte_count / 1048576:.1f} MB"


def tree_bytes(path: pathlib.Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def main() -> None:
    if not CATALOG.is_file():
        print("No catalogue was produced.")
        return
    entries = json.loads(CATALOG.read_text(encoding="utf-8"))
    community = [e for e in entries if e.get("chineseDescriptionOrigin") == "community"]
    sprites = list((RESOURCES / "PokemonSprites.xcassets").glob("*.imageset/sprite.png"))
    cries = list((RESOURCES / "Cries").glob("pokemon-cry-*.wav"))

    print(f"Covers **1..{len(entries)}**, sprites {len(sprites)}, cries {len(cries)}.")
    print()
    print("| asset | count | size |")
    print("| --- | --- | --- |")
    print(f"| catalogue JSON | {len(entries)} | {megabytes(tree_bytes(CATALOG))} |")
    print(f"| sprites | {len(sprites)} | {megabytes(tree_bytes(RESOURCES / 'PokemonSprites.xcassets'))} |")
    print(f"| cries | {len(cries)} | {megabytes(tree_bytes(RESOURCES / 'Cries'))} |")
    print()

    print("### Simplified Chinese dex entries")
    print()
    print(f"- official localization: **{len(entries) - len(community)}**")
    print(f"- 52poke community translation: **{len(community)}**")
    if WIKI.is_file():
        wiki = json.loads(WIKI.read_text(encoding="utf-8"))
        print(f"- filled from the wiki: {wiki.get('descriptionsResolved')} of "
              f"{wiki.get('descriptionsRequested')} dex entries, "
              f"{wiki.get('genusResolved')} of {wiki.get('genusRequested')} genera")
        for failure in (wiki.get("failures") or [])[:20]:
            print(f"  - unresolved #{failure['id']}: {failure['reason']}")
    print()

    no_japanese = [e["id"] for e in entries if not e.get("japaneseDescription")]
    print("### Japanese (not filled on purpose)")
    print()
    print(f"- missing dex entries: **{len(no_japanese)}**")
    print(f"- missing genera: **{sum(1 for e in entries if not e.get('japaneseGenus'))}**")
    print()
    if community:
        shown = ", ".join(f"#{e['id']} {e['chineseName']}" for e in community[:25])
        print(f"Community-translated: {shown}{' ...' if len(community) > 25 else ''}")
        print()

    missing = [e["id"] for e in entries if not e.get("chineseDescription")]
    if missing:
        print(f"**{len(missing)} entries still have no Chinese text:** {missing[:25]}")


if __name__ == "__main__":
    main()
