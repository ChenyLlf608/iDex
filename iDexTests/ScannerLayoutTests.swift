import SwiftUI
import Testing
import UIKit
@testable import iDex

@MainActor
@Suite(.serialized)
struct ScannerLayoutTests {
    @Test func scanningCyclesThroughThreeLightsAndStopsForSpeech() async throws {
        let scene = try #require(UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.first)
        let previousWindow = scene.windows.first { $0.isKeyWindow }
        let window = UIWindow(windowScene: scene)
        let host = UIHostingController(rootView: ScannerLights(isScanning: true, isSpeaking: false)
            .padding(24).background(DexTheme.red))
        window.rootViewController = host
        window.makeKeyAndVisible()
        defer {
            window.isHidden = true
            previousWindow?.makeKey()
        }
        host.view.layoutIfNeeded()
        try await Task.sleep(for: .milliseconds(100))

        func snapshot() -> UIImage {
            UIGraphicsImageRenderer(bounds: host.view.bounds).image { _ in
                host.view.drawHierarchy(in: host.view.bounds, afterScreenUpdates: true)
            }
        }

        var frames = Set<Data>()
        for _ in 0..<20 {
            let image = snapshot()
            if frames.insert(try #require(image.pngData())).inserted {
                Attachment.record(image, named: "scanner-chase-\(frames.count)", as: .png)
            }
            if frames.count >= 3 { break }
            try await Task.sleep(for: .milliseconds(50))
        }
        #expect(frames.count >= 3)

        host.rootView = ScannerLights(isScanning: true, isSpeaking: true)
            .padding(24).background(DexTheme.red)
        try await Task.sleep(for: .milliseconds(100))
        let speaking = snapshot()
        try await Task.sleep(for: .milliseconds(300))
        #expect(snapshot().pngData() == speaking.pngData())
        Attachment.record(speaking, named: "scanner-chase-stopped-for-speech", as: .png)
    }

    @Test func scannerLightsDistinguishIdleScanningAndSpeech() throws {
        for (name, scanning, speaking) in [("idle", false, false), ("scanning", true, false),
                                           ("speaking", false, true), ("speech-overrides-scan", true, true)] {
            let renderer = ImageRenderer(content: ScannerLights(isScanning: scanning, isSpeaking: speaking)
                .padding(24).background(DexTheme.red))
            renderer.scale = 3
            let image = try #require(renderer.uiImage)
            Attachment.record(image, named: "scanner-lights-\(name)", as: .png)
        }
    }

    @Test(arguments: AppLanguage.allCases)
    func scannerStatesFitCoverAndExpandedLayouts(language: AppLanguage) throws {
        let savedLanguage = UserDefaults.standard.object(forKey: "idex.language")
        let savedViewed = UserDefaults.standard.object(forKey: "idex.lastViewed")
        let soundWasEnabled = DeviceAudio.shared.isSoundEnabled
        DeviceAudio.shared.isSoundEnabled = false
        defer {
            DeviceAudio.shared.isSoundEnabled = soundWasEnabled
            UserDefaults.standard.set(savedLanguage, forKey: "idex.language")
            UserDefaults.standard.set(savedViewed, forKey: "idex.lastViewed")
        }
        let store = DexStore()
        store.language = language
        store.useSample = true
        store.capturedImage = UIImage(named: "pokemon-025")
        let candidate = RecognitionCandidate(dexID: 25, name: "pikachu", confidence: 0.1, logit: 1)

        for size in [CGSize(width: 466, height: 678), CGSize(width: 951, height: 669)] {
            for scanning in [true, false] {
                if !scanning {
                    try store.applyRecognition(RecognitionResult(candidates: [candidate], accepted: nil, logitMargin: 0))
                }
                store.isScanning = scanning
                let renderer = ImageRenderer(content: ContentView(store: store)
                    .frame(width: size.width, height: size.height))
                renderer.scale = 2
                let image = try #require(renderer.uiImage)
                #expect(image.size == size)
                let posture = size.width > 850 ? "expanded" : "cover"
                let state = scanning ? "scanning" : "best-match"
                Attachment.record(image, named: "scanner-\(posture)-\(state)-\(language.rawValue)", as: .png)
            }
            store.rescan()
            store.capturedImage = UIImage(named: "pokemon-025")
        }
        store.suspend()
    }

    @Test(arguments: AppLanguage.allCases)
    func recordsFitCoverAndExpandedLayouts(language: AppLanguage) throws {
        let savedLanguage = UserDefaults.standard.object(forKey: "idex.language")
        let savedViewed = UserDefaults.standard.object(forKey: "idex.lastViewed")
        let soundWasEnabled = DeviceAudio.shared.isSoundEnabled
        DeviceAudio.shared.isSoundEnabled = false
        defer {
            DeviceAudio.shared.isSoundEnabled = soundWasEnabled
            UserDefaults.standard.set(savedLanguage, forKey: "idex.language")
            UserDefaults.standard.set(savedViewed, forKey: "idex.lastViewed")
        }
        let store = DexStore()
        store.language = language
        // A long name and two type badges exercise the compact entry column.
        store.selected = PokemonCatalog.shared.pokemon(id: 71)
        for size in [CGSize(width: 466, height: 678), CGSize(width: 951, height: 669)] {
            let expanded = size.width > 850
            let renderer = ImageRenderer(content: Group {
                if expanded {
                    ContentView(store: store)
                } else {
                    CompactRecordView(store: store)
                }
            }.frame(width: size.width, height: size.height)
                .containerShape(UnevenRoundedRectangle(
                    topLeadingRadius: expanded ? 55 : 8,
                    bottomLeadingRadius: expanded ? 55 : 8,
                    bottomTrailingRadius: expanded ? 55 : 59,
                    topTrailingRadius: expanded ? 55 : 59
                )))
            renderer.scale = 2
            let image = try #require(renderer.uiImage)
            #expect(image.size == size)
            Attachment.record(image, named: "record-\(expanded ? "expanded" : "cover")-\(language.rawValue)", as: .png)
        }
        store.suspend()
    }
}
