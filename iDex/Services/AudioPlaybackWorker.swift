import AVFoundation
import Foundation
import os

nonisolated protocol DeviceAudioPlayback: Sendable {
    /// Returns only after the output engine has rendered its first buffer.
    func activate() async -> Bool
    func deactivate() async
    func play(_ cue: DeviceSound, request: AudioPlaybackRequest)
    func playCry(dexID: Int, request: AudioPlaybackRequest, completion: @escaping @Sendable () -> Void)
    func stopSound()
    func speak(_ text: String, languageCode: String, request: AudioPlaybackRequest)
    func stopSpeaking()
}

/// Invalidated immediately by mute, suspend, or a newer request, even while a
/// native audio call occupies the serial queue. The UI never waits for it.
nonisolated final class AudioPlaybackRequest: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    private let speechActivity: (@Sendable (Bool) -> Void)?

    init(speechActivity: (@Sendable (Bool) -> Void)? = nil) {
        self.speechActivity = speechActivity
    }

    var isCancelled: Bool { lock.withLock { cancelled } }
    func cancel() { lock.withLock { cancelled = true } }

    func reportSpeechActivity(_ speaking: Bool) {
        guard !isCancelled else { return }
        speechActivity?(speaking)
    }
}

/// Each delegate belongs to one utterance, so a replaced utterance's delayed
/// cancellation cannot extinguish the next reading's light.
nonisolated final class SpeechPlaybackObserver: NSObject, AVSpeechSynthesizerDelegate {
    private let utteranceID: ObjectIdentifier
    private let request: AudioPlaybackRequest

    init(utterance: AVSpeechUtterance, request: AudioPlaybackRequest) {
        utteranceID = ObjectIdentifier(utterance)
        self.request = request
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didStart utterance: AVSpeechUtterance) {
        if ObjectIdentifier(utterance) == utteranceID { request.reportSpeechActivity(true) }
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        if ObjectIdentifier(utterance) == utteranceID { request.reportSpeechActivity(false) }
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        if ObjectIdentifier(utterance) == utteranceID { request.reportSpeechActivity(false) }
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didPause utterance: AVSpeechUtterance) {
        if ObjectIdentifier(utterance) == utteranceID { request.reportSpeechActivity(false) }
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didContinue utterance: AVSpeechUtterance) {
        if ObjectIdentifier(utterance) == utteranceID { request.reportSpeechActivity(true) }
    }
}

