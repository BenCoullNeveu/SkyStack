/*
    Subframe measurement script for PixInsight CLI (SubframeSelector, measure only).
    Reads configuration from JSON files:
    - input_files.json: array of image paths to measure
    - params.json: { result_path: "...", subframe_scale: 1.0, camera_gain: 1.0 }
    Writes params.result_path: [{ path, fwhm, eccentricity, ... }] (one entry per
    measured frame; frames SubframeSelector could not measure are left out), then
    C:/Temp/PixStack/measure_complete.tmp containing "done" or "error: <message>".
*/

// Measurement table columns. Fixed indexes that must match the process
// implementation -- these are the ones WBPP (BPP-SubframeAnalyzer.js) uses.
var COLUMNS = {
    path: 3,
    weight: 4,
    fwhm: 5,
    eccentricity: 6,
    psf_signal_weight: 7,
    psf_power: 8,
    snr: 9,
    median: 10,
    mad: 11,
    noise: 12,
    stars: 14,
    mstar: 26,
    psf_snr: 28
};

function readJson(path) {
    if (!File.exists(path)) {
        throw new Error("Missing JSON file: " + path);
    }
    return JSON.parse(File.readTextFile(path));
}

function writeText(path, text) {
    let file = new File();
    file.createForWriting(path);
    file.outTextLn(text);
    file.close();
}

function writeSignal(text) {
    var scriptDir = "C:/Temp/PixStack";
    if (!File.directoryExists(scriptDir)) {
        File.createDirectory(scriptDir);
    }
    writeText(scriptDir + "/measure_complete.tmp", text);
}

// Process enumeration constants live on the prototype in plain PJSR
// (WBPP's bare SubframeSelector.X names aren't available outside it).
function ssEnum(name) {
    let value = SubframeSelector.prototype[name];
    if (value === undefined) {
        value = SubframeSelector[name];
    }
    if (value === undefined) {
        throw new Error("SubframeSelector has no constant '" + name + "' in this PixInsight version.");
    }
    return value;
}

function main() {
    Console.writeln("== Subframe Measurement Script for PixInsight CLI ==");

    let configDir = "C:/Temp/PixStack/";
    let inputFiles = readJson(configDir + "input_files.json");
    let params = readJson(configDir + "params.json");

    if (!inputFiles || inputFiles.length < 1) {
        throw new Error("No input files to measure.");
    }
    Console.writeln("Measuring " + inputFiles.length + " frames");

    var SS = new SubframeSelector;
    SS.routine = ssEnum("MeasureSubframes");
    SS.nonInteractive = true;
    SS.fileCache = false;
    SS.noNoiseAndSignalWarnings = true;
    SS.subframeScale = params.subframe_scale || 1.0;   // 1.0 => FWHM in pixels
    SS.cameraGain = params.camera_gain || 1.0;
    SS.cameraResolution = ssEnum("Bits16");
    SS.dataUnit = ssEnum("DataNumber");
    SS.subframes = inputFiles.map(function (f) {
        return [true, f, "", ""]; // enabled, path, localNormalizationDataPath, drizzlePath
    });

    if (!SS.executeGlobal()) {
        throw new Error("SubframeSelector failed");
    }

    let results = [];
    for (let i = 0; i < SS.measurements.length; ++i) {
        let row = SS.measurements[i];
        let entry = {};
        for (let key in COLUMNS) {
            entry[key] = row[COLUMNS[key]];
        }
        if (typeof entry.path !== "string") {
            throw new Error("Unexpected SubframeSelector measurement layout (column " +
                            COLUMNS.path + " is not a file path) -- check COLUMNS against this PixInsight version.");
        }
        results.push(entry);
    }
    writeText(params.result_path, JSON.stringify(results));
    Console.writeln(">> Measured " + results.length + " of " + inputFiles.length + " frames.");
}

try {
    main();
    writeSignal("done");
} catch (error) {
    Console.criticalln("Measurement failed: " + error.message);
    writeSignal("error: " + error.message);
}
