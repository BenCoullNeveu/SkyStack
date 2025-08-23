/*
    Basic stacking script for PixInsight CLI.
    Now reads configuration from JSON files:
    - input_files.json: array of input file paths
    - params.json: { output_path: "..." }
*/

function readJson(path) {
    if (!File.exists(path)) {
        throw new Error("Missing JSON file: " + path);
    }
    let text = File.readTextFile(path);
    return JSON.parse(text);
}

function main() {
    Console.writeln("== Basic Stacking Script for PixInsight CLI ==");

    let configDir = "C:/Temp/PixStack/";
    let inputFilePath = configDir + "input_files.json";
    let paramFilePath = configDir + "params.json";

    // Load input files and output path
    let inputFiles = readJson(inputFilePath);
    let params = readJson(paramFilePath);

    if (!inputFiles || inputFiles.length < 2) {
        Console.criticalln("Error: Need at least two input files.");
        exit(1);
    }

    let outputPath = params.output_path;

    // Console.writeln("Input files:");
    // inputFiles.forEach(f => Console.writeln(" - " + f));

    // Set up ImageIntegration
    let P = new ImageIntegration;

    P.images = inputFiles.map(function (f) {
        return [
            true, // enabled
            f, // path
            "", // drizzlePath
            "" // localNormalizationDataPath
        ];
    });

    // Set parameters
    P.combination = ImageIntegration.prototype.Average;
    P.normalization = ImageIntegration.prototype.AdditiveWithScaling;
    P.rejection = ImageIntegration.prototype.WinsorizedSigmaClip;
    P.rejectionNormalization = ImageIntegration.prototype.Scale;
    P.weightMode = ImageIntegration.prototype.DontCare;
    P.evaluateSNR = false;
    

    // Set rejection parameters
    P.sigmaLow = 4.0;
    P.sigmaHigh = 3.0;

    // Set other parameters
    P.generateIntegratedImage = true;
    P.generateRejectionMaps = false;
    P.generate64BitResult = true;
    P.autoMemorySize = true;
    P.noGUIMessages = true;
    P.showImages = true;

    Console.writeln(">> Running ImageIntegration...");
    P.executeGlobal();

    let win = ImageWindow.activeWindow;
    if (!win.isNull) {
        let base = outputPath.toLowerCase().endsWith(".fits") ? outputPath.slice(0, -5) : outputPath;
        let finalPath = base + ".fits";

        Console.writeln("Saving integrated image to: " + finalPath);
        win.saveAs(
            finalPath,     // file path
            false,         // query format options
            false,         // warning on format features
            false,         // strict image writing mode
            false           // overwrite protection
        );

    } else {
        throw new Error("No active image window after ImageIntegration — integration likely failed.");
    }


    Console.writeln("Done.");
}

main();