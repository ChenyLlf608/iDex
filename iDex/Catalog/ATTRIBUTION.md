# Offline catalog and visual assets

The app bundles the full national dex, species 1–1025, matching what the recognition model covers.

Names, genera, English, Simplified Chinese, and Japanese Pokédex text, types, height, weight, and base stats were fetched from the PokeAPI data repository on 24 October 2026:

- https://github.com/PokeAPI/pokeapi/tree/master/data/v2/csv
- API/data documentation: https://pokeapi.co/docs/v2

`Resources/pokemon-catalog.json` stores metric values (metres and kilograms). Descriptions use the latest available entry for each language in the upstream data. English, Chinese, and Japanese text can therefore come from different game versions. Japanese uses upstream language ID 11 (`ja`, including kanji), with whitespace normalized in the same way as English. The catalog is bundled; browsing and speech require no API call.

## Simplified Chinese entries PokeAPI does not carry

Official Simplified Chinese localization begins with *Sun/Moon* (2016), and PokeAPI stores Pokédex text per game version, so a species that never appeared in a game PokeAPI has ingested Chinese text for has no Chinese entry there. For 1–649 that is 157 species.

Those entries are taken from [52poke](https://wiki.52poke.com) (神奇宝贝百科), whose `{{图鉴}}` template records Chinese text per game version. `scripts/catalog/fetch_wiki_descriptions.py` prefers versions that shipped an official Chinese localization and falls back to earlier games only when the species appears in none of them; the text there is then a community translation rather than official localized text.

`scripts/catalog/wiki-descriptions.json` records, for every entry taken this way, the game version it came from, whether that version was officially localized, and the source page. Each catalog entry carries `chineseDescriptionOrigin` (`official` or `community`), and the interface labels community translations in the entry panel rather than presenting them as official text.

52poke content is licensed **CC BY-NC-SA 3.0**, which requires attribution, prohibits commercial use, and requires derivative works to carry the same license. This attribution satisfies the first; iDex is a non-commercial fan project, which satisfies the second; the third applies to the Chinese description text sourced this way.

Sprites come from two archives, because official 2D pixel art stopped after Black & White. `Resources/sprite-sources.json` records the exact URL used for every sprite.

Species 1–649 use the Pokémon Database's Black & White sprite archive:

- https://pokemondb.net/sprites
- https://img.pokemondb.net/sprites/black-white/normal/pikachu.png
- Fallback source: https://github.com/PokeAPI/sprites

Species 650–1025 have no official sprite in that style: *X/Y* (2013) moved to 3D models and no 2D pixel sprite was ever drawn for them. Those use the **Smogon sprite project**'s Gen-5-style artwork, as served by Pokémon Showdown:

- https://play.pokemonshowdown.com/sprites/gen5/
- Project threads and artist credits: https://smogon.com/forums/threads/3486712/

These are fan drawings by many contributors, not official assets, and are used here on the same non-commercial fan-project basis as the rest of the catalog. Later species deliberately do **not** fall back to an official 3D render: one high-resolution model among pixel sprites looks worse than a loud build failure.

## Japanese dex text is incomplete on purpose

PokeAPI is well behind on *Scarlet/Violet* text in languages other than English. For 650–1025 it is missing the Japanese dex entry for 127 species and the Japanese genus for 112, almost all of them generation 9. Filling these would mean a second scraper against a Japanese wiki; that was weighed and declined. English is complete upstream, Simplified Chinese is completed from 52poke, and `Pokemon.description(in:)` states plainly where a Japanese entry is absent rather than showing an empty panel.

Cries for every catalogued species come from [PokéAPI/cries](https://github.com/PokeAPI/cries) at commit `b3a9044bb839b5e3152aaa536c7b463a0fcf0140`, using `cries/pokemon/latest/{id}.ogg`. The upstream repository credits Pokémon Showdown, Veekun, and The Sounds Resource. `Resources/Cries/cry-sources.json` records each source URL, source and output SHA-256 hashes, species ID, and PCM frame count. The files are converted to mono 22,050 Hz, 16-bit PCM WAV without trimming or normalizing. Some upstream `.ogg` paths contain MP3 audio; conversion detects the actual format. Run `python3 scripts/audio/build_cries.py` with `ffmpeg` installed to regenerate them. The compiled app needs no decoder package or network access.

The upstream license is preserved in `Resources/Cries/PokeAPI-Cries-LICENSE.txt`. It states that audio contents are copyright The Pokémon Company and that the repository is distributed under CC0 1.0; this does not transfer the underlying Pokémon audio rights.

Pokémon names, text, artwork, sprites, and cries are copyright and trademarks of Nintendo, Creatures Inc., GAME FREAK inc., and The Pokémon Company. iDex is an unofficial fan project and is not endorsed by those companies. These assets require appropriate rights review before public commercial distribution.

Three locally bundled pixel font faces support all interface languages:

- **Press Start 2P**, The Press Start 2P Project Authors, SIL Open Font License 1.1. https://github.com/google/fonts/tree/main/ofl/pressstart2p
- **Fusion Pixel**, TakWolf and contributors, SIL Open Font License 1.1. Simplified Chinese and Japanese, 12 px proportional OTF, release 2026.10.16. https://github.com/TakWolf/fusion-pixel-font

Full font license texts are bundled beside the font files. `PixelFont.register()` registers the fonts without modifying app metadata. At small sizes, use integer multiples of the source pixel grid for crisp rendering. Chinese and Japanese each use their language-specific Fusion Pixel face, including the corresponding regional glyph forms.

To rebuild, run `python3 scripts/catalog/fetch_wiki_descriptions.py` and then `python3 scripts/catalog/build_catalog.py` from the repository root; the **Rebuild catalogue** workflow does both and commits the result. The script caches upstream CSVs and the font archive under `.catalog-cache/`, and skips sprite downloads that already exist. The compiled app performs no catalog or sprite downloads.
