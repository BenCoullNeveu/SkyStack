/*
    Post-processing script for PixInsight CLI.

    Applies a list of processes, in order, to each image and saves the result.
    Reads C:/Temp/PixStack/params.json:
    {
      "items": [ { "input": ".../masterLight_X.xisf", "output": ".../processed/masterLight_X_bxtc.xisf" } ],
      "steps": [ { "name": "bxt_correct", "process": "BlurXTerminator", "params": { ... } } ]
    }
    Writes C:/Temp/PixStack/postprocess_result.json with per-image status, then
    C:/Temp/PixStack/postprocess_complete.tmp ("done" or "error: ...").
*/

// Processes this script may instantiate. Each entry checks the process is
// installed (third-party ones like BlurXTerminator may not be).
var FACTORIES = {
    BlurXTerminator: function () {
        if (typeof BlurXTerminator === "undefined") {
            throw new Error("BlurXTerminator is not installed in this PixInsight.");
        }
        return new BlurXTerminator;
    }
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

function ensureDir(dir) {
    if (!File.directoryExists(dir)) {
        File.createDirectory(dir, true);
    }
}

function makeProcess(step) {
    let factory = FACTORIES[step.process];
    if (!factory) {
        throw new Error("Unsupported post-processing process: " + step.process);
    }
    let P = factory();
    for (let key in step.params) {
        if (P[key] === undefined) {
            Console.warningln(step.process + " has no parameter '" + key + "'; setting it anyway.");
        }
        P[key] = step.params[key];
    }
    return P;
}

function processOne(item, steps) {
    let windows = ImageWindow.open(item.input);
    if (!windows || windows.length < 1 || windows[0].isNull) {
        throw new Error("Could not open " + item.input);
    }
    let win = windows[0];
    try {
        for (let i = 0; i < steps.length; ++i) {
            Console.writeln("  " + steps[i].name + " (" + steps[i].process + ")");
            if (!makeProcess(steps[i]).executeOn(win.mainView)) {
                throw new Error(steps[i].process + " failed");
            }
        }
        ensureDir(File.extractDrive(item.output) + File.extractDirectory(item.output));
        if (!win.saveAs(item.output, false, false, false, false)) {
            throw new Error("Could not save " + item.output);
        }
    } finally {
        win.forceClose();
    }
}

function main() {
    Console.writeln("== Post-processing Script for PixInsight CLI ==");
    let configDir = "C:/Temp/PixStack/";
    let params = readJson(configDir + "params.json");
    if (!params.items || params.items.length < 1) {
        throw new Error("No images to process.");
    }
    if (!params.steps || params.steps.length < 1) {
        throw new Error("No post-processing steps given.");
    }
    // fail fast on a missing process instead of once per image
    params.steps.forEach(makeProcess);

    let results = [];
    for (let i = 0; i < params.items.length; ++i) {
        let item = params.items[i];
        Console.writeln("\n>> " + item.input);
        try {
            processOne(item, params.steps);
            results.push({ input: item.input, status: "ok", output: item.output });
        } catch (error) {
            Console.criticalln("Post-processing failed for " + item.input + ": " + error.message);
            results.push({ input: item.input, status: "error", error: error.message });
        }
    }
    writeText(configDir + "postprocess_result.json", JSON.stringify(results, null, 2));
}

try {
    main();
    writeText("C:/Temp/PixStack/postprocess_complete.tmp", "done");
} catch (error) {
    Console.criticalln("Post-processing failed: " + error.message);
    writeText("C:/Temp/PixStack/postprocess_complete.tmp", "error: " + error.message);
}
