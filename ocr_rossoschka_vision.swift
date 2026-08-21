#!/usr/bin/env swift
// ocr_rossoschka_vision.swift
// Uses macOS Vision framework (same OCR engine as Preview.app) to extract
// text from all PNGs in rossoschka_final/ and save to rossoschka_text_vision/

import Vision
import AppKit
import Foundation

let srcDir  = "rossoschka_tafeln"
let destDir = "rossoschka_tafeln_text"
let langs   = ["de-DE", "en-US"]  // German primary, English fallback

// --- Setup ---
let fm = FileManager.default
let cwd = URL(fileURLWithPath: fm.currentDirectoryPath)
let src  = cwd.appendingPathComponent(srcDir)
let dest = cwd.appendingPathComponent(destDir)

guard fm.fileExists(atPath: src.path) else {
    print("Source directory '\(srcDir)' not found."); exit(1)
}
try! fm.createDirectory(at: dest, withIntermediateDirectories: true)

// --- Enumerate PNGs ---
let files = try! fm.contentsOfDirectory(at: src, includingPropertiesForKeys: nil)
    .filter { $0.pathExtension.lowercased() == "png" }
    .sorted { $0.lastPathComponent < $1.lastPathComponent }

print("Extracting text from \(files.count) images using Vision framework...\n")

var ok = 0, skipped = 0, errors = 0

for (i, imageURL) in files.enumerated() {
    let name     = imageURL.deletingPathExtension().lastPathComponent
    let destFile = dest.appendingPathComponent(name + ".txt")

    guard !fm.fileExists(atPath: destFile.path) else { skipped += 1; continue }

    guard let nsImage = NSImage(contentsOf: imageURL),
          let cgImage = nsImage.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("  [\(i+1)/\(files.count)] ERR \(imageURL.lastPathComponent): could not load")
        errors += 1; continue
    }

    // Synchronous OCR via semaphore
    let semaphore = DispatchSemaphore(value: 0)
    var resultText = ""
    var ocrError: Error?

    let request = VNRecognizeTextRequest { req, err in
        defer { semaphore.signal() }
        if let err { ocrError = err; return }
        guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
        resultText = obs.compactMap { $0.topCandidates(1).first?.string }
                        .joined(separator: "\n")
    }
    request.recognitionLevel        = .accurate
    request.recognitionLanguages    = langs
    request.usesLanguageCorrection  = true

    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    do {
        try handler.perform([request])
    } catch {
        ocrError = error
        semaphore.signal()
    }
    semaphore.wait()

    if let err = ocrError {
        print("  [\(i+1)/\(files.count)] ERR \(imageURL.lastPathComponent): \(err)")
        errors += 1; continue
    }

    do {
        try resultText.write(to: destFile, atomically: true, encoding: .utf8)
        let preview = resultText.components(separatedBy: "\n").first(where: { !$0.isEmpty }) ?? "(no text)"
        print("  [\(String(format: "%3d", i+1))/\(files.count)] \(imageURL.lastPathComponent)")
        print("          → \(preview.prefix(80))")
        ok += 1
    } catch {
        print("  [\(i+1)/\(files.count)] ERR writing \(destFile.lastPathComponent): \(error)")
        errors += 1
    }
}

print("\nDone. \(ok) extracted, \(skipped) skipped (already exist), \(errors) errors.")
print("Text files in ./\(destDir)/")
