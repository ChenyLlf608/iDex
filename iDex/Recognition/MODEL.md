# Kanto image recognition

iDex bundles a real, offline DenseNet121 model for the **151 original Kanto species**. The app does not upload captured or imported images and does not download model weights at runtime.

## Source and transformation

- Upstream: [BiernyVR/pokemon-classifier-mobilenetv3](https://huggingface.co/BiernyVR/pokemon-classifier-mobilenetv3/tree/3bf528cf0f6ab8464ea3bac0f70d84886e8dab25).
- Pinned revision: `3bf528cf0f6ab8464ea3bac0f70d84886e8dab25`.
- Despite the repository name, this revision's current model is **DenseNet121**, with a 151-class head. The older MobileNet checkpoint in the same repository is not used.
- Source weights: `pokemon_densenet121_best.pth`, loaded with PyTorch's tensor-only `weights_only=True` option.
- Source checkpoint SHA-256: `58e54044ed3ff747a26717313570e4c0b0f2204e7bdfcef7351d011861b2defe`.
- Upstream repository metadata declares **MIT**. Pokémon names, images and characters remain the property of Nintendo, Creatures, GAME FREAK and The Pokémon Company. This is an unofficial fan project.
- Converted to a Core ML ML Program, with FP16 weights and an RGB image input. The packaged model occupies approximately 14 MB.
- Input: 224×224 RGB, stretched to fill the square input. Scaling by `1/255` and ImageNet normalization (`mean=[.485,.456,.406]`, `std=[.229,.224,.225]`) are embedded in the model.
- Output: 151 raw logits, using the exact ordering of the bundled `pokemon_labels.json`.

The upstream author describes training on artwork, real-world cards, toys and figures, and reports validation accuracy. iDex has **not independently reproduced that training evaluation** and does not adopt those accuracy claims.

## Acceptance and uncertainty

The Swift actor computes ordinary softmax scores. Unlike the upstream example, it does **not** apply temperature `0.25`, which would inflate displayed confidence. Every successful inference offers its highest-ranked candidate, regardless of score. A result is also accepted for automatic discovery and narration when both relaxed heuristics pass:

1. Top softmax score ≥ 0.25.
2. Difference between the two highest logits ≥ 0.5.

There is no raw-logit minimum.

Otherwise, `accepted` is nil, but the app still selects and opens the first candidate’s record as “Best match”, showing its score. The top three candidates remain available. The model has no trained background/unknown class: these thresholds limit automatic discovery and narration of weak matches but cannot guarantee rejection of unrelated objects, non-Kanto species, or unfamiliar artwork. Scores are model confidence, not calibrated real-world probabilities. Never automatically save or narrate an uncertain match as a confirmed discovery.

## Reproduction and checks

From the repository root, use a separate environment:

```sh
uv venv --python 3.11 /tmp/idex-coreml-env
uv pip install --python /tmp/idex-coreml-env/bin/python -r scripts/model/requirements.txt
/tmp/idex-coreml-env/bin/python scripts/model/convert_model.py --verify --verify-sprites
xcrun coremlc compile iDex/Recognition/NationalClassifier.mlpackage /tmp/idex-model-compiled
```

The source checkpoint is downloaded into `/tmp/idex-model-source`, outside the app and repository. `convert_model.py` regenerates the Core ML package and labels, then optionally compares Core ML with PyTorch on two upstream examples and three synthetic negatives. Recorded outputs are in `scripts/model/verification.json`.

Historical checks on macOS Core ML (acceptance counts below used the previous 60% / logit 3.0 / margin 2.0 policy; recorded JSON files retain that historical evidence):

- Upstream Pikachu image: correct #25, accepted (ordinary softmax ≈ 88.4% after white alpha composition).
- Upstream Charizard image: correct #6, accepted (≈ 87.8%).
- White image, black image and seeded random noise: all abstained.
- All five Core ML top classifications matched PyTorch; maximum absolute logit difference ≤ 0.014.
- `xcrun coremlc` compiled the package successfully.
- The production Swift `PokemonClassifier.classify(image:)` API also identified both original upstream CGImages correctly (≈85.9% and ≈84.4%, respectively). Their direct alpha handling differs from the Python white-composited inputs.
- All 151 bundled Black/White pixel sprites had the correct top classification after white alpha composition and nearest-neighbor resizing; 146 passed the conservative acceptance thresholds, and 5 correctly ranked sprites were left uncertain. Recorded in `scripts/model/sprite-verification.json`. This is a narrow in-domain artwork check, not a measurement of camera recognition accuracy.
- On the iPhone Duo iOS 27.1 simulator, all five `RecognitionTests` cases passed using the production API: Pikachu and Charizard with the app's exact opaque white 384px UIKit image preparation, white/black blank rejection, and catalog label coverage. Test result: `Test-iDex-2026.10.24_14-02-03-+0800.xcresult`.

These are smoke checks, not a broad benchmark or proof of camera accuracy. A physical-device camera test remains necessary to evaluate lighting, framing, latency and generalization.

## Swift API

```swift
let classifier = PokemonClassifier()
let result = try await classifier.classify(image: cgImage)
if let bestMatch = result.candidates.first {
    // Always show bestMatch.dexID, bestMatch.name and bestMatch.confidence.
}
if let accepted = result.accepted {
    // Only accepted results automatically update discoveries and trigger narration.
}
```

Model loading and prediction are serialized inside an actor. Physical-device configuration uses `.all` compute units so Core ML can choose the available CPU, GPU and Neural Engine. Simulator builds explicitly use `.cpuOnly`: the accelerated simulator path failed the native sample flow, whereas CPU execution passed the actual bundled-model tests. This setting affects the simulator only; physical-device performance still needs measurement.
