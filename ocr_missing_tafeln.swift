#!/usr/bin/env swift
// ocr_missing_tafeln.swift
// OCR the tafeln PNGs that have no matching text_vision file.
// Saves text to rossoschka_text_vision/<stem>.txt

import Vision
import AppKit
import Foundation

let tafelnDir  = "rossoschka_tafeln"
let textDir    = "rossoschka_text_vision"
let langs      = ["de-DE", "en-US"]

let fm  = FileManager.default
let cwd = URL(fileURLWithPath: fm.currentDirectoryPath)
let tDir = cwd.appendingPathComponent(tafelnDir)
let xDir = cwd.appendingPathComponent(textDir)

// --- Build set of stems already in text_vision (to avoid re-OCR) ---
let existingTxt = Set(
    (try? fm.contentsOfDirectory(atPath: xDir.path))?.map {
        URL(fileURLWithPath: $0).deletingPathExtension().lastPathComponent
    } ?? []
)

// --- Find tafeln PNGs whose stem has no matching text file ---
let tafelnFiles = (try? fm.contentsOfDirectory(at: tDir, includingPropertiesForKeys: nil))?
    .filter { $0.pathExtension.lowercased() == "png" }
    .filter { !existingTxt.contains($0.deletingPathExtension().lastPathComponent) }
    .sorted { $0.lastPathComponent < $1.lastPathComponent }
    ?? []

print("Found \(tafelnFiles.count) tafeln PNGs without text files. Running Vision OCR…\n")

func ocrText(_ cgImage: CGImage) -> String {
    var result = ""
    let sem = DispatchSemaphore(value: 0)
    let req = VNRecognizeTextRequest { req, _ in
        defer { sem.signal() }
        guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
        result = obs.compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    }
    req.recognitionLevel     = .accurate
    req.recognitionLanguages = langs
    req.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try? handler.perform([req])
    sem.wait()
    return result
}

var ok = 0; var failed = 0
for (i, url) in tafelnFiles.enumerated() {
    let stem = url.deletingPathExtension().lastPathComponent
    let dest = xDir.appendingPathComponent(stem + ".txt")

    guard let nsImg = NSImage(contentsOf: url),
          let cgImg = nsImg.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("  [\(i+1)/\(tafelnFiles.count)] ERR load: \(url.lastPathComponent)")
        failed += 1; continue
    }

    let text = ocrText(cgImg)
    do {
        try text.write(to: dest, atomically: true, encoding: .utf8)
        let preview = text.components(separatedBy: "\n").first(where: { !$0.isEmpty }) ?? "(empty)"
        print("  [\(i+1)/\(tafelnFiles.count)] \(url.lastPathComponent)")
        print("         → \(preview.prefix(80))")
        ok += 1
    } catch {
        print("  [\(i+1)/\(tafelnFiles.count)] ERR write \(stem): \(error)")
        failed += 1
    }
}
print("\nDone. \(ok) OCR'd, \(failed) failed.")
