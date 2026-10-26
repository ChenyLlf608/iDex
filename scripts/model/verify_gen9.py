"""Measure a full-dex candidate classifier against the 151 bundled sprites.

The shipped Kanto model was checked by compositing each bundled Black/White
sprite onto white, resizing to 224 with nearest-neighbour, and recording top-1,
softmax confidence and the logit margin (`scripts/model/sprite-verification.json`).
This repeats exactly that preparation so the two runs are directly comparable,
and answers one question before any catalogue work is undertaken: does a model
choosing between 1025 species still identify the 151 this app already knows?

An in-domain artwork check. It bounds the candidate from above: a model that
cannot place a clean sprite will not place a camera frame.

    python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
    python -m pip install transformers pillow numpy
    python scripts/model/verify_gen9.py --output artifacts/gen9-sprites.json
"""

import argparse
import json
import pathlib
import unicodedata

import numpy as np
import torch
from PIL import Image
from transformers import AutoConfig, AutoModelForImageClassification

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPRITES = ROOT / "iDex/Resources/PokemonSprites.xcassets"
KANTO_LABELS = ROOT / "iDex/Recognition/pokemon_labels.json"
KANTO_COUNT = 151

# The shipped model's acceptance policy, so "accepted" means the same thing
# in both reports.
MIN_CONFIDENCE = 0.25
MIN_MARGIN = 0.5

RESAMPLING = {"nearest": Image.Resampling.NEAREST, "bilinear": Image.Resampling.BILINEAR}


def normalize(name: str) -> str:
    """Fold the two naming conventions onto one key.

    The catalogue uses PokeAPI slugs (`nidoran-f`, `mr-mime`); candidate models
    label with display names (`Nidoran♀`, `Mr. Mime`). Gender signs carry
    meaning that stripping punctuation alone would discard, so they are spelled
    out before everything else is removed.
    """
    folded = unicodedata.normalize("NFKC", name).lower()
    folded = folded.replace("♀", "f").replace("♂", "m")
    return "".join(character for character in folded if character.isalnum())


def prepare(path: pathlib.Path, resample: Image.Resampling) -> Image.Image:
    foreground = Image.open(path).convert("RGBA")
    background = Image.new("RGBA", foreground.size, "white")
    composited = Image.alpha_composite(background, foreground).convert("RGB")
    return composited.resize((224, 224), resample)


def as_tensor(image: Image.Image) -> torch.Tensor:
    """ViT preprocessing: scale to 0...1 then normalize by mean and std 0.5."""
    array = np.asarray(image, dtype=np.float32) / 255.0
    array = (array - 0.5) / 0.5
    return torch.from_numpy(array).permute(2, 0, 1).unsqueeze(0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="skshmjn/Pokemon-classifier-gen9-1025")
    parser.add_argument("--revision", default="9e8d54de136b99afc212322eae13ddc07a6fd779",
                        help="Pinned so a later upload cannot silently change the result")
    parser.add_argument("--resample", default="nearest", choices=sorted(RESAMPLING),
                        help="nearest reproduces the shipped check and the app's own resize")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "artifacts/gen9-sprite-verification.json")
    arguments = parser.parse_args()

    config = AutoConfig.from_pretrained(arguments.repository, revision=arguments.revision)
    id2label = {int(key): value for key, value in config.id2label.items()}
    model = AutoModelForImageClassification.from_pretrained(arguments.repository, revision=arguments.revision)
    model.eval()

    kanto = {entry["id"]: entry["name"] for entry in json.loads(KANTO_LABELS.read_text(encoding="utf-8"))}

    # Index order is expected to be dex order, but that is checked rather than
    # assumed: a silent off-by-one would read as a catastrophic accuracy drop.
    index_of: dict[str, int] = {}
    for index, label in id2label.items():
        index_of.setdefault(normalize(label), index)

    alignment = []
    for dex in range(1, KANTO_COUNT + 1):
        expected = normalize(kanto[dex])
        by_position = normalize(id2label.get(dex - 1, ""))
        found = index_of.get(expected)
        if by_position != expected:
            alignment.append({"dex": dex, "catalogue": kanto[dex],
                              "labelAtIndex": id2label.get(dex - 1), "foundAtIndex": found})

    records = []
    with torch.no_grad():
        for dex in range(1, KANTO_COUNT + 1):
            sprite = SPRITES / f"pokemon-{dex:03}.imageset/sprite.png"
            image = prepare(sprite, RESAMPLING[arguments.resample])
            logits = model(pixel_values=as_tensor(image)).logits.reshape(-1).numpy()
            order = np.argsort(logits)[::-1]
            probabilities = np.exp(logits - logits.max())
            probabilities /= probabilities.sum()
            confidence = float(probabilities[order[0]])
            margin = float(logits[order[0]] - logits[order[1]])
            top = int(order[0])
            records.append({
                "expectedID": dex,
                "expected": kanto[dex],
                "topID": top + 1,
                "topLabel": id2label.get(top),
                "correct": top + 1 == dex,
                "confidence": confidence,
                "logitMargin": margin,
                "accepted": confidence >= MIN_CONFIDENCE and margin >= MIN_MARGIN,
                "runnerUp": id2label.get(int(order[1])),
            })

    correct = sum(record["correct"] for record in records)
    accepted = sum(record["accepted"] for record in records)
    confidences = sorted(record["confidence"] for record in records)
    summary = {
        "repository": arguments.repository,
        "revision": arguments.revision,
        "classes": len(id2label),
        "resample": arguments.resample,
        "input": "Bundled Black/White sprites, white alpha composite, resize to 224",
        "acceptance": {"minConfidence": MIN_CONFIDENCE, "minLogitMargin": MIN_MARGIN},
        "count": len(records),
        "correctTop1": correct,
        "accepted": accepted,
        "acceptedWrong": sum(r["accepted"] and not r["correct"] for r in records),
        "medianConfidence": round(confidences[len(confidences) // 2], 4),
        "labelAlignmentMismatches": alignment,
        "misses": [{k: r[k] for k in ("expectedID", "expected", "topLabel", "confidence")}
                   for r in records if not r["correct"]],
        "records": records,
    }

    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in summary.items() if key != "records"},
                     indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
