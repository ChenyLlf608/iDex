import Foundation

/// Turns ordinary speech synthesis into the clipped, band-limited buzz of a
/// handheld electronic index. Everything here is plain sample math on mono
/// float frames, so the character of the voice can be reasoned about and
/// tested without audio hardware, a session, or a synthesizer.
///
/// The chain is ordered the way the artefacts stack in real small-speaker
/// hardware: restrict the band, let the cabinet resonance through, add the
/// carrier buzz, then let quantization and drive act on what remains.
///
/// Character comes from the resonance rather than from a low cutoff. Rolling
/// the top off early reads as muffled, and it buries the fricatives Mandarin
/// needs to stay legible; a peak near the ear's own sensitivity does the work
/// of sounding like a cheap driver while making the reading easier to follow.
nonisolated struct RetroVoiceProcessor: Sendable {
    /// How far the effect is pushed. `classic` targets a readable but
    /// unmistakably electronic reading; `heavy` trades intelligibility
    /// for grit.
    nonisolated enum Preset: Sendable { case subtle, classic, heavy }

    /// Everything below the earpiece band, where a small driver has no output.
    var highPassHz: Double
    /// The upper edge. Kept well above the fricative range on purpose.
    var lowPassHz: Double
    /// Centre of the cabinet resonance that supplies the tinny presence.
    var presenceHz: Double
    /// Height of that resonance, in decibels.
    var presenceDecibels: Double
    /// Width of the resonance. Higher is narrower and more obviously a horn.
    var presenceQ: Double
    /// Carrier frequency of the ring modulator; the source of the metallic edge.
    var ringHz: Double
    /// How much of the signal passes through the carrier, 0...1. Small values
    /// colour the voice; large ones start eating consonants.
    var ringDepth: Float
    /// Samples held per step. Above 1 this aliases, standing in for a converter
    /// slower than the playback rate — at the cost of smearing consonants.
    var holdFactor: Int
    /// Quantization steps per unit amplitude, emulating a narrow sample word.
    var quantizeLevels: Float
    /// Soft-clip amount, which doubles as the limiter after levelling.
    var drive: Float
    /// Perceived level the reading is matched to before clipping.
    var targetRMS: Float
    /// Ceiling applied last, leaving headroom for the mixer.
    var outputPeak: Float

    init(preset: Preset = .classic) {
        switch preset {
        case .subtle:
            highPassHz = 260
            lowPassHz = 6400
            presenceHz = 2900
            presenceDecibels = 4
            presenceQ = 0.9
            ringHz = 70
            ringDepth = 0.03
            holdFactor = 1
            quantizeLevels = 4096
            drive = 1.2
            targetRMS = 0.14
            outputPeak = 0.9
        case .classic:
            highPassHz = 300
            lowPassHz = 5600
            presenceHz = 2800
            presenceDecibels = 6
            presenceQ = 1.1
            ringHz = 74
            ringDepth = 0.08
            holdFactor = 1
            quantizeLevels = 1400
            drive = 1.5
            targetRMS = 0.15
            outputPeak = 0.9
        case .heavy:
            highPassHz = 430
            lowPassHz = 4200
            presenceHz = 2600
            presenceDecibels = 7
            presenceQ = 1.4
            ringHz = 82
            ringDepth = 0.2
            holdFactor = 2
            quantizeLevels = 320
            drive = 2.4
            targetRMS = 0.16
            outputPeak = 0.92
        }
    }

    /// Rewrites `samples` in place. A rate of zero or an empty input is left
    /// untouched so a failed synthesis degrades to silence rather than noise.
    func process(_ samples: inout [Float], sampleRate: Double) {
        guard sampleRate > 0, !samples.isEmpty else { return }
        bandLimit(&samples, sampleRate: sampleRate)
        addPresence(&samples, sampleRate: sampleRate)
        ringModulate(&samples, sampleRate: sampleRate)
        if holdFactor > 1 { sampleAndHold(&samples) }
        quantize(&samples)
        matchLoudness(&samples)
        softClip(&samples)
        trimPeak(&samples)
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

        // Nyquist guard: a cutoff at or above half the rate is a no-op filter
        // that would otherwise produce a useless coefficient.
        let cutoff = min(lowPassHz, sampleRate * 0.45)
        let lowRC = 1 / (2 * Double.pi * cutoff)
        let lowAlpha = Float(dt / (lowRC + dt))
        var stageOne: Float = 0
        var stageTwo: Float = 0
        for index in samples.indices {
            stageOne += lowAlpha * (samples[index] - stageOne)
            stageTwo += lowAlpha * (stageOne - stageTwo)
            samples[index] = stageTwo
        }
    }

    /// A peaking biquad standing in for the one resonance a small plastic
    /// cabinet has. This is what carries the "device speaker" impression, and
    /// it lifts the consonants instead of burying them.
    private func addPresence(_ samples: inout [Float], sampleRate: Double) {
        guard presenceDecibels != 0, presenceHz > 0, presenceQ > 0 else { return }
        guard presenceHz < sampleRate * 0.45 else { return }
        let amplitude = pow(10, presenceDecibels / 40)
        let omega = 2 * Double.pi * presenceHz / sampleRate
        let alpha = sin(omega) / (2 * presenceQ)
        let cosine = cos(omega)

        let a0 = 1 + alpha / amplitude
        let b0 = Float((1 + alpha * amplitude) / a0)
        let b1 = Float((-2 * cosine) / a0)
        let b2 = Float((1 - alpha * amplitude) / a0)
        let a1 = Float((-2 * cosine) / a0)
        let a2 = Float((1 - alpha / amplitude) / a0)

        var x1: Float = 0, x2: Float = 0, y1: Float = 0, y2: Float = 0
        for index in samples.indices {
            let x0 = samples[index]
            let y0 = b0 * x0 + b1 * x1 + b2 * x2 - a1 * y1 - a2 * y2
            x2 = x1; x1 = x0
            y2 = y1; y1 = y0
            samples[index] = y0
        }
    }

    /// Partial-depth ring modulation. Leaving most of the dry signal through
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

    /// Match perceived level, not the single loudest plosive. Peak
    /// normalization let one hard consonant set the gain and left the body of
    /// a reading too quiet to follow; the soft clip after this acts as the
    /// limiter for whatever now overshoots.
    private func matchLoudness(_ samples: inout [Float]) {
        guard targetRMS > 0 else { return }
        var sum = 0.0
        for sample in samples { sum += Double(sample) * Double(sample) }
        let rms = Float((sum / Double(samples.count)).squareRoot())
        guard rms > 0.0001 else { return }
        // Bounded so near-silence is not amplified into hiss.
        let gain = min(targetRMS / rms, 24)
        for index in samples.indices { samples[index] *= gain }
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

    /// Only attenuates. A quiet reading is left where the loudness match put it.
    private func trimPeak(_ samples: inout [Float]) {
        var peak: Float = 0
        for sample in samples { peak = max(peak, abs(sample)) }
        guard peak > outputPeak else { return }
        let gain = outputPeak / peak
        for index in samples.indices { samples[index] *= gain }
    }

    /// Linear resampling between the synthesizer's rate and the output graph's.
    ///
    /// Going down in rate, the source is filtered first. A premium voice can
    /// render at 48 kHz; decimating that straight to the graph's 22,050 folds
    /// everything above the new Nyquist back into the speech band as noise,
    /// which reads as harsh and muddy at the same time.
    static func resample(_ samples: [Float], from source: Double, to target: Double) -> [Float] {
        guard source > 0, target > 0, samples.count > 1 else { return samples }
        guard abs(source - target) > 1 else { return samples }
        var input = samples
        if target < source { antiAlias(&input, cutoff: target * 0.45, sampleRate: source) }
        let ratio = source / target
        let count = Int(Double(input.count) / ratio)
        guard count > 1 else { return samples }
        var output = [Float](repeating: 0, count: count)
        let last = input.count - 1
        for index in 0..<count {
            let position = Double(index) * ratio
            let left = min(Int(position), last)
            let right = min(left + 1, last)
            let fraction = Float(position - Double(left))
            output[index] = input[left] * (1 - fraction) + input[right] * fraction
        }
        return output
    }

    /// Four one-pole stages. Not brick-wall, but enough slope that what folds
    /// back sits below the quantization floor the chain adds later.
    private static func antiAlias(_ samples: inout [Float], cutoff: Double, sampleRate: Double) {
        guard cutoff > 0, cutoff < sampleRate / 2 else { return }
        let dt = 1 / sampleRate
        let rc = 1 / (2 * Double.pi * cutoff)
        let alpha = Float(dt / (rc + dt))
        var stages: [Float] = [0, 0, 0, 0]
        for index in samples.indices {
            var signal = samples[index]
            for stage in stages.indices {
                stages[stage] += alpha * (signal - stages[stage])
                signal = stages[stage]
            }
            samples[index] = signal
        }
    }
}