/// One live output engine serves every chime and bundled Pokémon cry. A foreground warmup starts the
/// actual hardware, so the first short cue doesn't depend on speech priming it.
/// Native setup and speech calls stay off the UI and cooperative executors.
nonisolated final class AudioPlaybackWorker: DeviceAudioPlayback, @unchecked Sendable {
    private static let logger = Logger(subsystem: "zihanghuang.iDex", category: "audio")
    private let queue = DispatchQueue(label: "app.idex.audio", qos: .userInitiated)
    private let soundVolume: Float
    private let speechVolume: Float
    private let rendered = AudioRenderProgress()
    private var engine: AVAudioEngine?
    private var player: AVAudioPlayerNode?
    private var buffers: [DeviceSound: AVAudioPCMBuffer] = [:]
    private var currentRequest: AudioPlaybackRequest?
    private var currentCue: DeviceSound?
    private var synthesizer: AVSpeechSynthesizer?
    private var speechObserver: SpeechPlaybackObserver?
    private var voices: [String: AVSpeechSynthesisVoice] = [:]
    /// Speech is rendered offline and played as one buffer, so it needs its own
    /// node: scheduling it on the cue player would cut a cry short.
    private var speechPlayer: AVAudioPlayerNode?
    private var speechRequest: AudioPlaybackRequest?
    /// Playback state of the rendered reading. The synthesizer's own
    /// `isSpeaking` only covers its direct output, which the retro path bypasses.
    private var speechPlaying = false
    private let retroVoice = RetroVoiceProcessor(preset: .classic)

    init(soundVolume: Float = 0.32, speechVolume: Float = 0.9) {
        self.soundVolume = soundVolume
        self.speechVolume = speechVolume
    }

    func activate() async -> Bool {
        await withCheckedContinuation { continuation in
            queue.async {
                let session = AVAudioSession.sharedInstance()
                do {
                    try session.setCategory(.playback, mode: .default, options: [.mixWithOthers])
                } catch {
                    Self.logger.error("Audio configuration failed: \(error.localizedDescription, privacy: .public)")
                    continuation.resume(returning: false)
                    return
                }
                session.activate(options: []) { active, error in
                    self.queue.async {
                        guard active, error == nil else {
                            Self.logger.error("Audio activation failed: \(error?.localizedDescription ?? "not activated", privacy: .public)")
                            continuation.resume(returning: false)
                            return
                        }
                        do {
                            try self.startEngine()
                            self.waitForOutput(until: .now.advanced(by: .seconds(2)), completion: continuation)
                        } catch {
                            Self.logger.error("Audio engine failed: \(error.localizedDescription, privacy: .public)")
                            self.failActivation(continuation)
                        }
                    }
                }
            }
        }
    }

    private func startEngine() throws {
        if engine == nil {
            guard let format = AVAudioFormat(standardFormatWithSampleRate: DeviceSoundWave.sampleRate, channels: 1) else {
                throw PlaybackError.invalidFormat
            }
            let engine = AVAudioEngine()
            let player = AVAudioPlayerNode()
            let chimes = AVAudioMixerNode()
            let chimeGain = AVAudioMixerNode()
            let speech = AVAudioPlayerNode()
            let speechGain = AVAudioMixerNode()
            engine.attach(player)
            engine.attach(chimes)
            engine.attach(chimeGain)
            engine.attach(speech)
            engine.attach(speechGain)
            try engine.connectNode(player, to: chimes, format: format)
            try engine.connectNode(chimes, to: chimeGain, format: format)
            try engine.connectNode(chimeGain, to: engine.mainMixerNode, format: format)
            // The reading joins the graph past the cue tap, so its level stays
            // independent of the chime gain the tests mute.
            try engine.connectNode(speech, to: speechGain, format: format)
            try engine.connectNode(speechGain, to: engine.mainMixerNode, format: format)
            // Observe real rendered PCM before the final gain. Tests mute that
            // gain while still proving a cue reached the live render graph.
            let progress = rendered
            try chimes.installAudioTap(onBus: 0, bufferSize: 256, format: format) { buffer, _ in
                progress.record(buffer)
            }
            chimeGain.outputVolume = soundVolume
            speechGain.outputVolume = speechVolume
            engine.mainMixerNode.outputVolume = 1
            for cue in DeviceSound.allCases {
                let samples = DeviceSoundWave.samples(for: cue)
                guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)),
                      let channel = buffer.floatChannelData?[0] else { throw PlaybackError.invalidFormat }
                buffer.frameLength = AVAudioFrameCount(samples.count)
                samples.withUnsafeBufferPointer { source in
                    if let base = source.baseAddress { channel.update(from: base, count: source.count) }
                }
                buffers[cue] = buffer
            }
            self.engine = engine
            self.player = player
            self.speechPlayer = speech
        }
        guard let engine, let player else { throw PlaybackError.invalidFormat }
        if !engine.isRunning {
            rendered.reset()
            engine.prepare()
            try engine.start()
            // No tone is scheduled during startup: output begins with silence.
            try player.playAudio()
            try speechPlayer?.playAudio()
        }
    }

    private func waitForOutput(until deadline: ContinuousClock.Instant, completion: CheckedContinuation<Bool, Never>) {
        if rendered.snapshot.frames > 0 {
            completion.resume(returning: true)
        } else if ContinuousClock.now >= deadline {
            Self.logger.error("Audio output did not begin rendering")
            failActivation(completion)
        } else {
            queue.asyncAfter(deadline: .now() + .milliseconds(10)) {
                self.waitForOutput(until: deadline, completion: completion)
            }
        }
    }

    private func failActivation(_ continuation: CheckedContinuation<Bool, Never>) {
        player?.stop()
        engine?.stop()
        AVAudioSession.sharedInstance().deactivate(options: [.notifyOthersOnDeactivation]) { _, _ in
            continuation.resume(returning: false)
        }
    }

    func deactivate() async {
        await withCheckedContinuation { continuation in
            queue.async {
                self.currentRequest = nil
                self.currentCue = nil
                self.player?.stop()
                self.engine?.stop()
                AVAudioSession.sharedInstance().deactivate(options: [.notifyOthersOnDeactivation]) { _, error in
                    if let error { Self.logger.error("Audio deactivation failed: \(error.localizedDescription, privacy: .public)") }
                    continuation.resume()
                }
            }
        }
    }

    func play(_ cue: DeviceSound, request: AudioPlaybackRequest) {
        queue.async {
            guard !request.isCancelled else { return }
            let interval = DexPerformance.begin("Audio Chime Start")
            defer { DexPerformance.end("Audio Chime Start", interval) }
            do {
                try self.startEngine()
                guard !request.isCancelled, let player = self.player, let buffer = self.buffers[cue] else { return }
                try self.schedule(buffer, on: player, cue: cue, request: request)
            } catch {
                Self.logger.error("Chime playback failed: \(error.localizedDescription, privacy: .public)")
            }
        }
    }

    func playCry(dexID: Int, request: AudioPlaybackRequest, completion: @escaping @Sendable () -> Void) {
        queue.async {
            guard !request.isCancelled else { return }
            do {
                try self.startEngine()
                guard let player = self.player else { throw PlaybackError.invalidFormat }
                let buffer = try Self.cryBuffer(dexID: dexID)
                guard !request.isCancelled else { return }
                try self.schedule(buffer, on: player, request: request, completion: completion)
            } catch {
                Self.logger.error("Cry playback failed for dex \(dexID): \(error.localizedDescription, privacy: .public)")
                // A missing or unreadable asset must not prevent the entry reading.
                if !request.isCancelled { completion() }
            }
        }
    }

    /// Files are already mono 22,050 Hz PCM. Decode on the audio queue and retain
    /// only the scheduled buffer, without loading the whole catalog into memory.
    static func cryBuffer(dexID: Int, bundle: Bundle = .main) throws -> AVAudioPCMBuffer {
        guard let url = bundle.url(forResource: String(format: "pokemon-cry-%03d", dexID), withExtension: "wav") else {
            throw PlaybackError.missingCry
        }
        let file = try AVAudioFile(forReading: url)
        guard file.processingFormat.sampleRate == DeviceSoundWave.sampleRate,
              file.processingFormat.channelCount == 1,
              file.length > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: AVAudioFrameCount(file.length)) else {
            throw PlaybackError.invalidFormat
        }
        try file.read(into: buffer)
        return buffer
    }

    private func schedule(_ buffer: AVAudioPCMBuffer, on player: AVAudioPlayerNode, cue: DeviceSound? = nil,
                          request: AudioPlaybackRequest, completion: (@Sendable () -> Void)? = nil) throws {
        player.stop() // Discard previous audio; keep the engine running.
        currentRequest = request
        currentCue = cue
        player.scheduleBuffer(buffer, atTime: nil, options: cue == .scan ? .loops : [], completionCallbackType: .dataPlayedBack) { [weak self] in
            guard let self else { return }
            self.queue.async {
                guard self.currentRequest === request else { return }
                self.currentRequest = nil
                self.currentCue = nil
                if !request.isCancelled { completion?() }
            }
        }
        try player.playAudio()
    }

    func stopSound() {
        queue.async {
            self.currentRequest = nil
            self.currentCue = nil
            self.player?.stop()
        }
    }

    func speak(_ text: String, languageCode: String, request: AudioPlaybackRequest) {
        queue.async {
            guard !request.isCancelled else { return }
            let interval = DexPerformance.begin("Audio Speech Startup")
            defer { DexPerformance.end("Audio Speech Startup", interval) }
            let synthesizer: AVSpeechSynthesizer
            if let existing = self.synthesizer {
                synthesizer = existing
                synthesizer.stopSpeaking(at: .immediate)
            } else {
                synthesizer = AVSpeechSynthesizer()
                synthesizer.usesApplicationAudioSession = true
                self.synthesizer = synthesizer
            }
            self.speechPlayer?.stop()
            self.speechRequest = request
            self.speechPlaying = false
            let voice = self.voices[languageCode] ?? Self.narrationVoice(for: languageCode)
            if let voice { self.voices[languageCode] = voice }
            guard !request.isCancelled else { return }
            self.render(text: text, languageCode: languageCode, voice: voice,
                        on: synthesizer, request: request)
        }
    }

    /// Silence ahead of the reading, so it does not tread on the cry that
    /// confirms a scan.
    private static let speechLeadIn = 0.2

    /// A male voice at the smoothest quality the device has installed. The
    /// electronic character is supplied deliberately by `RetroVoiceProcessor`,
    /// so the source should be as fluent as possible rather than as synthetic
    /// as possible: a compact voice only sounds broken once the chain runs.
    private static func narrationVoice(for languageCode: String) -> AVSpeechSynthesisVoice? {
        let available = AVSpeechSynthesisVoice.speechVoices()
        let prefix = languageCode.prefix(2).lowercased()
        let sameLanguage = available.filter { $0.language.lowercased().hasPrefix(prefix) }
        guard !sameLanguage.isEmpty else { return AVSpeechSynthesisVoice(language: languageCode) }
        let exact = sameLanguage.filter { $0.language.caseInsensitiveCompare(languageCode) == .orderedSame }
        let pool = exact.isEmpty ? sameLanguage : exact
        // Fall back to the whole pool where no male voice is installed, rather
        // than going silent on that language.
        let male = pool.filter { $0.gender == .male }
        let candidates = male.isEmpty ? pool : male
        return candidates.max { Self.fluency($0) < Self.fluency($1) }
            ?? AVSpeechSynthesisVoice(language: languageCode)
    }

    private static func fluency(_ voice: AVSpeechSynthesisVoice) -> Int {
        switch voice.quality {
        case .premium: return 3
        case .enhanced: return 2
        default: return 1
        }
    }

    /// Rate sits just above the system default. The effect chain costs some
    /// intelligibility, but a slow reading sounded laboured rather than retro.
    private static func utterance(text: String, languageCode: String,
                                  voice: AVSpeechSynthesisVoice?) -> AVSpeechUtterance {
        let utterance = AVSpeechUtterance(string: text)
        utterance.voice = voice
        utterance.rate = languageCode.hasPrefix("zh") ? 0.52 : 0.54
        // The band-pass already removes the fundamental, so lowering the pitch
        // any further only muddies a male voice.
        utterance.pitchMultiplier = 0.95
        utterance.volume = 1
        return utterance
    }

    /// Synthesis is written to buffers instead of spoken directly: the retro
    /// chain needs the whole reading before it can normalize it, and playing
    /// one finished buffer also removes the stutter of streamed synthesis.
    private func render(text: String, languageCode: String, voice: AVSpeechSynthesisVoice?,
                        on synthesizer: AVSpeechSynthesizer, request: AudioPlaybackRequest) {
        let collector = SpeechSampleCollector()
        synthesizer.write(Self.utterance(text: text, languageCode: languageCode, voice: voice)) { [weak self] buffer in
            guard let self, let pcm = buffer as? AVAudioPCMBuffer else { return }
            guard pcm.frameLength == 0 else {
                collector.append(pcm)
                return
            }
            // A zero-length buffer terminates synthesis for this utterance.
            guard collector.finish() else { return }
            let rendered = collector.drain()
            self.queue.async {
                self.playRendered(rendered.samples, sourceRate: rendered.rate, text: text,
                                  languageCode: languageCode, voice: voice,
                                  on: synthesizer, request: request)
            }
        }
    }

    private func playRendered(_ samples: [Float], sourceRate: Double, text: String,
                              languageCode: String, voice: AVSpeechSynthesisVoice?,
                              on synthesizer: AVSpeechSynthesizer, request: AudioPlaybackRequest) {
        guard !request.isCancelled, speechRequest === request else { return }
        guard !samples.isEmpty else {
            // Nothing was rendered. Read the entry plainly rather than drop it.
            Self.logger.error("Speech rendering produced no samples; reading without the effect chain")
            speakDirectly(text: text, languageCode: languageCode, voice: voice,
                          on: synthesizer, request: request)
            return
        }
        do {
            try startEngine()
            guard let speechPlayer,
                  let format = AVAudioFormat(standardFormatWithSampleRate: DeviceSoundWave.sampleRate, channels: 1)
            else { throw PlaybackError.invalidFormat }
            var voiced = RetroVoiceProcessor.resample(samples, from: sourceRate, to: DeviceSoundWave.sampleRate)
            retroVoice.process(&voiced, sampleRate: DeviceSoundWave.sampleRate)
            var frames = [Float](repeating: 0, count: Int(Self.speechLeadIn * DeviceSoundWave.sampleRate))
            frames.append(contentsOf: voiced)
            guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames.count)),
                  let channel = buffer.floatChannelData?[0] else { throw PlaybackError.invalidFormat }
            buffer.frameLength = AVAudioFrameCount(frames.count)
            frames.withUnsafeBufferPointer { source in
                if let base = source.baseAddress { channel.update(from: base, count: source.count) }
            }
            guard !request.isCancelled else { return }
            speechPlayer.stop()
            speechPlaying = true
            request.reportSpeechActivity(true)
            speechPlayer.scheduleBuffer(buffer, atTime: nil, options: [], completionCallbackType: .dataPlayedBack) { [weak self] in
                guard let self else { return }
                self.queue.async {
                    guard self.speechRequest === request else { return }
                    self.speechRequest = nil
                    self.speechPlaying = false
                    request.reportSpeechActivity(false)
                }
            }
            try speechPlayer.playAudio()
        } catch {
            Self.logger.error("Speech playback failed: \(error.localizedDescription, privacy: .public)")
            speechPlaying = false
            request.reportSpeechActivity(false)
        }
    }

    /// An utterance consumed by `write` cannot be spoken again, so the fallback
    /// builds its own.
    private func speakDirectly(text: String, languageCode: String, voice: AVSpeechSynthesisVoice?,
                               on synthesizer: AVSpeechSynthesizer, request: AudioPlaybackRequest) {
        guard !request.isCancelled else { return }
        let utterance = Self.utterance(text: text, languageCode: languageCode, voice: voice)
        utterance.volume = speechVolume
        utterance.preUtteranceDelay = Self.speechLeadIn
        let observer = SpeechPlaybackObserver(utterance: utterance, request: request)
        speechObserver = observer
        synthesizer.delegate = observer
        synthesizer.speak(utterance)
    }

    func stopSpeaking() {
        queue.async {
            self.speechRequest = nil
            self.speechPlaying = false
            self.speechPlayer?.stop()
            self.synthesizer?.stopSpeaking(at: .immediate)
        }
    }

    /// A nonblocking snapshot also waits for previously enqueued commands.
    func status() async -> Status {
        await withCheckedContinuation { continuation in
            queue.async {
                let progress = self.rendered.snapshot
                continuation.resume(returning: Status(
                    cachedSoundCount: self.buffers.count,
                    hasSpeechSynthesizer: self.synthesizer != nil,
                    soundIsPlaying: self.currentRequest != nil && (self.player?.isPlaying ?? false),
                    speechIsSpeaking: self.speechPlaying || (self.synthesizer?.isSpeaking ?? false),
                    renderedFrames: progress.frames,
                    renderedSignalFrames: progress.signalFrames,
                    outputPorts: AVAudioSession.sharedInstance().currentRoute.outputs.map { $0.portType.rawValue },
                    isOnMainThread: Thread.isMainThread,
                    hasSwiftTask: withUnsafeCurrentTask { $0 != nil }
                ))
            }
        }
    }

    private enum PlaybackError: Error { case invalidFormat, missingCry }

    nonisolated struct Status: Sendable {
        let cachedSoundCount: Int
        let hasSpeechSynthesizer: Bool
        let soundIsPlaying: Bool
        let speechIsSpeaking: Bool
        let renderedFrames: Int
        let renderedSignalFrames: Int
        let outputPorts: [String]
        let isOnMainThread: Bool
        let hasSwiftTask: Bool
    }
}

