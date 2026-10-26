import Foundation

/// Turns ordinary speech synthesis into the clipped, band-limited buzz of a
/// handheld electronic index. Everything here is plain sample math on mono
/// float frames, so the character of the voice can be reasoned about and
/// tested without audio hardware, a session, or a synthesizer.
///
/// The chain is ordered the way the artefacts stack in real small-speaker
/// hardware: restrict the band first, add the carrier buzz, then let
/// quantization and drive act on what remains. Reordering changes the timbre.
nonisolated struct RetroVoiceProcessor: Sendable {
    /// How far the effect is pushed. `classic` targets the readable but
    /// unmistakably electronic reading of a toy index; `heavy` trades
    /// intelligibility for grit.
    nonisolated enum Preset: Sendable { case subtle, classic, heavy }

    /// Everything below the earpiece band, where a small driver has no output.
    var highPassHz: Double
    /// The upper edge that removes modern sibilance and air.
    var lowPassHz: Double
    /// Carrier frequency of the ring modulator; the source of the metallic edge.
    var ringHz: Double
    /// How much of the signal passes through the carrier, 0...1.
    var ringDepth: Float
    /// Samples held per step. Above 1 this aliases, standing in for a converter
    /// that is slower than the playback rate.
    var holdFactor: Int
    /// Quantization steps per unit amplitude, emulating a narrow sample word.
    var quantizeLevels: Float
    /// Soft-clip amount applied after quantization.
    var drive: Float
    /// Peak the result is normalized to, leaving headroom for the mixer.
    var outputPeak: Float

    init(preset: Preset = .classic) {
        switch preset {
        case .subtle:
            highPassHz = 300
            lowPassHz = 4200
            ringHz = 48
            ringDepth = 0.12
            holdFactor = 1
            quantizeLevels = 2048
            drive = 1.3
            outputPeak = 0.86
        case .classic:
            highPassHz = 430
            lowPassHz = 3300
            ringHz = 62
            ringDepth = 0.24
            holdFactor = 2
            quantizeLevels = 320
            drive = 2.0
            outputPeak = 0.9
        case .heavy:
            highPassHz = 560
            lowPassHz = 2700
            ringHz = 84
            ringDepth = 0.38
            holdFactor = 3
            quantizeLevels = 96
            drive = 3.2
            outputPeak = 0.92
        }
    }

    /// Rewrites `samples` in place. A rate of zero or an empty input is left
    /// untouched so a failed synthesis degrades to silence rather than noise.
    func process(_ samples: inout [Float], sampleRate: Double) {
        guard sampleRate > 0, !samples.isEmpty else { return }
        bandLimit(&samples, sampleRate: sampleRate)
        ringModulate(&samples, sampleRate: sampleRate)
        if holdFactor > 1 { sampleAndHold(&samples) }
        quantize(&samples)
        softClip(&samples)
        normalize(&samples)
    }

    /// One pole up, two poles down. The gentle skirts matter more than a sharp
    /// cutoff: a steep filter sounds like a filter, this sounds like a speaker.
    private func bandLimit(_ samples: inout [Float], sampleRate: Double) {
        let dt = 1 / sampleRate

        let highRC = 1 / (2 * Double.pi * highPassHz)
        let highAlpha = Float(highRC / (highRC + dt))
        var previousInput: Float = 0
        var previousOutput: Float = 0
        for index in samples.indices {
            let input = samples[index]
            previousOutput = highAlpha * (previousOutput + input - previousInput)
            previousInput = input
            samples[index] = previousOutput
        }

        let lowRC = 1 / (2 * Double.pi * lowPassHz)
        let lowAlpha = Float(dt / (lowRC + dt))
        var stageOne: Float = 0
        var stageTwo: Float = 0
        for index in samples.indices {
            stageOne += lowAlpha * (samples[index] - stageOne)
            stageTwo += lowAlpha * (stageOne - stageTwo)
            samples[index] = stageTwo
        }
    }

    /// Partial-depth ring modulation. Leaving part of the dry signal through
    /// keeps consonants legible while the carrier supplies the buzz.
    private func ringModulate(_ samples: inout [Float], sampleRate: Double) {
        guard ringDepth > 0 else { return }
        let step = 2 * Double.pi * ringHz / sampleRate
        let dry = 1 - ringDepth
        for index in samples.indices {
            let carrier = Float(cos(step * Double(index)))
            samples[index] *= dry + ringDepth * carrier
        }
    }

    private func sampleAndHold(_ samples: inout [Float]) {
        var held: Float = 0
        for index in samples.indices {
            if index % holdFactor == 0 { held = samples[index] }
            samples[index] = held
        }
    }

    private func quantize(_ samples: inout [Float]) {
        guard quantizeLevels > 0 else { return }
        for index in samples.indices {
            samples[index] = (samples[index] * quantizeLevels).rounded() / quantizeLevels
        }
    }

    /// Normalized so raising `drive` adds harmonics without also adding level.
    private func softClip(_ samples: inout [Float]) {
        guard drive > 0 else { return }
        let ceiling = tanhf(drive)
        guard ceiling > 0 else { return }
        for index in samples.indices {
            samples[index] = tanhf(samples[index] * drive) / ceiling
        }
    }

    private func normalize(_ samples: inout [Float]) {
        var peak: Float = 0
        for sample in samples { peak = max(peak, abs(sample)) }
        guard peak > 0.0001 else { return }
        let gain = outputPeak / peak
        for index in samples.indices { samples[index] *= gain }
    }

    /// Linear resampling between the synthesizer's rate and the output graph's.
    /// Speech is already band-limited well below Nyquist here, so interpolation
    /// artefacts land under the grit the chain adds on purpose.
    static func resample(_ samples: [Float], from source: Double, to target: Double) -> [Float] {
        guard source > 0, target > 0, samples.count > 1 else { return samples }
        guard abs(source - target) > 1 else { return samples }
        let ratio = source / target
        let count = Int(Double(samples.count) / ratio)
        guard count > 1 else { return samples }
        var output = [Float](repeating: 0, count: count)
        let last = samples.count - 1
        for index in 0..<count {
            let position = Double(index) * ratio
            let left = min(Int(position), last)
            let right = min(left + 1, last)
            let fraction = Float(position - Double(left))
            output[index] = samples[left] * (1 - fraction) + samples[right] * fraction
        }
        return output
    }
}
