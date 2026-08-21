#!/usr/bin/env swift
// ocr_and_rename_tafeln.swift
// For each PNG in rossoschka_tafeln/ that has no surname label (no uppercase letters),
// OCR it with Apple Vision, extract first/last surname, and rename in place.

import Vision
import AppKit
import Foundation

let tafelnDir = "rossoschka_tafeln"
let langs     = ["de-DE", "en-US"]

let fm  = FileManager.default
let cwd = URL(fileURLWithPath: fm.currentDirectoryPath)
let dir = cwd.appendingPathComponent(tafelnDir)

guard fm.fileExists(atPath: dir.path) else {
    print("Directory '\(tafelnDir)' not found."); exit(1)
}

// Files needing surnames: those with no uppercase letter in the stem
let allFiles = try! fm.contentsOfDirectory(at: dir, includingPropertiesForKeys: nil)
    .filter { $0.pathExtension.lowercased() == "png" }
    .filter { url in
        let stem = url.deletingPathExtension().lastPathComponent
        return !stem.contains(where: { $0.isUppercase })
    }
    .sorted { $0.lastPathComponent < $1.lastPathComponent }

print("Found \(allFiles.count) files without surname labels. Running Vision OCR…\n")

// --- OCR helper ---
func ocrText(_ cgImage: CGImage) -> String {
    var result = ""
    let sem = DispatchSemaphore(value: 0)
    let req = VNRecognizeTextRequest { req, _ in
        defer { sem.signal() }
        guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
        result = obs.compactMap { $0.topCandidates(1).first?.string }.joined(separator: " ")
    }
    req.recognitionLevel       = .accurate
    req.recognitionLanguages   = langs
    req.usesLanguageCorrection = true
    let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
    try? handler.perform([req])
    sem.wait()
    return result
}

// --- Surname extraction ---
// A surname is the uppercase word immediately before a date token.
let datePat  = try! NSRegularExpression(pattern: #"\b\d{1,2}[.\-]\d{1,2}[.\-]\d{2,4}\b|\b(?:19|20)\d{2}\b"#)
let namePat  = try! NSRegularExpression(pattern: #"^[A-ZÄÖÜÉ][A-ZÄÖÜÉSS\-]{1,}$"#)
let ignoredWords: Set<String> = ["DR","PROF","VON","VAN","DER","DIE","DEM","UND","IM","IN","AM","AN","AUF","II","III","IV"]

func extractSurnames(_ text: String) -> [String] {
    let ns    = text as NSString
    let range = NSRange(location: 0, length: ns.length)
    // tokenize on whitespace / punctuation
    var tokens = text.components(separatedBy: CharacterSet.whitespacesAndNewlines
        .union(.init(charactersIn: ",;:/\\|_()")))
        .map { $0.trimmingCharacters(in: CharacterSet(charactersIn: ".+*'\"!?")) }
        .filter { !$0.isEmpty }

    var surnames: [String] = []
    for (i, tok) in tokens.enumerated() {
        let r = NSRange(tok.startIndex..., in: tok)
        guard datePat.firstMatch(in: tok, range: NSRange(tok.startIndex..., in: tok)) != nil else { continue }
        // search backwards for a name token
        for j in stride(from: i-1, through: max(i-4, 0), by: -1) {
            let prev = tokens[j]
            if prev.isEmpty { continue }
            let pr = NSRange(prev.startIndex..., in: prev)
            if namePat.firstMatch(in: prev, range: pr) != nil && !ignoredWords.contains(prev) && prev.count > 1 {
                surnames.append(prev)
                break
            }
        }
    }
    return surnames
}

// --- Process each file ---
var renamed = 0; var failed = 0

for (i, url) in allFiles.enumerated() {
    let name = url.lastPathComponent
    guard let nsImg  = NSImage(contentsOf: url),
          let cgImg  = nsImg.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
        print("  [\(i+1)/\(allFiles.count)] ERR load: \(name)"); failed += 1; continue
    }

    let text     = ocrText(cgImg)
    let surnames = extractSurnames(text)

    guard !surnames.isEmpty else {
        print("  [\(i+1)/\(allFiles.count)] NO NAMES: \(name)")
        failed += 1; continue
    }

    let first = surnames.first!
    let last  = surnames.last!
    let label = first == last ? first : "\(first)-\(last)"

    let stem    = url.deletingPathExtension().lastPathComponent
    let newName = "\(stem)-\(label).png"
    let dest    = dir.appendingPathComponent(newName)

    if fm.fileExists(atPath: dest.path) {
        print("  [\(i+1)/\(allFiles.count)] EXISTS \(name) → \(newName)")
        continue
    }

    do {
        try fm.moveItem(at: url, to: dest)
        print("  [\(i+1)/\(allFiles.count)] \(name)")
        print("         → \(newName)")
        renamed += 1
    } catch {
        print("  [\(i+1)/\(allFiles.count)] ERR rename \(name): \(error)")
        failed += 1
    }
}

print("\nDone. Renamed: \(renamed), Failed/no names: \(failed)")
