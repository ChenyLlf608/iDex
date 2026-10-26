#!/usr/bin/env python3
"""Rebuild the offline catalogue from cached upstream CSVs and sprites.

Covers 1..649 by default. The interface is built around Black/White pixel
sprites, and those only exist up to Genesect; recognition reaches further, so
the app reports anything past the catalogue as out of range rather than
inventing an entry for it.

PokeAPI carries no Simplified Chinese dex entry for 157 of those species.
Those are filled from `wiki-descriptions.json`, which records whether each
came from a game with an official Chinese localization or from a community
translation. See fetch_wiki_descriptions.py.
"""
import argparse
import concurrent.futures
import csv
import io
import json
from pathlib import Path
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / '.catalog-cache'
RESOURCES = ROOT / 'iDex' / 'Resources'
WIKI_DESCRIPTIONS = Path(__file__).resolve().parent / 'wiki-descriptions.json'
# Genesect: the last species with a Black/White sprite.
DEFAULT_MAX_SPECIES = 649
CSV_BASE = 'https://raw.githubusercontent.com/PokeAPI/pokeapi/master/data/v2/csv/'
FILES = ['pokemon.csv', 'pokemon_species_names.csv', 'pokemon_species_flavor_text.csv', 'pokemon_types.csv', 'types.csv', 'pokemon_stats.csv', 'stats.csv']

def download(url, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        return path.read_bytes()
    for attempt in range(4):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': 'iDex-development-catalog/1.0'})
            data = urllib.request.urlopen(request, timeout=40).read()
            path.write_bytes(data)
            return data
        except Exception:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)

