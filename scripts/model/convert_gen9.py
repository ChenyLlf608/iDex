#!/usr/bin/env python3
"""Convert a full-dex candidate to Core ML at several weight precisions.

The question this answers is a trade: a 1025-class ViT is an order of magnitude
larger than the shipped DenseNet121, so the app only grows sensibly if weight
compression is close to free. Each variant is measured the way it would ship --
compiled with coremlcompiler, since that is what lands in the bundle -- and
scored on the same 151 sprites as `verify_gen9.py`, through Core ML rather than
PyTorch so the numbers include conversion and quantization error.

Variants are attempted independently. A compression API that is unavailable or
renamed records its failure and leaves the rest of the run intact.

Requires macOS for prediction and `xcrun`. See requirements.txt.
"""

import argparse
import json
import pathlib
import shutil
import subprocess
import traceback

import coremltools as ct
import numpy as np
import torch
from PIL import Image
from transformers import AutoConfig, AutoModelForImageClassification

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPRITES = ROOT / "iDex/Resources/PokemonSprites.xcassets"
KANTO_LABELS = ROOT / "iDex/Recognition/pokemon_labels.json"
KANTO_COUNT = 151

MIN_CONFIDENCE = 0.25
MIN_MARGIN = 0.5


class ImageClassifier(torch.nn.Module):
    """Folds the ViT normalization into the graph, matching how the shipped
    model embeds its own so the Swift side keeps passing a plain image."""

    def __init__(self, backbone):
        super().__init__()
        self.backbone = backbone
        self.register_buffer("mean", torch.tensor([0.5, 0.5, 0.5]).reshape(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor([0.5, 0.5, 0.5]).reshape(1, 3, 1, 1))

    def forward(self, image):
        return self.backbone(pixel_values=(image - self.mean) / self.std).logits


def directory_bytes(path: pathlib.Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def compiled_bytes(package: pathlib.Path, workspace: pathlib.Path) -> int | None:
    """Size as it ships: Xcode compiles the package into the bundle."""
    output = workspace / f"{package.stem}-compiled"
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    try:
        subprocess.run(["xcrun", "coremlcompiler", "compile", str(package), str(output)],
                       check=True, capture_output=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return directory_bytes(output)


def prepare(path: pathlib.Path) -> Image.Image:
    foreground = Image.open(path).convert("RGBA")
    background = Image.new("RGBA", foreground.size, "white")
    composited = Image.alpha_composite(background, foreground).convert("RGB")
    # Bilinear, matching what Vision does for the app's own scaleFill path.
    return composited.resize((224, 224), Image.Resampling.BILINEAR)


def score(model: ct.models.MLModel, kanto: dict[int, str], id2label: dict[int, str]) -> dict:
    records = []
    for dex in range(1, KANTO_COUNT + 1):
        image = prepare(SPRITES / f"pokemon-{dex:03}.imageset/sprite.png")
        logits = np.asarray(model.predict({"image": image})["logits"]).reshape(-1)
        order = np.argsort(logits)[::-1]
        probabilities = np.exp(logits - logits.max())
        probabilities /= probabilities.sum()
        top = int(order[0])
        confidence = float(probabilities[top])
        margin = float(logits[top] - logits[int(order[1])])
        records.append({
            "expectedID": dex,
            "expected": kanto[dex],
            "topID": top + 1,
            "topLabel": id2label.get(top),
            "correct": top + 1 == dex,
            "confidence": confidence,
            "accepted": confidence >= MIN_CONFIDENCE and margin >= MIN_MARGIN,
        })
    confidences = sorted(record["confidence"] for record in records)
    return {
        "count": len(records),
        "correctTop1": sum(record["correct"] for record in records),
        "accepted": sum(record["accepted"] for record in records),
        "acceptedWrong": sum(r["accepted"] and not r["correct"] for r in records),
        "medianConfidence": round(confidences[len(confidences) // 2], 4),
        "misses": [{k: r[k] for k in ("expectedID", "expected", "topLabel", "confidence")}
                   for r in records if not r["correct"]],
    }


def compress(base: ct.models.MLModel, variant: str):
    if variant == "fp16":
        return base
    import coremltools.optimize.coreml as cto
    if variant == "int8":
        config = cto.OptimizationConfig(
            global_config=cto.OpLinearQuantizerConfig(mode="linear_symmetric", dtype="int8"))
        return cto.linear_quantize_weights(base, config=config)
    nbits = int(variant.removeprefix("palettize"))
    config = cto.OptimizationConfig(global_config=cto.OpPalettizerConfig(nbits=nbits, mode="kmeans"))
    return cto.palettize_weights(base, config=config)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default="skshmjn/Pokemon-classifier-gen9-1025")
    parser.add_argument("--revision", default="9e8d54de136b99afc212322eae13ddc07a6fd779")
    parser.add_argument("--variants", default="fp16,int8,palettize6,palettize4")
    parser.add_argument("--workspace", type=pathlib.Path, default=ROOT / "artifacts/gen9")
    parser.add_argument("--output", type=pathlib.Path, default=ROOT / "artifacts/gen9-coreml.json")
    arguments = parser.parse_args()
    arguments.workspace.mkdir(parents=True, exist_ok=True)

    config = AutoConfig.from_pretrained(arguments.repository, revision=arguments.revision)
    id2label = {int(key): value for key, value in config.id2label.items()}
    backbone = AutoModelForImageClassification.from_pretrained(
        arguments.repository, revision=arguments.revision).eval()
    kanto = {entry["id"]: entry["name"] for entry in json.loads(KANTO_LABELS.read_text(encoding="utf-8"))}

    example = torch.rand(1, 3, 224, 224)
    with torch.no_grad():
        traced = torch.jit.trace(ImageClassifier(backbone).eval(), example, strict=False)

    base = ct.convert(
        traced,
        convert_to="mlprogram",
        minimum_deployment_target=ct.target.iOS16,
        compute_precision=ct.precision.FLOAT16,
        inputs=[ct.ImageType(name="image", shape=example.shape, scale=1 / 255.0,
                             color_layout=ct.colorlayout.RGB)],
        outputs=[ct.TensorType(name="logits")],
    )
    base.author = f"{arguments.repository}; Core ML conversion for iDex"
    base.short_description = f"Offline ViT image classifier for {len(id2label)} Pokemon."
    base.version = arguments.revision
    base.user_defined_metadata["source"] = \
        f"https://huggingface.co/{arguments.repository}/tree/{arguments.revision}"
    base.input_description["image"] = "224 by 224 RGB image; normalization is embedded in the model."
    base.output_description["logits"] = f"{len(id2label)} unnormalized scores in dex order."

    results = []
    for variant in [name.strip() for name in arguments.variants.split(",") if name.strip()]:
        entry = {"variant": variant}
        try:
            model = compress(base, variant)
            package = arguments.workspace / f"Gen9Classifier-{variant}.mlpackage"
            if package.exists():
                shutil.rmtree(package)
            model.save(str(package))
            entry["packageBytes"] = directory_bytes(package)
            entry["compiledBytes"] = compiled_bytes(package, arguments.workspace)
            entry.update(score(ct.models.MLModel(str(package)), kanto, id2label))
        except Exception as error:  # noqa: BLE001 - one variant must not sink the run
            entry["error"] = f"{type(error).__name__}: {error}"
            entry["traceback"] = traceback.format_exc()[-1500:]
        results.append(entry)
        print(json.dumps({k: v for k, v in entry.items() if k not in ("misses", "traceback")},
                         ensure_ascii=False))

    labels = [{"id": index + 1, "name": id2label[index]} for index in sorted(id2label)]
    (arguments.workspace / "pokemon_labels_gen9.json").write_text(
        json.dumps(labels, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    summary = {
        "repository": arguments.repository,
        "revision": arguments.revision,
        "classes": len(id2label),
        "baseline": {"name": "shipped KantoClassifier (151 classes)",
                     "compiledBytes": directory_bytes(ROOT / "iDex/Recognition/KantoClassifier.mlpackage"),
                     "correctTop1": 151, "count": 151, "accepted": 146},
        "variants": results,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
