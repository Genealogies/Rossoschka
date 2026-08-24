#!/usr/bin/env swift
// get_word_boxes.swift
// Return Vision OCR bounding boxes for a PNG as JSON.
// Usage: swift get_word_boxes.swift <image_path>
// Output: JSON array of {text, x, y, w, h, conf}
//   Coordinates are normalised [0..1], top-left origin.

import Vision
import AppKit
import Foundation

guard CommandLine.arguments.count > 1 else {
    print("[]"); exit(1)
}

let imagePath = CommandLine.arguments[1]

guard let nsImg = NSImage(contentsOfFile: imagePath),
      let cgImg = nsImg.cgImage(forProposedRect: nil, context: nil, hints: nil) else {
    fputs("Cannot load: \(imagePath)\n", stderr)
    print("[]"); exit(1)
}

var boxes: [[String: Any]] = []
let sem = DispatchSemaphore(value: 0)

let req = VNRecognizeTextRequest { req, _ in
    defer { sem.signal() }
    guard let obs = req.results as? [VNRecognizedTextObservation] else { return }
    for ob in obs {
        guard let top = ob.topCandidates(1).first else { continue }
        let bb = ob.boundingBox          // normalised, bottom-left origin
        boxes.append([
            "text": top.string,
            "x":    Double(bb.minX),
            "y":    Double(1.0 - bb.maxY),   // flip to top-left origin
            "w":    Double(bb.width),
            "h":    Double(bb.height),
            "conf": Double(top.confidence)
        ])
    }
}
req.recognitionLevel       = .accurate
req.recognitionLanguages   = ["de-DE", "en-US"]
req.usesLanguageCorrection = true

let handler = VNImageRequestHandler(cgImage: cgImg, options: [:])
try? handler.perform([req])
sem.wait()

if let data = try? JSONSerialization.data(withJSONObject: boxes),
   let str  = String(data: data, encoding: .utf8) {
    print(str)
} else {
    print("[]")
}
