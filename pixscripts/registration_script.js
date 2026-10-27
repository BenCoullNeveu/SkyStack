/*
    Registration Script for PixInsight CLI.
    Reads configuration from JSON files:
    - input_files.json: array of calibrated light paths
    - params.json: { reference: "...", output_dir: "...", postfix: "_r",
                     distortion_correction: true }
    Writes C:/Temp/PixStack/registration_complete.tmp containing "done" or
    "error: <message>".
*/

function readJson(path) {
    if (!File.exists(path)) {
        throw new Error("Missing JSON file: " + path);
    }
    return JSON.parse(File.readTextFile(path));
}

function writeSignal(text) {
    var scriptDir = "C:/Temp/PixStack";
    if (!File.directoryExists(scriptDir)) {
        File.createDirectory(scriptDir);
    }
    let file = new File();
    file.createForWriting(scriptDir + "/registration_complete.tmp");
    file.outTextLn(text);
    file.close();
}

function main() {
    Console.writeln("== Registration Script for PixInsight CLI ==");

    let configDir = "C:/Temp/PixStack/";
    let inputFiles = readJson(configDir + "input_files.json");
    let params = readJson(configDir + "params.json");

    if (!inputFiles || inputFiles.length < 1) {
        throw new Error("No input files to register.");
    }
    if (!File.exists(params.reference)) {
        throw new Error("Reference image not found: " + params.reference);
    }

    Console.writeln("Registering " + inputFiles.length + " frames to " + params.reference);

    var P = new StarAlignment;
    P.referenceImage = params.reference;
    P.referenceIsFile = true;
    P.targets = inputFiles.map(function (f) {
        return [true, true, f]; // enabled, isFile, image
    });
    P.mode = StarAlignment.prototype.RegisterMatch;
    P.distortionCorrection = params.distortion_correction !== false;
    P.generateDrizzleData = false;
    P.pixelInterpolation = StarAlignment.prototype.Auto;
    P.outputDirectory = params.output_dir;
    P.outputExtension = ".xisf";
    P.outputPrefix = "";
    P.outputPostfix = params.postfix;
    P.overwriteExistingFiles = true;
    P.onError = StarAlignment.prototype.Continue;
    P.noGUIMessages = true;

    P.executeGlobal();
    Console.writeln(">> StarAlignment completed.");
}

try {
    main();
    writeSignal("done");
} catch (error) {
    Console.criticalln("Registration failed: " + error.message);
    writeSignal("error: " + error.message);
}