/// Collects the buffers `AVSpeechSynthesizer.write` delivers. The callback
/// arrives on the synthesizer's own queue, so the samples are guarded and the
/// terminator is latched: a repeated zero-length buffer must not schedule the
/// same reading twice.
nonisolated private final class SpeechSampleCollector: @unchecked Sendable {
    private let lock = NSLock()
    private var samples: [Float] = []
    private var rate = DeviceSoundWave.sampleRate
    private var finished = false

    /// True only for the first caller.
    func finish() -> Bool {
        lock.withLock {
            if finished { return false }
            finished = true
            return true
        }
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        let frames = Int(buffer.frameLength)
        guard frames > 0 else { return }
        var chunk = [Float](repeating: 0, count: frames)
        if let channel = buffer.floatChannelData?[0] {
            for index in 0..<frames { chunk[index] = channel[index] }
        } else if let channel = buffer.int16ChannelData?[0] {
            for index in 0..<frames { chunk[index] = Float(channel[index]) / 32_768 }
        } else {
            return
        }
        lock.withLock {
            guard !finished else { return }
            rate = buffer.format.sampleRate
            samples.append(contentsOf: chunk)
        }
    }

    func drain() -> (samples: [Float], rate: Double) { lock.withLock { (samples, rate) } }
}

/// A small tap on the chime mixer confirms hardware output is running. This
/// observes only app playback; it never records microphone input.
nonisolated private final class AudioRenderProgress: @unchecked Sendable {
    private let lock = NSLock()
    private var frames = 0
    private var signalFrames = 0

    func record(_ buffer: AVReadOnlyAudioPCMBuffer) {
        let count = buffer.frameLength
        var hasSignal = false
        if case .float(let channel) = buffer.channelData(0) {
            for index in 0..<count where abs(channel[index]) > 0.0001 {
                hasSignal = true
                break
            }
        }
        lock.withLock {
            frames += count
            if hasSignal { signalFrames += count }
        }
    }

    func reset() { lock.withLock { frames = 0; signalFrames = 0 } }
    var snapshot: (frames: Int, signalFrames: Int) { lock.withLock { (frames, signalFrames) } }
}