def rows(file):
    return list(csv.DictReader(io.StringIO(download(CSV_BASE + file, CACHE / file).decode('utf-8'))))

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--max-species', type=int, default=DEFAULT_MAX_SPECIES,
                        help='Highest national dex number to include (default: the last '
                             'species with a Black/White sprite)')
    args = parser.parse_args()
    maximum = args.max_species

    wiki = {}
    if WIKI_DESCRIPTIONS.exists():
        wiki = json.loads(WIKI_DESCRIPTIONS.read_text(encoding='utf-8')).get('entries', {})

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        csvs = dict(zip(FILES, pool.map(rows, FILES)))
    species_names = {(int(r['pokemon_species_id']), int(r['local_language_id'])): r for r in csvs['pokemon_species_names.csv']}
    type_names = {r['id']: r['identifier'] for r in csvs['types.csv']}
    stat_names = {r['id']: r['identifier'] for r in csvs['stats.csv']}
    flavor = {}
    # Retain the latest available text in each language (CSV is ordered by version id).
    for r in csvs['pokemon_species_flavor_text.csv']:
        if int(r['species_id']) <= maximum and r['language_id'] in ('9', '11', '12'):
            normalized = ' '.join(r['flavor_text'].replace('\f', ' ').split())
            flavor[(int(r['species_id']), int(r['language_id']))] = normalized.replace(' ', '') if r['language_id'] == '12' else normalized
    entries = []
    slug_by_id = {}
    for p in csvs['pokemon.csv']:
        number = int(p['id'])
        if not 1 <= number <= maximum:
            continue
        en = species_names[(number, 9)]
        zh = species_names[(number, 12)]
        ja = species_names[(number, 11)]
        slug_by_id[number] = p['identifier']
        # PokeAPI has no Chinese entry for species absent from every game it has
        # ingested Chinese text for. Fall back to the wiki, keeping track of
        # whether that text is an official localization or a translation.
        chinese_description = flavor.get((number, 12))
        chinese_origin = 'official'
        supplement = wiki.get(str(number))
        if not chinese_description and supplement:
            chinese_description = supplement['chineseDescription']
            chinese_origin = 'official' if supplement['official'] else 'community'
        entry = {
            'id': number,
            'englishName': en['name'],
            'chineseName': zh['name'],
            'japaneseName': ja['name'],
            'types': [type_names[t['type_id']] for t in csvs['pokemon_types.csv'] if int(t['pokemon_id']) == number],
            'height': int(p['height']) / 10,
            'weight': int(p['weight']) / 10,
            'englishDescription': flavor[(number, 9)],
            'chineseDescription': chinese_description,
            'chineseDescriptionOrigin': chinese_origin,
            'japaneseDescription': flavor[(number, 11)],
            'englishGenus': en['genus'],
            'chineseGenus': zh['genus'],
            'japaneseGenus': ja['genus'],
            'stats': [{'name': stat_names[s['stat_id']], 'value': int(s['base_stat'])} for s in csvs['pokemon_stats.csv'] if int(s['pokemon_id']) == number]
        }
        entries.append(entry)
    assert len(entries) == maximum, f'expected {maximum} entries, built {len(entries)}'
    assert all(p['japaneseName'] and p['japaneseDescription'] and p['japaneseGenus']
               and p['englishDescription'] and len(p['stats']) == 6 for p in entries)
    # Name a gap rather than failing on a bare assert: the fix is to rerun
    # fetch_wiki_descriptions.py, which the message should say outright.
    untranslated = [p['id'] for p in entries if not p['chineseDescription']]
    if untranslated:
        raise SystemExit(
            f'{len(untranslated)} species have no Simplified Chinese dex entry: '
            f'{untranslated[:20]}{"..." if len(untranslated) > 20 else ""}\n'
            'Run scripts/catalog/fetch_wiki_descriptions.py to refresh '
            f'{WIKI_DESCRIPTIONS.name}, then build again.')
    community = [p['id'] for p in entries if p['chineseDescriptionOrigin'] == 'community']
    RESOURCES.mkdir(parents=True, exist_ok=True)
    (RESOURCES / 'pokemon-catalog.json').write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n')
    assets = RESOURCES / 'PokemonSprites.xcassets'
    assets.mkdir(exist_ok=True)
    (assets / 'Contents.json').write_text(json.dumps({'info': {'author': 'xcode', 'version': 1}}, indent=2))
    def sprite(p):
        number = p['id']
        asset = assets / f'pokemon-{number:03d}.imageset'
        path = asset / 'sprite.png'
        slug = slug_by_id[number]
        urls = [f'https://img.pokemondb.net/sprites/black-white/normal/{slug}.png', f'https://raw.githubusercontent.com/PokeAPI/sprites/master/sprites/pokemon/{number}.png']
        for source in urls:
            try:
                data = download(source, path)
                if not data.startswith(b'\x89PNG'):
                    path.unlink()
                    raise ValueError('Not a PNG')
                break
            except Exception:
                if source == urls[-1]:
                    raise
        (asset / 'Contents.json').write_text(json.dumps({'images': [{'filename': 'sprite.png', 'idiom': 'universal'}], 'info': {'author': 'xcode', 'version': 1}}, indent=2))
        return number, source
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        sources = dict(pool.map(sprite, entries))
    (RESOURCES / 'sprite-sources.json').write_text(json.dumps(sources, indent=2) + '\n')
    font_dir = RESOURCES / 'Fonts'
    font_dir.mkdir(exist_ok=True)
    for filename in ['PressStart2P-Regular.ttf', 'OFL.txt']:
        target = 'PressStart2P-OFL.txt' if filename == 'OFL.txt' else filename
        download('https://raw.githubusercontent.com/google/fonts/main/ofl/pressstart2p/' + filename, font_dir / target)
    fusion_url = 'https://github.com/TakWolf/fusion-pixel-font/releases/download/2026.10.16/fusion-pixel-font-12px-proportional-otf-v2026.10.16.zip'
    archive = download(fusion_url, CACHE / 'fusion-pixel-font.zip')
    with zipfile.ZipFile(io.BytesIO(archive)) as zipped:
        for name in zipped.namelist():
            if name.endswith(('zh_hans.otf', '-ja.otf', 'OFL.txt', 'LICENSE.txt')):
                output_name = 'FusionPixel-OFL.txt'
                if name.endswith('zh_hans.otf'):
                    output_name = 'FusionPixel-12px-zh-Hans.otf'
                elif name.endswith('-ja.otf'):
                    output_name = 'FusionPixel-12px-ja.otf'
                output = font_dir / output_name
                output.write_bytes(zipped.read(name))
    print(f'Built {len(entries)} English, Simplified Chinese, and Japanese entries '
          f'and {len(sources)} sprites.')
    print(f'Chinese dex entries: {len(entries) - len(community)} official, '
          f'{len(community)} community translation.')
    print('Pikachu:', entries[24])
    print('Fonts:', [p.name for p in font_dir.iterdir()])

if __name__ == '__main__':
    main()
